"""What happens to the boards file when things go wrong: bad contents, failed saves, kills, old versions."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from laneboard import schema, storage
from laneboard.store import BadFile, NotFound, Store, StoreError, Unavailable, restore_backup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def version1_file():
    """A file exactly as version 1 wrote it, with notes, sections, ticks and a revision to keep."""
    return {"version": 1, "boards": [{
        "id": "b-0a1b2c3d", "name": "Old board", "description": "From before", "prefix": "T", "groupLabel": "Area",
        "rev": 41, "created": "2025-01-01T00:00:00Z", "updated": "2025-02-01T00:00:00Z",
        "columns": [{"id": "todo", "name": "Todo", "limit": None}, {"id": "done", "name": "Done", "limit": 3}],
        "groups": [{"id": "web", "name": "Web", "prefix": "WEB", "color": "teal", "link": "https://example.com",
                    "details": [{"label": "Owner", "value": "Sam"}]}],
        "checklists": [{"id": "rel", "name": "Release", "items": [
            {"id": "h", "text": "Before", "heading": True}, {"id": "i1", "text": "Tests", "heading": False}]}],
        "template": ["Problem"], "counters": {"WEB": 9, "T": 2},
        "cards": [
            {"key": "WEB-9", "title": "Card with everything é中\U0001f600", "group": "web", "column": "todo", "rank": 2.5,
             "summary": "Sum", "blocked": "Waiting", "fields": [{"label": "Size", "value": "S"}],
             "sections": [{"label": "Steps", "body": "One\nTwo"}], "notes": "Keep me.\nLine two",
             "checks": ["i1"], "created": "2025-01-02T00:00:00Z", "updated": "2025-01-03T00:00:00Z"},
            {"key": "T-2", "title": "Plain", "group": None, "column": "done", "rank": 1, "summary": "", "blocked": "",
             "fields": [], "sections": [], "notes": "", "checks": [], "created": "2025-01-02T00:00:00Z",
             "updated": "2025-01-02T00:00:00Z"},
        ]}]}


class FileCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "boards.json")

    def tearDown(self):
        for root, _, names in os.walk(self.tmp.name):
            for name in names:
                os.chmod(os.path.join(root, name), stat.S_IWRITE | stat.S_IREAD)
        self.tmp.cleanup()

    def write(self, content):
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(self.path, mode, **({} if mode == "wb" else {"encoding": "utf-8"})) as f:
            f.write(content)

    def read(self):
        with open(self.path, "rb") as f:
            return f.read()


class UpgradeTest(FileCase):
    def setUp(self):
        super().setUp()
        self.old = version1_file()
        self.write(json.dumps(self.old))
        self.raw = self.read()

    def test_opening_an_old_file_does_not_touch_it(self):
        Store(self.path).list_boards()
        self.assertEqual(self.read(), self.raw)
        self.assertFalse(os.path.exists(self.path + ".v1.bak"))

    def test_every_value_survives_the_upgrade(self):
        store = Store(self.path)
        board = store.get_board("b-0a1b2c3d")
        want = self.old["boards"][0]
        added = {"labels": [], "properties": [], "views": [], "settings": schema.default_settings()}
        self.assertEqual({k: v for k, v in board.items() if k != "cards"}, {**{k: v for k, v in want.items() if k != "cards"}, **added})
        for got, expected in zip(board["cards"], want["cards"]):
            self.assertEqual(got, {**expected, "archived": None, "labels": [], "properties": {}, "tasks": []})

    def test_first_save_keeps_the_old_file_under_its_version(self):
        store = Store(self.path)
        store.create_card("b-0a1b2c3d", {"title": "New"})
        with open(self.path + ".v1.bak", "rb") as f:
            self.assertEqual(f.read(), self.raw)
        saved = json.loads(self.read())
        self.assertEqual(saved["version"], schema.SCHEMA_VERSION)
        self.assertEqual(saved["boards"][0]["rev"], 42)
        cards = {c["key"]: c for c in saved["boards"][0]["cards"]}
        self.assertEqual(cards["WEB-9"]["notes"], "Keep me.\nLine two")
        self.assertEqual(cards["WEB-9"]["checks"], ["i1"])
        self.assertEqual(cards["WEB-9"]["sections"], [{"label": "Steps", "body": "One\nTwo"}])

    def test_the_versioned_copy_is_only_written_once(self):
        store = Store(self.path)
        store.create_card("b-0a1b2c3d", {"title": "New"})
        store.create_card("b-0a1b2c3d", {"title": "Newer"})
        with open(self.path + ".v1.bak", "rb") as f:
            self.assertEqual(f.read(), self.raw)

    def test_a_failed_first_save_leaves_the_old_file_and_stays_upgradable(self):
        store = Store(self.path)
        with mock.patch("laneboard.storage.write_atomic", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(Unavailable):
                store.create_card("b-0a1b2c3d", {"title": "New"})
        self.assertEqual(self.read(), self.raw)
        store.create_card("b-0a1b2c3d", {"title": "New"})
        self.assertEqual(json.loads(self.read())["version"], schema.SCHEMA_VERSION)

    def test_files_from_before_checklists_and_templates_still_load(self):
        old = version1_file()
        del old["boards"][0]["checklists"], old["boards"][0]["template"], old["boards"][0]["counters"]
        for card in old["boards"][0]["cards"]:
            card.pop("checks")
        self.write(json.dumps(old))
        board = Store(self.path).get_board("b-0a1b2c3d")
        self.assertEqual((board["checklists"], board["template"]), ([], []))
        self.assertEqual(board["counters"], {"WEB": 9, "T": 2})
        self.assertEqual(board["cards"][0]["checks"], [])

    def test_a_newer_file_is_refused_and_left_alone(self):
        self.write(json.dumps({"version": schema.SCHEMA_VERSION + 1, "boards": []}))
        raw = self.read()
        with self.assertRaises(BadFile) as caught:
            Store(self.path)
        self.assertIn("newer", str(caught.exception))
        self.assertEqual(self.read(), raw)

    def test_unknown_fields_are_kept(self):
        old = version1_file()
        old["futureThing"] = {"a": 1}
        old["boards"][0]["cards"][0]["custom"] = "mine"
        self.write(json.dumps(old))
        store = Store(self.path)
        store.update_card("b-0a1b2c3d", "WEB-9", {"title": "Renamed"})
        saved = json.loads(self.read())
        self.assertEqual(saved["futureThing"], {"a": 1})
        self.assertEqual(saved["boards"][0]["cards"][0]["custom"], "mine")


class BadContentsTest(FileCase):
    def assert_refused(self, content, *phrases):
        self.write(content)
        raw = self.read()
        with self.assertRaises(BadFile) as caught:
            Store(self.path)
        for phrase in phrases:
            self.assertIn(phrase, str(caught.exception))
        self.assertEqual(self.read(), raw, "a refused file must not be touched")

    def board(self, **changes):
        data = version1_file()
        data["boards"][0].update(changes)
        return json.dumps(data)

    def card(self, **changes):
        data = version1_file()
        data["boards"][0]["cards"][0].update(changes)
        return json.dumps(data)

    def test_broken_json_names_the_line(self):
        self.assert_refused('{\n "version": 2,\n oops\n}', "line 3", "was not changed", "backups")

    def test_empty_file(self):
        self.assert_refused("", "is empty")

    def test_binary_junk(self):
        self.assert_refused(b"\x89PNG\r\n\x1a\n\x00\xff\xfe", "isn't UTF-8")

    def test_deep_nesting(self):
        self.assert_refused("[" * 200000, "nested too deeply")

    def test_infinity_is_refused(self):
        self.assert_refused('{"version": 2, "boards": [], "x": Infinity}', "Infinity")

    def test_not_a_board_file(self):
        self.assert_refused('{"something": "else"}', "doesn't look like a Laneboard file")
        self.assert_refused("[1, 2]", "doesn't look like a Laneboard file")
        self.assert_refused('{"boards": []}', "version")
        self.assert_refused('{"version": "two", "boards": []}', "version")
        self.assert_refused('{"version": true, "boards": []}', "version")

    def test_wrong_shapes_name_the_place(self):
        cases = [
            (self.board(columns=[]), "at least one column"),
            (self.board(columns="x"), "at least one column"),
            (self.board(cards="x"), "cards must be a list"),
            (self.board(rev="x"), "rev"),
            (self.board(id="../../etc"), "no usable id"),
            (self.board(groups=[{"id": "g", "name": "G", "prefix": "G", "link": "javascript:alert(1)"}], cards=[]), "http"),
            (self.card(fields="x"), "card WEB-9"),
            (self.card(sections=[{"label": 3}]), "card WEB-9"),
            (self.card(notes=5), "notes must be text"),
            (self.card(rank="high"), "rank"),
            (self.card(rank=True), "rank"),
            (self.card(column="gone"), "column that doesn't exist"),
            (self.card(group="gone"), "group that doesn't exist"),
            (self.card(key="not a key"), "no key"),
            (self.card(title=None), "title"),
            (self.card(checks="i1"), "checks"),
            (self.card(archived=5), "archived"),
        ]
        for content, phrase in cases:
            with self.subTest(phrase=phrase):
                self.assert_refused(content, phrase)

    def test_duplicates_are_refused(self):
        data = version1_file()
        data["boards"].append(json.loads(json.dumps(data["boards"][0])))
        self.assert_refused(json.dumps(data), "two boards share")
        self.assert_refused(self.board(cards=version1_file()["boards"][0]["cards"] * 2), "two cards share")

    def test_a_folder_instead_of_a_file(self):
        with self.assertRaises(Unavailable) as caught:
            Store(self.tmp.name)
        self.assertIn("is a folder", str(caught.exception))

    def test_byte_order_marks_from_editors_are_fine(self):
        for encoding, bom in (("utf-8", b"\xef\xbb\xbf"), ("utf-16-le", b"\xff\xfe")):
            self.write(bom + json.dumps(version1_file()).encode(encoding))
            self.assertEqual(Store(self.path).get_board("b-0a1b2c3d")["name"], "Old board")

    def test_a_hand_written_minimal_file_gets_its_defaults(self):
        self.write(json.dumps({"version": 2, "boards": [{"id": "b1", "name": "Hand", "columns": [{"id": "a", "name": "A"}],
                                                         "cards": [{"key": "T-5", "title": "Mine", "column": "a"}]}]}))
        store = Store(self.path)
        board = store.get_board("b1")
        self.assertEqual(board["cards"][0]["notes"], "")
        self.assertEqual(board["counters"], {"T": 5})
        self.assertEqual(store.create_card("b1", {"title": "Next"})[1], "T-6")

    def test_lone_surrogates_in_a_hand_edited_file_do_not_stop_saves(self):
        data = version1_file()
        data["boards"][0]["cards"][1]["notes"] = "half \ud83d emoji"
        self.write(json.dumps(data))  # json.dumps writes it as the escape \ud83d
        store = Store(self.path)
        store.create_card("b-0a1b2c3d", {"title": "Still saves"})
        again = Store(self.path).get_board("b-0a1b2c3d")
        self.assertEqual(again["cards"][1]["notes"], "half \ud83d emoji")
        self.assertEqual(len(again["cards"]), 3)


class RunningServerFileTest(FileCase):
    """The file changing underneath a store that's already open."""

    def setUp(self):
        super().setUp()
        self.store = Store(self.path)
        self.board = self.store.list_boards()[0]["id"]
        self.store.create_card(self.board, {"title": "One"})

    def edit(self, change):
        data = json.loads(self.read())
        change(data)
        time.sleep(0.01)
        self.write(json.dumps(data))

    def test_a_hand_edit_moves_rev_so_open_pages_notice(self):
        rev = self.store.get_board(self.board)["rev"]
        self.edit(lambda d: d["boards"][0].update(name="Edited by hand"))
        board = self.store.get_board(self.board)
        self.assertEqual(board["name"], "Edited by hand")
        self.assertGreater(board["rev"], rev)
        self.assertEqual(self.store.list_boards()[0]["rev"], board["rev"])

    def test_a_stale_write_is_refused_after_a_hand_edit(self):
        rev = self.store.get_board(self.board)["rev"]
        self.edit(lambda d: d["boards"][0].update(name="Edited by hand"))
        with self.assertRaises(StoreError):
            self.store.update_board(self.board, {"description": "Late"}, rev=rev)
        self.store.update_board(self.board, {"description": "Fresh"}, rev=self.store.get_board(self.board)["rev"])
        saved = json.loads(self.read())["boards"][0]
        self.assertEqual((saved["name"], saved["description"]), ("Edited by hand", "Fresh"))

    def test_breaking_the_file_refuses_reads_and_saves_then_recovers(self):
        good = self.read()
        time.sleep(0.01)
        self.write("{ half a fi")
        for attempt in (lambda: self.store.list_boards(), lambda: self.store.create_card(self.board, {"title": "No"})):
            with self.assertRaises(BadFile):
                attempt()
        self.assertEqual(self.read(), b"{ half a fi", "the broken file must not be overwritten")
        time.sleep(0.01)
        self.write(good)
        self.assertEqual(len(self.store.get_board(self.board)["cards"]), 1)

    def test_a_deleted_file_is_written_back_from_memory(self):
        os.remove(self.path)
        self.assertEqual(len(self.store.get_board(self.board)["cards"]), 1)
        self.store.create_card(self.board, {"title": "Two"})
        self.assertEqual(len(json.loads(self.read())["boards"][0]["cards"]), 2)

    def test_two_stores_on_one_file_never_lose_a_card(self):
        other = Store(self.path)
        errors = []

        def add(store, label):
            for n in range(25):
                try:
                    store.create_card(self.board, {"title": f"{label} {n}"})
                except Exception as e:  # noqa: BLE001 (the test reports whatever went wrong)
                    errors.append(e)

        threads = [threading.Thread(target=add, args=(s, label)) for s, label in ((self.store, "a"), (other, "b"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        cards = json.loads(self.read())["boards"][0]["cards"]
        self.assertEqual(len(cards), 51)
        self.assertEqual(len({c["key"] for c in cards}), 51)


class FailedSaveTest(FileCase):
    def setUp(self):
        super().setUp()
        self.store = Store(self.path)
        self.board = self.store.list_boards()[0]["id"]
        self.store.create_card(self.board, {"title": "One"})
        self.good = self.read()

    def assert_nothing_changed(self):
        self.assertEqual(self.read(), self.good)
        self.assertEqual(len(self.store.get_board(self.board)["cards"]), 1)
        self.assertEqual([n for n in os.listdir(self.tmp.name) if n.endswith(".tmp")], [])

    def test_disk_full_while_writing(self):
        with mock.patch("os.fsync", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(Unavailable) as caught:
                self.store.create_card(self.board, {"title": "Two"})
        self.assertIn("No space left", str(caught.exception))
        self.assertIn("not saved", str(caught.exception))
        self.assert_nothing_changed()

    def test_replace_keeps_failing(self):
        with mock.patch("os.replace", side_effect=PermissionError(13, "Access is denied")):
            with self.assertRaises(Unavailable):
                self.store.create_card(self.board, {"title": "Two"})
        self.assert_nothing_changed()

    def test_a_moment_of_sharing_violation_is_waited_out(self):
        real = os.replace
        failures = [PermissionError(13, "in use"), PermissionError(13, "in use")]

        def flaky(src, dst):
            if failures and dst == self.path:
                raise failures.pop()
            return real(src, dst)

        with mock.patch("os.name", "nt"), mock.patch("os.replace", flaky), mock.patch("laneboard.storage._sync_folder"):
            self.store.create_card(self.board, {"title": "Two"})
        self.assertEqual(len(json.loads(self.read())["boards"][0]["cards"]), 2)

    def test_a_moment_of_lock_during_the_backup_copy_is_waited_out(self):
        real = shutil.copyfile
        failures = [PermissionError(13, "in use"), PermissionError(13, "in use")]

        def flaky(src, dst):
            if failures:
                raise failures.pop()
            return real(src, dst)

        with mock.patch("os.name", "nt"), mock.patch("shutil.copyfile", flaky), mock.patch("laneboard.storage._sync_folder"):
            self.store.create_card(self.board, {"title": "Two"})
        self.assertEqual(len(json.loads(self.read())["boards"][0]["cards"]), 2)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can write to anything")
    def test_read_only_file(self):
        os.chmod(self.path, stat.S_IREAD)
        with self.assertRaises(Unavailable) as caught:
            self.store.create_card(self.board, {"title": "Two"})
        self.assertIn("read-only", str(caught.exception))
        self.assert_nothing_changed()
        os.chmod(self.path, stat.S_IWRITE | stat.S_IREAD)
        self.store.create_card(self.board, {"title": "Two"})

    def test_unwritable_folder_at_first_run(self):
        with mock.patch("os.makedirs", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(Unavailable) as caught:
                Store(os.path.join(self.tmp.name, "sub", "boards.json"))
        self.assertIn("Permission denied", str(caught.exception))

    def test_a_held_lock_times_out_with_a_message(self):
        with mock.patch.object(storage, "LOCK_TIMEOUT", 0.2):
            with storage.FileLock(self.path + ".lock"):
                held = storage.FileLock(self.path + ".lock", timeout=0.1)
                with self.assertRaises(Unavailable) as caught:
                    held.__enter__()
        self.assertIn("Try again", str(caught.exception))

    def test_failed_backup_stops_the_save(self):
        with mock.patch("laneboard.storage.copy_atomic", side_effect=PermissionError(13, "Access is denied")):
            with self.assertRaises(Unavailable):
                self.store.create_card(self.board, {"title": "Two"})
        self.assert_nothing_changed()

    def test_file_permissions_are_kept(self):
        if os.name == "nt":
            self.skipTest("no POSIX modes on Windows")
        os.chmod(self.path, 0o640)
        self.store.create_card(self.board, {"title": "Two"})
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)

    def test_a_symlinked_file_stays_linked(self):
        target = os.path.join(self.tmp.name, "real", "boards.json")
        os.makedirs(os.path.dirname(target))
        link = os.path.join(self.tmp.name, "link.json")
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("this account can't make symlinks")
        store = Store(link)
        store.create_card(store.list_boards()[0]["id"], {"title": "Through the link"})
        self.assertTrue(os.path.islink(link))
        with open(target, encoding="utf-8") as f:
            self.assertIn("Through the link", f.read())


class BackupTest(FileCase):
    def setUp(self):
        super().setUp()
        self.store = Store(self.path)
        self.board = self.store.list_boards()[0]["id"]

    def names(self):
        return [b["name"] for b in storage.list_backups(self.path)]

    def test_previous_version_is_kept(self):
        self.store.create_card(self.board, {"title": "One"})
        before = self.read()
        self.store.create_card(self.board, {"title": "Two"})
        with open(self.path + ".bak", "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertIn(storage.PREVIOUS, self.names())

    def test_a_snapshot_is_kept_once_a_day(self):
        first = self.read()
        self.store.create_card(self.board, {"title": "One"})
        self.store.create_card(self.board, {"title": "Two"})
        snaps = os.listdir(storage.backups_folder(self.path))
        self.assertEqual(len(snaps), 1)
        with open(os.path.join(storage.backups_folder(self.path), snaps[0]), "rb") as f:
            self.assertEqual(f.read(), first)

    def test_deleting_a_board_can_be_walked_back(self):
        self.store.create_card(self.board, {"title": "Precious"})
        self.store.delete_board(self.board)
        self.store.create_board("Something else")
        self.store.create_board("And another")  # the .bak no longer has it
        snap = next(n for n in self.names() if "before-delete" in n)
        restore_backup(self.path, snap)
        self.assertEqual([c["title"] for c in self.store.get_board(self.board)["cards"]], ["Precious"])

    def test_restore_keeps_what_it_replaces_and_running_stores_notice(self):
        self.store.create_card(self.board, {"title": "One"})
        rev = self.store.get_board(self.board)["rev"]
        self.store.create_card(self.board, {"title": "Two"})
        self.store.create_card(self.board, {"title": "Three"})
        done = restore_backup(self.path, storage.PREVIOUS)
        self.assertEqual(done["cards"], 2)
        self.assertTrue(os.path.exists(done["kept"]))
        with open(done["kept"], encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["boards"][0]["cards"]), 3)
        board = self.store.get_board(self.board)
        self.assertEqual(len(board["cards"]), 2)
        self.assertGreater(board["rev"], rev + 2)  # pages holding the newer copy must see a change

    def test_restore_rescues_a_broken_file(self):
        self.store.create_card(self.board, {"title": "One"})
        self.store.create_card(self.board, {"title": "Two"})
        self.write("{ broken")
        with self.assertRaises(BadFile):
            Store(self.path)
        restore_backup(self.path, storage.PREVIOUS)
        self.assertEqual(len(Store(self.path).get_board(self.board)["cards"]), 1)
        kept = [b for b in storage.list_backups(self.path) if "before-restore" in b["name"]]
        with open(kept[0]["path"], "rb") as f:
            self.assertEqual(f.read(), b"{ broken")

    def test_restore_refuses_a_broken_backup_and_unknown_names(self):
        self.store.create_card(self.board, {"title": "One"})
        self.store.create_card(self.board, {"title": "Two"})
        good = self.read()
        with open(self.path + ".bak", "w", encoding="utf-8") as f:
            f.write("nope")
        with self.assertRaises(BadFile):
            restore_backup(self.path, storage.PREVIOUS)
        for name in ("nothing", "../boards.json", "boards.json"):
            with self.assertRaises(NotFound):
                restore_backup(self.path, name)
        self.assertEqual(self.read(), good)

    def test_old_snapshots_are_pruned(self):
        folder = storage.backups_folder(self.path)
        os.makedirs(folder)
        for n in range(storage.KEEP_SNAPSHOTS + 5):
            name = os.path.join(folder, f"boards-2020-01-{n + 1:02d}.json")
            with open(name, "w", encoding="utf-8") as f:
                f.write("{}")
            os.utime(name, (1000 + n, 1000 + n))
        self.store.create_card(self.board, {"title": "One"})
        kept = os.listdir(folder)
        self.assertEqual(len(kept), storage.KEEP_SNAPSHOTS)
        self.assertNotIn("boards-2020-01-01.json", kept)

    def test_stale_temp_files_are_cleared_but_fresh_ones_stay(self):
        stale = os.path.join(self.tmp.name, ".boards-old.tmp")
        fresh = os.path.join(self.tmp.name, ".boards-new.tmp")
        for name in (stale, fresh):
            with open(name, "w", encoding="utf-8") as f:
                f.write("partial")
        os.utime(stale, (1000, 1000))
        Store(self.path)
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(fresh))


KILL_WRITER = """
import sys
sys.path.insert(0, sys.argv[2])
from laneboard.store import Store
store = Store(sys.argv[1])
board = store.list_boards()[0]["id"]
n = 0
while True:
    n += 1
    store.create_card(board, {"title": "Card " + str(n), "notes": "x" * 4000})
    print(n, flush=True)
"""


class KillTest(FileCase):
    def test_killing_a_writer_at_random_moments_never_loses_a_confirmed_save(self):
        Store(self.path)
        confirmed = 0
        for delay in (0.25, 0.4, 0.31, 0.47, 0.36):
            proc = subprocess.Popen([sys.executable, "-c", KILL_WRITER, self.path, ROOT], stdout=subprocess.PIPE, text=True)
            time.sleep(delay)
            proc.kill()
            out, _ = proc.communicate()
            lines = out.split()
            confirmed += len(lines)
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)  # whole, valid JSON every time
            self.assertGreaterEqual(len(data["boards"][0]["cards"]), confirmed)
            confirmed = len(data["boards"][0]["cards"])
        self.assertEqual(len(Store(self.path).get_board(data["boards"][0]["id"])["cards"]), confirmed)
        self.assertGreater(confirmed, 0, "the writer never got going, so this proved nothing")


if __name__ == "__main__":
    unittest.main()
