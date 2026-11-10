"""The search language: words, qualifiers, properties, sorting, archived cards, errors and speed."""
import copy
import os
import time
import unittest

from laneboard import properties as P
from laneboard import query as Q
from laneboard.schema import StoreError

STAMP = "2026-01-01T00:00:00Z"


def define(*raw):
    return P.clean_definitions({"properties": [], "cards": []}, list(raw))


PROPS = define(
    {"name": "Points", "type": "number", "min": 0, "max": 100},
    {"name": "Hours", "type": "number", "decimals": 1, "unit": "h"},
    {"name": "Priority", "type": "choice", "choices": ["Low", "Medium", "High", "Highest"]},
    {"name": "Platform", "type": "multi", "choices": ["iOS", "Android", "Web"]},
    {"name": "Reviewed", "type": "checkbox"},
    {"name": "Link", "type": "url"},
    {"name": "Stars", "type": "rating"},
    {"name": "Owner", "type": "text"},
    {"name": "Story points", "type": "number"},
)


def card(key, title, column, rank, **extra):
    out = {"key": key, "title": title, "column": column, "rank": rank, "group": None, "summary": "", "blocked": "",
           "notes": "", "fields": [], "sections": [], "checks": [], "archived": None, "created": STAMP, "updated": STAMP,
           "labels": [], "properties": {}, "tasks": []}
    out.update(extra)
    return out


def make_board():
    """Five columns (Ready and Review hold at most one card), three groups, three labels, every property type."""
    board = {
        "id": "b-test", "name": "Test", "description": "", "prefix": "T", "groupLabel": "Area", "rev": 1,
        "created": STAMP, "updated": STAMP, "template": [], "counters": {"T": 20},
        "columns": [{"id": "backlog", "name": "Backlog", "limit": None}, {"id": "ready", "name": "Ready", "limit": 1},
                    {"id": "in-progress", "name": "In progress", "limit": None},
                    {"id": "review", "name": "Review", "limit": 1}, {"id": "done", "name": "Done", "limit": None}],
        "groups": [{"id": "web", "name": "Web site", "prefix": "T", "color": "blue", "link": "", "details": []},
                   {"id": "api", "name": "API", "prefix": "T", "color": "teal", "link": "", "details": []},
                   {"id": "apps", "name": "Apps", "prefix": "T", "color": "green", "link": "", "details": []}],
        "labels": [{"id": "bug", "name": "Bug", "color": "rose"}, {"id": "feature", "name": "Feature", "color": "blue"},
                   {"id": "fix-later", "name": "Fix later", "color": "slate"}],
        "properties": copy.deepcopy(PROPS),
        "views": [],
        "settings": {"cardFace": ["group", "labels", "field", "progress", "blocked"], "limitPolicy": "warn",
                     "defaultView": None},
        "checklists": [{"id": "ship", "name": "Ship", "items": [{"id": "before", "text": "Before", "heading": True},
                                                                 {"id": "tests", "text": "Tests pass", "heading": False},
                                                                 {"id": "docs", "text": "Docs", "heading": False}]}],
        "cards": [
            card("T-1", "Login page crashes", "backlog", 2, group="web", labels=["bug"], summary="People can't sign in.",
                 notes="Steps:\nopen the  login\npage and wait", checks=["tests"],
                 tasks=[{"id": "a", "text": "Write a failing test", "done": True}, {"id": "b", "text": "Fix the form", "done": False}],
                 fields=[{"label": "Team", "value": "Accounts"}], sections=[{"label": "Plan", "body": "Retry the session"}],
                 created="2026-01-03T00:00:00Z", updated="2026-03-01T00:00:00Z",
                 properties={"points": 3, "hours": 2.5, "priority": "high", "platform": ["ios", "web"], "reviewed": True,
                             "link": "https://example.com/issue/1", "stars": 4, "owner": "Ann Lee", "story-points": 2}),
            card("T-2", "Café menu", "backlog", 1, group="api", labels=["feature"], created="2026-01-02T00:00:00Z",
                 updated="2026-02-01T00:00:00Z",
                 properties={"points": 5, "priority": "low", "platform": ["android"], "stars": 2, "owner": "Bo"}),
            card("T-10", "Export to PDF", "ready", 1, labels=["feature", "fix-later"], blocked="Waiting on the printer",
                 tasks=[{"id": "x", "text": "Pick a library", "done": True}], created="2026-01-10T00:00:00Z",
                 properties={"points": 8, "priority": "medium", "platform": ["ios", "android", "web"]}),
            card("T-3", "Write docs", "ready", 2, checks=["tests", "docs"], notes="Cover the install.",
                 created="2026-01-04T00:00:00Z", updated="2026-04-01T00:00:00Z"),
            card("T-4", "Old crash report", "review", 1, labels=["bug"], archived="2026-02-01T00:00:00Z",
                 properties={"points": 1}),
            card("T-5", "Review the release", "review", 2, group="web", created="2026-01-05T00:00:00Z",
                 properties={"points": 13, "stars": 2, "priority": "highest", "hours": 0.5}),
            card("T-6", "Shipped", "done", 1, group="apps", properties={"reviewed": True}),
        ],
    }
    P.check_definitions(board, "test")  # the stored values are what the registry would store
    return board


BOARD = make_board()


def keys(text="", sort="", archived=False, board=None):
    return Q.search(BOARD if board is None else board, text, sort, archived)


class ErrorCase(unittest.TestCase):
    def fails(self, text, message, position, board=None, sort=""):
        with self.assertRaises(Q.QueryError) as caught:
            Q.search(BOARD if board is None else board, text, sort)
        self.assertEqual(str(caught.exception), message, text)
        self.assertEqual(caught.exception.position, position, text)
        return caught.exception


class Words(unittest.TestCase):
    def test_an_empty_search_lists_active_cards_in_display_order(self):
        for text in ("", "   ", None):
            self.assertEqual(keys(text), ["T-2", "T-1", "T-10", "T-3", "T-5", "T-6"])

    def test_words_must_all_match_inside_any_text(self):
        self.assertEqual(keys("login"), ["T-1"])
        self.assertEqual(keys("logi crash"), ["T-1"])
        self.assertEqual(keys("crash"), ["T-1"])
        self.assertEqual(keys("login export"), [])
        self.assertEqual(keys("t-1"), ["T-1", "T-10"])  # inside text, as the search box always matched

    def test_case_and_accents_are_ignored_both_ways(self):
        for text in ("cafe", "CAFÉ", "Café", "cafe\u0301", "ＣＡＦＥ"):
            self.assertEqual(keys(text), ["T-2"], text)
        board = make_board()
        board["cards"][0]["title"] = "Strasse"
        self.assertEqual(keys("straße", board=board), ["T-1"])

    def test_phrases(self):
        self.assertEqual(keys('"login page"'), ["T-1"])
        self.assertEqual(keys('"page login"'), [])
        self.assertEqual(keys('"open the login page"'), ["T-1"])  # any whitespace in the notes matches a space
        self.assertEqual(keys('"crashes people"'), [])  # a phrase doesn't run from one part of a card into another
        self.assertEqual(keys('"can\'t sign"'), ["T-1"])

    def test_escaped_quotes_in_phrases(self):
        board = make_board()
        board["cards"][0]["title"] = 'Say "hi" to C:\\temp'
        self.assertEqual(keys(r'"say \"hi\""', board=board), ["T-1"])
        self.assertEqual(keys(r'"c:\\temp"', board=board), ["T-1"])
        self.assertEqual(Q.split_sort(r'"a \" sort:x"'), (r'"a \" sort:x"', ""))

    def test_or_not_minus_and_parentheses(self):
        self.assertEqual(keys("login OR export"), ["T-1", "T-10"])
        self.assertEqual(keys("login OR export OR menu"), ["T-2", "T-1", "T-10"])
        self.assertEqual(keys("-login"), ["T-2", "T-10", "T-3", "T-5", "T-6"])
        self.assertEqual(keys("NOT login"), keys("-login"))
        self.assertEqual(keys("--login"), ["T-1"])
        self.assertEqual(keys("NOT -login"), ["T-1"])
        self.assertEqual(keys('-"login page" -menu'), ["T-10", "T-3", "T-5", "T-6"])
        self.assertEqual(keys("crash page OR export"), ["T-1", "T-10"])  # side by side binds tighter than OR
        self.assertEqual(keys("crash (page OR export)"), ["T-1"])
        self.assertEqual(keys("(crash OR menu) -(café OR nothing)"), ["T-1"])
        self.assertEqual(keys("login AND crash"), ["T-1"])
        self.assertEqual(keys("login AND export"), [])

    def test_lower_case_keywords_and_a_lone_minus_are_words(self):
        board = make_board()
        board["cards"][0]["title"] = "Either or neither, not and - all"
        self.assertEqual(keys("neither or menu", board=board), [])  # as OR it would find Café menu too
        self.assertEqual(keys("not neither", board=board), ["T-1"])
        self.assertEqual(keys("neither and either", board=board), ["T-1"])
        self.assertIn("T-1", keys("- all", board=board))

    def test_plain_words_cover_the_same_parts_as_the_search_box_plus_the_new_ones(self):
        board = make_board()
        board["groups"].append({"id": "zulu", "name": "Zuluteam", "prefix": "T", "color": "slate", "link": "", "details": []})
        board["labels"].append({"id": "kilo", "name": "Kilolabel", "color": "amber"})
        board["cards"] = [card(
            "T-20", "Alphatitle", "in-progress", 1, group="zulu", summary="Bravosummary", blocked="Charlieblocked",
            notes="Deltanotes", fields=[{"label": "Foxtrotlabel", "value": "Golfvalue"}],
            sections=[{"label": "Hotellabel", "body": "Indiabody"}], tasks=[{"id": "a", "text": "Juliettask", "done": False}],
            labels=["kilo"], properties={"owner": "Limaowner", "link": "https://mike.example/x", "hours": 7.5,
                                         "priority": "medium", "platform": ["android"], "stars": 3, "reviewed": True})]
        found = ("t-20", "alphatitle", "bravosummary", "charlieblocked", "deltanotes", "zuluteam", "foxtrotlabel",
                 "golfvalue", "hotellabel", "indiabody", "juliettask", "kilolabel", "limaowner", "mike.example",
                 '"7.5 h"', "medium", "android", "3/5", "reviewed", "priority")
        for word in found:
            self.assertEqual(keys(word, board=board), ["T-20"], word)
        for word in ("progress", "pass", "2026", "ship", "zulu-", "web"):  # column, checklist, stamps, ids
            self.assertEqual(keys(word, board=board), [], word)

    def test_words_with_a_colon_that_are_not_qualifiers(self):
        board = make_board()
        board["cards"][0]["notes"] = "Meet at 10:30. See http://example.org/a. Re: the menu"
        self.assertEqual(keys("10:30", board=board), ["T-1"])
        self.assertEqual(keys("http://example.org/a", board=board), ["T-1"])
        self.assertEqual(keys("re:", board=board), ["T-1"])

    def test_query_objects(self):
        query = Q.compile_query("login OR is:blocked", BOARD)
        self.assertEqual([c["key"] for c in BOARD["cards"] if query.matches(c)], ["T-1", "T-10"])
        self.assertIs(query.matches(BOARD["cards"][1]), False)
        self.assertFalse(query.wants_archived)
        self.assertEqual(query.sort, "")


class Qualifiers(ErrorCase):
    def test_column_by_id_name_prefix_case_and_accents(self):
        for text in ("column:ready", "col:Ready", "col:READY", "col:rea", "column:\"ready\"", "COL:ready"):
            self.assertEqual(keys(text), ["T-10", "T-3"], text)
        for text in ("col:in-progress", 'col:"in progress"', "col:in", "col:In-Pro"):
            self.assertEqual(keys(text), [], text)
        self.assertEqual(keys("col:review"), ["T-5"])
        board = make_board()
        board["columns"][0]["name"] = "Révisé"
        self.assertEqual(keys("col:revise", board=board), ["T-2", "T-1"])

    def test_column_errors(self):
        self.fails("col:r", '"r" matches more than one column: Ready, Review. Type more of the name.', 4)
        self.fails("bug col:nope", 'There\'s no column called "nope". Columns: Backlog, Ready, In progress, Review, Done.', 8)
        self.fails("col:", 'Type what to look for after "col:".', 0)
        self.fails('col:""', 'Type what to look for after "col:".', 0)

    def test_group(self):
        self.assertEqual(keys("group:web"), ["T-1", "T-5"])
        self.assertEqual(keys('group:"web site"'), ["T-1", "T-5"])
        self.assertEqual(keys("group:api"), ["T-2"])  # an exact id wins over the start of "Apps"
        self.assertEqual(keys("group:app"), ["T-6"])
        self.assertEqual(keys("-has:group"), ["T-10", "T-3"])
        self.assertEqual(keys("has:group"), ["T-2", "T-1", "T-5", "T-6"])
        self.fails("group:a", '"a" matches more than one group: API, Apps. Type more of the name.', 6)
        self.fails("group:x", 'There\'s no group called "x". Groups: Web site, API, Apps.', 6)
        board = make_board()
        board["groups"] = []
        self.fails("group:x", "This board has no groups.", 0, board=board)

    def test_label(self):
        self.assertEqual(keys("label:bug"), ["T-1"])
        self.assertEqual(keys("label:Feature"), ["T-2", "T-10"])
        self.assertEqual(keys("labels:fe"), ["T-2", "T-10"])
        self.assertEqual(keys("label:fix-later"), ["T-10"])
        self.assertEqual(keys('label:"fix later"'), ["T-10"])
        self.assertEqual(keys("label:feature label:fix"), ["T-10"])
        self.assertEqual(keys("has:labels"), ["T-2", "T-1", "T-10"])
        self.assertEqual(keys("-has:labels"), ["T-3", "T-5", "T-6"])
        self.fails("label:f", '"f" matches more than one label: Feature, Fix later. Type more of the name.', 6)
        self.fails("label:chore", 'There\'s no label called "chore". Labels: Bug, Feature, Fix later.', 6)
        board = make_board()
        board["labels"] = []
        self.fails("label:bug", "This board has no labels.", 0, board=board)

    def test_key_title_and_notes(self):
        self.assertEqual(keys("key:T-1"), ["T-1"])
        self.assertEqual(keys("key:t-10"), ["T-10"])
        self.assertEqual(keys("key:T-1 OR key:T-3"), ["T-1", "T-3"])
        self.assertEqual(keys("key:1"), ["T-1", "T-10"])
        self.assertEqual(keys("key:T-"), keys(""))
        self.assertEqual(keys("title:crash"), ["T-1"])  # T-4 says crash too, but it's archived
        self.assertEqual(keys("title:login"), ["T-1"])
        self.assertEqual(keys("title:accounts"), [])  # a field, not the title
        self.assertEqual(keys('title:"page crashes"'), ["T-1"])
        self.assertEqual(keys("notes:install"), ["T-3"])
        self.assertEqual(keys('notes:"login page"'), ["T-1"])
        self.assertEqual(keys("notes:menu"), [])

    def test_is(self):
        self.assertEqual(keys("is:blocked"), ["T-10"])
        self.assertEqual(keys("-is:blocked"), ["T-2", "T-1", "T-3", "T-5", "T-6"])
        self.assertEqual(keys("is:done"), ["T-6"])
        self.assertEqual(keys("is:over-limit"), ["T-10", "T-3"])  # Review holds one active card and one archived
        self.assertEqual(keys("is:overlimit"), keys("is:Over-Limit"))
        self.assertEqual(keys("is:active"), keys(""))
        self.assertEqual(keys("is:archived"), ["T-4"])
        self.fails("is:late", 'is: can be blocked, archived, active, over-limit or done, not "late".', 3)

    def test_has(self):
        self.assertEqual(keys("has:notes"), ["T-1", "T-3"])
        self.assertEqual(keys("has:summary"), ["T-1"])
        self.assertEqual(keys("has:tasks"), ["T-1", "T-10"])
        self.assertEqual(keys("has:checklist"), keys(""))
        self.assertEqual(keys("has:points"), ["T-2", "T-1", "T-10", "T-5"])
        self.assertEqual(keys("has:story-points"), ["T-1"])
        self.assertEqual(keys("has:Reviewed"), ["T-1", "T-6"])
        self.assertEqual(keys("-has:priority"), ["T-3", "T-6"])
        board = make_board()
        board["checklists"] = []
        self.assertEqual(keys("has:checklist", board=board), [])
        self.fails("has:x", 'There\'s nothing called "x" to look for with has:. Use notes, summary, labels, tasks, checklist, '
                   'group or a property: Points, Hours, Priority, Platform, Reviewed, Link, Stars, Owner, Story points.', 4)
        board["properties"] = []
        self.fails("has:x", 'There\'s nothing called "x" to look for with has:. Use notes, summary, labels, tasks, checklist '
                   'or group.', 4, board=board)

    def test_tasks(self):
        self.assertEqual(keys("tasks:open"), ["T-1"])
        self.assertEqual(keys("tasks:done"), ["T-10"])
        self.assertEqual(keys("-tasks:done"), ["T-2", "T-1", "T-3", "T-5", "T-6"])
        self.fails("tasks:half", 'tasks: can be done or open, not "half".', 6)

    def test_progress_counts_checklist_lines_and_tasks(self):
        # T-1: 1 of 2 lines and 1 of 2 tasks. T-10: 0 of 2 lines, 1 of 1 task. T-3: 2 of 2 lines.
        self.assertEqual(keys("progress:50"), ["T-1"])
        self.assertEqual(keys("progress:100"), ["T-3"])
        self.assertEqual(keys("progress:100%"), ["T-3"])
        self.assertEqual(keys("progress:0"), ["T-2", "T-5", "T-6"])
        self.assertEqual(keys("progress:>33"), ["T-1", "T-10", "T-3"])
        self.assertEqual(keys("progress:>=33.4"), ["T-1", "T-3"])
        self.assertEqual(keys("progress:<50"), ["T-2", "T-10", "T-5", "T-6"])
        self.assertEqual(keys("progress:<=50"), ["T-2", "T-1", "T-10", "T-5", "T-6"])
        self.assertEqual(keys("progress:33..50"), ["T-1", "T-10"])
        self.assertEqual(keys("progress:=50"), ["T-1"])
        self.assertEqual(keys("progress:.5..1"), [])

    def test_cards_without_checklists_or_tasks_have_no_progress(self):
        board = make_board()
        board["checklists"] = []
        for text in ("progress:>=0", "progress:<=100", "progress:0..100"):  # T-1 has half its tasks done, T-10 all
            self.assertEqual(keys(text, board=board), ["T-1", "T-10"], text)
        self.assertEqual(keys("progress:0", board=board), [])
        self.assertEqual(keys("-progress:>=0", board=board), ["T-2", "T-3", "T-5", "T-6"])

    def test_progress_errors(self):
        self.fails("progress:half", 'Progress is a percent, like progress:50 or progress:>=80, not "half".', 9)
        self.fails("progress:>150", "Progress is a percent from 0 to 100.", 10)
        self.fails("progress:80..20", "The range 80..20 runs backwards. Put the smaller number first.", 9)
        self.fails("progress:..20", "Write a range with a value at each end, like progress:2..5.", 9)
        self.fails("progress:>=", 'Type a value after "progress:>=".', 9)

    def test_qualifier_names_ignore_case(self):
        self.assertEqual(keys("IS:Blocked"), ["T-10"])
        self.assertEqual(keys("Title:LOGIN"), ["T-1"])


class Properties(ErrorCase):
    def test_numbers(self):
        self.assertEqual(keys("points:3"), ["T-1"])
        self.assertEqual(keys("points:=3"), ["T-1"])
        self.assertEqual(keys("points:>3"), ["T-2", "T-10", "T-5"])
        self.assertEqual(keys("points:>=5"), ["T-2", "T-10", "T-5"])
        self.assertEqual(keys("points:<5"), ["T-1"])
        self.assertEqual(keys("points:<=5"), ["T-2", "T-1"])
        self.assertEqual(keys("points:2..8"), ["T-2", "T-1", "T-10"])
        self.assertEqual(keys("points:5..5"), ["T-2"])
        self.assertEqual(keys("-points:>=5"), ["T-1", "T-3", "T-6"])  # no value is never >= 5
        self.assertEqual(keys("points:<100 -has:points"), [])

    def test_number_text_forms_go_through_the_registry(self):
        self.assertEqual(keys("hours:2.5"), ["T-1"])
        self.assertEqual(keys("hours:2.5h"), ["T-1"])
        self.assertEqual(keys("hours:<1h"), ["T-5"])
        self.assertEqual(keys("hours:0.4..0.6"), ["T-5"])
        self.assertEqual(keys("points:2.5"), [])  # read as typed, not rounded to the property's 0 places
        self.assertEqual(keys("points:>2.5"), ["T-2", "T-1", "T-10", "T-5"])
        self.assertEqual(keys("points:<=2.5"), [])
        self.assertEqual(keys("story-points:2"), ["T-1"])
        self.assertEqual(keys("storypoints:2"), ["T-1"])
        self.assertEqual(keys("Story-Points:2"), ["T-1"])
        self.fails("points:lots", "Points must be a number from 0 to 100.", 7)
        self.fails("points:1,200", "Points must be a number written without commas, like 1200 or 2.5.", 7)
        self.fails("points:>=500", "Points must be a number from 0 to 100.", 9)
        self.fails("points:8..2", "The range 8..2 runs backwards. Put the smaller value first.", 7)
        self.fails("points:3..", "Write a range with a value at each end, like points:2..5.", 7)
        self.fails("points:<", 'Type a value after "points:<".', 7)

    def test_ratings(self):
        self.assertEqual(keys("stars:4"), ["T-1"])
        self.assertEqual(keys("stars:4/5"), ["T-1"])
        self.assertEqual(keys("stars:>3"), ["T-1"])
        self.assertEqual(keys("stars:<3"), ["T-2", "T-5"])
        self.assertEqual(keys("stars:2..4"), ["T-2", "T-1", "T-5"])
        self.fails("stars:9", "Stars must be a whole number from 1 to 5.", 6)
        self.fails("stars:3/10", "Stars is rated out of 5, not 10.", 6)
        self.fails("stars:2.5", "Stars must be a whole number from 1 to 5.", 6)

    def test_choices(self):
        self.assertEqual(keys("priority:high"), ["T-1"])
        self.assertEqual(keys("priority:HIGH"), ["T-1"])
        self.assertEqual(keys("priority:highe"), ["T-5"])
        self.assertEqual(keys("priority:me"), ["T-10"])
        self.assertEqual(keys("priority:=low"), ["T-2"])
        self.assertEqual(keys("priority:low OR priority:medium"), ["T-2", "T-10"])
        self.assertEqual(keys("-priority:low"), ["T-1", "T-10", "T-3", "T-5", "T-6"])
        self.fails("priority:hig", '"hig" matches more than one choice of Priority: High, Highest. Type more of the name.', 9)
        self.fails("priority:hihg", 'Priority has no choice called "hihg". Choices: Low, Medium, High, Highest.', 9)
        self.fails("priority:>low", 'Priority has no choice called ">low". Choices: Low, Medium, High, Highest.', 9)
        self.fails("priority:low..high", 'Priority has no choice called "low..high". Choices: Low, Medium, High, Highest.', 9)

    def test_multi_means_has_that_choice(self):
        self.assertEqual(keys("platform:ios"), ["T-1", "T-10"])
        self.assertEqual(keys("platform:web"), ["T-1", "T-10"])
        self.assertEqual(keys("platform:and"), ["T-2", "T-10"])
        self.assertEqual(keys("platform:ios -platform:android"), ["T-1"])
        self.fails("platform:mac", 'Platform has no choice called "mac". Choices: iOS, Android, Web.', 9)

    def test_checkboxes(self):
        for word in ("yes", "true", "1", "y", "YES"):
            self.assertEqual(keys(f"reviewed:{word}"), ["T-1", "T-6"], word)
        for word in ("no", "false", "0", "n"):
            self.assertEqual(keys(f"reviewed:{word}"), ["T-2", "T-10", "T-3", "T-5"], word)
        self.fails("reviewed:maybe", 'Reviewed must be yes or no, not "maybe".', 9)
        self.fails("reviewed:>yes", 'Reviewed must be yes or no, not ">yes".', 9)

    def test_links_and_text_match_inside_the_value(self):
        self.assertEqual(keys("link:example.com"), ["T-1"])
        self.assertEqual(keys("link:EXAMPLE.com/issue"), ["T-1"])
        self.assertEqual(keys("link:other.org"), [])
        self.assertEqual(keys("owner:ann"), ["T-1"])
        self.assertEqual(keys('owner:"ann lee"'), ["T-1"])
        self.assertEqual(keys("owner:bo"), ["T-2"])
        self.assertEqual(keys("owner:>a"), [])  # no comparisons on text: it's looked for as written

    def test_unknown_properties(self):
        self.fails("bug pionts:3", 'There\'s no property called "pionts". Properties: Points, Hours, Priority, Platform, '
                   'Reviewed, Link, Stars, Owner, Story points.', 4)
        board = make_board()
        board["properties"] = []
        for card_ in board["cards"]:
            card_["properties"] = {}
        self.fails("points:3", 'There\'s no qualifier called "points". Qualifiers: column, group, label, key, title, notes, '
                   'is, has, tasks, progress, sort.', 0, board=board)

    def test_qualifiers_win_over_properties_with_the_same_name(self):
        board = make_board()
        board["properties"] = P.clean_definitions(board, board["properties"] + [{"name": "Notes", "type": "text"}])
        self.assertEqual(board["properties"][-1]["id"], "notes-2")
        board["cards"][1]["properties"]["notes-2"] = "from the property"
        self.assertEqual(keys("notes:install", board=board), ["T-3"])
        self.assertEqual(keys("notes-2:property", board=board), ["T-2"])
        self.assertEqual(keys("has:notes-2", board=board), ["T-2"])

    def test_every_type_lists_comparisons_the_language_handles(self):
        for kind in P.TYPES.values():
            self.assertLessEqual(kind.comparisons, Q._ALL_COMPARISONS)


class Errors(ErrorCase):
    def test_query_error_is_a_store_error_with_status_400(self):
        error = self.fails("(", 'This "(" is never closed.', 0)
        self.assertIsInstance(error, StoreError)
        self.assertEqual(error.status, 400)
        self.assertEqual(error.part, "query")

    def test_parentheses(self):
        self.fails("(login", 'This "(" is never closed.', 0)
        self.fails("a (b (c)", 'This "(" is never closed.', 2)
        self.fails("login)", 'This ")" has no "(" before it.', 5)
        self.fails(") login", 'This ")" has no "(" before it.', 0)
        self.fails("(a) b)", 'This ")" has no "(" before it.', 5)
        self.fails("a ()", "There's nothing between these parentheses.", 2)
        self.fails("-()", "There's nothing between these parentheses.", 1)

    def test_operators_need_terms(self):
        self.fails("OR login", "OR needs a search term on each side of it.", 0)
        self.fails("login OR", "OR needs a search term on each side of it.", 6)
        self.fails("a OR OR b", "OR needs a search term on each side of it.", 2)
        self.fails("(a OR) b", "OR needs a search term on each side of it.", 3)
        self.fails("AND a", "AND needs a search term on each side of it.", 0)
        self.fails("a AND", "AND needs a search term on each side of it.", 2)
        self.fails("a AND OR b", "AND needs a search term on each side of it.", 2)
        self.fails("login NOT", "NOT needs a search term after it.", 6)
        self.fails("(NOT) a", "NOT needs a search term after it.", 1)
        self.fails("a -OR b", '"-" needs a search term right after it.', 2)

    def test_quotes(self):
        self.fails('login "page', 'This quote is never closed. Add a " where the phrase ends.', 6)
        self.fails('title:"page', 'This quote is never closed. Add a " where the phrase ends.', 6)
        self.fails(r'"page\"', 'This quote is never closed. Add a " where the phrase ends.', 0)

    def test_describe_error_points_at_the_spot(self):
        text = "bug pionts:3"
        with self.assertRaises(Q.QueryError) as caught:
            Q.search(BOARD, text)
        shown = Q.describe_error(text, caught.exception).split("\n")
        self.assertEqual(shown[0], str(caught.exception))
        self.assertEqual(shown[1], "  bug pionts:3")
        self.assertEqual(shown[2], "      ^")

    def test_describe_error_on_long_and_odd_text(self):
        text = "a\tb " + "x " * 100 + "col:nope " + "y " * 100
        with self.assertRaises(Q.QueryError) as caught:
            Q.search(BOARD, text)
        message, line, pointer = Q.describe_error(text, caught.exception).split("\n")
        self.assertTrue(line.startswith("  ...") and line.endswith("..."))
        self.assertLessEqual(len(line), 70)
        self.assertEqual(line[pointer.index("^"):pointer.index("^") + 4], "nope")
        self.assertNotIn("\t", Q.describe_error("a\tcol:", Q.QueryError("x", 2)))
        self.assertEqual(Q.describe_error("", Q.QueryError("Nothing", 0)), "Nothing")
        self.assertEqual(Q.describe_error("abc", Q.QueryError("End", 99)).split("\n")[2], "     ^")
        self.assertEqual(Q.describe_error("abc", ValueError("Plain")), "Plain")


class Hostile(ErrorCase):
    def test_length_is_capped_with_a_plain_message(self):
        self.assertEqual(keys("a " * 249 + "zz"), [])
        for text in ("a" * 501, "x " * 5_000_000):
            with self.assertRaises(Q.QueryError) as caught:
                Q.search(BOARD, text)
            self.assertEqual(str(caught.exception),
                             f"A search can be at most 500 characters long (this one has {len(text)}).")
            self.assertEqual(caught.exception.position, 500)
        for call in (lambda: Q.split_sort("a" * 501), lambda: Q.compile_query("a" * 501, BOARD)):
            self.assertRaises(Q.QueryError, call)
        with self.assertRaises(Q.QueryError) as caught:
            Q.compile_sort("title," * 100, BOARD)
        self.assertEqual(caught.exception.part, "sort")

    def test_text_must_be_text(self):
        for value in (3, ["login"], b"login"):
            self.fails(value, "A search must be text.", 0)

    def test_deep_parentheses_are_refused(self):
        self.assertEqual(keys("(" * 20 + "login" + ")" * 20), ["T-1"])
        self.fails("(" * 21 + "login" + ")" * 21, "Parentheses can go at most 20 deep.", 20)
        self.fails("(" * 250, "Parentheses can go at most 20 deep.", 20)

    def test_long_chains_stay_flat(self):
        self.assertEqual(keys("-" * 400 + "login"), ["T-1"])
        self.assertEqual(keys("NOT " * 120 + "login"), ["T-1"])
        self.assertEqual(keys(" OR ".join(["no"] * 60) + " OR login"), ["T-1"])
        self.assertEqual(keys(" ".join(["o"] * 249)), keys("o"))

    def test_odd_unicode(self):
        board = make_board()
        board["cards"][0]["title"] = "Rocket \U0001F680 \u0130stanbul \ufb01le \u05e9\u05dc\u05d5\u05dd"
        for text in ("\U0001F680", "istanbul", "file", "\u05e9\u05dc\u05d5\u05dd", "rocket\u00a0\U0001F680"):
            self.assertEqual(keys(text, board=board), ["T-1"], ascii(text))
        for text in ("\ud800", "\u202e", "lo\u200bgin", "\u0301\u0301", "\U000e0001", "\ufeff", "\uffff"):
            keys(text, board=board)  # no crash; what matches is whatever folding leaves
        self.assertEqual(keys("login\x00crash"), ["T-1"])  # control characters separate words
        self.assertEqual(keys("\u0301"), keys(""))  # nothing left after folding matches every card
        self.assertEqual(keys("col:\u0301ready"), ["T-10", "T-3"])

    def test_fold(self):
        self.assertEqual(Q.fold("Café"), "cafe")
        self.assertEqual(Q.fold("CAFE\u0301"), "cafe")
        self.assertEqual(Q.fold("Straße"), "strasse")
        self.assertEqual(Q.fold("\u210cello"), "hello")
        self.assertEqual(Q.fold("Ｗｅｂ"), "web")
        self.assertEqual(Q.fold("Ångström"), "angstrom")
        self.assertEqual(Q.fold("plain ascii"), "plain ascii")


class Sorting(ErrorCase):
    def test_default_and_rank_mean_manual_order(self):
        board = make_board()
        board["cards"][1]["rank"] = 2  # T-2 ties with T-1: the key decides, T-1 first
        board["cards"].append(card("T-11", "Tie", "backlog", 2))
        self.assertEqual(keys(board=board)[:3], ["T-1", "T-2", "T-11"])
        self.assertEqual(keys(sort="rank", board=board)[:3], ["T-1", "T-2", "T-11"])
        self.assertEqual(keys(sort="-rank", board=board)[:3], ["T-11", "T-2", "T-1"])
        self.assertEqual(Q.compile_sort("", BOARD).key(BOARD["cards"][0]), Q.compile_sort("rank", BOARD).key(BOARD["cards"][0])[1:])

    def test_sort_inside_columns_by_title_and_key(self):
        self.assertEqual(keys(sort="title"), ["T-2", "T-1", "T-10", "T-3", "T-5", "T-6"])
        self.assertEqual(keys(sort="-title"), ["T-1", "T-2", "T-3", "T-10", "T-5", "T-6"])
        self.assertEqual(keys(sort="key"), ["T-1", "T-2", "T-3", "T-10", "T-5", "T-6"])
        self.assertEqual(keys(sort="-key"), ["T-2", "T-1", "T-10", "T-3", "T-5", "T-6"])

    def test_empty_values_sort_last_both_ways(self):
        board = make_board()
        for c in board["cards"]:
            c["column"] = "backlog"
        self.assertEqual(keys(sort="points", board=board), ["T-1", "T-2", "T-10", "T-5", "T-6", "T-3"])
        self.assertEqual(keys(sort="-points", board=board), ["T-5", "T-10", "T-2", "T-1", "T-6", "T-3"])
        self.assertEqual(keys(sort="priority", board=board), ["T-2", "T-10", "T-1", "T-5", "T-6", "T-3"])
        self.assertEqual(keys(sort="-priority", board=board), ["T-5", "T-1", "T-10", "T-2", "T-6", "T-3"])
        self.assertEqual(keys(sort="reviewed", board=board), ["T-6", "T-1", "T-2", "T-10", "T-3", "T-5"])
        self.assertEqual(keys(sort="-reviewed", board=board), ["T-6", "T-1", "T-2", "T-10", "T-3", "T-5"])
        self.assertEqual(keys(sort="owner", board=board), ["T-1", "T-2", "T-6", "T-10", "T-3", "T-5"])
        self.assertEqual(keys(sort="-link", board=board), ["T-1", "T-2", "T-6", "T-10", "T-3", "T-5"])
        self.assertEqual(keys(sort="platform", board=board), ["T-10", "T-1", "T-2", "T-6", "T-3", "T-5"])
        self.assertEqual(keys(sort="group", board=board), ["T-1", "T-5", "T-2", "T-6", "T-10", "T-3"])
        self.assertEqual(keys(sort="-group", board=board), ["T-6", "T-2", "T-1", "T-5", "T-10", "T-3"])

    def test_ties_go_to_the_next_name_then_manual_order(self):
        board = make_board()
        for c in board["cards"]:
            c["column"] = "backlog"
        self.assertEqual(keys(sort="stars,-points", board=board), ["T-5", "T-2", "T-1", "T-10", "T-6", "T-3"])
        self.assertEqual(keys(sort="-stars, points", board=board), ["T-1", "T-2", "T-5", "T-10", "T-6", "T-3"])
        self.assertEqual(keys(sort="stars", board=board), ["T-2", "T-5", "T-1", "T-6", "T-10", "T-3"])

    def test_stamps_and_progress(self):
        board = make_board()
        for c in board["cards"]:
            c["column"] = "backlog"
        self.assertEqual(keys(sort="created", board=board), ["T-6", "T-2", "T-1", "T-3", "T-5", "T-10"])
        self.assertEqual(keys(sort="-updated", board=board)[:3], ["T-3", "T-1", "T-2"])
        self.assertEqual(keys(sort="-progress", board=board), ["T-3", "T-1", "T-10", "T-2", "T-6", "T-5"])
        del board["cards"][0]["created"]
        self.assertEqual(keys(sort="-created", board=board)[-1], "T-1")
        board["checklists"] = []
        self.assertEqual(keys(sort="progress", board=board), ["T-1", "T-10", "T-2", "T-6", "T-3", "T-5"])

    def test_sort_errors(self):
        for text, message, position in (
                ("title,pionts", 'There\'s no property called "pionts" to sort by. Sort by title, key, created, updated, '
                                 'rank, group, progress or a property: Points, Hours, Priority, Platform, Reviewed, Link, '
                                 'Stars, Owner, Story points.', 6),
                ("title, -", 'Type what to sort by after "-", like -title.', 7),
                (" - pionts", 'There\'s no property called "pionts" to sort by. Sort by title, key, created, updated, '
                              'rank, group, progress or a property: Points, Hours, Priority, Platform, Reviewed, Link, '
                              'Stars, Owner, Story points.', 3)):
            with self.assertRaises(Q.QueryError) as caught:
                Q.compile_sort(text, BOARD)
            self.assertEqual((str(caught.exception), caught.exception.position, caught.exception.part),
                             (message, position, "sort"), text)
        board = make_board()
        board["properties"] = []
        with self.assertRaises(Q.QueryError) as caught:
            Q.search(board, "", "due")
        self.assertEqual(str(caught.exception), 'There\'s nothing called "due" to sort by. Sort by title, key, created, '
                                                 'updated, rank, group or progress.')
        self.assertEqual(Q.compile_sort("title,,", BOARD).key(BOARD["cards"][0])[0], (0, "login page crashes"))

    def test_sort_in_the_search_text(self):
        self.assertEqual(Q.split_sort("bug sort:-points label:ui"), ("bug label:ui", "-points"))
        self.assertEqual(Q.split_sort("sort:title bug sort:-points,key"), ("bug", "title,-points,key"))
        self.assertEqual(Q.split_sort("  bug  "), ("bug", ""))
        self.assertEqual(Q.split_sort('bug "sort:title"'), ('bug "sort:title"', ""))
        self.assertEqual(Q.split_sort('sort:"story points"'), ("", "story points"))
        self.assertEqual(Q.split_sort("Sort:title"), ("", "title"))
        self.assertEqual(keys("sort:-points"), ["T-2", "T-1", "T-10", "T-3", "T-5", "T-6"])
        self.assertEqual(keys("is:active sort:title"), keys(sort="title"))
        self.assertEqual(Q.compile_query("bug sort:-points", BOARD).sort, "-points")
        board = make_board()
        for c in board["cards"]:
            c["column"] = "backlog"
        # the search's own sort comes first; the separate sort breaks its ties
        self.assertEqual(keys("sort:stars", "-points", board=board), ["T-5", "T-2", "T-1", "T-10", "T-6", "T-3"])
        self.assertEqual(keys('sort:"story points"', board=board)[0], "T-1")
        self.assertEqual(keys("sort:story-points", board=board)[0], "T-1")

    def test_sort_errors_in_the_search_text_point_into_it(self):
        error = self.fails("bug sort:pionts", 'There\'s no property called "pionts" to sort by. Sort by title, key, created, '
                           'updated, rank, group, progress or a property: Points, Hours, Priority, Platform, Reviewed, '
                           'Link, Stars, Owner, Story points.', 9)
        self.assertEqual(error.part, "query")
        self.assertEqual(self.fails("a sort:title,-", 'Type what to sort by after "-", like -title.', 13).part, "query")
        self.fails("bug sort:", "Type what to sort by after sort:, like sort:title or sort:-points.", 4)
        self.fails("-sort:title", "Sorting can't be left out with - or NOT. To sort the other way, put the minus inside: "
                   "sort:-title.", 1)
        self.fails("NOT sort:title", "Sorting can't be left out with - or NOT. To sort the other way, put the minus inside: "
                   "sort:-title.", 4)
        error = self.fails("bug", 'There\'s nothing called "x" to sort by. Sort by title, key, created, updated, rank, '
                           'group or progress.', 0, board={**make_board(), "properties": []}, sort="x")
        self.assertEqual(error.part, "sort")


class Archived(unittest.TestCase):
    def test_archived_cards_only_when_asked(self):
        self.assertNotIn("T-4", keys("crash"))
        self.assertEqual(keys("crash", archived=True), ["T-1", "T-4"])
        self.assertEqual(keys("", archived=True), ["T-2", "T-1", "T-10", "T-3", "T-4", "T-5", "T-6"])
        self.assertEqual(keys("is:archived", archived=True), ["T-4"])
        self.assertEqual(keys("is:active", archived=True), keys(""))
        self.assertEqual(keys("is:archived OR login"), ["T-1", "T-4"])
        self.assertEqual(keys("-is:active"), ["T-4"])
        self.assertEqual(keys("-is:archived"), keys(""))
        self.assertEqual(keys("is:archived points:1"), ["T-4"])
        self.assertEqual(keys("is:archived", sort="-points"), ["T-4"])

    def test_wants_archived_follows_what_the_search_asks(self):
        for text, wanted in (("is:archived", True), ("-is:active", True), ("NOT (is:active)", True),
                             ("login OR is:archived", True), ("-is:archived", False), ("NOT (is:archived OR x)", False),
                             ("--is:archived", True), ("is:active", False), ("login", False), ('"is:archived"', False)):
            self.assertIs(Q.compile_query(text, BOARD).wants_archived, wanted, text)


class OldBoards(ErrorCase):
    def old_board(self):
        """A version 2 board as it was: no labels, properties, views, settings or tasks anywhere."""
        board = {"id": "b-old", "name": "Old", "prefix": "T", "groupLabel": "Group", "rev": 3, "created": STAMP,
                 "updated": STAMP, "columns": [{"id": "todo", "name": "To do", "limit": None},
                                               {"id": "done", "name": "Done", "limit": 1}],
                 "groups": [], "checklists": [], "template": [], "counters": {"T": 3},
                 "cards": [{"key": "T-1", "title": "Paint the fence", "column": "todo", "rank": 1, "group": None,
                            "summary": "", "blocked": "", "notes": "Two coats", "fields": [], "sections": [], "checks": [],
                            "archived": None, "created": STAMP, "updated": STAMP},
                           {"key": "T-2", "title": "Buy paint", "column": "done", "rank": 1},
                           {"key": "T-3", "title": "Old note", "column": "done", "rank": 2, "archived": STAMP}]}
        return board

    def test_searches_work_on_older_dicts(self):
        board = self.old_board()
        self.assertEqual(keys("paint", board=board), ["T-1", "T-2"])
        self.assertEqual(keys("coats", board=board), ["T-1"])
        self.assertEqual(keys("has:labels OR has:tasks OR tasks:open OR tasks:done", board=board), [])
        self.assertEqual(keys("progress:>=0", board=board), [])
        self.assertEqual(keys("has:checklist", board=board), [])
        self.assertEqual(keys("is:over-limit", board=board), [])
        self.assertEqual(keys("is:done", board=board), ["T-2"])
        self.assertEqual(keys("is:archived", board=board), ["T-3"])
        self.assertEqual(keys(sort="-title", board=board), ["T-1", "T-2"])
        self.assertEqual(keys(sort="progress,created", board=board), ["T-1", "T-2"])
        self.fails("label:x", "This board has no labels.", 0, board=board)
        self.fails("points:3", 'There\'s no qualifier called "points". Qualifiers: column, group, label, key, title, notes, '
                   'is, has, tasks, progress, sort.', 0, board=board)
        self.assertEqual(keys("", board={"columns": [{"id": "a", "name": "A"}]}), [])

    def test_a_card_in_a_column_that_is_not_there_comes_last(self):
        board = self.old_board()
        board["cards"][0]["column"] = "gone"
        self.assertEqual(keys(board=board), ["T-2", "T-1"])


class Speed(unittest.TestCase):
    def test_twenty_thousand_cards(self):
        board = make_board()
        words = ["login", "export", "café", "report", "sync", "crash", "menu", "search", "upload", "billing"]
        columns = [c["id"] for c in board["columns"]]
        groups = [None, "web", "api", "apps"]
        priorities = ["low", "medium", "high", "highest", None]
        platforms = [["ios"], ["android", "web"], ["ios", "web"], []]
        cards = []
        for i in range(1, 20001):
            values = {"points": i % 21, "priority": priorities[i % 5], "platform": platforms[i % 4] or None,
                      "stars": i % 5 + 1, "owner": f"Person {i % 37}", "reviewed": True if i % 3 == 0 else None,
                      "link": f"https://example.com/{i}"}
            cards.append(card(
                f"T-{i}", f"{words[i % 10]} {words[i * 7 % 10]} item {i}", columns[i % 5], i, group=groups[i % 4],
                summary="A short summary of what the card is about.", notes="Notes about the work. " * 6,
                blocked="Waiting on someone" if i % 13 == 0 else "", fields=[{"label": "Team", "value": "Core"}],
                checks=["tests"] if i % 2 else [], labels=[["bug"], ["feature"], ["bug", "fix-later"], []][i % 4],
                tasks=[{"id": "a", "text": "First step", "done": i % 2 == 0}],
                archived="2026-02-01T00:00:00Z" if i % 50 == 0 else None,
                properties={k: v for k, v in values.items() if v is not None}))
        board["cards"] = cards
        text = ('(login OR export OR "item 1") -is:blocked points:>=3 priority:high label:bug NOT notes:"never there" '
                'platform:web OR (col:review stars:2..4 has:owner progress:>=30) OR title:cafe')
        start = time.perf_counter()
        found = Q.search(board, text, "-points,title")
        took = time.perf_counter() - start
        start = time.perf_counter()
        plain = Q.search(board, "item -crash report", "title")
        took_plain = time.perf_counter() - start
        if os.environ.get("LANEBOARD_DEBUG"):
            print(f"\n20,000 cards: complex search {took * 1000:.0f} ms ({len(found)} found), "
                  f"plain words {took_plain * 1000:.0f} ms ({len(plain)} found)")
        self.assertTrue(found)
        self.assertTrue(plain)
        self.assertLess(took, 3)
        self.assertLess(took_plain, 3)
        query = Q.compile_query(text, board)
        expected = [c["key"] for c in cards if not c["archived"] and query.matches(c)]
        self.assertEqual(sorted(found), sorted(expected))


if __name__ == "__main__":
    unittest.main()
