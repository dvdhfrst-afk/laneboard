"""Running a search: counts, several boards at once, and plain words matching what the page always matched."""
import os
import tempfile
import unittest

from laneboard import query as Q
from laneboard.store import Store


class Boards(unittest.TestCase):
    """Three boards through the real store: one with Points and a Bug label, one with Points only, one with neither."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(os.path.join(tmp.name, "boards.json"), starter=False)
        self.app = self.make("App", labels=["Bug"], properties=[{"name": "Points", "type": "number"}],
                             cards=[("Crash on start", {"labels": ["Bug"], "properties": {"points": 5}}),
                                    ("Slow search", {"properties": {"points": 2}}), ("Old crash", {})])
        self.store.update_card(self.app, "T-3", {"archived": True})
        self.site = self.make("Site", properties=[{"name": "Points", "type": "number"}],
                              cards=[("Crash in the footer", {"properties": {"points": 1}}), ("New logo", {})])
        self.home = self.make("Home", cards=[("Fix the crash barrier", {}), ("Paint the fence", {})])

    def make(self, name, cards=(), **parts):
        board = self.store.create_board(name)["id"]
        if parts:
            self.store.update_board(board, parts)
        for title, values in cards:
            self.store.create_card(board, {"title": title, **values})
        return board

    def boards(self):
        return self.store.all_data()["boards"]

    def found(self, text="", sort="", archived=False):
        return [(board["name"], keys, considered) for board, keys, considered in Q.search_boards(self.boards(), text, sort, archived)]


class Counting(Boards):
    def test_run_counts_the_cards_it_looked_at(self):
        board = self.store.get_board(self.app)
        self.assertEqual(Q.run(board, "crash"), (["T-1"], 2))
        self.assertEqual(Q.run(board, "crash", archived=True), (["T-1", "T-3"], 3))
        self.assertEqual(Q.run(board, "crash is:archived"), (["T-3"], 3))
        self.assertEqual(Q.run(board, ""), (["T-1", "T-2"], 2))
        self.assertEqual(Q.search(board, "crash"), ["T-1"])


class SeveralBoards(Boards):
    def test_words_search_every_board_and_only_boards_with_matches_come_back(self):
        self.assertEqual(self.found("crash"), [("App", ["T-1"], 2), ("Site", ["T-1"], 2), ("Home", ["T-1"], 2)])
        self.assertEqual(self.found("logo"), [("Site", ["T-2"], 2)])
        self.assertEqual(self.found("nothing like this"), [])

    def test_a_board_without_what_the_search_names_is_passed_over(self):
        self.assertEqual(self.found("crash points:>=1"), [("App", ["T-1"], 2), ("Site", ["T-1"], 2)])
        self.assertEqual(self.found("label:bug"), [("App", ["T-1"], 2)])

    def test_a_sort_a_board_lacks_leaves_it_in_board_order(self):
        self.assertEqual(self.found("", "-points"), [("App", ["T-1", "T-2"], 2), ("Site", ["T-1", "T-2"], 2),
                                                     ("Home", ["T-1", "T-2"], 2)])
        self.assertEqual(self.found("sort:points"), [("App", ["T-2", "T-1"], 2), ("Site", ["T-1", "T-2"], 2),
                                                     ("Home", ["T-1", "T-2"], 2)])

    def test_an_error_only_when_no_board_can_read_it(self):
        with self.assertRaises(Q.QueryError) as caught:
            self.found("crash pionts:3")
        self.assertEqual((caught.exception.position, caught.exception.part), (6, "query"))
        self.assertIn('There\'s no property called "pionts". Properties: Points.', str(caught.exception))
        with self.assertRaises(Q.QueryError) as caught:
            self.found("", "-size")
        self.assertEqual((caught.exception.position, caught.exception.part), (1, "sort"))

    def test_the_error_from_the_board_that_read_furthest(self):
        self.store.update_board(self.site, {"groups": [{"name": "Front end", "prefix": "T"}]})
        for text, message, position in (("label:bgu", 'There\'s no label called "bgu". Labels: Bug.', 6),
                                        ("group:back", 'There\'s no group called "back". Groups: Front end.', 6),
                                        ("crash label:bug group:front", "This board has no groups.", 16)):
            with self.subTest(text=text), self.assertRaises(Q.QueryError) as caught:
                self.found(text)
            self.assertEqual((str(caught.exception), caught.exception.position), (message, position))

    def test_text_that_is_never_readable_is_an_error_even_with_no_boards(self):
        for board in self.boards():
            self.store.delete_board(board["id"])
        self.assertEqual(self.found("anything"), [])
        with self.assertRaises(Q.QueryError):
            self.found("x" * 501)
        with self.assertRaises(Q.QueryError) as caught:
            self.found("", "x" * 501)
        self.assertEqual(caught.exception.part, "sort")


class PlainWords(unittest.TestCase):
    """Words with no syntax match what the page's search box matched before the search language: the key, title,
    summary, blocked line, notes, group name and field and section text, every word somewhere, ignoring capitals."""

    def test_each_part_the_page_searched(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(os.path.join(tmp, "boards.json"), starter=False)
            board = store.create_board("Work")["id"]
            store.update_board(board, {"groups": [{"name": "Garden shed", "prefix": "T", "color": "teal"}]})
            parts = {"title": "Alpha", "summary": "Bravo", "blocked": "Charlie", "notes": "Delta",
                     "fields": [{"label": "Echo", "value": "Foxtrot"}], "sections": [{"label": "Golf", "body": "Hotel"}],
                     "group": "garden-shed"}
            store.create_card(board, {**parts, "title": "Alpha"})
            store.create_card(board, {"title": "Nothing here"})
            found = store.get_board(board)
        for words in ("t-1", "ALPHA", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "garden shed",
                      "alpha hotel", "alp", "fox"):
            self.assertEqual(Q.search(found, words), ["T-1"], words)
        self.assertEqual(Q.search(found, "alpha nothing"), [])
        self.assertEqual(Q.search(found, ""), ["T-1", "T-2"])


if __name__ == "__main__":
    unittest.main()
