"""The commands for one card and for a board's labels, properties, views and settings."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

from laneboard import cli
from laneboard.schema import COLORS
from laneboard.store import Store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = os.path.join(self.tmp.name, "boards.json")

    def run_cli(self, *args, code=0):
        """Run a command in this process. Returns (standard output, standard error)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                got = cli.main([*args, "--data", self.data])
            except SystemExit as e:
                got = e.code
        self.assertEqual(got, code, f"{args}: {out.getvalue()}{err.getvalue()}")
        self.assertNotIn("Traceback", out.getvalue() + err.getvalue())
        return out.getvalue(), err.getvalue()

    def lines(self, *args):
        return self.run_cli(*args)[0].splitlines()

    def board(self, name="Work"):
        store = Store(self.data, starter=False)
        return next(b for b in store.all_data()["boards"] if b["name"] == name)

    def card(self, key, name="Work"):
        return next(c for c in self.board(name)["cards"] if c["key"] == key)

    def unchanged(self, *args, code=1, says=()):
        with open(self.data, "rb") as f:
            before = f.read()
        out, err = self.run_cli(*args, code=code)
        for phrase in (says if isinstance(says, tuple) else (says,)):
            self.assertIn(phrase, err)
        self.assertEqual(out, "")
        with open(self.data, "rb") as f:
            self.assertEqual(f.read(), before, f"{args} must change nothing")
        return err


class NewBoardTest(Case):
    def test_new_makes_a_board_and_nothing_else(self):
        self.assertEqual(self.lines("new", "Work", "--description", "Things to do"), [f"Added Work as {self.board()['id']}."])
        self.assertEqual([b["name"] for b in Store(self.data).list_boards()], ["Work"])
        self.assertEqual(self.board()["description"], "Things to do")
        self.run_cli("new", "Work")
        err = self.unchanged("cards", "Work", says="Several boards are called 'Work'")
        self.assertIn(self.board()["id"], err)
        self.unchanged("new", " ", says="Board name can't be empty")


class OneCardTest(Case):
    def setUp(self):
        super().setUp()
        self.run_cli("new", "Work")
        self.run_cli("labels", "Work", "--add", "Bug")
        self.run_cli("labels", "Work", "--add", "Docs", "--color", "amber")
        self.run_cli("properties", "Work", "--add", "Points", "--type", "number", "--min", "0", "--unit", "h")
        self.run_cli("properties", "Work", "--add", "Priority", "--type", "choice", "--choices", "Low, High")
        self.run_cli("properties", "Work", "--add", "Reviewed", "--type", "checkbox")
        self.run_cli("add", "Work", "Fix the form", "--summary", "It loses text")

    def test_edit_changes_what_it_is_told(self):
        out, _ = self.run_cli("edit", "Work", "T-1", "--title", "Fix the long form", "--label", "bug", "--label", "Docs",
                              "--set", "Points=2", "--set", "priority=high", "--set", "Reviewed=yes",
                              "--blocked", "Waiting on a reply", "--notes", "First line\nSecond line", "--column", "Done")
        self.assertEqual(out, "Changed T-1.\n")
        card = self.card("T-1")
        self.assertEqual((card["title"], card["labels"], card["column"], card["blocked"], card["notes"]),
                         ("Fix the long form", ["bug", "docs"], "done", "Waiting on a reply", "First line\nSecond line"))
        self.assertEqual(card["properties"], {"points": 2, "priority": "high", "reviewed": True})
        self.run_cli("edit", "Work", "T-1", "--unlabel", "Bug", "--clear", "points", "--set", "Reviewed=", "--blocked", "",
                     "--summary", "")
        card = self.card("T-1")
        self.assertEqual((card["labels"], card["properties"], card["blocked"], card["summary"]),
                         (["docs"], {"priority": "high"}, "", ""))

    def test_edit_moves_to_the_bottom_of_a_new_column_only(self):
        self.run_cli("add", "Work", "Second", "--column", "Done")
        self.run_cli("edit", "Work", "T-1", "--column", "done")
        ranks = {c["key"]: c["rank"] for c in self.board()["cards"]}
        self.assertGreater(ranks["T-1"], ranks["T-2"])
        before = self.card("T-1")["rank"]
        self.run_cli("edit", "Work", "T-1", "--column", "Done", "--title", "Same column")
        self.assertEqual(self.card("T-1")["rank"], before)

    def test_edit_groups(self):
        Store(self.data).update_board(self.board()["id"], {"groups": [{"name": "Website", "prefix": "WEB"}]})
        self.run_cli("edit", "Work", "T-1", "--group", "website")
        self.assertEqual(self.card("T-1")["group"], "website")
        self.run_cli("edit", "Work", "T-1", "--group", "")
        self.assertIsNone(self.card("T-1")["group"])

    def test_edit_refusals_are_plain_and_change_nothing(self):
        self.unchanged("edit", "Work", "T-1", code=2, says="say what to change")
        self.unchanged("edit", "Work", "T-1", "--set", "Points", code=2, says="--set takes PROPERTY=VALUE")
        self.unchanged("edit", "Work", "T-1", "--set", "Points=-1", says="Points must be a number of 0 or more.")
        self.unchanged("edit", "Work", "T-1", "--set", "Priority=urgent", says='Priority has no choice called "urgent"')
        self.unchanged("edit", "Work", "T-1", "--set", "Colour=red", says="Work has no property called 'Colour'. It has: Points, Priority, Reviewed.")
        self.unchanged("edit", "Work", "T-1", "--label", "Feature", says="Work has no label called 'Feature'. It has: Bug, Docs.")
        self.unchanged("edit", "Work", "T-1", "--column", "Later", says="no column called 'Later'")
        self.unchanged("edit", "Work", "T-9", "--title", "x", says="There's no card T-9 on Work.")
        self.unchanged("edit", "Work", "T-1", "--title", "", says="Title can't be empty")
        self.unchanged("edit", "Work", "T-1", "--notes", "x", "--notes-file", "f.txt", code=2, says="not allowed with")
        self.unchanged("edit", "Work", "T-1", "--notes-file", os.path.join(self.tmp.name, "missing.txt"), says="Can't read")

    def test_notes_from_a_file_or_standard_input(self):
        path = os.path.join(self.tmp.name, "notes.txt")
        with open(path, "wb") as f:
            f.write("﻿From a file é\r\nline two".encode("utf-8"))
        self.run_cli("edit", "Work", "T-1", "--notes-file", path)
        self.assertEqual(self.card("T-1")["notes"], "From a file é\nline two")
        result = subprocess.run([sys.executable, "-m", "laneboard", "edit", "Work", "T-1", "--notes-file", "-", "--data", self.data],
                                input="Piped in\n".encode("utf-16"), capture_output=True, cwd=self.tmp.name,
                                env={**os.environ, "PYTHONPATH": ROOT}, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.card("T-1")["notes"], "Piped in")

    def test_show_prints_everything(self):
        self.run_cli("edit", "Work", "T-1", "--label", "Docs", "--set", "Points=3", "--set", "Priority=low",
                     "--notes", "Line one\nLine two", "--blocked", "A reply")
        self.run_cli("task", "Work", "T-1", "--add", "Write", "--add", "Test")
        self.run_cli("task", "Work", "T-1", "--done", "2")
        lines = self.lines("show", "Work", "T-1")
        self.assertEqual(lines[:6], ["T-1  Fix the form", "Column: Backlog", "Labels: Docs", "Blocked by: A reply",
                                     "Summary: It loses text", "Points: 3 h"])
        self.assertIn("Priority: Low", lines)
        self.assertIn("Tasks (1 of 2 done):", lines)
        self.assertIn("  1. [ ] Write", lines)
        self.assertIn("  2. [x] Test", lines)
        self.assertEqual(lines[lines.index("Notes:") + 1:lines.index("Notes:") + 3], ["  Line one", "  Line two"])
        self.assertTrue(lines[-1].startswith("Created "))
        shown = json.loads(self.run_cli("show", "Work", "T-1", "--json")[0])
        self.assertEqual(shown, self.card("T-1"))
        self.unchanged("show", "Work", "T-2", says="There's no card T-2 on Work.")

    def test_cards_lists_labels(self):
        self.run_cli("edit", "Work", "T-1", "--label", "Docs", "--label", "Bug", "--blocked", "x")
        self.assertTrue(self.lines("cards", "Work")[0].endswith("Fix the form  [Bug, Docs]  [blocked]"))

    def test_delete_needs_yes(self):
        err = self.unchanged("delete", "Work", "T-1", code=2, says="add --yes")
        self.assertIn("usage: laneboard delete", err)
        self.assertEqual(self.lines("delete", "Work", "T-1", "--yes"), ["Deleted T-1 (Fix the form)."])
        self.assertEqual(self.board()["cards"], [])
        self.unchanged("delete", "Work", "T-1", "--yes", says="There's no card T-1 on Work.")

    def test_tasks_by_position(self):
        self.assertEqual(self.lines("task", "Work", "T-1"), ["T-1 has no tasks yet."])
        self.assertEqual(self.lines("task", "Work", "T-1", "--add", "One", "--add", "Two", "--add", "Three"),
                         ["1. [ ] One", "2. [ ] Two", "3. [ ] Three"])
        self.assertEqual(self.lines("task", "Work", "T-1", "--done", "1", "--done", "3", "--remove", "2", "--add", "Four"),
                         ["1. [x] One", "2. [x] Three", "3. [ ] Four"])
        self.assertEqual(self.lines("task", "Work", "T-1", "--reopen", "1"), ["1. [ ] One", "2. [x] Three", "3. [ ] Four"])
        ids = [t["id"] for t in self.card("T-1")["tasks"]]
        self.run_cli("task", "Work", "T-1", "--remove", "1")
        self.assertEqual([t["id"] for t in self.card("T-1")["tasks"]], ids[1:], "tasks keep their ids")
        self.unchanged("task", "Work", "T-1", "--done", "3", says="T-1 has 2 tasks, so there's no task 3.")
        self.unchanged("task", "Work", "T-1", "--done", "0", code=2, says="0 isn't a task number (tasks are numbered from 1)")
        self.unchanged("task", "Work", "T-1", "--add", " ", says="A task can't be empty")


class TransferTest(Case):
    def test_transfer_says_where_it_went_and_what_stayed(self):
        self.run_cli("new", "Work")
        self.run_cli("new", "Home")
        self.run_cli("labels", "Work", "--add", "Bug")
        self.run_cli("labels", "Work", "--add", "Docs")
        self.run_cli("labels", "Home", "--add", "bug")
        self.run_cli("add", "Work", "Travels", "--column", "Review")
        self.run_cli("edit", "Work", "T-1", "--label", "Bug", "--label", "Docs")
        self.run_cli("add", "Home", "Already here")
        self.assertEqual(self.lines("transfer", "Work", "T-1", "Home"),
                         ["Moved T-1 to Home as T-2, in Review.", "Not carried over: Label: Docs."])
        self.assertEqual(self.board()["cards"], [])
        self.assertEqual(self.card("T-2", "Home")["labels"], ["bug"])
        self.assertEqual(self.lines("transfer", "Home", "T-2", "work", "--column", "done"), ["Moved T-2 to Work as T-2, in Done."])
        self.unchanged("transfer", "Work", "T-2", "Work", says="already on this board")
        self.unchanged("transfer", "Work", "T-2", "Nowhere", says="There's no board called 'Nowhere'")
        self.unchanged("transfer", "Work", "T-2", "Home", "--column", "Later", says='Home has no column called "Later"')


class LabelsTest(Case):
    def setUp(self):
        super().setUp()
        self.run_cli("new", "Work")

    def test_listing_adding_renaming_and_removing(self):
        self.assertEqual(self.lines("labels", "Work"), ["Work has no labels yet. Add one with --add NAME."])
        self.assertEqual(self.lines("labels", "Work", "--add", " Bug "), ["Added Bug."])
        self.run_cli("labels", "Work", "--add", "Docs", "--color", "violet")
        self.run_cli("add", "Work", "One")
        self.run_cli("edit", "Work", "T-1", "--label", "Bug", "--label", "Docs")
        self.assertEqual(self.lines("labels", "Work"), [f"{'Bug':<24} {COLORS[0]:<7} 1 card", f"{'Docs':<24} {'violet':<7} 1 card"])
        self.assertEqual(self.lines("labels", "Work", "--rename", "bug=Defect"), ["Renamed Bug to Defect."])
        self.assertEqual(self.card("T-1")["labels"], ["bug", "docs"], "renaming keeps the label on its cards")
        self.assertEqual(self.lines("labels", "Work", "--remove", "Docs"), ["Removed Docs from the board and from 1 card."])
        self.assertEqual(self.card("T-1")["labels"], ["bug"])
        self.assertEqual([lab["name"] for lab in self.board()["labels"]], ["Defect"])

    def test_refusals(self):
        self.run_cli("labels", "Work", "--add", "Bug")
        self.unchanged("labels", "Work", "--add", "bug", says="There are two labels named bug.")
        self.unchanged("labels", "Work", "--color", "rose", code=2, says="--color goes with --add")
        self.unchanged("labels", "Work", "--add", "X", "--color", "plaid", code=2, says="invalid choice")
        self.unchanged("labels", "Work", "--rename", "Bug", code=2, says="--rename takes OLD=NEW")
        self.unchanged("labels", "Work", "--rename", "Nope=X", says="Work has no label called 'Nope'. It has: Bug.")
        self.unchanged("labels", "Work", "--remove", "Nope", says="no label called 'Nope'")


class PropertiesTest(Case):
    def setUp(self):
        super().setUp()
        self.run_cli("new", "Work")

    def test_listing_adding_renaming_and_removing(self):
        self.assertEqual(self.lines("properties", "Work"), ["Work has no properties yet. Add one with --add NAME --type TYPE."])
        self.assertEqual(self.lines("properties", "Work", "--add", "Points", "--type", "number", "--min", "0", "--max", "40",
                                    "--decimals", "1", "--unit", "h"), ["Added Points."])
        self.run_cli("properties", "Work", "--add", "Platforms", "--type", "multi", "--choices", "iOS,Web, ,Desktop")
        self.run_cli("properties", "Work", "--add", "Effort", "--type", "rating", "--max", "5")
        self.run_cli("add", "Work", "One")
        self.run_cli("edit", "Work", "T-1", "--set", "Points=1.5", "--set", "Platforms=web, ios")
        self.assertEqual(self.lines("properties", "Work"), [
            f"{'Points':<24} number, min 0, max 40, decimals 1, unit h  (1 card with a value)",
            f"{'Platforms':<24} multi, choices iOS, Web, Desktop  (1 card with a value)",
            f"{'Effort':<24} rating, max 5  (0 cards with a value)"])
        self.assertEqual(self.lines("properties", "Work", "--rename", "points=Hours"), ["Renamed Points to Hours."])
        self.assertEqual(self.card("T-1")["properties"], {"points": 1.5, "platforms": ["ios", "web"]})
        self.assertEqual(self.lines("properties", "Work", "--remove", "Platforms"),
                         ["Removed Platforms from the board and cleared it on 1 card."])
        self.assertEqual(self.card("T-1")["properties"], {"points": 1.5})

    def test_refusals(self):
        self.run_cli("properties", "Work", "--add", "Points", "--type", "number")
        self.unchanged("properties", "Work", "--add", "Size", code=2, says="--add needs --type, one of text, number")
        self.unchanged("properties", "Work", "--type", "text", code=2, says="go with --add")
        self.unchanged("properties", "Work", "--add", "Size", "--type", "dates", code=2, says="invalid choice")
        self.unchanged("properties", "Work", "--add", "Note", "--type", "text", "--min", "1", code=2,
                       says="a text property doesn't take --min")
        self.unchanged("properties", "Work", "--add", "Size", "--type", "number", "--min", "lots", code=2, says="--min")
        self.unchanged("properties", "Work", "--add", "Stage", "--type", "choice", says="Stage needs at least one choice.")
        self.unchanged("properties", "Work", "--add", "points", "--type", "text", says="two properties named")
        self.unchanged("properties", "Work", "--remove", "Size", says="Work has no property called 'Size'. It has: Points.")


class ViewsTest(Case):
    def setUp(self):
        super().setUp()
        self.run_cli("new", "Work")

    def test_views_are_stored_as_given(self):
        self.assertEqual(self.lines("views", "Work"), ["Work has no saved views yet. Add one with --add NAME --query Q."])
        self.assertEqual(self.lines("views", "Work", "--add", "Open bugs", "--query", "label:bug is:open", "--sort=-points,title"),
                         ["Added Open bugs."])
        self.run_cli("views", "Work", "--add", "Everything", "--query", "")
        self.assertEqual(self.board()["views"][0], {"id": "open-bugs", "name": "Open bugs", "query": "label:bug is:open",
                                                     "sort": "-points,title"})
        self.assertEqual(self.lines("views", "Work", "--default", "open BUGS"), ["The board opens with Open bugs."])
        self.assertEqual(self.lines("views", "Work"), [f"{'Open bugs':<24} label:bug is:open  sorted by -points,title  (opens by default)",
                                                       f"{'Everything':<24} (every card)"])
        self.assertEqual(self.lines("views", "Work", "--remove", "Open bugs"), ["Removed Open bugs."])
        self.assertIsNone(self.board()["settings"]["defaultView"], "removing the default view clears it")
        self.run_cli("views", "Work", "--default", "Everything")
        self.assertEqual(self.lines("views", "Work", "--default", "none"), ["The board opens with no view."])

    def test_refusals(self):
        self.run_cli("views", "Work", "--add", "Mine", "--query", "x")
        self.unchanged("views", "Work", "--add", "Mine2", code=2, says="--add needs --query")
        self.unchanged("views", "Work", "--query", "x", code=2, says="--query and --sort go with --add")
        self.unchanged("views", "Work", "--add", "mine", "--query", "y", says="two saved views named")
        self.unchanged("views", "Work", "--remove", "Yours", says="Work has no saved view called 'Yours'. It has: Mine.")
        self.unchanged("views", "Work", "--default", "Yours", says='There\'s no saved view called "Yours". Saved views: Mine.')


class SettingsTest(Case):
    def setUp(self):
        super().setUp()
        self.run_cli("new", "Work")

    def test_showing_and_changing(self):
        self.assertEqual(self.lines("settings", "Work"), [
            "Name: Work", "Key prefix: T", "Limit policy: warn (a column over its limit is flagged)",
            "Card face: group, labels, field, progress, blocked", "Default view: none"])
        self.run_cli("properties", "Work", "--add", "Points", "--type", "number")
        out = self.lines("settings", "Work", "--name", "Office", "--description", "Line one", "--prefix", "OF",
                         "--limit-policy", "refuse", "--card-face", "labels, points ,blocked")
        self.assertEqual(out, ["Name: Office", "Description: Line one", "Key prefix: OF",
                               "Limit policy: refuse (a full column takes no more cards)", "Card face: labels, Points, blocked",
                               "Default view: none"])
        board = self.board("Office")
        self.assertEqual(board["settings"]["cardFace"], ["labels", "points", "blocked"])
        self.assertTrue(board["properties"][0]["onCard"])
        self.assertIn("Card face: (only the title)", self.lines("settings", "Office", "--card-face", ""))
        self.run_cli("add", "Office", "Keyed")
        self.assertEqual(self.board("Office")["cards"][0]["key"], "OF-1")

    def test_refusals(self):
        self.unchanged("settings", "Work", "--limit-policy", "stop", code=2, says="invalid choice")
        self.unchanged("settings", "Work", "--card-face", "labels,colour", says="A card can't show \"colour\"")
        self.unchanged("settings", "Work", "--card-face", "labels,Labels", says='lists "Labels" twice')
        self.unchanged("settings", "Work", "--prefix", "a b", says="prefix")
        self.unchanged("settings", "Nope", says="There's no board called 'Nope'")


class FullColumnTest(Case):
    def test_a_refusing_board_says_which_column_is_full(self):
        self.run_cli("new", "Work")
        board = self.board()
        Store(self.data).update_board(board["id"], {"columns": [dict(c, limit=1) for c in board["columns"]]})
        self.run_cli("settings", "Work", "--limit-policy", "refuse")
        self.run_cli("add", "Work", "Fits")
        err = self.unchanged("add", "Work", "Too many", says="Backlog is full: its limit is 1 card.")
        self.assertEqual(err.count("\n"), 1)
        self.run_cli("add", "Work", "Elsewhere", "--column", "Done")
        self.unchanged("move", "Work", "T-2", "Backlog", says="Backlog is full")
        self.unchanged("edit", "Work", "T-2", "--column", "Backlog", says="Backlog is full")


if __name__ == "__main__":
    unittest.main()
