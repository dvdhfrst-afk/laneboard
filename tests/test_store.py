import json
import os
import tempfile
import time
import unittest

from laneboard.store import (Conflict, NotFound, StaleRevision, Store, StoreError, default_data_path)


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "boards.json")
        self.store = Store(self.path)
        self.board = self.store.list_boards()[0]["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def add_groups(self):
        return self.store.update_board(self.board, {"groups": [
            {"name": "Website", "prefix": "WEB", "color": "teal"},
            {"name": "Mobile app", "prefix": "APP"},
        ]})


class FirstRunTest(StoreTestCase):
    def test_creates_a_starter_board(self):
        boards = self.store.list_boards()
        self.assertEqual([b["name"] for b in boards], ["My board"])
        board = self.store.get_board(self.board)
        self.assertEqual([c["name"] for c in board["columns"]], ["Backlog", "Ready", "In progress", "Review", "Done"])
        self.assertEqual(board["rev"], 1)
        self.assertTrue(os.path.exists(self.path))

    def test_rejects_a_file_that_is_not_a_board_file(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"something": "else"}, f)
        with self.assertRaises(StoreError):
            Store(self.path)

    def test_rejects_unreadable_json(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        with self.assertRaises(StoreError):
            Store(self.path)

    def test_data_path_can_come_from_the_environment(self):
        old = os.environ.get("LANEBOARD_DATA")
        os.environ["LANEBOARD_DATA"] = os.path.join(self.tmp.name, "elsewhere.json")
        try:
            self.assertEqual(default_data_path(), os.environ["LANEBOARD_DATA"])
        finally:
            if old is None:
                del os.environ["LANEBOARD_DATA"]
            else:
                os.environ["LANEBOARD_DATA"] = old


class BoardTest(StoreTestCase):
    def test_create_rename_and_delete(self):
        board = self.store.create_board("  Side work  ", "Small jobs")
        self.assertEqual(board["name"], "Side work")
        renamed = self.store.update_board(board["id"], {"name": "Side jobs", "groupLabel": "Client"}, rev=1)
        self.assertEqual((renamed["name"], renamed["groupLabel"], renamed["rev"]), ("Side jobs", "Client", 2))
        self.store.delete_board(board["id"], rev=2)
        self.assertNotIn(board["id"], [b["id"] for b in self.store.list_boards()])

    def test_board_needs_a_name(self):
        with self.assertRaises(StoreError):
            self.store.create_board("   ")

    def test_unknown_setting_is_refused(self):
        with self.assertRaises(StoreError):
            self.store.update_board(self.board, {"colour": "red"})

    def test_prefix_must_look_like_a_key_prefix(self):
        for bad in ("vm", "1AB", "TOOLONGXX", ""):
            with self.assertRaises(StoreError):
                self.store.update_board(self.board, {"prefix": bad})

    def test_missing_board(self):
        with self.assertRaises(NotFound):
            self.store.get_board("b-missing")

    def test_columns_keep_their_ids_when_renamed_and_reordered(self):
        board = self.store.get_board(self.board)
        cols = board["columns"]
        changed = [dict(cols[1], name="Up next"), dict(cols[0]), *cols[2:], {"name": "Parked", "limit": None}]
        board = self.store.update_board(self.board, {"columns": changed})
        self.assertEqual([c["id"] for c in board["columns"]][:2], [cols[1]["id"], cols[0]["id"]])
        self.assertEqual(board["columns"][0]["name"], "Up next")
        self.assertEqual(board["columns"][-1]["id"], "parked")

    def test_column_limits(self):
        cols = self.store.get_board(self.board)["columns"]
        for bad in (0, 1000, 2.5, "3", True):
            with self.assertRaises(StoreError):
                self.store.update_board(self.board, {"columns": [dict(cols[0], limit=bad)]})
        board = self.store.update_board(self.board, {"columns": [dict(cols[0], limit="")]})
        self.assertIsNone(board["columns"][0]["limit"])

    def test_duplicate_column_names_are_refused(self):
        with self.assertRaises(StoreError):
            self.store.update_board(self.board, {"columns": [{"name": "Todo"}, {"name": "todo"}]})

    def test_a_column_with_cards_cannot_be_removed(self):
        board, _ = self.store.create_card(self.board, {"title": "Write the intro"})
        with self.assertRaises(Conflict):
            self.store.update_board(self.board, {"columns": board["columns"][1:]})
        # An empty column can go.
        board = self.store.update_board(self.board, {"columns": board["columns"][:1]})
        self.assertEqual(len(board["columns"]), 1)

    def test_a_group_with_cards_cannot_be_removed(self):
        board = self.add_groups()
        self.store.create_card(self.board, {"title": "Fix the header", "group": board["groups"][0]["id"]})
        with self.assertRaises(Conflict):
            self.store.update_board(self.board, {"groups": board["groups"][1:]})
        board = self.store.update_board(self.board, {"groups": board["groups"][:1]})
        self.assertEqual([g["name"] for g in board["groups"]], ["Website"])

    def test_group_validation(self):
        with self.assertRaises(StoreError):
            self.store.update_board(self.board, {"groups": [{"name": "A", "prefix": "A", "color": "plaid"}]})
        with self.assertRaises(StoreError):
            self.store.update_board(self.board, {"groups": [{"name": "A", "prefix": "A", "link": "javascript:alert(1)"}]})
        board = self.store.update_board(self.board, {"groups": [
            {"name": "A", "prefix": "A", "link": "https://example.com/a", "details": [{"label": "Owner", "value": "Team A"}]}]})
        self.assertEqual(board["groups"][0]["color"], "slate")
        self.assertEqual(board["groups"][0]["details"], [{"label": "Owner", "value": "Team A"}])


class CardTest(StoreTestCase):
    def test_keys_come_from_the_group_prefix_or_the_board_prefix(self):
        board = self.add_groups()
        web = board["groups"][0]["id"]
        _, k1 = self.store.create_card(self.board, {"title": "No group"})
        _, k2 = self.store.create_card(self.board, {"title": "Header", "group": web})
        _, k3 = self.store.create_card(self.board, {"title": "Footer", "group": web})
        self.assertEqual((k1, k2, k3), ("T-1", "WEB-1", "WEB-2"))

    def test_deleted_keys_are_not_reused(self):
        self.store.create_card(self.board, {"title": "One"})
        board, key = self.store.create_card(self.board, {"title": "Two"})
        self.store.delete_card(self.board, key)
        _, again = self.store.create_card(self.board, {"title": "Three"})
        self.assertEqual(again, "T-3")

    def test_new_cards_go_to_the_first_column_at_the_bottom(self):
        board, k1 = self.store.create_card(self.board, {"title": "One"})
        board, k2 = self.store.create_card(self.board, {"title": "Two"})
        cards = {c["key"]: c for c in board["cards"]}
        self.assertEqual(cards[k1]["column"], board["columns"][0]["id"])
        self.assertGreater(cards[k2]["rank"], cards[k1]["rank"])

    def test_update_merges_changes(self):
        board, key = self.store.create_card(self.board, {"title": "Draft", "summary": "First pass",
                                                         "fields": [{"label": "Size", "value": "S"}]})
        board = self.store.update_card(self.board, key, {"notes": "Started.\n\n", "column": "ready"})
        card = next(c for c in board["cards"] if c["key"] == key)
        self.assertEqual(card["title"], "Draft")
        self.assertEqual(card["summary"], "First pass")
        self.assertEqual(card["fields"], [{"label": "Size", "value": "S"}])
        self.assertEqual(card["notes"], "Started.")
        self.assertEqual(card["column"], "ready")

    def test_card_validation(self):
        _, key = self.store.create_card(self.board, {"title": "Real"})
        bad = [
            {"title": ""},
            {"title": "x" * 201},
            {"column": "nowhere"},
            {"group": "nobody"},
            {"rank": "first"},
            {"fields": [{"label": "", "value": "x"}]},
            {"sections": "not a list"},
            {"priority": "high"},
        ]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(StoreError):
                self.store.update_card(self.board, key, change)

    def test_missing_card(self):
        with self.assertRaises(NotFound):
            self.store.update_card(self.board, "T-99", {"title": "Nope"})

    def test_stale_revision_is_refused(self):
        board, _ = self.store.create_card(self.board, {"title": "One"})
        with self.assertRaises(StaleRevision):
            self.store.create_card(self.board, {"title": "Two"}, rev=board["rev"] - 1)
        board, _ = self.store.create_card(self.board, {"title": "Two"}, rev=board["rev"])
        self.assertEqual(len(board["cards"]), 2)


class FileTest(StoreTestCase):
    def test_changes_survive_a_restart_and_leave_a_backup(self):
        self.store.create_card(self.board, {"title": "Kept"})
        self.assertTrue(os.path.exists(self.path + ".bak"))
        again = Store(self.path)
        self.assertEqual([c["title"] for c in again.get_board(self.board)["cards"]], ["Kept"])

    def test_edits_to_the_file_are_picked_up(self):
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        data["boards"][0]["name"] = "Edited by hand"
        time.sleep(0.01)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.assertEqual(self.store.get_board(self.board)["name"], "Edited by hand")

    def test_failed_change_leaves_the_file_alone(self):
        with open(self.path, encoding="utf-8") as f:
            before = f.read()
        with self.assertRaises(StoreError):
            self.store.create_card(self.board, {"title": ""})
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)


class ChecklistTest(StoreTestCase):
    def add_checklist(self):
        board = self.store.update_board(self.board, {"checklists": [{"name": "Release", "items": [
            {"text": "Before", "heading": True}, {"text": "Tests pass"}, {"text": "Notes written"}]}]})
        return board["checklists"][0]

    def test_items_get_ids_and_keep_them_through_edits(self):
        cl = self.add_checklist()
        self.assertEqual(cl["id"], "release")
        heading, tests, notes = cl["items"]
        self.assertTrue(heading["heading"])
        board = self.store.update_board(self.board, {"checklists": [{"id": "release", "name": "Release", "items": [
            tests, {"text": "Changelog updated"}, notes]}]})
        ids = [i["id"] for i in board["checklists"][0]["items"]]
        self.assertEqual((ids[0], ids[2]), (tests["id"], notes["id"]))
        self.assertNotIn(ids[1], (tests["id"], notes["id"]))

    def test_each_card_keeps_its_own_ticks(self):
        cl = self.add_checklist()
        tests_id = cl["items"][1]["id"]
        self.store.create_card(self.board, {"title": "One"})
        board, _ = self.store.create_card(self.board, {"title": "Two"})
        board = self.store.update_card(self.board, "T-1", {"checks": [tests_id, tests_id, "gone", cl["items"][0]["id"]]})
        self.assertEqual({c["key"]: c["checks"] for c in board["cards"]}, {"T-1": [tests_id], "T-2": []})
        board = self.store.update_card(self.board, "T-1", {"title": "One, renamed"})
        self.assertEqual(board["cards"][0]["checks"], [tests_id])

    def test_removing_a_line_removes_its_ticks(self):
        cl = self.add_checklist()
        self.store.create_card(self.board, {"title": "One", "checks": [cl["items"][1]["id"], cl["items"][2]["id"]]})
        board = self.store.update_board(self.board, {"checklists": [{"id": "release", "name": "Release", "items": [cl["items"][2]]}]})
        self.assertEqual(board["cards"][0]["checks"], [cl["items"][2]["id"]])

    def test_checklist_validation(self):
        for bad in ("nope", [{"name": ""}], [{"name": "A"}, {"name": "a"}], [{"name": "A", "items": [{"text": ""}]}],
                    [{"name": "A", "items": "x"}]):
            with self.assertRaises(StoreError):
                self.store.update_board(self.board, {"checklists": bad})
        with self.assertRaises(StoreError):
            self.store.create_card(self.board, {"title": "One", "checks": "x"})

    def test_files_from_before_checklists_still_load(self):
        self.store.create_card(self.board, {"title": "Old"})
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        del data["boards"][0]["checklists"]
        del data["boards"][0]["cards"][0]["checks"]
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        board = Store(self.path).get_board(self.board)
        self.assertEqual((board["checklists"], board["cards"][0]["checks"]), ([], []))

    def test_import_keeps_checklists_and_ticks(self):
        cl = self.add_checklist()
        self.store.create_card(self.board, {"title": "One", "checks": [cl["items"][1]["id"]]})
        board = self.store.import_board(self.store.get_board(self.board))
        self.assertEqual(board["checklists"], [cl])
        self.assertEqual(board["cards"][0]["checks"], [cl["items"][1]["id"]])


class TemplateTest(StoreTestCase):
    def test_template_is_saved_and_checked(self):
        board = self.store.update_board(self.board, {"template": [" Problem ", "Checks"]})
        self.assertEqual(board["template"], ["Problem", "Checks"])
        for bad in ("Problem", [""], ["A", "a"], [3]):
            with self.assertRaises(StoreError):
                self.store.update_board(self.board, {"template": bad})


class ImportTest(StoreTestCase):
    def test_import_keeps_keys_and_moves_counters_past_them(self):
        board = self.store.import_board({
            "name": "Imported",
            "groupLabel": "Area",
            "columns": [{"name": "Todo"}, {"name": "Done"}],
            "groups": [{"name": "Docs", "prefix": "DOC", "color": "amber"}],
            "cards": [
                {"key": "DOC-7", "title": "Old card", "group": "docs", "column": "done"},
                {"title": "No key yet", "group": "docs"},
            ],
        })
        keys = sorted(c["key"] for c in board["cards"])
        self.assertEqual(keys, ["DOC-7", "DOC-8"])
        _, key = self.store.create_card(board["id"], {"title": "Next", "group": "docs"})
        self.assertEqual(key, "DOC-9")

    def test_import_refuses_duplicate_keys(self):
        with self.assertRaises(StoreError):
            self.store.import_board({"name": "Dupes", "cards": [{"key": "T-1", "title": "a"}, {"key": "T-1", "title": "b"}]})

    def test_import_refuses_bad_keys(self):
        with self.assertRaises(StoreError):
            self.store.import_board({"name": "Bad", "cards": [{"key": "t1", "title": "a"}]})

    def test_sample_board_imports(self):
        sample = os.path.join(os.path.dirname(__file__), os.pardir, "sample", "sample-board.json")
        with open(sample, encoding="utf-8") as f:
            board = self.store.import_board(json.load(f))
        self.assertGreater(len(board["cards"]), 0)


if __name__ == "__main__":
    unittest.main()
