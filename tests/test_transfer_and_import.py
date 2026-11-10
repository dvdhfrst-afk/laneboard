"""Moving a card to another board, and importing boards that carry labels, properties, views, settings and tasks."""
import json
import os
import tempfile
import unittest

from laneboard import schema
from laneboard.schema import COLORS
from laneboard.store import Conflict, NotFound, StaleRevision, Store, StoreError


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "boards.json")
        self.store = Store(self.path)
        self.source = self.store.list_boards()[0]["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def board(self, board_id):
        return self.store.get_board(board_id)

    def saved(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def unchanged(self, call, kind, *phrases):
        before = self.saved()
        with self.assertRaises(kind) as caught:
            call()
        for phrase in phrases:
            self.assertIn(phrase, str(caught.exception))
        self.assertEqual(self.saved(), before, "a refused change must change nothing")
        return str(caught.exception)


class TransferTest(Case):
    def setUp(self):
        super().setUp()
        self.store.update_board(self.source, {
            "groups": [{"name": "Website", "prefix": "WEB"}, {"name": "Mobile", "prefix": "MOB"}],
            "labels": ["Bug", "Docs"],
            "properties": [{"name": "Points", "type": "number"},
                           {"name": "Priority", "type": "choice", "choices": ["Low", "High"]},
                           {"name": "Owner", "type": "text"}],
            "checklists": [{"name": "Ship", "items": [{"text": "Tested"}, {"text": "Shipped"}]}],
            "template": ["Problem"]})
        ticks = [item["id"] for item in self.board(self.source)["checklists"][0]["items"]]
        _, self.key = self.store.create_card(self.source, {
            "title": "Move me", "column": "in-progress", "group": "website", "labels": ["Bug", "Docs"],
            "properties": {"points": 3, "priority": "high", "owner": "Front desk"}, "checks": ticks,
            "summary": "Short", "blocked": "Waiting", "notes": "Notes é\nline two",
            "fields": [{"label": "Size", "value": "Large"}], "sections": [{"label": "Problem", "body": "Body"}],
            "tasks": ["One", {"text": "Two", "done": True}]})
        self.target = self.store.create_board("Elsewhere")["id"]
        self.store.update_board(self.target, {
            "prefix": "EL", "groups": [{"name": "website", "prefix": "W"}],
            "labels": [{"name": "BUG", "color": "rose"}],
            "properties": [{"name": "priority", "type": "multi", "choices": ["High", "Medium"]},
                           {"name": "Points", "type": "rating", "max": 5}],
            "checklists": [{"name": "ship", "items": [{"text": "tested"}]}],
            "columns": [{"name": "Doing"}, {"name": "Backlog"}, {"name": "In progress"}]})
        self.store.create_card(self.target, {"title": "Already there"})

    def move(self, **options):
        return self.store.transfer_card(self.source, self.key, self.target, **options)

    def test_the_card_moves_in_one_save_with_both_revs_bumped(self):
        source_rev, target_rev = self.board(self.source)["rev"], self.board(self.target)["rev"]
        card = next(c for c in self.board(self.source)["cards"] if c["key"] == self.key)
        result = self.move(rev=source_rev, target_rev=target_rev)
        self.assertEqual(set(result), {"board", "target", "key", "dropped"})
        self.assertEqual((result["board"]["rev"], result["target"]["rev"]), (source_rev + 1, target_rev + 1))
        self.assertEqual(result["board"]["cards"], [])
        moved = result["target"]["cards"][-1]
        self.assertEqual(moved["key"], result["key"])
        self.assertEqual(result["key"], "W-1", "the key comes from the target's group prefix and counters")
        for part in ("title", "notes", "sections", "fields", "tasks", "summary", "blocked", "created"):
            self.assertEqual(moved[part], card[part], part)
        saved = {b["id"]: b for b in self.saved()["boards"]}
        self.assertEqual(saved[self.source]["cards"], [])
        self.assertEqual(saved[self.target]["cards"][-1], moved)
        self.assertEqual((saved[self.source]["rev"], saved[self.target]["rev"]), (source_rev + 1, target_rev + 1))
        schema.check_data(self.saved(), "the saved file")

    def test_parts_carry_by_name_and_the_rest_are_listed(self):
        result = self.move()
        moved = result["target"]["cards"][-1]
        target = result["target"]
        self.assertEqual(moved["group"], target["groups"][0]["id"])
        self.assertEqual(moved["labels"], [target["labels"][0]["id"]])
        self.assertEqual(moved["properties"], {"priority": ["high"], "points": 3})
        self.assertEqual(moved["checks"], [target["checklists"][0]["items"][0]["id"]])
        self.assertEqual(result["dropped"], ["Label: Docs", "Owner: Front desk", "Tick: Shipped"])

    def test_nothing_carries_that_does_not_fit(self):
        self.store.update_card(self.source, self.key, {"properties": {"points": 9, "priority": "low"}, "group": "mobile"})
        result = self.move()
        moved = result["target"]["cards"][-1]
        self.assertEqual((moved["group"], moved["properties"]), (None, {}))
        self.assertEqual(result["key"], "EL-2")
        self.assertIn("Group: Mobile", result["dropped"])
        self.assertIn("Points: 9", result["dropped"])
        self.assertIn("Priority: Low", result["dropped"])

    def test_a_choice_carries_by_name_and_never_by_an_id_another_choice_kept(self):
        # Both boards made "High" with the id high; on the target it has since been renamed Urgent.
        for board in (self.source, self.target):
            self.store.update_board(board, {"properties": [{"name": "Level", "type": "choice", "choices": ["Low", "High"]},
                                                           {"name": "Areas", "type": "multi", "choices": ["Web", "iOS"]}]})
        props = self.board(self.target)["properties"]
        self.store.update_board(self.target, {"properties": [
            dict(props[0], choices=[dict(c, name="Urgent") if c["id"] == "high" else c for c in props[0]["choices"]]),
            dict(props[1], choices=[dict(c, name="iPhone") if c["id"] == "ios" else c for c in props[1]["choices"]])]})
        self.store.update_card(self.source, self.key, {"properties": {"level": "high", "areas": ["ios"]}})
        result = self.move()
        self.assertEqual(result["target"]["cards"][-1]["properties"], {})
        self.assertEqual([d for d in result["dropped"] if d.startswith(("Level", "Areas"))], ["Level: High", "Areas: iOS"])

    def test_the_column_is_named_or_matched_by_name_or_the_first(self):
        result = self.move()
        self.assertEqual(result["target"]["cards"][-1]["column"], "in-progress", "the column with the same name")
        self.assertEqual(result["target"]["cards"][-1]["rank"], max(c["rank"] for c in result["target"]["cards"]))
        back = self.store.transfer_card(self.target, result["key"], self.source, column=" DONE ")
        self.assertEqual(back["target"]["cards"][-1]["column"], "done", "a column named, ignoring case")
        again = self.store.transfer_card(self.source, back["key"], self.target, column="backlog")
        self.assertEqual(again["target"]["cards"][-1]["column"], "backlog", "a column by id")
        last = self.store.transfer_card(self.target, again["key"], self.source)
        self.assertEqual(last["target"]["cards"][-1]["column"], "backlog")
        self.store.update_card(self.source, last["key"], {"column": "review"})
        first = self.store.transfer_card(self.source, last["key"], self.target)
        self.assertEqual(first["target"]["cards"][-1]["column"], "doing", "no same-named column: the first")

    def test_refusals_change_nothing(self):
        self.unchanged(lambda: self.move(column="Nowhere"), StoreError, 'Elsewhere has no column called "Nowhere". Columns: Doing, Backlog, In progress.')
        self.unchanged(lambda: self.move(column=3), StoreError, "column's name or id")
        self.unchanged(lambda: self.store.transfer_card(self.source, self.key, self.source), StoreError, "already on this board")
        self.unchanged(lambda: self.store.transfer_card(self.source, self.key, "nope"), NotFound, "The board to move it to doesn't exist")
        self.unchanged(lambda: self.store.transfer_card("nope", self.key, self.target), NotFound, "That board doesn't exist")
        self.unchanged(lambda: self.store.transfer_card(self.source, "T-99", self.target), NotFound, "T-99")
        self.unchanged(lambda: self.move(rev=self.board(self.source)["rev"] - 1), StaleRevision)
        self.unchanged(lambda: self.move(target_rev=self.board(self.target)["rev"] - 1), StaleRevision)

    def test_a_full_column_on_a_refusing_board_takes_no_card(self):
        columns = [dict(c, limit=1) for c in self.board(self.target)["columns"]]
        self.store.update_board(self.target, {"columns": columns, "settings": {"limitPolicy": "refuse"}})
        self.unchanged(lambda: self.move(column="Doing"), Conflict, "Doing is full")
        self.move(column="Backlog")

    def test_an_archived_card_moves_and_stays_archived(self):
        self.store.update_board(self.target, {"settings": {"limitPolicy": "refuse"},
                                              "columns": [dict(c, limit=1) for c in self.board(self.target)["columns"]]})
        self.store.update_card(self.source, self.key, {"archived": True})
        moved = self.move(column="Doing")["target"]["cards"][-1]
        self.assertTrue(moved["archived"])

    def test_a_board_already_handed_out_never_changes(self):
        held = [self.board(self.source), self.board(self.target)]
        snapshot = json.dumps(held, sort_keys=True)
        self.move()
        self.assertEqual(json.dumps(held, sort_keys=True), snapshot)


class LooseImportTest(Case):
    LOOSE = {
        "name": "Planning", "prefix": "PL",
        "columns": ["To do", {"name": "Doing", "limit": 2}, "Done"],
        "groups": [{"name": "Web"}],
        "labels": ["Bug", {"name": "Docs", "color": "amber"}],
        "properties": [{"name": "Points", "type": "number", "min": 0},
                       {"name": "Platforms", "type": "multi", "choices": ["iOS", {"name": "Web", "color": "teal"}]},
                       {"name": "Reviewed", "type": "checkbox"},
                       {"name": "Effort", "type": "rating", "max": 5}],
        "views": [{"name": "Bugs", "query": "label:bug", "sort": "-points"}],
        "settings": {"cardFace": ["labels", "Points"], "limitPolicy": "refuse", "defaultView": "bugs"},
        "cards": [
            {"title": "One", "column": "doing", "group": "web", "labels": ["bug", "New one", "Another"],
             "properties": {"Points": "3", "platforms": "iOS, Web", "Reviewed": "yes", "Effort": 4},
             "tasks": ["Plan", {"text": "Build", "done": True}]},
            {"title": "Two", "column": "Doing", "archived": True, "properties": {"points": 5}},
            {"title": "Three", "key": "PL-7", "column": "Done", "labels": ["Docs"]},
        ]}

    def test_the_loose_shape_imports(self):
        board = self.store.import_data(json.loads(json.dumps(self.LOOSE)))[0]
        self.assertEqual([c["name"] for c in board["columns"]], ["To do", "Doing", "Done"])
        self.assertEqual(board["groups"][0]["prefix"], "PL", "a group with no prefix takes the board's")
        self.assertEqual([(lab["name"], lab["color"]) for lab in board["labels"]],
                         [("Bug", COLORS[0]), ("Docs", "amber"), ("New one", COLORS[2]), ("Another", COLORS[3])])
        self.assertEqual([p["id"] for p in board["properties"]], ["points", "platforms", "reviewed", "effort"])
        self.assertEqual(board["views"], [{"id": "bugs", "name": "Bugs", "query": "label:bug", "sort": "-points"}])
        self.assertEqual(board["settings"], {"cardFace": ["labels", "points"], "limitPolicy": "refuse", "defaultView": "bugs"})
        one, two, three = board["cards"]
        self.assertEqual(one["labels"], ["bug", "new-one", "another"])
        self.assertEqual(one["properties"], {"points": 3, "platforms": ["ios", "web"], "reviewed": True, "effort": 4})
        self.assertEqual([(t["text"], t["done"]) for t in one["tasks"]], [("Plan", False), ("Build", True)])
        self.assertTrue(two["archived"])
        self.assertEqual((two["column"], two["properties"]), ("doing", {"points": 5}))
        self.assertEqual((three["key"], three["labels"], three["tasks"]), ("PL-7", ["docs"], []))
        schema.check_data(self.saved(), "the saved file")

    def test_errors_name_the_board_card_and_part(self):
        cases = [
            ({"cards": [{"title": "Fine"}, {"title": "Bad", "properties": {"Points": -1}}]},
             'Board "Planning", card number 2 ("Bad"): Points must be a number of 0 or more.'),
            ({"cards": [{"title": "Odd", "properties": {"Colour": "red"}}]},
             'Board "Planning", card number 1 ("Odd"): There\'s no property called "Colour". Properties: Points, Platforms, Reviewed, Effort.'),
            ({"cards": [{"title": "Odd", "properties": {"Platforms": "Android"}}]}, 'card number 1 ("Odd"): Platforms has no choice'),
            ({"cards": [{"title": "Odd", "tasks": [{"text": ""}]}]}, 'card number 1 ("Odd"): A task can\'t be empty'),
            ({"cards": [{"title": "Odd", "labels": "Bug"}]}, 'card number 1 ("Odd"): Labels must be a list'),
            ({"settings": {"defaultView": "Mine"}}, 'Board "Planning": There\'s no saved view called "Mine". Saved views: Bugs.'),
            ({"settings": {"cardFace": ["colour"]}}, 'Board "Planning": A card can\'t show "colour"'),
            ({"labels": ["Bug", "bug"]}, 'Board "Planning": There are two labels named bug.'),
            ({"properties": [{"name": "Points", "type": "dates"}]}, 'Board "Planning": '),
            ({"views": [{"name": "A", "query": 5}]}, 'Board "Planning": '),
            ({"cards": [{"title": "A", "column": "doing"}, {"title": "B", "column": "doing"}, {"title": "C", "column": "doing"}]},
             'Board "Planning": That would put 3 cards in Doing, over its limit of 2, and this board refuses cards once a column is full.'),
        ]
        for change, message in cases:
            with self.subTest(message=message):
                raw = json.loads(json.dumps({**self.LOOSE, **change}))
                self.unchanged(lambda: self.store.import_data(raw), StoreError, message)

    def test_a_file_of_boards_is_all_or_nothing_and_names_the_board(self):
        good = json.loads(json.dumps(self.LOOSE))
        bad = {"name": "Second", "cards": [{"title": "X", "tasks": "do it"}]}
        message = self.unchanged(lambda: self.store.import_data({"version": 3, "boards": [good, bad]}), StoreError)
        self.assertTrue(message.startswith('Board number 2 ("Second"), card number 1 ("X"): '), message)
        self.assertEqual(len(self.store.list_boards()), 1)

    def test_a_warning_board_takes_any_number(self):
        raw = json.loads(json.dumps({**self.LOOSE, "settings": {"limitPolicy": "warn"},
                                     "cards": [{"title": t, "column": "Doing"} for t in "ABC"]}))
        board = self.store.import_data(raw)[0]
        self.assertEqual(len(board["cards"]), 3)


class NativeRoundTripTest(Case):
    def test_an_export_with_every_new_part_comes_back_exactly(self):
        board = self.store.update_board(self.source, {
            "labels": ["Bug", {"name": "Docs", "color": "violet"}],
            "properties": [{"name": "Points", "type": "number", "decimals": 1, "unit": "h"},
                           {"name": "Stage", "type": "choice", "choices": ["Draft", "Final"]},
                           {"name": "Link", "type": "url"}],
            "views": [{"name": "Docs", "query": "label:docs", "sort": "title"}]})
        board = self.store.update_board(self.source, {
            "labels": [dict(board["labels"][0], name="Defect"), board["labels"][1]],
            "properties": [dict(board["properties"][0], name="Hours"), *board["properties"][1:]],
            "views": [dict(board["views"][0], name="Writing")],
            "settings": {"cardFace": ["blocked", "Hours", "labels"], "limitPolicy": "refuse", "defaultView": "Writing"}})
        board, key = self.store.create_card(self.source, {
            "title": "Every part", "labels": ["Defect", "Docs"], "tasks": ["A", {"text": "B", "done": True}],
            "properties": {"points": 2.5, "stage": "final", "link": "https://example.com/a"}})
        made = self.store.import_data(json.loads(json.dumps(board)))[0]
        self.assertNotEqual(made["id"], board["id"])
        for field in ("labels", "properties", "views", "settings", "cards", "counters"):
            self.assertEqual(made[field], board[field], field)

    def test_a_whole_version_3_file_imports_with_its_templates_ignored(self):
        self.store.create_card(self.source, {"title": "x"})
        whole = self.saved()
        whole["templates"] = [{"id": "t1", "name": "Saved"}]
        other = Store(os.path.join(self.tmp.name, "other.json"))
        made = other.import_data(whole)
        self.assertEqual(made[0]["cards"][0]["title"], "x")
        self.assertEqual(other.all_data()["templates"], [])


if __name__ == "__main__":
    unittest.main()
