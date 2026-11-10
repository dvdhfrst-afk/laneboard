"""Saved views are read when they are saved, kept when a later change breaks them, and reported when used."""
import json
import os
import tempfile
import unittest

from laneboard.query import QueryError, broken_views, prepare
from laneboard.store import Store, StoreError
from tests.test_cli_board_parts import Case as CliCase


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(os.path.join(self.tmp.name, "boards.json"), starter=False)
        self.board = self.store.create_board("Work")["id"]
        self.change(labels=["Bug", "Docs"], properties=[{"name": "Points", "type": "number"},
                                                          {"name": "Priority", "type": "choice", "choices": ["Low", "High"]}])
        self.store.create_card(self.board, {"title": "Fix it", "labels": ["Bug"], "properties": {"points": 5}})

    def change(self, **changes):
        return self.store.update_board(self.board, changes)

    def get(self):
        return self.store.get_board(self.board)

    def refused(self, *phrases, position=None, part="query", **changes):
        rev = self.get()["rev"]
        with self.assertRaises(QueryError) as caught:
            self.change(**changes)
        for phrase in phrases:
            self.assertIn(phrase, str(caught.exception))
        if position is not None:
            self.assertEqual((caught.exception.position, caught.exception.part), (position, part))
        self.assertEqual(self.get()["rev"], rev, "a refused view changes nothing")
        return caught.exception


class SavingAView(Case):
    def test_a_view_that_reads_is_kept_as_typed(self):
        board = self.change(views=[{"name": "Big bugs", "query": "label:BUG points:>=3", "sort": "-points, title"}])
        self.assertEqual(board["views"][0]["query"], "label:BUG points:>=3")
        self.assertEqual(broken_views(board), [])

    def test_a_search_that_does_not_read_is_refused_with_its_place(self):
        error = self.refused('The saved view "Big" can\'t be saved: There\'s no property called "pionts". Properties: Points, Priority.',
                             position=4, views=[{"name": "Big", "query": "bug pionts:3"}])
        self.assertEqual(error.status, 400)
        self.refused('Priority has no choice called "hihg". Choices: Low, High.', position=9,
                     views=[{"name": "Urgent", "query": "priority:hihg"}])
        self.refused("This quote is never closed", position=0, views=[{"name": "Odd", "query": '"open'}])

    def test_a_sort_that_does_not_read_is_refused_pointing_into_the_sort(self):
        self.refused('There\'s no property called "size" to sort by', position=7, part="sort",
                     views=[{"name": "Sized", "query": "", "sort": "title,-size"}])

    def test_a_sort_written_in_the_search_points_into_the_search(self):
        self.refused('There\'s no property called "size" to sort by', position=10, part="query",
                     views=[{"name": "Sized", "query": "bug sort:-size"}])

    def test_parts_added_in_the_same_change_can_be_named(self):
        board = self.change(labels=["Bug", "Docs", "Later"], properties=[{"name": "Points", "type": "number"},
                                                                          {"name": "Effort", "type": "rating"}],
                            views=[{"name": "Later", "query": "label:later effort:>2", "sort": "-effort"}])
        self.assertEqual([v["name"] for v in board["views"]], ["Later"])

    def test_a_property_removed_in_the_same_change_can_not_be_named(self):
        self.refused('There\'s no property called "points"', views=[{"name": "Big", "query": "points:>3"}],
                     properties=[{"name": "Priority", "type": "choice", "choices": ["Low", "High"]}])

    def test_the_default_view_by_name_or_id(self):
        self.change(views=[{"name": "Big bugs", "query": "label:bug"}, {"name": "Docs", "query": "label:docs"}])
        self.assertEqual(self.change(settings={"defaultView": " big BUGS "})["settings"]["defaultView"], "big-bugs")
        self.assertEqual(self.change(settings={"defaultView": "docs"})["settings"]["defaultView"], "docs")
        self.assertIsNone(self.change(settings={"defaultView": ""})["settings"]["defaultView"])


class AViewThatStopsWorking(Case):
    def setUp(self):
        super().setUp()
        self.change(views=[{"name": "Big", "query": "points:>3", "sort": "-points"}, {"name": "Bugs", "query": "label:bug"}],
                    settings={"defaultView": "Big"})
        self.change(properties=[{"name": "Priority", "type": "choice", "choices": ["Low", "High"]}])

    def test_removing_what_it_names_keeps_it(self):
        board = self.get()
        self.assertEqual([v["query"] for v in board["views"]], ["points:>3", "label:bug"])
        self.assertEqual(board["settings"]["defaultView"], "big")
        broken = broken_views(board)
        self.assertEqual([view["name"] for view, _ in broken], ["Big"])
        self.assertIn('There\'s no property called "points"', str(broken[0][1]))
        with self.assertRaises(QueryError):
            prepare(board, board["views"][0]["query"], board["views"][0]["sort"])

    def test_other_changes_to_the_list_still_work(self):
        views = self.get()["views"]
        board = self.change(views=views + [{"name": "Docs", "query": "label:docs"}])
        self.assertEqual(len(board["views"]), 3)
        board = self.change(views=[dict(board["views"][0], name="Big ones")] + board["views"][1:])
        self.assertEqual(board["views"][0]["name"], "Big ones")
        self.change(labels=["Bug", "Docs", "Later"])

    def test_changing_its_search_checks_it_again(self):
        views = self.get()["views"]
        self.refused('There\'s no property called "points"', views=[dict(views[0], query="points:>5")] + views[1:])
        self.refused('There\'s no property called "points"', part="sort", views=[dict(views[0], query="priority:high")] + views[1:])
        board = self.change(views=[dict(views[0], query="priority:high", sort="-priority")] + views[1:])
        self.assertEqual(broken_views(board), [])


class Imports(Case):
    def boards(self):
        return len(self.store.list_boards())

    def test_a_view_can_name_a_label_that_only_a_card_brings(self):
        made = self.store.import_board({"name": "Garden", "views": [{"name": "Seeds", "query": "label:seeds"}],
                                        "cards": [{"title": "Sow", "labels": ["Seeds"]}]})
        self.assertEqual(made["views"][0]["query"], "label:seeds")

    def test_a_view_that_does_not_read_adds_nothing(self):
        before = self.boards()
        with self.assertRaises(StoreError) as caught:
            self.store.import_data({"name": "Garden", "views": [{"name": "Big", "query": "pionts:3"}], "cards": [{"title": "Sow"}]})
        self.assertNotIsInstance(caught.exception, QueryError, "an import has no search box to point into")
        self.assertIn('Board "Garden": The saved view "Big" can\'t be saved: There\'s no qualifier called "pionts"', str(caught.exception))
        self.assertEqual(self.boards(), before)

    def test_a_board_as_exported_comes_back_with_its_views(self):
        self.change(views=[{"name": "Big", "query": "points:>3 label:bug", "sort": "-points"}], settings={"defaultView": "Big"})
        exported = json.loads(json.dumps(self.get()))
        made = self.store.import_board(exported)
        self.assertEqual((made["views"], made["settings"]), (exported["views"], exported["settings"]))


class CommandLine(CliCase):
    def setUp(self):
        super().setUp()
        self.run_cli("new", "Work")
        self.run_cli("properties", "Work", "--add", "Points", "--type", "number")

    def test_a_view_that_does_not_read_is_refused_with_a_pointer(self):
        err = self.unchanged("views", "Work", "--add", "Big", "--query", "bug pionts:>3")
        self.assertEqual(err.splitlines(), ['laneboard: The saved view "Big" can\'t be saved: There\'s no property called "pionts". '
                                            'Properties: Points.', "  bug pionts:>3", "      ^"])
        err = self.unchanged("views", "Work", "--add", "Big", "--query", "", "--sort=-size")
        self.assertEqual(err.splitlines()[1:], ["  -size", "   ^"])

    def test_check_lists_views_that_stopped_working_and_still_passes(self):
        self.run_cli("views", "Work", "--add", "Big", "--query", "points:>3")
        self.assertEqual(self.lines("check")[-1], "OK")
        self.run_cli("properties", "Work", "--remove", "Points")
        out = self.lines("check")
        self.assertIn('Warning: on Work, the saved view "Big" doesn\'t work any more: There\'s no qualifier called "points". '
                      'Qualifiers: column, group, label, key, title, notes, is, has, tasks, progress, sort.', out)
        self.assertEqual(out[-2:], ["Those views are kept as they are. Change or remove them with laneboard views.", "OK"])

    def test_settings_sets_the_default_view_by_name_or_id(self):
        self.run_cli("views", "Work", "--add", "Big ones", "--query", "points:>3")
        self.assertIn("Default view: Big ones", self.lines("settings", "Work", "--default", "BIG ONES"))
        self.assertIn("Default view: none", self.lines("settings", "Work", "--default", "none"))
        self.assertIn("Default view: Big ones", self.lines("settings", "Work", "--default", "big-ones"))
        self.unchanged("settings", "Work", "--default", "Small", says='There\'s no saved view called "Small". Saved views: Big ones.')


if __name__ == "__main__":
    unittest.main()
