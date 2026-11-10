"""Board summaries: counts per column, group and label, property totals, progress, and old boards."""
import copy
import json
import os
import tempfile
import unittest

from laneboard import properties as P
from laneboard import samples, stats
from laneboard.store import Store


def card(key, column="todo", **values):
    made = {"key": key, "title": f"Card {key}", "column": column, "rank": 1, "group": None, "summary": "",
            "blocked": "", "notes": "", "fields": [], "sections": [], "checks": [], "archived": None,
            "labels": [], "properties": {}, "tasks": []}
    made.update(values)
    return made


def board(cards, **extra):
    props = P.clean_definitions({"properties": [], "cards": []}, [
        {"name": "Points", "type": "number", "decimals": 1, "unit": "pt"},
        {"name": "Hours", "type": "number"},
        {"name": "Priority", "type": "choice", "choices": ["Low", "High"]},
        {"name": "Platform", "type": "multi", "choices": ["iOS", "Web"]},
        {"name": "Reviewed", "type": "checkbox"},
        {"name": "Link", "type": "url"},
        {"name": "Confidence", "type": "rating", "max": 4},
        {"name": "Owner", "type": "text"},
    ])
    made = {"id": "b-1", "name": "Stats", "prefix": "T", "columns": [
        {"id": "todo", "name": "To do", "limit": None}, {"id": "doing", "name": "Doing", "limit": 1},
        {"id": "done", "name": "Done", "limit": 2}],
        "groups": [{"id": "web", "name": "Web", "prefix": "WEB", "color": "teal", "link": "", "details": []},
                   {"id": "api", "name": "API", "prefix": "API", "color": "violet", "link": "", "details": []}],
        "labels": [{"id": "bug", "name": "Bug", "color": "rose"}, {"id": "chore", "name": "Chore", "color": "slate"}],
        "properties": props,
        "checklists": [{"id": "rel", "name": "Release", "items": [
            {"id": "head", "text": "Before", "heading": True}, {"id": "t1", "text": "Tests", "heading": False},
            {"id": "t2", "text": "Notes", "heading": False}]}],
        "cards": cards}
    made.update(extra)
    return made


def full_board():
    return board([
        card("T-1", group="web", labels=["bug"], blocked="Waiting", checks=["t1", "head", "gone"],
             tasks=[{"id": "a", "text": "One", "done": True}, {"id": "b", "text": "Two", "done": False}],
             properties={"points": 2.5, "hours": 3, "priority": "high", "platform": ["ios", "web"], "reviewed": True,
                         "link": "https://example.com", "confidence": 3, "owner": "Sam"}),
        card("T-2", column="doing", group="web", labels=["bug", "chore"], checks=["t1", "t2"],
             properties={"points": 0.1, "priority": "low", "platform": ["web"], "confidence": 4}),
        card("T-3", column="doing", group="api", properties={"points": 0.2, "hours": 5},
             tasks=[{"id": "a", "text": "Only", "done": True}]),
        card("T-4", column="done"),
        card("T-5", column="done", archived="2026-01-01T00:00:00Z", group="api", labels=["bug"], blocked="Old",
             checks=["t1"], tasks=[{"id": "a", "text": "Hidden", "done": True}],
             properties={"points": 100, "hours": 100, "priority": "high", "reviewed": True, "confidence": 1}),
    ])


class Summary(unittest.TestCase):
    def setUp(self):
        self.board = full_board()
        self.s = stats.summarize(self.board)

    def prop(self, pid):
        return next(p for p in self.s["properties"] if p["id"] == pid)

    def test_counts(self):
        self.assertEqual((self.s["cards"], self.s["archived"], self.s["blocked"]), (4, 1, 1))
        self.assertEqual(self.s["tasks"], {"done": 2, "total": 3})
        # Two lines can be ticked on each of the four cards on the board; headings and lost ids don't count.
        self.assertEqual(self.s["checks"], {"done": 3, "total": 8})

    def test_columns(self):
        cols = {c["id"]: c for c in self.s["columns"]}
        self.assertEqual([c["id"] for c in self.s["columns"]], ["todo", "doing", "done"])
        self.assertEqual(cols["todo"], {"id": "todo", "name": "To do", "cards": 1, "limit": None, "over": False,
                                        "sums": {"points": 2.5, "hours": 3}})
        self.assertEqual(cols["doing"]["cards"], 2)
        self.assertTrue(cols["doing"]["over"])  # two cards, limit one
        self.assertEqual(cols["doing"]["sums"], {"points": 0.3, "hours": 5})  # 0.1 + 0.2 shown as 0.3
        self.assertEqual((cols["done"]["cards"], cols["done"]["over"]), (1, False))  # the archived card doesn't count
        self.assertEqual(cols["done"]["sums"], {"points": 0, "hours": 0})

    def test_groups_and_labels(self):
        self.assertEqual([(g["id"], g["cards"], g["color"]) for g in self.s["groups"]], [("web", 2, "teal"), ("api", 1, "violet")])
        self.assertEqual(self.s["noGroup"], 1)
        self.assertEqual([(lab["id"], lab["cards"]) for lab in self.s["labels"]], [("bug", 2), ("chore", 1)])
        self.assertEqual(self.s["noLabels"], 2)

    def test_numbers(self):
        points = self.prop("points")
        self.assertEqual((points["cards"], points["total"], points["min"], points["max"]), (3, 2.8, 0.1, 2.5))
        self.assertEqual(points["average"], 0.933)  # one decimal place, averages get two more
        self.assertEqual((points["unit"], points["decimals"], points["type"], points["name"]), ("pt", 1, "number", "Points"))
        hours = self.prop("hours")
        self.assertEqual((hours["total"], hours["average"], hours["min"], hours["max"]), (8, 4, 3, 5))
        self.assertIsInstance(hours["total"], int)

    def test_choices_ratings_checkboxes_and_text(self):
        self.assertEqual([(o["name"], o["cards"]) for o in self.prop("priority")["options"]], [("Low", 1), ("High", 1)])
        self.assertEqual(self.prop("priority")["cards"], 2)
        self.assertEqual([(o["id"], o["cards"]) for o in self.prop("platform")["options"]], [("ios", 1), ("web", 2)])
        self.assertEqual(self.prop("confidence"), {"id": "confidence", "name": "Confidence", "type": "rating", "cards": 2,
                                                   "average": 3.5, "outOf": 4})
        self.assertEqual(self.prop("reviewed"), {"id": "reviewed", "name": "Reviewed", "type": "checkbox", "cards": 1})
        self.assertEqual(self.prop("link")["cards"], 1)
        self.assertEqual(self.prop("owner")["cards"], 1)
        self.assertEqual([p["id"] for p in self.s["properties"]], [p["id"] for p in self.board["properties"]])

    def test_json_safe_and_pure(self):
        before = copy.deepcopy(self.board)
        again = stats.summarize(self.board)
        self.assertEqual(self.board, before)
        self.assertEqual(json.loads(json.dumps(again, allow_nan=False)), again)

    def test_no_values(self):
        s = stats.summarize(board([card("T-1")]))
        points = next(p for p in s["properties"] if p["id"] == "points")
        self.assertEqual((points["total"], points["average"], points["min"], points["max"], points["cards"]), (0, None, None, None, 0))
        rating = next(p for p in s["properties"] if p["id"] == "confidence")
        self.assertIsNone(rating["average"])

    def test_every_property_type_is_summed_up(self):
        self.assertEqual(set(stats._SUMMARIES), set(P.TYPE_NAMES))

    def test_a_card_exactly_at_the_limit_is_not_over(self):
        s = stats.summarize(board([card("T-1", column="done"), card("T-2", column="done")]))
        self.assertFalse(next(c for c in s["columns"] if c["id"] == "done")["over"])


class OldBoards(unittest.TestCase):
    def test_a_version_2_board(self):
        old = {"id": "b-1", "name": "Old", "columns": [{"id": "a", "name": "A", "limit": None}],
               "groups": [], "checklists": [], "cards": [
                   {"key": "T-1", "title": "One", "column": "a", "group": None, "blocked": "", "checks": [], "archived": None},
                   {"key": "T-2", "title": "Two", "column": "a", "group": None, "blocked": "x", "checks": [],
                    "archived": "2026-01-01T00:00:00Z"}]}
        s = stats.summarize(old)
        self.assertEqual((s["cards"], s["archived"], s["blocked"]), (1, 1, 0))
        self.assertEqual((s["labels"], s["properties"], s["tasks"], s["checks"]), ([], [], {"done": 0, "total": 0},
                                                                                  {"done": 0, "total": 0}))
        self.assertEqual(s["columns"], [{"id": "a", "name": "A", "cards": 1, "limit": None, "over": False, "sums": {}}])

    def test_a_board_from_the_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(os.path.join(tmp, "boards.json"), starter=False)
            made = store.import_board(samples.load("software-release"))
        s = stats.summarize(made)
        self.assertEqual((s["cards"], s["archived"], s["blocked"]), (5, 1, 1))
        self.assertEqual(s["checks"], {"done": 6, "total": 15})
        self.assertEqual([c["cards"] for c in s["columns"]], [2, 1, 1, 1])
        self.assertEqual([(g["name"], g["cards"]) for g in s["groups"]], [("Web app", 2), ("API", 2)])

    def test_an_empty_board(self):
        s = stats.summarize({"name": "Empty", "columns": [{"id": "a", "name": "A"}]})
        self.assertEqual(s["cards"], 0)
        self.assertEqual(s["columns"][0]["limit"], None)


if __name__ == "__main__":
    unittest.main()
