"""Board templates: the built-in ones, the file check for saved ones, and board to template and back."""
import copy
import json
import os
import re
import tempfile
import unittest

from laneboard import properties as P
from laneboard import query as Q
from laneboard import schema
from laneboard import templates as T
from laneboard.schema import COLORS, BadFile, NotFound, StoreError, slug
from laneboard.store import Store

PARTS = ("group", "labels", "field", "progress", "blocked")
TERM = re.compile(r'(?:^|[\s(])-?([A-Za-z][A-Za-z0-9-]*):("[^"]*"|[^\s)]+)')
NUMBER_TERM = re.compile(r"(<=|>=|<|>)?-?[0-9]+(\.[0-9]+)?(\.\.-?[0-9]+(\.[0-9]+)?)?")


def native_board():
    """A board the way the store keeps it: ids everywhere, labels, properties, views and settings."""
    props = P.clean_definitions({"properties": [], "cards": []}, [
        {"name": "Points", "type": "number", "min": 0, "max": 20, "unit": "pts"},
        {"name": "Priority", "type": "choice", "choices": ["Low", {"name": "High", "color": "rose"}]},
        {"name": "Platform", "type": "multi", "choices": ["iOS", "Web"]},
        {"name": "Reviewed", "type": "checkbox"},
        {"name": "Rating", "type": "rating", "max": 7},
        {"name": "Group", "type": "text"}])
    by_name = {p["name"]: p for p in props}
    high = by_name["Priority"]["choices"][1]["id"]
    return {
        "id": "b-1", "name": "Source board", "description": "d", "prefix": "SRC", "groupLabel": "Area", "rev": 9,
        "created": "2026-01-01T00:00:00Z", "updated": "2026-01-01T00:00:00Z",
        "columns": [{"id": "todo", "name": "To do", "limit": None}, {"id": "doing", "name": "Doing", "limit": 2},
                    {"id": "done", "name": "Done", "limit": None}],
        "groups": [{"id": "web", "name": "Web", "prefix": "WEB", "color": "teal", "link": "https://example.com",
                    "details": [{"label": "Owner", "value": "Nobody"}]}],
        "labels": [{"id": "bug", "name": "Bug", "color": "rose"}, {"id": "chore", "name": "Chore", "color": "slate"}],
        "properties": props,
        "views": [{"id": "open-bugs", "name": "Open bugs", "query": "label:bug -column:done", "sort": "-points"},
                  {"id": "all", "name": "Everything", "query": "", "sort": ""}],
        "settings": {"cardFace": ["group", "labels", by_name["Points"]["id"], "progress", by_name["Group"]["id"]],
                     "limitPolicy": "refuse", "defaultView": "open-bugs"},
        "checklists": [{"id": "done-list", "name": "Done list", "items": [
            {"id": "i1", "text": "Tests pass", "heading": False}, {"id": "i2", "text": "Parts", "heading": True}]}],
        "template": ["Steps"], "counters": {"SRC": 3},
        "cards": [
            {"key": "SRC-2", "title": "Second", "column": "doing", "group": "web", "rank": 2, "summary": "s", "blocked": "b",
             "notes": "private notes", "fields": [{"label": "Size", "value": "S"}],
             "sections": [{"label": "Steps", "body": "one"}], "checks": ["i1"], "archived": None,
             "labels": ["bug"], "tasks": [{"id": "a", "text": "Do it", "done": True}],
             "properties": {by_name["Points"]["id"]: 5, by_name["Priority"]["id"]: high,
                            by_name["Platform"]["id"]: [by_name["Platform"]["choices"][0]["id"]],
                            by_name["Reviewed"]["id"]: True, by_name["Rating"]["id"]: 4}},
            {"key": "SRC-1", "title": "First", "column": "todo", "group": None, "rank": 1, "notes": "more notes",
             "archived": None, "labels": [], "tasks": [], "properties": {}},
            {"key": "SRC-3", "title": "Old one", "column": "done", "group": None, "rank": 3, "archived": "2026-02-01T00:00:00Z"},
        ]}


def flatten(template):
    """The text of every string in a template, to check what it must not contain."""
    return json.dumps(template, ensure_ascii=False)


def plain_template(**changes):
    template = {"id": "mine", "name": "Mine", "description": "", "board": {"columns": [{"name": "A", "limit": None}]}, "cards": []}
    template.update(changes)
    return template


def refused(case, template, *words):
    with case.assertRaises(BadFile) as caught:
        T.check_template(template, "The boards file")
    message = str(caught.exception)
    case.assertTrue(message.startswith("The boards file: template "), message)
    for word in words:
        case.assertIn(word, message)
    return message


class BuiltIns(unittest.TestCase):
    def test_the_six_are_there_in_order(self):
        self.assertEqual([t["id"] for t in T.builtin_templates()],
                         ["basic", "software", "personal", "content", "support", "research"])

    def test_loaded_once_and_callers_get_copies(self):
        first = T.builtin_templates()
        first[0]["name"] = "Changed"
        first[0]["board"]["columns"].clear()
        again = T.builtin_templates()
        self.assertEqual(again[0]["name"], "Basic")
        self.assertTrue(again[0]["board"]["columns"])

    def test_every_one_passes_the_file_check(self):
        for template in T.builtin_templates():
            T.check_template(copy.deepcopy(template), "The boards file")

    def test_json_files_sit_in_the_package_folder(self):
        names = sorted(f for f in os.listdir(T.FOLDER) if f.endswith(".json"))
        self.assertEqual(names, sorted(i + ".json" for i in T.BUILTIN_IDS))

    def test_each_has_a_name_a_sentence_and_made_up_cards(self):
        for t in T.builtin_templates():
            self.assertTrue(t["name"].strip(), t["id"])
            self.assertTrue(t["description"].endswith("."), t["id"])
            self.assertTrue(3 <= len(t["cards"]) <= 6, t["id"])
            self.assertTrue(t["board"]["views"], t["id"])

    def test_no_dashes_and_no_leftover_marks(self):
        for t in T.builtin_templates():
            text = flatten(t)
            for bad in (chr(0x2014), chr(0x2013), "TODO", "FIXME", "lorem"):
                self.assertNotIn(bad, text, t["id"])

    def test_unique_ids_and_names(self):
        built = T.builtin_templates()
        self.assertEqual(len({t["id"] for t in built}), len(built))
        self.assertEqual(len({t["name"].casefold() for t in built}), len(built))

    def test_the_listed_contents(self):
        by_id = {t["id"]: t["board"] for t in T.builtin_templates()}
        self.assertEqual([c["name"] for c in by_id["basic"]["columns"]], ["Backlog", "Ready", "In progress", "Review", "Done"])
        self.assertEqual([c["limit"] for c in by_id["basic"]["columns"]], [None, None, 1, None, None])
        self.assertEqual([x["name"] for x in by_id["software"]["labels"]], ["Bug", "Feature", "Chore"])
        self.assertEqual([(p["name"], p["type"]) for p in by_id["software"]["properties"]],
                         [("Points", "number"), ("Priority", "choice"), ("Component", "choice")])
        self.assertEqual([c["name"] for c in by_id["software"]["properties"][1]["choices"]], ["Low", "Medium", "High", "Urgent"])
        self.assertEqual([c["name"] for c in by_id["personal"]["columns"]], ["To do", "Doing", "Done"])
        self.assertEqual([x["name"] for x in by_id["personal"]["labels"]], ["Home", "Errands", "Health"])
        self.assertEqual([c["name"] for c in by_id["content"]["columns"]], ["Ideas", "Drafting", "Editing", "Ready", "Published"])
        self.assertEqual([(p["name"], p["type"]) for p in by_id["content"]["properties"]],
                         [("Channel", "multi"), ("Word count", "number")])
        self.assertEqual([c["name"] for c in by_id["support"]["columns"]], ["New", "Triaged", "Waiting on customer", "Resolved"])
        self.assertEqual([(p["name"], p["type"]) for p in by_id["support"]["properties"]],
                         [("Severity", "choice"), ("Customer tier", "choice")])
        self.assertEqual(by_id["support"]["settings"]["limitPolicy"], "refuse")
        self.assertEqual({c["name"]: c["limit"] for c in by_id["support"]["columns"]}["Triaged"], 5)
        self.assertEqual([c["name"] for c in by_id["research"]["columns"]], ["Questions", "Reading", "Notes", "Written up"])
        self.assertEqual([(p["name"], p["type"]) for p in by_id["research"]["properties"]],
                         [("Source", "url"), ("Confidence", "rating")])

    def test_every_property_definition_passes_the_registry(self):
        for t in T.builtin_templates():
            cleaned = P.clean_definitions({"properties": [], "cards": []}, t["board"]["properties"])
            self.assertEqual(len(cleaned), len(t["board"]["properties"]), t["id"])
            P.check_definitions({"properties": copy.deepcopy(cleaned), "cards": []}, t["id"])
            self.assertLessEqual(len(cleaned), P.MAX_PROPERTIES)

    def test_every_starter_value_fits_its_property(self):
        for t in T.builtin_templates():
            board = {"properties": P.clean_definitions({"properties": [], "cards": []}, t["board"]["properties"])}
            for card in t["cards"]:
                for key, value in (card.get("properties") or {}).items():
                    prop = P.find_property(board, key)
                    self.assertIsNotNone(prop, (t["id"], key))
                    self.assertIsNotNone(P.TYPES[prop["type"]].parse_text(value, prop), (t["id"], key))

    def test_view_queries_only_name_things_the_template_has(self):
        for t in T.builtin_templates():
            board = t["board"]
            names = {"label": [x["name"] for x in board["labels"]], "column": [c["name"] for c in board["columns"]],
                     "group": [g["name"] for g in board["groups"]]}
            names["col"] = names["column"]
            defs = {"properties": P.clean_definitions({"properties": [], "cards": []}, board["properties"])}
            for view in board["views"]:
                self.assertTrue(view["query"] or view["sort"] or view["name"], t["id"])
                for qualifier, value in TERM.findall(view["query"]):
                    value = value.strip('"')
                    where = f"{t['id']}: {view['name']}: {qualifier}:{value}"
                    if qualifier in ("label", "column", "col", "group"):
                        self.assertTrue(any(n.casefold().startswith(value.casefold()) for n in names[qualifier]), where)
                    elif qualifier == "has":
                        self.assertTrue(value in ("notes", "summary", "labels", "tasks", "checklist")
                                        or P.find_property(defs, value), where)
                    elif qualifier == "is":
                        self.fail(where + " uses a qualifier that isn't about labels, columns, groups or properties")
                    else:
                        prop = P.find_property(defs, qualifier)
                        self.assertIsNotNone(prop, where)
                        if prop["type"] in ("choice", "multi"):
                            self.assertTrue(any(c["name"].casefold() == value.casefold() for c in prop["choices"]), where)
                        else:
                            self.assertRegex(value, NUMBER_TERM, where)
                for sort in filter(None, (s.strip().lstrip("-") for s in view["sort"].split(","))):
                    self.assertTrue(sort in ("title", "key", "created", "updated", "rank") or P.find_property(defs, sort),
                                    f"{t['id']}: {view['name']}: sort {sort}")

    def test_card_face_and_default_view_point_at_things_that_exist(self):
        for t in T.builtin_templates():
            board = t["board"]
            defs = {"properties": P.clean_definitions({"properties": [], "cards": []}, board["properties"])}
            for part in board["settings"].get("cardFace", []):
                self.assertTrue(part in PARTS or P.find_property(defs, part), (t["id"], part))

    def test_cards_use_columns_labels_and_groups_the_template_has(self):
        for t in T.builtin_templates():
            columns = {c["name"].casefold() for c in t["board"]["columns"]}
            labels = {x["name"].casefold() for x in t["board"]["labels"]}
            for card in t["cards"]:
                self.assertIn(card["column"].casefold(), columns)
                for name in card.get("labels", []):
                    self.assertIn(name.casefold(), labels)
                self.assertNotIn("key", card)
                self.assertNotIn("notes", card)
                self.assertNotIn("archived", card)

    def test_colors_are_known(self):
        for t in T.builtin_templates():
            for x in t["board"]["labels"]:
                self.assertIn(x["color"], COLORS)
            for p in t["board"]["properties"]:
                for c in p.get("choices", []):
                    self.assertIn(c["color"], COLORS)

    def test_a_template_makes_a_board_in_the_import_shape(self):
        for t in T.builtin_templates():
            board = T.board_from_template(t)
            self.assertEqual(board["name"], t["name"])
            self.assertEqual(board["description"], t["description"])
            self.assertEqual(len(board["cards"]), len(t["cards"]))
            for key in ("columns", "labels", "properties", "views", "settings"):
                self.assertIn(key, board)


class Describing(unittest.TestCase):
    def test_describe_a_built_in(self):
        info = T.describe(T.find_template([], "support"))
        self.assertEqual(set(info), {"id", "name", "description", "builtIn", "columns", "cards"})
        self.assertEqual((info["id"], info["name"], info["builtIn"], info["columns"], info["cards"]), ("support", "Customer support", True, 4, 5))

    def test_describe_a_saved_one(self):
        template = plain_template(cards=[{"title": "x"}], description="About")
        info = T.describe(template)
        self.assertEqual(info, {"id": "mine", "name": "Mine", "description": "About", "builtIn": False, "columns": 1, "cards": 1})


class Lookups(unittest.TestCase):
    def setUp(self):
        self.saved = [plain_template(id="software-2", name="Software"), plain_template(id="weekly", name="Weekly review")]

    def test_built_ins_by_id_first(self):
        self.assertEqual(T.find_template(self.saved, "software")["name"], "Software team")

    def test_saved_by_id(self):
        self.assertEqual(T.find_template(self.saved, "weekly")["name"], "Weekly review")

    def test_by_name_ignoring_case(self):
        self.assertEqual(T.find_template(self.saved, "WEEKLY REVIEW")["id"], "weekly")
        self.assertEqual(T.find_template(self.saved, "customer SUPPORT")["id"], "support")
        self.assertEqual(T.find_template(self.saved, "  Personal ")["id"], "personal")

    def test_built_in_name_beats_a_saved_name(self):
        self.assertEqual(T.find_template([plain_template(id="x", name="Basic")], "basic")["id"], "basic")
        self.assertEqual(T.find_template([plain_template(id="x", name="Research notes")], "research notes")["id"], "research")

    def test_missing_and_odd_input(self):
        self.assertIsNone(T.find_template(self.saved, "nothing"))
        self.assertIsNone(T.find_template(self.saved, None))
        self.assertIsNone(T.find_template(self.saved, 3))
        self.assertIsNone(T.find_template(None, "weekly"))
        self.assertIsNone(T.find_template([None, "x", {}], "weekly"))

    def test_a_saved_template_is_returned_as_is_and_built_ins_are_copies(self):
        self.assertIs(T.find_template(self.saved, "weekly"), self.saved[1])
        T.find_template([], "basic")["name"] = "Changed"
        self.assertEqual(T.find_template([], "basic")["name"], "Basic")

    def test_get_template_says_what_there_is(self):
        self.assertEqual(T.get_template(self.saved, "weekly")["id"], "weekly")
        with self.assertRaises(NotFound) as caught:
            T.get_template(self.saved, "nope")
        self.assertIn("There's no template called nope.", str(caught.exception))
        self.assertIn("Basic", str(caught.exception))
        self.assertIn("Weekly review", str(caught.exception))


class FileChecks(unittest.TestCase):
    def test_a_small_template_passes_and_gets_defaults(self):
        template = plain_template()
        T.check_template(template, "The boards file")
        board = template["board"]
        self.assertEqual((board["prefix"], board["groupLabel"], board["labels"], board["properties"], board["views"],
                          board["settings"], board["checklists"], board["template"]),
                         ("T", "Group", [], [], [], {}, [], []))

    def test_not_an_object_or_no_id_or_name(self):
        for bad in (None, [], "x"):
            with self.assertRaises(BadFile) as caught:
                T.check_template(bad, "The boards file")
            self.assertIn("a template isn't an object", str(caught.exception))
        with self.assertRaises(BadFile) as caught:
            T.check_template({"name": "x"}, "The boards file")
        self.assertIn("needs an id", str(caught.exception))
        with self.assertRaises(BadFile) as caught:
            T.check_template(plain_template(id="Not An Id"), "The boards file")
        self.assertIn("needs an id", str(caught.exception))
        with self.assertRaises(BadFile) as caught:
            T.check_template(plain_template(name=" "), "The boards file")
        self.assertIn("needs a name", str(caught.exception))

    def test_description_board_and_cards(self):
        refused(self, plain_template(description=5), "'Mine'", "description")
        refused(self, plain_template(board=None), "'Mine'", "board part")
        refused(self, plain_template(cards="x"), "'Mine'", "cards must be a list")

    def test_columns(self):
        refused(self, plain_template(board={"columns": []}), "columns")
        refused(self, plain_template(board={"columns": ["A"]}), "columns")
        refused(self, plain_template(board={"columns": [{"name": "A"}, {"name": "a"}]}), "two named 'a'")
        refused(self, plain_template(board={"columns": [{"name": "A", "limit": 0}]}), "limit of column 'A'")
        refused(self, plain_template(board={"columns": [{"name": "A", "limit": "3"}]}), "limit of column 'A'")
        refused(self, plain_template(board={"columns": [{"name": "A", "limit": True}]}), "limit of column 'A'")
        refused(self, plain_template(board={"columns": [{"name": f"C{i}"} for i in range(25)]}), "at most 20")

    def test_prefix_group_label_and_groups(self):
        columns = [{"name": "A", "limit": None}]
        refused(self, plain_template(board={"columns": columns, "prefix": "lower"}), "key prefix")
        refused(self, plain_template(board={"columns": columns, "groupLabel": ""}), "group label")
        refused(self, plain_template(board={"columns": columns, "groups": [{"name": "G", "color": "pink"}]}), "unknown color")
        refused(self, plain_template(board={"columns": columns, "groups": [{"name": "G", "prefix": "no"}]}), "bad key prefix")
        refused(self, plain_template(board={"columns": columns, "groups": [{"name": "G"}, {"name": "g"}]}), "two named")
        refused(self, plain_template(board={"columns": columns, "groups": [{"name": "G", "details": [{"value": "x"}]}]}), "details")

    def test_labels(self):
        columns = [{"name": "A", "limit": None}]
        refused(self, plain_template(board={"columns": columns, "labels": ["Bug"]}), "labels must be a list of labels")
        refused(self, plain_template(board={"columns": columns, "labels": [{"name": "Bug", "color": "pink"}]}), "label 'Bug'", "color")
        refused(self, plain_template(board={"columns": columns, "labels": [{"name": "Bug"}, {"name": "BUG"}]}), "two named 'BUG'")
        refused(self, plain_template(board={"columns": columns, "labels": [{"name": "x" * 41}]}), "1 to 40")
        refused(self, plain_template(board={"columns": columns, "labels": [{"name": f"L{i}"} for i in range(51)]}), "at most 50")

    def test_properties_go_through_the_registry(self):
        columns = [{"name": "A", "limit": None}]
        message = refused(self, plain_template(board={"columns": columns, "properties": [{"name": "P", "type": "color"}]}), "'Mine'")
        self.assertIn('There\'s no property type called "color"', message)
        refused(self, plain_template(board={"columns": columns, "properties": [{"name": "P", "type": "choice"}]}), "needs at least one choice")
        refused(self, plain_template(board={"columns": columns, "properties": [{"name": "P", "type": "text"}, {"name": "p", "type": "url"}]}), "two properties named")
        refused(self, plain_template(board={"columns": columns, "properties": "x"}), "Properties must be a list")

    def test_views(self):
        columns = [{"name": "A", "limit": None}]
        refused(self, plain_template(board={"columns": columns, "views": [{"name": "V", "query": 3}]}), "query of view 'V'")
        refused(self, plain_template(board={"columns": columns, "views": [{"name": "V", "query": "x" * 501}]}), "query of view 'V'")
        refused(self, plain_template(board={"columns": columns, "views": [{"name": "V"}, {"name": "v"}]}), "two named")
        refused(self, plain_template(board={"columns": columns, "views": [{"name": "V" + str(i)} for i in range(31)]}), "at most 30")

    def test_checklists_and_new_card_sections(self):
        columns = [{"name": "A", "limit": None}]
        refused(self, plain_template(board={"columns": columns, "checklists": [{"name": ""}]}), "checklist needs a name")
        refused(self, plain_template(board={"columns": columns, "checklists": [{"name": "C", "items": [{"id": "i"}]}]}), "needs text")
        refused(self, plain_template(board={"columns": columns, "template": ["a", "A"]}), "listed twice")
        refused(self, plain_template(board={"columns": columns, "template": [3]}), "new card sections")

    def test_settings(self):
        columns = [{"name": "A", "limit": None}]
        refused(self, plain_template(board={"columns": columns, "settings": []}), "settings must be an object")
        refused(self, plain_template(board={"columns": columns, "settings": {"limitPolicy": "never"}}), "warn or refuse")
        refused(self, plain_template(board={"columns": columns, "settings": {"cardFace": ["Mood"]}}), "'Mood'")
        refused(self, plain_template(board={"columns": columns, "settings": {"cardFace": "labels"}}), "cardFace")
        refused(self, plain_template(board={"columns": columns, "settings": {"defaultView": "Nope"}}), "defaultView")
        ok = plain_template(board={"columns": columns, "views": [{"name": "V"}],
                                   "settings": {"cardFace": ["labels", "progress"], "limitPolicy": "refuse", "defaultView": "v"}})
        T.check_template(ok, "The boards file")

    def test_starter_cards(self):
        columns = [{"name": "A", "limit": None}]
        board = {"columns": columns, "labels": [{"name": "Bug"}], "groups": [{"name": "G"}],
                 "properties": [{"name": "Points", "type": "number", "min": 0}]}

        def with_card(**card):
            return plain_template(board=copy.deepcopy(board), cards=[{"title": "T", "column": "A", **card}])

        T.check_template(with_card(labels=["bug"], group="g", properties={"points": "3"}, tasks=[{"text": "x"}]), "The boards file")
        refused(self, plain_template(board=copy.deepcopy(board), cards=["x"]), "starter card number 1")
        refused(self, plain_template(board=copy.deepcopy(board), cards=[{"title": ""}]), "needs a title")
        refused(self, with_card(column="Z"), "card 'T'", "column the template doesn't have")
        refused(self, with_card(group="Z"), "group the template doesn't have")
        refused(self, with_card(labels=["Nope"]), "label 'Nope'")
        refused(self, with_card(labels="Bug"), "labels must be a list")
        refused(self, with_card(properties={"Mood": "x"}), "'Mood'", "property the template doesn't have")
        refused(self, with_card(properties={"Points": "lots"}), "Points")
        refused(self, with_card(properties={"Points": -3}), "Points")
        refused(self, with_card(properties=[]), "properties must map")
        refused(self, with_card(tasks=["x"]), "each task")
        refused(self, with_card(tasks=[{"text": "x", "done": "yes"}]), "each task")
        refused(self, with_card(tasks=[{"text": "x" * 301}]), "each task")
        refused(self, with_card(summary=4), "summary must be text")
        refused(self, with_card(checks="i1"), "checks must be a list")
        refused(self, with_card(fields=[{"value": "x"}]), "fields")

    def test_the_list_check(self):
        good = [plain_template(id="one", name="One"), plain_template(id="two", name="Two")]
        T.check_templates(good, "The boards file")
        T.check_templates([], "The boards file")
        for bad, word in (([plain_template(id="one", name="One"), plain_template(id="one", name="Two")], "share the id"),
                          ([plain_template(id="one", name="One"), plain_template(id="two", name="ONE")], "are named"),
                          ([plain_template(id="basic", name="Mine")], "built-in"),
                          ("x", "must be a list")):
            with self.assertRaises(BadFile) as caught:
                T.check_templates(bad, "The boards file")
            self.assertIn(word, str(caught.exception))

    def test_the_error_is_a_plain_unavailable_error(self):
        with self.assertRaises(BadFile) as caught:
            T.check_template(plain_template(board={"columns": []}), "The boards file")
        self.assertNotIn("Traceback", str(caught.exception))
        self.assertEqual(caught.exception.status, 503)


class OneSetOfLimits(unittest.TestCase):
    """A template that passes the file check fits the store's limits, because both read them from the schema."""

    def test_no_module_keeps_limits_of_its_own(self):
        for module in (T, Q):
            with open(module.__file__, encoding="utf-8") as f:
                source = f.read()
            local = re.findall(r"^(MAX_\w+|\w*FACE_PARTS|LIMIT_POLICIES) *=", source, re.M)
            self.assertEqual([name for name in local if hasattr(schema, name) or "FACE" in name], [], module.__name__)

    def test_the_longest_names_the_check_allows_make_a_board(self):
        board = {"columns": [{"name": "C" * schema.MAX_COLUMN_NAME, "limit": schema.MAX_COLUMN_LIMIT}],
                 "groupLabel": "L" * schema.MAX_GROUP_LABEL, "groups": [{"name": "G" * schema.MAX_GROUP_NAME}],
                 "labels": [{"name": "B" * schema.MAX_LABEL_NAME}], "views": [{"name": "V" * schema.MAX_VIEW_NAME}],
                 "template": ["S" * schema.MAX_SECTION_NAME],
                 "checklists": [{"name": "K" * schema.MAX_CHECKLIST_NAME, "items": [{"text": "x" * schema.MAX_CHECK_TEXT}]}]}
        template = plain_template(board=board)
        T.check_template(template, "The boards file")
        with tempfile.TemporaryDirectory() as tmp:
            made = Store(os.path.join(tmp, "boards.json"), starter=False).import_board(T.board_from_template(template))
        self.assertEqual(made["columns"][0]["name"], "C" * schema.MAX_COLUMN_NAME)

    def test_one_more_is_refused_by_the_check(self):
        for part, value in (("columns", [{"name": "C" * (schema.MAX_COLUMN_NAME + 1)}]),
                            ("columns", [{"name": "C", "limit": schema.MAX_COLUMN_LIMIT + 1}]),
                            ("groupLabel", "L" * (schema.MAX_GROUP_LABEL + 1)),
                            ("groups", [{"name": "G" * (schema.MAX_GROUP_NAME + 1)}]),
                            ("groups", [{"name": "G", "link": "example.com"}]),
                            ("template", ["S" * (schema.MAX_SECTION_NAME + 1)]),
                            ("checklists", [{"name": "K" * (schema.MAX_CHECKLIST_NAME + 1)}]),
                            ("checklists", [{"name": "K", "items": [{"text": "x" * (schema.MAX_CHECK_TEXT + 1)}]}]),
                            ("views", [{"name": "V", "sort": "x" * (schema.MAX_SORT + 1)}])):
            board = {"columns": [{"name": "A", "limit": None}], part: value}
            with self.subTest(part=part, value=str(value)[:40]):
                refused(self, plain_template(board=board), "'Mine'")


class SavedInTheFile(unittest.TestCase):
    """Saved templates in a boards file get the full check when the file loads, as boards do."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "boards.json")

    def write(self, templates):
        data = {"version": 3, "boards": [{"id": "b1", "name": "Work", "columns": [{"id": "a", "name": "A"}], "cards": []}],
                "templates": templates}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        with open(self.path, "rb") as f:
            return f.read()

    def refused(self, templates, *phrases):
        raw = self.write(templates)
        with self.assertRaises(BadFile) as caught:
            Store(self.path)
        for phrase in phrases:
            self.assertIn(phrase, str(caught.exception))
        self.assertIn("Your file was not changed", str(caught.exception))
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), raw)

    def test_a_good_one_loads_with_its_defaults(self):
        self.write([plain_template(board={"columns": [{"name": "A"}]})])
        saved = Store(self.path).all_data()["templates"][0]
        self.assertEqual((saved["board"]["prefix"], saved["board"]["columns"][0]["limit"], saved["board"]["labels"]), ("T", None, []))

    def test_bad_ones_are_refused_naming_the_template(self):
        self.refused([plain_template(board={"columns": [{"name": "A"}], "properties": [{"name": "P", "type": "colour"}]})],
                     "template 'Mine'", 'no property type called "colour"')
        self.refused([plain_template(cards=[{"title": "Card", "column": "Nowhere"}])], "template 'Mine', card 'Card'")
        self.refused([plain_template(id="basic")], "belongs to a built-in template")
        self.refused([plain_template(), plain_template(id="other", name="MINE")], "two templates are named 'MINE'")


class FromBoard(unittest.TestCase):
    def test_structure_is_kept_by_name(self):
        board = native_board()
        template = T.template_from_board(board, "My setup", "Mine to reuse")
        self.assertEqual((template["name"], template["description"], template["cards"]), ("My setup", "Mine to reuse", []))
        shape = template["board"]
        self.assertEqual((shape["prefix"], shape["groupLabel"]), ("SRC", "Area"))
        self.assertEqual(shape["columns"], [{"name": "To do", "limit": None}, {"name": "Doing", "limit": 2}, {"name": "Done", "limit": None}])
        self.assertEqual(shape["groups"], [{"name": "Web", "prefix": "WEB", "color": "teal", "link": "https://example.com",
                                            "details": [{"label": "Owner", "value": "Nobody"}]}])
        self.assertEqual(shape["labels"], [{"name": "Bug", "color": "rose"}, {"name": "Chore", "color": "slate"}])
        self.assertEqual(shape["views"][0], {"name": "Open bugs", "query": "label:bug -column:done", "sort": "-points"})
        self.assertEqual(shape["template"], ["Steps"])
        self.assertEqual(shape["checklists"][0]["items"][1], {"id": "i2", "text": "Parts", "heading": True})
        self.assertNotIn("id", json.dumps(shape["labels"] + shape["columns"] + shape["views"] + shape["properties"]))
        self.assertNotIn("onCard", flatten(shape["properties"]))

    def test_property_options_come_across(self):
        props = {p["name"]: p for p in T.template_from_board(native_board(), "X")["board"]["properties"]}
        self.assertEqual(props["Points"], {"name": "Points", "type": "number", "min": 0, "max": 20, "decimals": 0, "unit": "pts"})
        self.assertEqual(props["Priority"]["choices"], [{"name": "Low", "color": "blue"}, {"name": "High", "color": "rose"}])
        self.assertEqual(props["Rating"], {"name": "Rating", "type": "rating", "max": 7})
        self.assertEqual([c["name"] for c in props["Platform"]["choices"]], ["iOS", "Web"])

    def test_settings_use_names(self):
        settings = T.template_from_board(native_board(), "X")["board"]["settings"]
        self.assertEqual(settings["cardFace"], ["group", "labels", "Points", "progress", "group-2"])
        self.assertEqual(settings["limitPolicy"], "refuse")
        self.assertEqual(settings["defaultView"], "Open bugs")

    def test_a_board_without_the_newer_parts(self):
        board = {"name": "Old", "prefix": "T", "groupLabel": "Group", "columns": [{"id": "a", "name": "A", "limit": None}],
                 "groups": [], "checklists": [], "template": [], "cards": [{"key": "T-1", "title": "x", "column": "a", "rank": 1}]}
        template = T.template_from_board(board, "Old style", include_cards=True)
        self.assertEqual(template["board"]["labels"], [])
        self.assertEqual(template["board"]["properties"], [])
        self.assertEqual(template["board"]["settings"], {})
        self.assertEqual(template["cards"], [{"title": "x", "column": "A"}])

    def test_cards_are_stripped_unless_asked(self):
        self.assertEqual(T.template_from_board(native_board(), "X")["cards"], [])
        self.assertEqual(T.template_from_board(native_board(), "X", include_cards=False)["cards"], [])

    def test_starter_cards_keep_everything_but_notes_keys_and_archived(self):
        template = T.template_from_board(native_board(), "X", include_cards=True)
        first, second = template["cards"]
        self.assertEqual([first["title"], second["title"]], ["First", "Second"])  # by rank, archived one left out
        self.assertEqual(first, {"title": "First", "column": "To do"})
        self.assertEqual(second, {
            "title": "Second", "column": "Doing", "group": "Web", "summary": "s", "blocked": "b",
            "fields": [{"label": "Size", "value": "S"}], "sections": [{"label": "Steps", "body": "one"}],
            "checks": ["i1"], "labels": ["Bug"], "tasks": [{"text": "Do it", "done": True}],
            "properties": {"Points": "5 pts", "Priority": "High", "Platform": "iOS", "Reviewed": "yes", "Rating": "4/7"}})
        text = flatten(template)
        for gone in ("notes", "SRC-", "archived", "created", "rank"):
            self.assertNotIn(gone, text)

    def test_the_template_passes_its_own_file_check(self):
        template = T.template_from_board(native_board(), "X", include_cards=True)
        T.check_template(copy.deepcopy(template), "The boards file")

    def test_the_source_board_is_not_changed(self):
        board = native_board()
        before = copy.deepcopy(board)
        T.template_from_board(board, "X", include_cards=True)
        self.assertEqual(board, before)

    def test_ids_are_slugs_unique_against_taken_and_built_ins(self):
        board = native_board()
        self.assertEqual(T.template_from_board(board, "Weekly Review!")["id"], "weekly-review")
        self.assertEqual(T.template_from_board(board, "Weekly Review!", taken_ids=["weekly-review"])["id"], "weekly-review-2")
        self.assertEqual(T.template_from_board(board, "Weekly Review!", taken_ids=("weekly-review", "weekly-review-2"))["id"],
                         "weekly-review-3")
        self.assertEqual(T.template_from_board(board, "Basic")["id"], "basic-2")
        self.assertEqual(T.template_from_board(board, "Software")["id"], "software-2")
        self.assertEqual(T.template_from_board(board, "???")["id"], "item")

    def test_name_and_description_are_checked(self):
        board = native_board()
        for bad in ("", "   ", None, 4):
            with self.assertRaises(StoreError):
                T.template_from_board(board, bad)
        with self.assertRaises(StoreError):
            T.template_from_board(board, "x" * 121)
        with self.assertRaises(StoreError):
            T.template_from_board(board, "ok", "d" * 1001)
        self.assertEqual(T.template_from_board(board, "  Spaced   out ")["name"], "Spaced   out")

    def test_a_board_that_cant_be_a_template_is_a_plain_user_error(self):
        board = native_board()
        board["labels"] = [{"id": f"l{i}", "name": f"L{i}", "color": "slate"} for i in range(51)]
        with self.assertRaises(StoreError) as caught:
            T.template_from_board(board, "Too many")
        self.assertNotIsInstance(caught.exception, BadFile)
        self.assertIn("can't be saved as a template", str(caught.exception))


class BackToABoard(unittest.TestCase):
    def test_the_import_shape(self):
        template = T.find_template([], "software")
        board = T.board_from_template(template)
        self.assertEqual(set(board), {"name", "description", "prefix", "groupLabel", "columns", "groups", "labels", "properties",
                                      "views", "settings", "checklists", "template", "cards"})
        self.assertEqual(board["name"], "Software team")
        self.assertEqual(board["columns"][2], {"name": "In progress", "limit": 3})
        self.assertEqual(board["prefix"], "APP")

    def test_name_and_description_can_be_replaced(self):
        template = T.find_template([], "basic")
        board = T.board_from_template(template, "Chores", "House stuff")
        self.assertEqual((board["name"], board["description"]), ("Chores", "House stuff"))
        board = T.board_from_template(template, name="", description="")
        self.assertEqual((board["name"], board["description"]), ("Basic", ""))

    def test_it_is_a_fresh_copy(self):
        template = T.find_template([], "support")
        board = T.board_from_template(template)
        board["columns"][0]["name"] = "Changed"
        board["cards"][0]["title"] = "Changed"
        board["settings"]["cardFace"].append("x")
        again = T.board_from_template(template)
        self.assertEqual(again["columns"][0]["name"], "New")
        self.assertNotEqual(again["cards"][0]["title"], "Changed")
        self.assertNotIn("x", again["settings"]["cardFace"])

    def test_round_trip_keeps_the_structure(self):
        original = T.template_from_board(native_board(), "Round", "Trip", include_cards=True)
        board = T.board_from_template(original)
        again = T.template_from_board(import_like(board), "Round", "Trip", include_cards=True)
        self.assertEqual(again, original)

    def test_round_trip_for_every_built_in(self):
        for built in T.builtin_templates():
            board = T.board_from_template(built)
            again = T.template_from_board(import_like(board), built["name"], built["description"], include_cards=True,
                                          taken_ids=[built["id"]])
            self.assertEqual(again["board"]["columns"], built["board"]["columns"], built["id"])
            self.assertEqual(again["board"]["labels"], built["board"]["labels"], built["id"])
            self.assertEqual(again["board"]["views"], built["board"]["views"], built["id"])
            self.assertEqual(again["board"]["template"], built["board"]["template"], built["id"])
            self.assertEqual(again["board"]["settings"]["limitPolicy"], built["board"]["settings"].get("limitPolicy", "warn")
                             if "settings" in built["board"] else "warn")
            self.assertEqual([p["name"] for p in again["board"]["properties"]], [p["name"] for p in built["board"]["properties"]])
            self.assertEqual([c["title"] for c in again["cards"]], [c["title"] for c in built["cards"]], built["id"])

    def test_include_cards_off_gives_a_board_with_no_cards(self):
        template = T.template_from_board(native_board(), "Bare")
        self.assertEqual(T.board_from_template(template)["cards"], [])
        self.assertEqual(len(T.board_from_template(T.template_from_board(native_board(), "Full", include_cards=True))["cards"]), 2)


def import_like(shape):
    """What the store does with an import dict, reduced to what these tests need: ids for everything, then keys for cards."""
    board = {"name": shape["name"], "description": shape["description"], "prefix": shape["prefix"], "groupLabel": shape["groupLabel"],
             "checklists": copy.deepcopy(shape["checklists"]), "template": list(shape["template"])}

    def ided(items, **extra):
        out, used = [], set()
        for item in items:
            made = {**item, "id": slug(item["name"], used)}
            used.add(made["id"])
            out.append({**made, **extra})
        return out

    board["columns"] = ided(shape["columns"])
    board["groups"] = ided(shape["groups"])
    board["labels"] = ided(shape["labels"])
    board["views"] = ided(shape["views"])
    board["properties"] = P.clean_definitions({"properties": [], "cards": []}, shape["properties"])
    pseudo = {"properties": board["properties"]}
    by_name = {key: {x["name"].casefold(): x["id"] for x in board[key]} for key in ("columns", "groups", "labels", "views")}
    settings = shape.get("settings") or {}
    board["settings"] = {
        "cardFace": [part if part in PARTS else P.find_property(pseudo, part)["id"] for part in settings.get("cardFace", [])],
        "limitPolicy": settings.get("limitPolicy", "warn"),
        "defaultView": by_name["views"][settings["defaultView"].casefold()] if settings.get("defaultView") else None}
    cards = []
    for n, raw in enumerate(shape["cards"], 1):
        card = {"key": f"{shape['prefix']}-{n}", "title": raw["title"], "column": by_name["columns"][raw["column"].casefold()],
                "rank": n, "archived": None, "notes": "", "labels": [], "properties": {}, "tasks": []}
        if raw.get("group"):
            card["group"] = by_name["groups"][raw["group"].casefold()]
        for part in ("summary", "blocked", "fields", "sections", "checks"):
            if part in raw:
                card[part] = copy.deepcopy(raw[part])
        card["labels"] = [by_name["labels"][name.casefold()] for name in raw.get("labels", [])]
        card["tasks"] = [{"id": f"t{i}", **t} for i, t in enumerate(raw.get("tasks", []), 1)]
        for key, value in raw.get("properties", {}).items():
            prop = P.find_property(pseudo, key)
            card["properties"][prop["id"]] = P.TYPES[prop["type"]].parse_text(value, prop)
        cards.append(card)
    board["cards"] = cards
    return board


if __name__ == "__main__":
    unittest.main()
