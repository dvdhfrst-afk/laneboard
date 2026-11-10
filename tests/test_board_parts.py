"""Labels, properties, saved views, settings, card tasks and column limits, through the store."""
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
        self.board = self.store.list_boards()[0]["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, title="Card", **values):
        return self.store.create_card(self.board, {"title": title, **values})[1]

    def get(self):
        return self.store.get_board(self.board)

    def card(self, key):
        return next(c for c in self.get()["cards"] if c["key"] == key)

    def change(self, **changes):
        return self.store.update_board(self.board, changes)

    def saved(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)["boards"][0]

    def refused(self, call, *phrases, kind=StoreError):
        rev = self.get()["rev"]
        with self.assertRaises(kind) as caught:
            call()
        for phrase in phrases:
            self.assertIn(phrase, str(caught.exception))
        self.assertEqual(self.get()["rev"], rev, "a refused change must change nothing")
        return str(caught.exception)


class NewBoardTest(Case):
    def test_a_new_board_has_the_new_parts_empty(self):
        board = self.store.create_board("Fresh")
        self.assertEqual((board["labels"], board["properties"], board["views"]), ([], [], []))
        self.assertEqual(board["settings"], schema.default_settings())
        self.assertEqual(self.store.all_data()["templates"], [])
        card = self.store.create_card(board["id"], {"title": "One"})[0]["cards"][0]
        self.assertEqual((card["labels"], card["properties"], card["tasks"]), ([], {}, []))
        with open(self.path, encoding="utf-8") as f:
            schema.check_data(json.load(f), "the saved file")


class LabelTest(Case):
    def test_labels_get_ids_and_colors_in_turn(self):
        board = self.change(labels=["Bug", {"name": "Feature", "color": "green"}, {"name": "Chore"}])
        self.assertEqual(board["labels"], [{"id": "bug", "name": "Bug", "color": COLORS[0]},
                                           {"id": "feature", "name": "Feature", "color": "green"},
                                           {"id": "chore", "name": "Chore", "color": COLORS[2]}])

    def test_renaming_and_reordering_keep_ids_colors_and_cards(self):
        board = self.change(labels=["Bug", "Feature"])
        key = self.add(labels=["Feature", "bug"])
        self.assertEqual(self.card(key)["labels"], ["bug", "feature"], "cards list labels in board order")
        bug, feature = board["labels"]
        board = self.change(labels=[dict(feature, name="Enhancement"), bug])
        self.assertEqual([(lab["id"], lab["name"], lab["color"]) for lab in board["labels"]],
                         [("feature", "Enhancement", feature["color"]), ("bug", "Bug", bug["color"])])
        self.assertEqual(self.card(key)["labels"], ["feature", "bug"])

    def test_a_label_sent_by_name_alone_is_the_same_label(self):
        self.change(labels=[{"name": "Bug", "color": "rose"}])
        key = self.add(labels=["Bug"])
        board = self.change(labels=["bug", "Docs"])
        self.assertEqual(board["labels"][0], {"id": "bug", "name": "bug", "color": "rose"})
        self.assertEqual(self.card(key)["labels"], ["bug"])

    def test_removing_a_label_takes_it_off_every_card_in_the_same_save(self):
        self.change(labels=["Bug", "Docs"])
        keys = [self.add(labels=["Bug", "Docs"]), self.add(labels=["Bug"]), self.add()]
        rev = self.get()["rev"]
        board = self.change(labels=[{"id": "docs", "name": "Docs"}])
        self.assertEqual(board["rev"], rev + 1)
        self.assertEqual([self.card(k)["labels"] for k in keys], [["docs"], [], []])
        self.assertEqual([c["labels"] for c in self.saved()["cards"]], [["docs"], [], []])

    def test_a_new_label_never_inherits_a_removed_one(self):
        self.change(labels=["Bug"])
        key = self.add(labels=["Bug"])
        board = self.change(labels=[{"name": "Bug report"}])
        self.assertEqual(board["labels"][0]["id"], "bug-report")
        self.assertEqual(self.card(key)["labels"], [])
        board = self.change(labels=[{"id": "bug-report", "name": "Bug report"}, {"name": "bug"}])
        self.assertNotIn("bug", [c for c in self.card(key)["labels"]])

    def test_label_validation(self):
        bad = [("x", "Labels must be a list"), ([3], "needs a name"), ([""], "can't be empty"),
               (["x" * 41], "too long"), (["Bug", "BUG"], "two labels named"),
               ([{"name": "A", "color": "plaid"}], "must be one of"), ([{"name": "A", "color": ["blue"]}], "must be one of"),
               ([f"L{n}" for n in range(schema.MAX_LABELS + 1)], f"at most {schema.MAX_LABELS}")]
        for labels, phrase in bad:
            with self.subTest(labels=str(labels)[:40]):
                self.refused(lambda: self.change(labels=labels), phrase)

    def test_card_labels_by_id_or_name(self):
        self.change(labels=["Bug", "Feature"])
        key = self.add(labels=[" FEATURE ", "bug", "bug"])
        self.assertEqual(self.card(key)["labels"], ["bug", "feature"])
        self.store.update_card(self.board, key, {"labels": ["feature"]})
        self.assertEqual(self.card(key)["labels"], ["feature"])
        self.store.update_card(self.board, key, {"labels": None})
        self.assertEqual(self.card(key)["labels"], [])
        message = self.refused(lambda: self.store.update_card(self.board, key, {"labels": ["Bgu"]}), 'no label called "Bgu"')
        self.assertIn("Labels: Bug, Feature.", message)
        self.refused(lambda: self.store.update_card(self.board, key, {"labels": "bug"}), "must be a list")
        self.refused(lambda: self.store.update_card(self.board, key, {"labels": [["bug"]]}), "must be a list")

    def test_a_board_with_no_labels_says_so(self):
        key = self.add()
        self.refused(lambda: self.store.update_card(self.board, key, {"labels": ["Bug"]}), "This board has no labels yet.")

    def test_at_most_twenty_labels_on_a_card(self):
        self.change(labels=[f"L{n}" for n in range(schema.MAX_CARD_LABELS + 1)])
        self.add(labels=[f"L{n}" for n in range(schema.MAX_CARD_LABELS)])
        self.refused(lambda: self.add(labels=[f"L{n}" for n in range(schema.MAX_CARD_LABELS + 1)]), "at most 20 labels")


class PropertyTest(Case):
    def setUp(self):
        super().setUp()
        self.change(properties=[{"name": "Points", "type": "number", "min": 0, "max": 100},
                                {"name": "Priority", "type": "choice", "choices": ["Low", "High"]},
                                {"name": "Reviewed", "type": "checkbox"}])

    def test_values_are_cleaned_and_merged(self):
        key = self.add(properties={"points": 3, "priority": "High"})
        self.assertEqual(self.card(key)["properties"], {"points": 3, "priority": "high"})
        self.store.update_card(self.board, key, {"properties": {"reviewed": True, "points": None}})
        self.assertEqual(self.card(key)["properties"], {"priority": "high", "reviewed": True})
        self.store.update_card(self.board, key, {"properties": {"reviewed": False, "priority": ""}})
        self.assertEqual(self.card(key)["properties"], {})
        self.store.update_card(self.board, key, {"properties": {"points": 5}})
        self.store.update_card(self.board, key, {"properties": None})
        self.assertEqual(self.card(key)["properties"], {})

    def test_values_are_kept_in_property_order(self):
        key = self.add(properties={"reviewed": True, "points": 1})
        self.assertEqual(list(self.card(key)["properties"]), ["points", "reviewed"])

    def test_bad_values_get_plain_messages(self):
        key = self.add()
        cases = [({"points": 101}, "Points must be a number from 0 to 100."),
                 ({"points": "3"}, "Points must be a number"),
                 ({"priority": "hihg"}, 'Priority has no choice called "hihg". Choices: Low, High.'),
                 ({"Points": 3}, 'There\'s no property with the id "Points". Properties: Points, Priority, Reviewed.'),
                 ({"size": 3}, "no property with the id"),
                 ([], "must map property ids to values")]
        for values, phrase in cases:
            with self.subTest(values=values):
                self.refused(lambda: self.store.update_card(self.board, key, {"properties": values}), phrase)

    def test_removing_a_property_clears_it_everywhere_and_from_the_card_face(self):
        board = self.change(settings={"cardFace": ["labels", "points"]})
        self.assertTrue(board["properties"][0]["onCard"])
        keys = [self.add(properties={"points": n, "priority": "low"}) for n in (1, 2)]
        board = self.change(properties=board["properties"][1:])
        self.assertEqual(board["settings"]["cardFace"], ["labels"])
        self.assertEqual([self.card(k)["properties"] for k in keys], [{"priority": "low"}] * 2)
        self.assertEqual([c["properties"] for c in self.saved()["cards"]], [{"priority": "low"}] * 2)

    def test_removing_a_choice_clears_it_on_cards(self):
        key = self.add(properties={"priority": "low"})
        priority = self.get()["properties"][1]
        self.change(properties=[self.get()["properties"][0], dict(priority, choices=[priority["choices"][1]])])
        self.assertEqual(self.card(key)["properties"], {})

    def test_on_card_and_the_card_face_stay_in_step(self):
        props = self.get()["properties"]
        board = self.change(properties=[dict(props[0], onCard=True), *props[1:]])
        self.assertEqual(board["settings"]["cardFace"], [*schema.CARD_FACE_PARTS, "points"])
        board = self.change(settings={"cardFace": ["priority", "group"]})
        self.assertEqual([p["onCard"] for p in board["properties"]], [False, True, False])
        board = self.change(properties=[dict(p) for p in board["properties"]] + [{"name": "Link", "type": "url", "onCard": True}])
        self.assertEqual(board["settings"]["cardFace"], ["priority", "group", "link"])
        board = self.change(properties=[dict(p, onCard=False) if p["id"] == "priority" else p for p in board["properties"]])
        self.assertEqual(board["settings"]["cardFace"], ["group", "link"])

    def test_properties_and_the_card_face_can_change_together(self):
        board = self.change(properties=[*self.get()["properties"], {"name": "Effort", "type": "rating"}],
                            settings={"cardFace": ["Effort", "Points"]})
        self.assertEqual(board["settings"]["cardFace"], ["effort", "points"])

    def test_a_bad_definition_changes_nothing(self):
        key = self.add(properties={"points": 50})
        self.refused(lambda: self.change(properties=[{"name": "Points", "type": "text"}]), "can't change from number to text")
        self.assertEqual(self.card(key)["properties"], {"points": 50})


class ViewAndSettingsTest(Case):
    def test_views_are_stored_as_given(self):
        board = self.change(labels=["Bug"], properties=[{"name": "Points", "type": "number"}],
                            views=[{"name": "Open bugs", "query": "label:BUG  -is:done", "sort": "-points,title"}, {"name": "All"}])
        self.assertEqual(board["views"], [{"id": "open-bugs", "name": "Open bugs", "query": "label:BUG  -is:done", "sort": "-points,title"},
                                          {"id": "all", "name": "All", "query": "", "sort": ""}])
        board = self.change(views=[dict(board["views"][1], name="Everything"), {"id": "open-bugs", "name": "Bugs"}])
        self.assertEqual([(v["id"], v["name"], v["query"]) for v in board["views"]],
                         [("all", "Everything", ""), ("open-bugs", "Bugs", "label:BUG  -is:done")])

    def test_removing_the_default_view_clears_the_default(self):
        board = self.change(views=[{"name": "Mine", "query": "x"}, {"name": "Yours", "query": "y"}], settings={"defaultView": "mine"})
        self.assertEqual(board["settings"]["defaultView"], "mine")
        board = self.change(views=[board["views"][1]])
        self.assertIsNone(board["settings"]["defaultView"])
        board = self.change(settings={"defaultView": "YOURS"})
        self.assertEqual(board["settings"]["defaultView"], "yours")
        self.assertIsNone(self.change(settings={"defaultView": None})["settings"]["defaultView"])

    def test_view_validation(self):
        bad = [("x", "must be a list"), (["Mine"], "needs a name"), ([{"name": ""}], "can't be empty"),
               ([{"name": "A"}, {"name": "a"}], "two saved views named"), ([{"name": "A", "query": "x" * 501}], "too long"),
               ([{"name": "A", "sort": 5}], "must be text"), ([{"name": f"V{n}"} for n in range(31)], "at most 30")]
        for views, phrase in bad:
            with self.subTest(views=str(views)[:40]):
                self.refused(lambda: self.change(views=views), phrase)

    def test_settings_change_only_what_is_sent(self):
        board = self.change(settings={"limitPolicy": "refuse"})
        self.assertEqual(board["settings"], {**schema.default_settings(), "limitPolicy": "refuse"})
        board = self.change(settings={"cardFace": [" Labels", "BLOCKED"]})
        self.assertEqual(board["settings"], {"cardFace": ["labels", "blocked"], "limitPolicy": "refuse", "defaultView": None})
        self.assertEqual(self.change(settings={"cardFace": []})["settings"]["cardFace"], [])

    def test_settings_validation(self):
        self.change(properties=[{"name": "Points", "type": "number"}])
        bad = [("warn", "must be an object"), ({"colour": 1}, "no colour setting"), ({"limitPolicy": "stop"}, "warn"),
               ({"limitPolicy": ["warn"]}, "warn"), ({"cardFace": "labels"}, "must be a list"),
               ({"cardFace": ["labels", "Labels"]}, 'lists "Labels" twice'),
               ({"cardFace": ["colour"]}, 'A card can\'t show "colour". It can show group, labels, field, progress, blocked, and the properties Points.'),
               ({"defaultView": "nope"}, "This board has no saved views yet."), ({"defaultView": 3}, "default view")]
        for settings, phrase in bad:
            with self.subTest(settings=settings):
                self.refused(lambda: self.change(settings=settings), phrase)


class TaskTest(Case):
    def test_tasks_as_a_whole_list(self):
        key = self.add(tasks=["Write it", {"text": "Test it", "done": True}])
        tasks = self.card(key)["tasks"]
        self.assertEqual([(t["text"], t["done"]) for t in tasks], [("Write it", False), ("Test it", True)])
        self.assertTrue(all(schema.ID_RE.fullmatch(t["id"]) and len(t["id"]) <= 8 for t in tasks))
        self.assertEqual(len({t["id"] for t in tasks}), 2)
        reordered = [tasks[1], dict(tasks[0], done=True), {"id": tasks[1]["id"], "text": "Same id twice"}, {"id": "../x", "text": "Odd id"}]
        self.store.update_card(self.board, key, {"tasks": reordered})
        now = self.card(key)["tasks"]
        self.assertEqual([t["id"] for t in now[:2]], [tasks[1]["id"], tasks[0]["id"]])
        self.assertEqual(len({t["id"] for t in now}), 4)
        self.assertTrue(now[1]["done"])

    def test_task_validation(self):
        key = self.add()
        bad = [("x", "must be a list"), ([3], "needs text"), ([""], "A task can't be empty"), (["x" * 301], "too long"),
               ([{"text": "x", "done": "yes"}], "true or false"), (["x"] * 101, "at most 100 tasks")]
        for tasks, phrase in bad:
            with self.subTest(tasks=str(tasks)[:30]):
                self.refused(lambda: self.store.update_card(self.board, key, {"tasks": tasks}), phrase)

    def test_one_task_at_a_time(self):
        key = self.add()
        board, first = self.store.add_task(self.board, key, {"text": "First"}, rev=self.get()["rev"])
        board, second = self.store.add_task(self.board, key, {"text": "Second", "done": True}, rev=board["rev"])
        self.assertNotEqual(first, second)
        board = self.store.update_task(self.board, key, first, {"done": True}, rev=board["rev"])
        board = self.store.update_task(self.board, key, second, {"text": "Second, renamed", "done": False}, rev=board["rev"])
        self.assertEqual([(t["text"], t["done"]) for t in self.card(key)["tasks"]], [("First", True), ("Second, renamed", False)])
        board = self.store.delete_task(self.board, key, first, rev=board["rev"])
        self.assertEqual([t["id"] for t in self.card(key)["tasks"]], [second])
        self.assertEqual([t["text"] for t in self.saved()["cards"][0]["tasks"]], ["Second, renamed"])

    def test_task_changes_are_checked(self):
        key = self.add()
        _, tid = self.store.add_task(self.board, key, {"text": "One"})
        self.refused(lambda: self.store.add_task(self.board, key, {"text": ""}), "can't be empty")
        self.refused(lambda: self.store.add_task(self.board, key, {"text": "x", "colour": 1}), "no colour field")
        self.refused(lambda: self.store.update_task(self.board, key, tid, {"done": 1}), "true or false")
        self.refused(lambda: self.store.update_task(self.board, key, "nope", {"done": True}), "has no task nope", kind=NotFound)
        self.refused(lambda: self.store.delete_task(self.board, key, "nope"), "has no task nope", kind=NotFound)
        self.refused(lambda: self.store.add_task(self.board, "T-99", {"text": "x"}), "no card T-99", kind=NotFound)
        self.refused(lambda: self.store.add_task(self.board, key, {"text": "x"}, rev=1), "changed", kind=StaleRevision)
        self.store.update_card(self.board, key, {"tasks": ["t"] * schema.MAX_TASKS})
        self.refused(lambda: self.store.add_task(self.board, key, {"text": "One too many"}), "at most 100", kind=Conflict)

    def test_a_task_change_updates_the_card_stamp(self):
        key = self.add()
        before = self.card(key)
        board = self.store.data["boards"][0]
        self.store.data["boards"][0] = {**board, "cards": [{**before, "updated": "2020-01-01T00:00:00Z"}]}
        self.store.add_task(self.board, key, {"text": "x"})
        self.assertNotEqual(self.card(key)["updated"], "2020-01-01T00:00:00Z")


class LimitPolicyTest(Case):
    def setUp(self):
        super().setUp()
        board = self.get()
        self.columns = [c["id"] for c in board["columns"]]
        self.change(columns=[dict(c, limit=2 if c["id"] == "ready" else None) for c in board["columns"]])
        self.first = self.add("One", column="ready")
        self.second = self.add("Two", column="ready")
        self.outside = self.add("Three")

    def test_warn_lets_a_column_go_over(self):
        self.add("Over", column="ready")
        self.assertEqual(sum(1 for c in self.get()["cards"] if c["column"] == "ready"), 3)

    def test_refuse_keeps_cards_out_of_a_full_column(self):
        self.change(settings={"limitPolicy": "refuse"})
        message = self.refused(lambda: self.add("Over", column="ready"), "Ready is full", "its limit is 2 cards", kind=Conflict)
        self.assertNotIn("\n", message)
        self.refused(lambda: self.store.update_card(self.board, self.outside, {"column": "ready"}), "Ready is full", kind=Conflict)

    def test_reordering_inside_and_moving_out_are_fine(self):
        self.change(settings={"limitPolicy": "refuse"})
        self.store.update_card(self.board, self.first, {"rank": 99})
        self.store.update_card(self.board, self.first, {"column": "ready", "rank": 100, "title": "Still here"})
        self.store.update_card(self.board, self.first, {"column": "done"})
        self.store.update_card(self.board, self.outside, {"column": "ready"})

    def test_archived_cards_do_not_count_but_coming_back_does(self):
        self.change(settings={"limitPolicy": "refuse"})
        self.store.update_card(self.board, self.first, {"archived": True})
        third = self.add("Takes the place", column="ready")
        self.refused(lambda: self.store.update_card(self.board, self.first, {"archived": False}), "Ready is full", kind=Conflict)
        self.store.update_card(self.board, third, {"column": "done"})
        self.store.update_card(self.board, self.first, {"archived": False})

    def test_cards_from_a_removed_column_must_fit(self):
        self.change(settings={"limitPolicy": "refuse"})
        board = self.get()
        staying = [c for c in board["columns"] if c["id"] != "backlog"]
        self.refused(lambda: self.change(columns=staying, moveCards={"backlog": "ready"}), "over its limit of 2", kind=Conflict)
        self.change(columns=staying, moveCards={"backlog": "done"})

    def test_lowering_a_limit_is_not_refused(self):
        self.change(settings={"limitPolicy": "refuse"})
        board = self.change(columns=[dict(c, limit=1 if c["id"] == "ready" else c["limit"]) for c in self.get()["columns"]])
        self.assertEqual(next(c["limit"] for c in board["columns"] if c["id"] == "ready"), 1)


class CopyOnWriteTest(Case):
    def test_a_board_already_handed_out_never_changes(self):
        self.change(labels=["Bug"], properties=[{"name": "Points", "type": "number"}], views=[{"name": "V", "query": "x"}])
        key = self.add(labels=["Bug"], properties={"points": 1}, tasks=["One"])
        other = self.store.create_board("Other")
        held = self.get()
        snapshot = json.dumps(held, sort_keys=True)
        _, tid = self.store.add_task(self.board, key, {"text": "Two"})
        self.store.update_task(self.board, key, tid, {"done": True})
        self.store.delete_task(self.board, key, tid)
        self.change(labels=["Feature"], properties=[], views=[], settings={"cardFace": ["labels"], "limitPolicy": "refuse"})
        self.store.update_card(self.board, key, {"properties": {}, "tasks": [], "labels": []})
        self.store.transfer_card(self.board, key, other["id"])
        self.assertEqual(json.dumps(held, sort_keys=True), snapshot)


if __name__ == "__main__":
    unittest.main()
