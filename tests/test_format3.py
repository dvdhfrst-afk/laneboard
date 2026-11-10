"""File format 3: old files upgrade with everything kept, and the load check covers the new parts."""
import copy
import json
import os
import unittest

from laneboard import schema
from laneboard.store import BadFile, Store
from tests.test_files import FileCase, version1_file

# A boards file exactly as Laneboard 1.0.0 (file format 2) wrote it, from a real run: groups, a checklist with
# a heading and a tick, new card sections, a card with every part filled in, an archived card, and a deleted
# card whose key (REL-2) must never come back.
VERSION2 = json.loads(r"""
{"version": 2, "boards": [{"id": "b-09ba90f6", "name": "Release 2.0", "description": "Made with 1.0.0", "prefix": "REL",
 "groupLabel": "Area", "rev": 9, "created": "2026-11-10T05:48:43Z", "updated": "2026-11-10T05:48:43Z",
 "columns": [{"id": "backlog", "name": "Backlog", "limit": null}, {"id": "ready", "name": "Ready", "limit": null},
  {"id": "in-progress", "name": "In progress", "limit": 2}, {"id": "review", "name": "Review", "limit": null},
  {"id": "done", "name": "Done", "limit": null}],
 "groups": [{"id": "web", "name": "Web", "prefix": "WEB", "color": "teal", "link": "https://example.com/web",
  "details": [{"label": "Owner", "value": "Sam"}]}],
 "checklists": [{"id": "ship", "name": "Ship", "items": [{"id": "iaaa27c6d", "text": "Before", "heading": true},
  {"id": "iba903a99", "text": "Tests pass", "heading": false}, {"id": "ic22b3498", "text": "Notes written", "heading": false}]}],
 "template": ["Problem", "Done when"], "counters": {"WEB": 1, "REL": 3},
 "cards": [
  {"key": "WEB-1", "title": "Everything é中😀", "group": "web", "column": "in-progress", "rank": 0.5, "summary": "Two\nlines",
   "blocked": "Waiting on review", "fields": [{"label": "Size", "value": "S"}], "sections": [{"label": "Problem", "body": "One\nTwo"}],
   "notes": "Keep me.\nLine two", "checks": ["iba903a99"], "archived": null, "created": "2026-11-10T05:48:43Z",
   "updated": "2026-11-10T05:48:43Z"},
  {"key": "REL-1", "title": "Plain", "group": null, "column": "backlog", "rank": 2, "summary": "", "blocked": "", "fields": [],
   "sections": [], "notes": "", "checks": [], "archived": null, "created": "2026-11-10T05:48:43Z", "updated": "2026-11-10T05:48:43Z"},
  {"key": "REL-3", "title": "Archived one", "group": null, "column": "done", "rank": 3, "summary": "", "blocked": "", "fields": [],
   "sections": [], "notes": "old", "checks": [], "archived": "2026-11-10T05:48:43Z", "created": "2026-11-10T05:48:43Z",
   "updated": "2026-11-10T05:48:43Z"}]}]}
""")
BOARD_ADDED = {"labels": [], "properties": [], "views": [], "settings": schema.default_settings()}
CARD_ADDED = {"labels": [], "properties": {}, "tasks": []}


def version2_file():
    return copy.deepcopy(VERSION2)


def as_written(data):
    """The bytes Laneboard 1.0.0 wrote for this data."""
    return json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")


class UpgradeFromVersion2(FileCase):
    def setUp(self):
        super().setUp()
        self.old = version2_file()
        self.write(as_written(self.old))
        self.raw = self.read()

    def test_opening_and_reading_change_nothing(self):
        store = Store(self.path)
        store.list_boards()
        store.get_board("b-09ba90f6")
        self.assertEqual(store.status()["upgradedFrom"], 2)
        self.assertEqual(self.read(), self.raw)
        self.assertFalse(os.path.exists(self.path + ".v2.bak"))

    def test_every_part_survives_and_the_new_parts_start_empty(self):
        board = Store(self.path).get_board("b-09ba90f6")
        want = self.old["boards"][0]
        self.assertEqual({k: v for k, v in board.items() if k != "cards"},
                         {**{k: v for k, v in want.items() if k != "cards"}, **BOARD_ADDED})
        self.assertEqual(board["cards"], [{**card, **CARD_ADDED} for card in want["cards"]])

    def test_first_save_writes_format_3_and_keeps_the_original_byte_for_byte(self):
        store = Store(self.path)
        store.update_card("b-09ba90f6", "REL-1", {"title": "Plain, renamed"})
        with open(self.path + ".v2.bak", "rb") as f:
            self.assertEqual(f.read(), self.raw)
        saved = json.loads(self.read())
        self.assertEqual((saved["version"], saved["templates"]), (3, []))
        board = saved["boards"][0]
        self.assertEqual(board["rev"], 10)
        self.assertEqual(board["settings"], schema.default_settings())
        cards = {c["key"]: c for c in board["cards"]}
        old = {c["key"]: c for c in self.old["boards"][0]["cards"]}
        for key in ("WEB-1", "REL-3"):
            self.assertEqual(cards[key], {**old[key], **CARD_ADDED}, key)
        self.assertEqual(store.status()["upgradedFrom"], None)
        self.assertEqual(store.create_card("b-09ba90f6", {"title": "Next"})[1], "REL-4", "a deleted card's key stays used")

    def test_the_kept_original_is_never_overwritten(self):
        store = Store(self.path)
        store.update_card("b-09ba90f6", "REL-1", {"title": "One"})
        store.update_card("b-09ba90f6", "REL-1", {"title": "Two"})
        Store(self.path).update_card("b-09ba90f6", "REL-1", {"title": "Three"})
        with open(self.path + ".v2.bak", "rb") as f:
            self.assertEqual(f.read(), self.raw)

    def test_new_parts_work_on_an_upgraded_board(self):
        store = Store(self.path)
        store.update_board("b-09ba90f6", {"labels": ["Bug"], "properties": [{"name": "Points", "type": "number"}]})
        board = store.update_card("b-09ba90f6", "WEB-1", {"labels": ["Bug"], "properties": {"points": 3},
                                                           "tasks": ["Write it"]})
        card = next(c for c in board["cards"] if c["key"] == "WEB-1")
        self.assertEqual((card["labels"], card["properties"], card["tasks"][0]["text"]), (["bug"], {"points": 3}, "Write it"))
        self.assertEqual((card["notes"], card["checks"]), ("Keep me.\nLine two", ["iba903a99"]))
        schema.load_bytes(self.read(), "the saved file")


class UpgradeFromVersion1(FileCase):
    def setUp(self):
        super().setUp()
        self.old = version1_file()
        self.write(json.dumps(self.old))
        self.raw = self.read()

    def test_a_version_1_file_goes_through_2_to_3(self):
        data, start = schema.load_bytes(self.raw, "old")
        self.assertEqual((start, data["version"], data["templates"]), (1, 3, []))
        card = data["boards"][0]["cards"][0]
        self.assertEqual((card["archived"], card["labels"], card["properties"], card["tasks"]), (None, [], {}, []))

    def test_first_save_keeps_it_as_v1_bak(self):
        store = Store(self.path)
        store.update_card("b-0a1b2c3d", "T-2", {"title": "Changed"})
        with open(self.path + ".v1.bak", "rb") as f:
            self.assertEqual(f.read(), self.raw)
        self.assertFalse(os.path.exists(self.path + ".v2.bak"))
        saved = json.loads(self.read())
        self.assertEqual(saved["version"], 3)
        web = next(c for c in saved["boards"][0]["cards"] if c["key"] == "WEB-9")
        self.assertEqual({k: v for k, v in web.items() if k not in CARD_ADDED and k != "archived"}, self.old["boards"][0]["cards"][0])
        self.assertEqual(saved["boards"][0]["rev"], 42)

    def test_the_upgrade_steps_are_in_order(self):
        self.assertEqual(sorted(schema.UPGRADES), list(range(1, schema.SCHEMA_VERSION)))


class Version3Check(FileCase):
    """Bad version 3 files are refused with a message naming the place, and are left exactly as they were."""

    def good(self):
        data = version2_file()
        data["version"] = 3
        data["templates"] = []
        board = data["boards"][0]
        board.update(labels=[{"id": "bug", "name": "Bug", "color": "rose"}],
                     properties=[{"id": "points", "name": "Points", "type": "number", "onCard": True,
                                  "min": None, "max": None, "decimals": 0, "unit": ""}],
                     views=[{"id": "bugs", "name": "Bugs", "query": "label:bug", "sort": ""}],
                     settings={"cardFace": ["labels", "points"], "limitPolicy": "refuse", "defaultView": "bugs"})
        for card in board["cards"]:
            card.update(labels=["bug"], properties={"points": 2}, tasks=[{"id": "t1", "text": "Do it", "done": False}])
        return data

    def assert_refused(self, data, *phrases):
        self.write(json.dumps(data))
        raw = self.read()
        with self.assertRaises(BadFile) as caught:
            Store(self.path)
        for phrase in phrases:
            self.assertIn(phrase, str(caught.exception))
        self.assertEqual(self.read(), raw, "a refused file must not be touched")

    def test_a_good_file_loads_as_it_is(self):
        data = self.good()
        self.write(json.dumps(data))
        board = Store(self.path).get_board("b-09ba90f6")
        self.assertEqual(board["settings"], data["boards"][0]["settings"])
        self.assertEqual(board["cards"][0]["tasks"], [{"id": "t1", "text": "Do it", "done": False}])

    def test_a_hand_written_file_gets_the_defaults(self):
        self.write(json.dumps({"version": 3, "boards": [{"id": "b1", "name": "Hand", "columns": [{"id": "a", "name": "A"}],
                                                         "labels": [{"id": "x", "name": "X"}], "settings": {},
                                                         "views": [{"id": "v", "name": "V"}],
                                                         "cards": [{"key": "T-1", "title": "T", "column": "a",
                                                                    "tasks": [{"id": "a1", "text": "One"}]}]}]}))
        store = Store(self.path)
        board = store.get_board("b1")
        self.assertEqual(board["labels"][0]["color"], "slate")
        self.assertEqual(board["settings"], schema.default_settings())
        self.assertEqual(board["views"][0], {"id": "v", "name": "V", "query": "", "sort": ""})
        card = board["cards"][0]
        self.assertEqual((card["labels"], card["properties"], card["tasks"][0]["done"]), ([], {}, False))
        self.assertEqual(store.all_data()["templates"], [])

    def broken(self, change):
        data = self.good()
        change(data, data["boards"][0], data["boards"][0]["cards"][0])
        return data

    def test_bad_parts_name_the_place(self):
        cases = [
            (lambda d, b, c: b.update(labels="x"), "labels must be a list"),
            (lambda d, b, c: b["labels"].append({"name": "No id"}), "each label needs an id and a name"),
            (lambda d, b, c: b["labels"].append({"id": "bug", "name": "Again"}), "two labels share the id 'bug'"),
            (lambda d, b, c: b["labels"][0].update(color="plaid"), "label 'Bug' has an unknown color"),
            (lambda d, b, c: c.update(labels="bug"), "card WEB-1: labels must be a list of label ids"),
            (lambda d, b, c: c.update(labels=["gone"]), "card WEB-1: it has a label that doesn't exist"),
            (lambda d, b, c: c.update(labels=[["bug"]]), "labels must be a list of label ids"),
            (lambda d, b, c: c.update(labels=["bug", "bug"]), "it lists a label twice"),
            (lambda d, b, c: c.update(tasks={}), "card WEB-1: tasks must be a list"),
            (lambda d, b, c: c.update(tasks=[{"text": "No id"}]), "each task needs an id and text"),
            (lambda d, b, c: c.update(tasks=[{"id": "a", "text": 5}]), "each task needs an id and text"),
            (lambda d, b, c: c.update(tasks=[{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]), "two tasks share the id 'a'"),
            (lambda d, b, c: c.update(tasks=[{"id": "a", "text": "x", "done": "yes"}]), "done must be true or false"),
            (lambda d, b, c: b.update(views={}), "views must be a list"),
            (lambda d, b, c: b["views"].append({"id": "v2"}), "each saved view needs an id and a name"),
            (lambda d, b, c: b["views"].append({"id": "bugs", "name": "Again"}), "two saved views share the id 'bugs'"),
            (lambda d, b, c: b["views"][0].update(query=5), "the query of saved view 'Bugs' must be text"),
            (lambda d, b, c: b.update(settings=[]), "settings must be an object"),
            (lambda d, b, c: b["settings"].update(cardFace="labels"), "settings.cardFace must be a list"),
            (lambda d, b, c: b["settings"].update(cardFace=["labels", "colour"]), "settings.cardFace lists 'colour'"),
            (lambda d, b, c: b["settings"].update(cardFace=["labels", "labels"]), "lists something twice"),
            (lambda d, b, c: b["settings"].update(limitPolicy="stop"), "settings.limitPolicy must be warn or refuse"),
            (lambda d, b, c: b["settings"].update(defaultView="gone"), "settings.defaultView must be null or the id"),
            (lambda d, b, c: b["settings"].update(defaultView=["bugs"]), "settings.defaultView must be null or the id"),
            (lambda d, b, c: b["properties"][0].update(type="date"), "property 'Points' has no known type"),
            (lambda d, b, c: c.update(properties={"size": 3}), "a property that doesn't exist ('size')"),
            (lambda d, b, c: c.update(properties={"points": "three"}), "the value of 'Points' must be a number"),
            (lambda d, b, c: d.update(templates={}), "templates must be a list"),
            (lambda d, b, c: d.update(templates=[{"id": "t"}]), "each saved template needs an id and a name"),
            (lambda d, b, c: d.update(templates=["basic"]), "each saved template needs an id and a name"),
        ]
        for change, phrase in cases:
            with self.subTest(phrase=phrase):
                self.assert_refused(self.broken(change), phrase)

    def test_the_card_face_follows_a_removed_property_only_through_the_store(self):
        data = self.good()
        data["boards"][0]["properties"] = []
        for card in data["boards"][0]["cards"]:
            card["properties"] = {}
        self.assert_refused(data, "settings.cardFace lists 'points'")

    def test_unknown_fields_in_the_new_parts_are_kept(self):
        data = self.good()
        board = data["boards"][0]
        board["labels"][0]["note"] = "mine"
        board["settings"]["futureSetting"] = 1
        board["cards"][0]["tasks"][0]["extra"] = True
        data["templates"] = [{"id": "t", "name": "Mine", "whatever": [1]}]
        self.write(json.dumps(data))
        store = Store(self.path)
        store.update_card("b-09ba90f6", "REL-1", {"title": "Changed"})
        store.update_board("b-09ba90f6", {"settings": {"limitPolicy": "warn"}})
        saved = json.loads(self.read())
        board = saved["boards"][0]
        self.assertEqual(board["labels"][0]["note"], "mine")
        self.assertEqual(board["settings"]["futureSetting"], 1)
        self.assertTrue(board["cards"][0]["tasks"][0]["extra"])
        self.assertEqual(saved["templates"], data["templates"])


if __name__ == "__main__":
    unittest.main()
