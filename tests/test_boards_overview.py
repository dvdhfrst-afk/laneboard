"""Archived boards, the order of boards, the boards overview and backups: the store, the API and the command line."""
import json
import os

from laneboard import storage
from laneboard.store import NotFound, StoreError, describe_backups

from tests.test_api_search_templates import ApiCase
from tests.test_cli_board_parts import Case


def names(listed):
    return [b["name"] for b in listed]


class StoreTest(ApiCase):
    def setUp(self):
        super().setUp()
        self.ids = {n: self.store.create_board(n)["id"] for n in ("One", "Two", "Three", "Four")}

    def test_archiving_keeps_everything_and_restoring_brings_it_back(self):
        board = self.store.create_card(self.ids["Two"], {"title": "Kept"})[0]
        archived = self.store.update_board(board["id"], {"archived": True})
        self.assertTrue(archived["archived"])
        self.assertEqual(archived["cards"], board["cards"])
        self.assertEqual(archived["rev"], board["rev"] + 1)
        again = self.store.update_board(board["id"], {"archived": True})
        self.assertEqual(again["archived"], archived["archived"])  # archiving twice keeps the first time
        listed = {b["name"]: b for b in self.store.list_boards()}
        self.assertEqual(listed["Two"]["archivedAt"], archived["archived"])
        self.assertIsNone(listed["One"]["archivedAt"])
        self.assertEqual(listed["Two"]["cards"], 1)
        restored = self.store.update_board(board["id"], {"archived": False})
        self.assertIsNone(restored["archived"])
        with self.assertRaises(StoreError) as caught:
            self.store.update_board(board["id"], {"archived": "yes"})
        self.assertEqual(str(caught.exception), "archived must be true or false.")
        with open(self.path, encoding="utf-8") as f:
            self.assertTrue(all("archived" in b for b in json.load(f)["boards"]))

    def test_moving_stays_on_its_own_side(self):
        self.store.update_board(self.ids["Two"], {"archived": True})
        self.store.update_board(self.ids["Four"], {"archived": True})
        revs = {b["id"]: b["rev"] for b in self.store.list_boards()}
        self.store.move_board(self.ids["Three"], 0)
        self.assertEqual(names(self.store.list_boards()), ["Three", "One", "Two", "Four"])
        self.store.move_board(self.ids["Two"], 5)  # past the end: last of the archived boards
        listed = self.store.list_boards()
        self.assertEqual([b["name"] for b in listed if b["archivedAt"]], ["Four", "Two"])
        self.assertEqual([b["name"] for b in listed if not b["archivedAt"]], ["Three", "One"])
        self.assertEqual({b["id"]: b["rev"] for b in listed}, revs)  # the order is no board's change
        self.store.move_board(self.ids["One"], -3)
        self.assertEqual([b["name"] for b in self.store.list_boards() if not b["archivedAt"]], ["One", "Three"])
        for bad in ("1", 1.5, True, None):
            with self.subTest(bad=bad), self.assertRaises(StoreError):
                self.store.move_board(self.ids["One"], bad)
        with self.assertRaises(NotFound):
            self.store.move_board("b-00000000", 0)

    def test_an_imported_board_comes_in_use(self):
        exported = dict(self.store.get_board(self.ids["One"]), archived="2026-01-01T00:00:00Z")
        made = self.store.import_data(exported)[0]
        self.assertIsNone(made["archived"])


class ApiTest(ApiCase):
    def setUp(self):
        super().setUp()
        self.one = self.ok("POST", "/api/boards", {"name": "One"}, 201)["board"]
        self.two = self.ok("POST", "/api/boards", {"name": "Two"}, 201)["board"]
        self.ok("POST", f"/api/boards/{self.one['id']}/cards", {"title": "Find me", "column": "done"}, 201)
        self.ok("POST", f"/api/boards/{self.two['id']}/cards", {"title": "Find me too"}, 201)

    def test_archive_restore_and_search(self):
        board = self.ok("PATCH", f"/api/boards/{self.two['id']}", {"archived": True},
                        headers={"If-Match": str(self.two["rev"] + 1)})["board"]
        self.assertTrue(board["archived"])
        found = self.ok("GET", "/api/search?q=find")["results"]
        self.assertEqual([r["name"] for r in found], ["One"])
        found = self.ok("GET", "/api/search?q=find&archivedBoards=1")["results"]
        self.assertEqual([r["name"] for r in found], ["One", "Two"])
        self.assertEqual(self.ok("GET", f"/api/boards/{self.two['id']}/search?q=find")["matched"], 1)
        status, payload = self.request("GET", "/api/search?q=find&archivedBoards=maybe")
        self.assertEqual((status, payload["error"]),
                         (400, "archivedBoards must be 0 (leave archived boards out) or 1 (search them too)."))
        listed = self.ok("GET", "/api/boards")["boards"]
        self.assertEqual([bool(b["archivedAt"]) for b in listed], [False, True])
        self.ok("PATCH", f"/api/boards/{self.two['id']}", {"archived": False})
        self.assertEqual(len(self.ok("GET", "/api/search?q=find")["results"]), 2)

    def test_overview(self):
        boards = self.ok("GET", "/api/overview")["boards"]
        one = boards[0]
        self.assertEqual((one["name"], one["cards"], one["done"], one["notDone"], one["archived"], one["archivedAt"]),
                         ("One", 1, 1, 0, 0, None))
        self.assertEqual([(c["name"], c["cards"], c["done"]) for c in one["columns"]],
                         [("Backlog", 0, False), ("Ready", 0, False), ("In progress", 0, False), ("Review", 0, False),
                          ("Done", 1, True)])
        self.assertEqual((boards[1]["done"], boards[1]["notDone"]), (0, 1))

    def test_move(self):
        listed = self.ok("POST", f"/api/boards/{self.two['id']}/move", {"position": 0})["boards"]
        self.assertEqual(names(listed), ["Two", "One"])
        status, payload = self.request("POST", f"/api/boards/{self.two['id']}/move", {"position": "first"})
        self.assertEqual((status, payload["error"]), (400, "position must be a whole number: 0 for the first place."))
        status, _ = self.request("POST", f"/api/boards/{self.two['id']}/move", {"position": 0},
                                 headers={"Origin": "http://example.com"})
        self.assertEqual(status, 403)

    def test_backups_list_and_restore(self):
        self.ok("PATCH", f"/api/boards/{self.one['id']}", {"name": "Renamed"})
        backups = self.ok("GET", "/api/backups")["backups"]
        self.assertEqual(backups[0]["name"], "previous")
        self.assertEqual(set(backups[0]), {"name", "modified", "bytes", "boards", "cards"})
        self.assertRegex(backups[0]["modified"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual((backups[0]["boards"], backups[0]["cards"]), (2, 2))
        rev = self.ok("GET", f"/api/boards/{self.one['id']}")["board"]["rev"]
        restored = self.ok("POST", "/api/backups/restore", {"name": "previous"})["restored"]
        self.assertEqual((restored["boards"], restored["cards"]), (2, 2))
        self.assertRegex(restored["kept"], r"^boards-.*-before-restore\.json$")
        board = self.ok("GET", f"/api/boards/{self.one['id']}")["board"]
        self.assertEqual(board["name"], "One")
        self.assertGreater(board["rev"], rev)  # open pages notice
        self.assertIn(restored["kept"], [b["name"] for b in self.ok("GET", "/api/backups")["backups"]])

    def test_restore_refuses_anything_but_a_listed_name(self):
        self.ok("PATCH", f"/api/boards/{self.one['id']}", {"name": "Renamed"})
        outside = os.path.join(self.tmp.name, "elsewhere.json")
        with open(outside, "w", encoding="utf-8") as f:
            f.write('{"version": 3, "boards": [], "templates": []}')
        for name in (outside, "../elsewhere.json", "elsewhere.json", "PREVIOUS", " previous", ""):
            with self.subTest(name=name):
                status, payload = self.request("POST", "/api/backups/restore", {"name": name})
                self.assertIn(status, (400, 404), payload)
        for body in ({}, {"name": ["previous"]}, {"name": 3}):
            status, _ = self.request("POST", "/api/backups/restore", body)
            self.assertEqual(status, 400)
        status, _ = self.request("POST", "/api/backups/restore", None, headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 400)
        status, _ = self.request("POST", "/api/backups/restore", {"name": "previous"}, headers={"Origin": "http://example.com"})
        self.assertEqual(status, 403)
        self.assertEqual(self.ok("GET", f"/api/boards/{self.one['id']}")["board"]["name"], "Renamed")

    def test_restore_works_when_the_file_is_broken(self):
        self.ok("PATCH", f"/api/boards/{self.one['id']}", {"name": "Renamed"})
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ broken")
        status, _ = self.request("GET", "/api/boards")
        self.assertEqual(status, 503)
        self.assertEqual(self.ok("GET", "/api/backups")["backups"][0]["name"], "previous")
        restored = self.ok("POST", "/api/backups/restore", {"name": "previous"})["restored"]
        self.assertEqual(restored["boards"], 2)
        self.assertEqual(names(self.ok("GET", "/api/boards")["boards"]), ["One", "Two"])
        kept = [b for b in describe_backups(self.path) if b["name"] == restored["kept"]]
        self.assertEqual((kept[0]["boards"], kept[0]["cards"]), (None, None))  # the broken file, kept as it was


class CliTest(Case):
    def setUp(self):
        super().setUp()
        for name in ("Home", "Work", "Garden"):
            self.run_cli("new", name)
            self.run_cli("add", name, f"Water the {name.lower()}")

    def test_boards_and_board(self):
        self.assertEqual(self.lines("board", "Work", "--archive")[1:], [
            f"Archived: since {self.board('Work')['archived']}", "Place: 1 of 1 archived boards"])
        listed = self.lines("boards")
        self.assertEqual([line.split("  ")[1] for line in listed[:-1]], ["Home", "Garden"])
        self.assertEqual(listed[-1], "1 archived board not shown. See them with --archived or --all.")
        self.assertEqual([line.split("  ")[1] for line in self.lines("boards", "--archived")], ["Work"])
        every = self.lines("boards", "--all")
        self.assertTrue(every[1].endswith("(1 card)  [archived board]"), every)
        self.assertEqual(json.loads(self.run_cli("boards", "--all", "--json")[0])[1]["name"], "Work")
        heads = [line for line in self.lines("search", "water") if not line.startswith(" ")]
        self.assertEqual(heads, ["Home (1 of 1 card)", "Garden (1 of 1 card)"])
        self.assertIn("Work (1 of 1 card)", self.lines("search", "water", "--archived-boards"))
        self.assertIn("Work (1 of 1 card)", self.lines("search", "water", "--board", "Work"))
        self.assertEqual(self.lines("board", "Garden", "--move-to", "1"),
                         [f"Garden ({self.board('Garden')['id']}): 1 card, 0 done", "Archived: no", "Place: 1 of 2 boards in use"])
        self.assertEqual([line.split("  ")[1] for line in self.lines("boards")[:-1]], ["Garden", "Home"])
        out = self.lines("board", "Work", "--restore", "--rename", "Office")
        self.assertEqual(out[1:], ["Archived: no", "Place: 3 of 3 boards in use"])
        self.assertEqual(self.lines("boards", "--archived"), ["No archived boards."])

    def test_refusals(self):
        self.unchanged("board", "Work", "--archive", "--restore", code=2, says="not allowed with")
        self.unchanged("board", "Nope", "--archive", says="There's no board called 'Nope'")
        self.unchanged("board", "Work", "--rename", "", says="Board name can't be empty")
        self.unchanged("board", "Work", "--move-to", "first", code=2, says="invalid int value")

    def test_backups_say_what_they_hold(self):
        self.run_cli("add", "Home", "Another")
        listed = self.lines("backups")
        self.assertTrue(any(line.strip().startswith("previous") and line.endswith("3 boards, 3 cards") for line in listed),
                        listed)
        with open(storage.previous_path(self.data), "w", encoding="utf-8") as f:
            f.write("broken")
        self.assertTrue(any(line.endswith("can't be read") for line in self.lines("backups")))
