"""Done columns: which columns' cards count as done, and the one rule every part of Laneboard uses to tell."""
import copy
import json
import os
import tempfile
import unittest

from laneboard import query as Q
from laneboard import schema, stats
from laneboard import templates as T
from laneboard.formats import htmlpage, markdown, trello
from laneboard.store import Store, StoreError

from tests.test_query import BOARD


def board_with(*done):
    board = copy.deepcopy(BOARD)
    for col in board["columns"]:
        col["done"] = col["id"] in done
    return board


class TheRule(unittest.TestCase):
    def test_the_last_column_counts_when_none_is_marked(self):
        self.assertEqual(schema.done_columns(BOARD), {"done"})
        self.assertEqual(schema.done_columns(board_with()), {"done"})
        self.assertEqual(schema.done_columns({"columns": []}), set())

    def test_marked_columns_are_the_only_done_ones(self):
        self.assertEqual(schema.done_columns(board_with("review")), {"review"})
        self.assertEqual(schema.done_columns(board_with("review", "done")), {"review", "done"})

    def test_search(self):
        self.assertEqual(Q.search(BOARD, "is:done"), ["T-6"])
        board = board_with("review")
        self.assertEqual(Q.search(board, "is:done"), ["T-5"])  # T-6 sits in Done, which is no longer marked
        self.assertEqual(Q.search(board_with("review", "done"), "is:done"), ["T-5", "T-6"])
        self.assertEqual(Q.search(board, "-is:done"), [k for k in Q.search(board, "") if k != "T-5"])

    def test_summary(self):
        s = stats.summarize(board_with("review", "done"))
        self.assertEqual((s["done"], s["notDone"]), (2, s["cards"] - 2))
        self.assertEqual([c["id"] for c in s["columns"] if c["done"]], ["review", "done"])

    def test_the_web_page(self):
        html = htmlpage.export(board_with("review")).decode("utf-8")
        self.assertIn("<div><dt>Done</dt><dd>1</dd></div>", html)
        self.assertIn('<th scope="row">Review <span class="muted">(done)</span></th>', html)
        self.assertIn('<h2>Review <span class="count">1 / 1</span> <span class="muted">done</span></h2>', html)
        self.assertNotIn('<th scope="row">Done <span class="muted">(done)</span></th>', html)


class InTheFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "boards.json")
        self.store = Store(self.path)
        self.board = self.store.list_boards()[0]["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def columns(self):
        return self.store.get_board(self.board)["columns"]

    def test_a_new_board_marks_none(self):
        self.assertEqual([c["done"] for c in self.columns()], [False] * len(self.columns()))

    def test_marking_and_keeping(self):
        cols = self.columns()
        cols[1]["done"] = True
        self.store.update_board(self.board, {"columns": cols})
        self.assertTrue(self.columns()[1]["done"])
        # A column sent without done keeps the one it had.
        sent = [{"id": c["id"], "name": c["name"], "limit": c["limit"]} for c in self.columns()]
        self.store.update_board(self.board, {"columns": sent + [{"name": "Shipped"}]})
        self.assertEqual([c["done"] for c in self.columns()], [False, True] + [False] * (len(sent) - 2) + [False])
        with open(self.path, encoding="utf-8") as f:
            self.assertTrue(json.load(f)["boards"][0]["columns"][1]["done"])

    def test_refusals(self):
        cols = self.columns()
        cols[0]["done"] = "yes"
        rev = self.store.get_board(self.board)["rev"]
        with self.assertRaises(StoreError) as caught:
            self.store.update_board(self.board, {"columns": cols})
        self.assertIn("must be true or false", str(caught.exception))
        self.assertEqual(self.store.get_board(self.board)["rev"], rev)

    def test_the_file_check(self):
        with open(self.path, encoding="utf-8") as f:
            raw = json.load(f)
        raw["boards"][0]["columns"][0]["done"] = 1
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        with self.assertRaises(StoreError) as caught:
            Store(self.path).list_boards()
        self.assertIn("done must be true or false", str(caught.exception))

    def test_import_and_markdown_keep_it(self):
        board = self.store.get_board(self.board)
        board["columns"][0]["done"] = True
        again = markdown.import_(markdown.export(board)).boards[0]
        made = self.store.import_data(again)[0]
        self.assertEqual([c["done"] for c in made["columns"]], [c["done"] for c in board["columns"]])


class Templates(unittest.TestCase):
    def test_the_built_in_templates_mark_their_done_column(self):
        marked = {t["id"]: [c["name"] for c in t["board"]["columns"] if c.get("done")] for t in T.builtin_templates()}
        self.assertEqual(marked, {"basic": ["Done"], "software": ["Done"], "personal": ["Done"],
                                  "content": ["Published"], "support": ["Resolved"], "research": ["Written up"]})

    def test_a_template_from_a_board_keeps_the_marks(self):
        board = board_with("review")
        board.update(prefix="T", groupLabel="Group", groups=[], labels=[], properties=[], views=[], checklists=[],
                     settings=schema.default_settings())
        template = T.template_from_board(board, "Mine")
        self.assertEqual([c.get("done", False) for c in template["board"]["columns"]],
                         [c["done"] for c in board["columns"]])
        bad = copy.deepcopy(template)
        bad["board"]["columns"][0]["done"] = "no"
        with self.assertRaises(StoreError):
            T.check_template(bad, "That template")


class Imports(unittest.TestCase):
    def test_cards_of_an_archived_trello_list_go_to_the_done_column(self):
        data = {"name": "B", "lists": [{"id": "1", "name": "Open"}, {"id": "2", "name": "Finished"},
                                       {"id": "3", "name": "Old", "closed": True}],
                "cards": [{"name": "Gone", "idList": "3"}, {"name": "Here", "idList": "1"}]}
        result = trello.import_(json.dumps(data).encode("utf-8"))
        cards = {c["title"]: c for c in result.boards[0]["cards"]}
        self.assertEqual((cards["Gone"]["column"], cards["Gone"].get("archived")), ("Finished", True))
        self.assertEqual(cards["Here"]["column"], "Open")
        self.assertIn("came in as an archived card in Finished.", result.warnings[0])


if __name__ == "__main__":
    unittest.main()
