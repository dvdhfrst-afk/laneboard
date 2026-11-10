"""Archiving, ordering, moving columns away, and importing files nobody should trust."""
import json
import os
import random
import tempfile
import unittest

from laneboard import samples, schema
from laneboard.store import Conflict, Store, StoreError


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "boards.json")
        self.store = Store(self.path)
        self.board = self.store.list_boards()[0]["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, title="Card", **values):
        return self.store.create_card(self.board, {"title": title, **values})[1]

    def card(self, key, board=None):
        return next(c for c in self.store.get_board(board or self.board)["cards"] if c["key"] == key)


class ArchiveTest(Case):
    def test_archive_and_restore(self):
        self.add("Stays")
        key = self.add("Goes")
        self.assertIsNone(self.card(key)["archived"])
        self.store.update_card(self.board, key, {"archived": True})
        stamp = self.card(key)["archived"]
        self.assertTrue(stamp.endswith("Z"))
        listed = self.store.list_boards()[0]
        self.assertEqual((listed["cards"], listed["archived"]), (1, 1))
        self.store.update_card(self.board, key, {"archived": False})
        self.assertIsNone(self.card(key)["archived"])

    def test_archiving_twice_keeps_the_first_date(self):
        key = self.add()
        self.store.update_card(self.board, key, {"archived": True})
        board = self.store.data["boards"][0]
        self.store.data["boards"][0] = {**board, "cards": [{**board["cards"][0], "archived": "2020-01-01T00:00:00Z"}]}
        self.store.update_card(self.board, key, {"archived": True})
        self.assertEqual(self.card(key)["archived"], "2020-01-01T00:00:00Z")

    def test_a_restored_card_comes_back_at_the_bottom(self):
        first = self.add("First")
        self.add("Second")
        self.store.update_card(self.board, first, {"archived": True})
        self.store.update_card(self.board, first, {"archived": False})
        ranks = {c["title"]: c["rank"] for c in self.store.get_board(self.board)["cards"]}
        self.assertGreater(ranks["First"], ranks["Second"])

    def test_archived_must_be_yes_or_no(self):
        key = self.add()
        for bad in ("soon", 5, [], {}):
            with self.assertRaises(StoreError):
                self.store.update_card(self.board, key, {"archived": bad})

    def test_cards_cannot_be_created_archived(self):
        with self.assertRaises(StoreError):
            self.store.create_card(self.board, {"title": "x", "archived": True})

    def test_a_column_holding_only_archived_cards_can_go(self):
        board = self.store.get_board(self.board)
        key = self.add(column=board["columns"][4]["id"])
        self.store.update_card(self.board, key, {"archived": True})
        updated = self.store.update_board(self.board, {"columns": board["columns"][:4]})
        self.assertEqual(len(updated["columns"]), 4)
        self.assertEqual(self.card(key)["column"], board["columns"][0]["id"])
        self.store.update_card(self.board, key, {"archived": False})

    def test_a_group_holding_only_archived_cards_can_go(self):
        board = self.store.update_board(self.board, {"groups": [{"name": "Web", "prefix": "WEB"}]})
        key = self.add(group=board["groups"][0]["id"])
        self.store.update_card(self.board, key, {"archived": True})
        self.store.update_board(self.board, {"groups": []})
        self.assertIsNone(self.card(key)["group"])
        self.assertEqual(key, "WEB-1")

    def test_archived_state_survives_export_and_import(self):
        key = self.add()
        self.store.update_card(self.board, key, {"archived": True})
        made = self.store.import_board(self.store.get_board(self.board))
        self.assertEqual(made["cards"][0]["archived"], self.card(key)["archived"])


class RemoveColumnTest(Case):
    def test_cards_can_move_with_the_removal(self):
        board = self.store.get_board(self.board)
        cols = board["columns"]
        keys = [self.add(f"Card {n}", column=cols[1]["id"]) for n in range(3)]
        self.add("Already there", column=cols[0]["id"])
        updated = self.store.update_board(self.board, {"columns": cols[:1] + cols[2:], "moveCards": {cols[1]["id"]: cols[0]["id"]}})
        self.assertEqual(len(updated["columns"]), 4)
        in_order = sorted((c for c in updated["cards"] if c["column"] == cols[0]["id"]), key=lambda c: c["rank"])
        self.assertEqual([c["title"] for c in in_order], ["Already there", "Card 0", "Card 1", "Card 2"])
        self.assertEqual(self.card(keys[0])["column"], cols[0]["id"])

    def test_the_target_has_to_be_staying(self):
        cols = self.store.get_board(self.board)["columns"]
        self.add(column=cols[1]["id"])
        for target in ("nowhere", cols[1]["id"], 5, ["x"]):
            with self.assertRaises(StoreError):
                self.store.update_board(self.board, {"columns": cols[:1] + cols[2:], "moveCards": {cols[1]["id"]: target}})

    def test_without_a_target_it_is_refused(self):
        cols = self.store.get_board(self.board)["columns"]
        self.add(column=cols[1]["id"])
        with self.assertRaises(Conflict):
            self.store.update_board(self.board, {"columns": cols[:1] + cols[2:]})

    def test_move_cards_needs_columns(self):
        with self.assertRaises(StoreError):
            self.store.update_board(self.board, {"moveCards": {}})
        with self.assertRaises(StoreError):
            cols = self.store.get_board(self.board)["columns"]
            self.store.update_board(self.board, {"columns": cols, "moveCards": "x"})


class OrderTest(Case):
    def ordered(self, column):
        cards = [c for c in self.store.get_board(self.board)["cards"] if c["column"] == column]
        return [c["key"] for c in sorted(cards, key=lambda c: (c["rank"], schema.key_sort(c["key"])))]

    def test_moving_into_the_same_gap_forever_keeps_the_right_order(self):
        board = self.store.get_board(self.board)
        column = board["columns"][0]["id"]
        low, high = self.add("Low"), self.add("High")
        moved = [self.add(f"Card {n}") for n in range(3)]
        order = None
        for turn in range(120):  # halving a gap this many times would run out of float precision
            key = moved[turn % 3]
            cards = {c["key"]: c for c in self.store.get_board(self.board)["cards"]}
            order = self.ordered(column)
            order.remove(key)
            at = order.index(low) + 1  # always straight after Low
            rank = (cards[order[at - 1]]["rank"] + cards[order[at]]["rank"]) / 2
            self.store.update_card(self.board, key, {"rank": rank})
            order.insert(at, key)
            self.assertEqual(self.ordered(column), order, f"turn {turn}")
        self.assertEqual(order[0], low)
        self.assertEqual(order[-1], high)

    def test_ties_from_old_files_are_sorted_out_on_the_next_move(self):
        board = self.store.get_board(self.board)
        keys = [self.add(f"Card {n}", rank=5) for n in range(4)]
        self.store.update_card(self.board, keys[0], {"column": board["columns"][0]["id"]})
        ranks = [c["rank"] for c in self.store.get_board(self.board)["cards"]]
        self.assertEqual(len(set(ranks)), 4)

    def test_tidying_does_not_change_when_cards_were_updated(self):
        keys = [self.add(f"Card {n}", rank=5) for n in range(3)]
        before = self.card(keys[1])["updated"]
        self.store.update_card(self.board, keys[0], {"rank": 5})
        self.assertEqual(self.card(keys[1])["updated"], before)

    def test_rank_must_be_a_sensible_number(self):
        key = self.add()
        for bad in (float("inf"), float("-inf"), float("nan"), 1e308, True, "1", None, []):
            with self.subTest(bad=bad), self.assertRaises(StoreError):
                self.store.update_card(self.board, key, {"rank": bad})


class CopyOnWriteTest(Case):
    def test_a_board_already_handed_out_never_changes(self):
        key = self.add("Original", checks=[])
        held = self.store.get_board(self.board)
        snapshot = json.dumps(held, sort_keys=True)
        self.store.update_card(self.board, key, {"title": "Changed", "column": "done"})
        self.store.update_board(self.board, {"name": "New name", "checklists": [{"name": "C", "items": [{"text": "x"}]}]})
        self.add("Another")
        self.store.delete_card(self.board, key)
        self.assertEqual(json.dumps(held, sort_keys=True), snapshot)

    def test_a_refused_change_leaves_everything_alone(self):
        key = self.add("Original")
        rev = self.store.get_board(self.board)["rev"]
        with self.assertRaises(StoreError):
            self.store.update_card(self.board, key, {"title": "Changed", "fields": "bad"})
        self.assertEqual(self.card(key)["title"], "Original")
        self.assertEqual(self.store.get_board(self.board)["rev"], rev)


class FieldUpdateTest(Case):
    def test_an_old_oversized_field_does_not_block_a_move(self):
        key = self.add()
        board = self.store.data["boards"][0]
        self.store.data["boards"][0] = {**board, "cards": [{**board["cards"][0], "title": "x" * 500}]}
        self.store.update_card(self.board, key, {"column": "done"})
        self.assertEqual(self.card(key)["column"], "done")
        self.assertEqual(len(self.card(key)["title"]), 500)

    def test_a_null_column_is_refused_rather_than_moving_the_card(self):
        key = self.add(column="done")
        with self.assertRaises(StoreError):
            self.store.update_card(self.board, key, {"column": None})
        self.assertEqual(self.card(key)["column"], "done")

    def test_text_is_cleaned(self):
        key = self.add("  a\tb\r\nc\x00  ", summary="line one\r\nline two\x07\n\n", notes="half \ud83d emoji")
        card = self.card(key)
        self.assertEqual(card["title"], "a b c")
        self.assertEqual(card["summary"], "line one\nline two")
        self.assertEqual(card["notes"], "half � emoji")
        with open(self.path, encoding="utf-8") as f:
            self.assertIn("half", f.read())

    def test_non_ascii_round_trips(self):
        title = "日本語 é \U0001f600 مرحبا"
        key = self.add(title, notes=title)
        again = Store(self.path)
        card = next(c for c in again.get_board(self.board)["cards"] if c["key"] == key)
        self.assertEqual((card["title"], card["notes"]), (title, title))

    def test_columns_with_no_ascii_in_the_name_get_distinct_ids(self):
        board = self.store.update_board(self.board, {"columns": [{"name": "日本"}, {"name": "中文"}]})
        ids = [c["id"] for c in board["columns"]]
        self.assertEqual(len(set(ids)), 2)

    def test_unhashable_ids_get_a_message_not_a_crash(self):
        key = self.add()
        for value in (["x"], {"a": 1}, 5):
            for payload in ({"column": value}, {"group": value}):
                with self.subTest(payload=payload), self.assertRaises(StoreError):
                    self.store.update_card(self.board, key, payload)
            with self.subTest(color=value), self.assertRaises(StoreError):
                self.store.update_board(self.board, {"groups": [{"name": "G", "prefix": "G", "color": value}]})

    def test_column_and_group_ids_that_are_not_text_are_replaced(self):
        board = self.store.get_board(self.board)
        cols = [dict(c) for c in board["columns"]]
        cols.append({"id": ["junk"], "name": "Extra"})
        updated = self.store.update_board(self.board, {"columns": cols})
        self.assertEqual(updated["columns"][-1]["id"], "extra")
        updated = self.store.update_board(self.board, {"groups": [{"id": {"a": 1}, "name": "Web", "prefix": "WEB"}]})
        self.assertEqual(updated["groups"][0]["id"], "web")


class ImportTest(Case):
    def test_an_export_comes_back_exactly_after_renames(self):
        board = self.store.update_board(self.board, {"groups": [{"name": "Website", "prefix": "WEB", "color": "teal"}]})
        cols = board["columns"] + [{"name": "Parked", "limit": None}]
        board = self.store.update_board(self.board, {"columns": cols})
        # Rename after creating: ids no longer match what the names would slug to.
        renamed = [dict(c, name="Later") if c["id"] == "parked" else c for c in board["columns"]]
        board = self.store.update_board(self.board, {"columns": renamed, "groups": [dict(board["groups"][0], name="Site")],
                                                     "checklists": [{"name": "Ship", "items": [{"text": "Tested"}, {"text": "Heading", "heading": True}]}],
                                                     "template": ["Problem"]})
        tick = board["checklists"][0]["items"][0]["id"]
        board, key = self.store.create_card(self.board, {"title": "Moved", "column": "parked", "group": "website", "checks": [tick],
                                                         "notes": "Note é", "summary": "S", "blocked": "B",
                                                         "fields": [{"label": "L", "value": "V"}],
                                                         "sections": [{"label": "Sec", "body": "Body\nTwo"}]})
        board = self.store.update_card(self.board, key, {"rank": 7.25})
        made = self.store.import_data(json.loads(json.dumps(board)))[0]
        self.assertNotEqual(made["id"], board["id"])
        for field in ("name", "description", "prefix", "groupLabel", "columns", "groups", "checklists", "template", "counters", "cards"):
            self.assertEqual(made[field], board[field], field)

    def test_whole_file_import_adds_every_board(self):
        self.add("In the first board")
        self.store.create_board("Second")
        with open(self.path, encoding="utf-8") as f:
            whole = json.load(f)
        other = Store(os.path.join(self.tmp.name, "other.json"))
        made = other.import_data(whole)
        self.assertEqual([b["name"] for b in made], ["My board", "Second"])
        self.assertEqual(len(other.list_boards()), 3)

    def test_whole_file_import_is_all_or_nothing(self):
        before = self.store.list_boards()
        whole = {"version": 2, "boards": [{"name": "Fine"}, {"name": ""}]}
        with self.assertRaises(StoreError) as caught:
            self.store.import_data(whole)
        self.assertIn("Board number 2", str(caught.exception))
        self.assertEqual(self.store.list_boards(), before)

    def test_file_from_a_newer_version_is_refused(self):
        with self.assertRaises(StoreError) as caught:
            self.store.import_data({"version": schema.SCHEMA_VERSION + 1, "boards": [{"name": "x"}]})
        self.assertIn("newer", str(caught.exception))

    def test_empty_whole_file(self):
        with self.assertRaises(StoreError):
            self.store.import_data({"version": 2, "boards": []})

    def test_columns_and_groups_can_be_named_in_a_hand_written_file(self):
        made = self.store.import_board({"name": "Hand", "columns": [{"name": "To do"}, {"name": "Done"}],
                                        "groups": [{"name": "Docs", "prefix": "DOC"}],
                                        "cards": [{"title": "A", "column": "done", "group": "docs"},
                                                  {"title": "B", "column": "To Do", "group": "DOCS"}]})
        self.assertEqual([c["column"] for c in made["cards"]], ["done", "to-do"])
        self.assertEqual([c["key"] for c in made["cards"]], ["DOC-1", "DOC-2"])
        self.assertLess(made["cards"][0]["rank"], made["cards"][1]["rank"])

    def test_a_keyless_card_does_not_take_a_key_a_later_card_asks_for(self):
        made = self.store.import_board({"name": "Hand", "cards": [{"title": "No key"}, {"key": "T-1", "title": "Has key"}]})
        self.assertEqual(sorted(c["key"] for c in made["cards"]), ["T-1", "T-2"])

    def test_counters_keep_deleted_keys_from_coming_back(self):
        made = self.store.import_board({"name": "Hand", "counters": {"T": 40}})
        self.assertEqual(self.store.create_card(made["id"], {"title": "Next"})[1], "T-41")

    def test_hostile_values_are_refused_or_defused(self):
        script = "<script>alert(1)</script>"
        made = self.store.import_board({"name": script, "cards": [{"title": script, "notes": script, "evil": script, "key": "T-1"}]})
        self.assertEqual(made["name"], script)  # text stays text; the page only ever shows it as text
        self.assertNotIn("evil", made["cards"][0])
        bad_boards = [
            {"name": "x", "groups": [{"name": "g", "prefix": "G", "link": "javascript:alert(1)"}]},
            {"name": "x", "groups": [{"name": "g", "prefix": "G", "link": " data:text/html,x"}]},
            {"name": "x", "groups": [{"name": "g", "prefix": "<B>"}]},
            {"name": "x", "groups": [{"name": "g", "prefix": "G", "color": "red;background:url(x)"}]},
            {"name": "x", "prefix": "../../etc"},
            {"name": "x", "cards": [{"title": "c", "key": "../../etc/passwd"}]},
            {"name": "x", "cards": [{"title": "c", "key": "T-1\r\nX: y"}]},
            {"name": "x", "cards": [{"title": "c", "rank": float("inf")}]},
            {"name": "x", "cards": [{"title": "c", "rank": 1e308}]},
            {"name": "x", "cards": [{"title": "c", "column": ["a"]}]},
            {"name": "x", "cards": ["just text"]},
            {"name": "x", "cards": "nope"},
            {"name": "x", "columns": "nope"},
            {"name": ["x"]},
            {},
            {"name": "x", "cards": [{"title": "c"}] * (schema.MAX_CARDS + 1)},
        ]
        for bad in bad_boards:
            with self.subTest(bad=str(bad)[:60]), self.assertRaises(StoreError):
                self.store.import_data(bad)

    def test_imported_ids_cannot_carry_odd_characters(self):
        made = self.store.import_board({"id": "../../x", "name": "Named", "columns": [{"id": "../x", "name": "Safe"}],
                                        "groups": [{"id": "<b>", "name": "Group one", "prefix": "G"}]})
        self.assertRegex(made["id"], r"^b-[0-9a-f]{8}$")
        self.assertEqual(made["columns"][0]["id"], "safe")
        self.assertEqual(made["groups"][0]["id"], "group-one")

    def test_import_errors_name_the_card(self):
        with self.assertRaises(StoreError) as caught:
            self.store.import_board({"name": "x", "cards": [{"title": "ok"}, {"title": ""}]})
        self.assertIn('Board "x", card number 2', str(caught.exception))

    def test_random_junk_never_escapes_as_anything_but_a_store_error(self):
        rng = random.Random(20261107)
        atoms = [None, True, False, 0, -1, 1.5, "", "x", "T-1", "<b>", [], {}, ["x"], {"a": 1}, [{}], 1e20]
        fields = ["name", "description", "prefix", "groupLabel", "columns", "groups", "checklists", "template", "cards",
                  "counters", "title", "column", "group", "key", "rank", "notes", "fields", "sections", "checks", "items",
                  "text", "id", "limit", "color", "link", "details", "label", "value", "body", "heading", "archived"]

        def junk(depth=0):
            if depth > 3 or rng.random() < 0.4:
                return rng.choice(atoms)
            if rng.random() < 0.3:
                return [junk(depth + 1) for _ in range(rng.randint(0, 3))]
            return {rng.choice(fields): junk(depth + 1) for _ in range(rng.randint(0, 5))}

        key = self.add()
        for _ in range(400):
            value = junk()
            for call in (lambda: self.store.import_data(value),
                         lambda: self.store.update_board(self.board, value) if isinstance(value, dict) else None,
                         lambda: self.store.update_card(self.board, key, value) if isinstance(value, dict) else None,
                         lambda: self.store.create_card(self.board, value) if isinstance(value, dict) else None):
                try:
                    call()
                except StoreError:
                    pass
        with open(self.path, encoding="utf-8") as f:
            schema.check_data(json.load(f), "after the junk")  # whatever got in, the file still passes its own check

    def test_every_example_is_a_good_board(self):
        for name in samples.names():
            board = self.store.import_board(samples.load(name))
            self.assertTrue(board["description"], name)
            schema.check_board(json.loads(json.dumps(board)), 0, set(), name)
            self.assertGreaterEqual(len(board["cards"]), 5, name)
            self.assertLess(len(json.dumps(board)), 20000, name)

    def test_samples_refuse_unknown_names(self):
        for name in ("nope", "../store", "garden.json"):
            with self.assertRaises(StoreError):
                samples.load(name)


class LargeBoardTest(Case):
    def test_a_thousand_cards_round_trip(self):
        cards = [{"key": f"T-{n}", "title": f"Card {n}", "column": "backlog", "rank": n, "notes": "n" * 200} for n in range(1, 1001)]
        made = self.store.import_board({"name": "Big", "cards": cards})
        self.assertEqual(len(made["cards"]), 1000)
        self.assertEqual(self.store.create_card(made["id"], {"title": "Next"})[1], "T-1001")
        again = Store(self.path).get_board(made["id"])
        self.assertEqual(len(again["cards"]), 1001)

    def test_a_board_stops_at_the_limit_with_a_message(self):
        cards = [{"key": f"T-{n}", "title": "c", "rank": n} for n in range(1, schema.MAX_CARDS + 1)]
        made = self.store.import_board({"name": "Full", "cards": cards})
        with self.assertRaises(Conflict) as caught:
            self.store.create_card(made["id"], {"title": "One too many"})
        self.assertIn("at most", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
