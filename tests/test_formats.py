"""Import and export formats: the registry, detection, every format's mapping, lossless Markdown, a safe
web page, and refusing hostile files with plain messages."""
import copy
import json
import os
import tempfile
import unittest
from html.parser import HTMLParser

from laneboard import formats, samples
from laneboard.formats import common, github, htmlpage, markdown, native, plaintext, trello
from laneboard.properties import clean_definitions
from laneboard.schema import MAX_COLUMNS, MAX_NAME, MAX_TITLE, SCHEMA_VERSION, StoreError
from laneboard.store import Store, encode_data

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def fixture_json(name):
    return json.loads(fixture(name).decode("utf-8"))


def to_bytes(value):
    return json.dumps(value).encode("utf-8")


class TempStore:
    """A store in a temporary folder, so no test ever touches a real boards file."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        return Store(os.path.join(self.tmp.name, "boards.json"), starter=False)

    def __exit__(self, *exc):
        self.tmp.cleanup()


def sample_board(name="software-release"):
    with TempStore() as store:
        return store.import_board(samples.load(name))


def error(test, call, *args):
    with test.assertRaises(StoreError) as caught:
        call(*args)
    return str(caught.exception)


# Free text that looks like the layout: headings, cards, parts, fences closed and not, and backslashes.
TRICKY = "\n".join([
    "Line one",
    "",
    "## Not a column",
    "### REL-99 Not a card",
    "#### Tasks",
    "# Not a board",
    "#",
    "   ## three spaces in",
    "    ## four spaces in",
    "##### five hashes",
    "#hashtag",
    "```python",
    'print("# fine")',
    "## inside a closed fence",
    "```",
    "````",
    "an unclosed four backtick fence",
    "## after it",
    "\\# one backslash",
    "\\\\## two backslashes",
    "\\ just a backslash \\",
    "- [x] not a task",
    "> not a summary",
    "- Group: not a group line",
    "~~~",
    "## in tildes",
    "~~~",
    "trailing spaces kept  ",
])


def rich_board():
    """A version 3 board with every part Markdown writes, and text chosen to look like the layout."""
    props = clean_definitions({"properties": [], "cards": []}, [
        {"name": "Size: points", "type": "number", "decimals": 1, "unit": "pt"},
        {"name": "Priority", "type": "choice", "choices": ["Low", "High"]},
        {"name": "Platforms", "type": "multi", "choices": ["iOS", "Web", "Desktop"]},
        {"name": "Reviewed", "type": "checkbox"},
        {"name": "Spec", "type": "url"},
        {"name": "Confidence", "type": "rating", "max": 5},
        {"name": "Owner", "type": "text"},
    ])

    def card(key, title, column, rank=1, **extra):
        made = {"key": key, "title": title, "column": column, "rank": rank, "group": None, "summary": "", "blocked": "",
                "fields": [], "sections": [], "notes": "", "checks": [], "archived": None, "labels": [],
                "properties": {}, "tasks": [], "created": "2026-01-02T03:04:05Z", "updated": "2026-01-02T03:04:05Z"}
        made.update(extra)
        return made

    return {
        "id": "b-rich", "name": "Release #3: the big one", "description": "# Not a heading, a description with a \\ in it",
        "prefix": "REL", "groupLabel": "Component", "rev": 7, "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-03T00:00:00Z",
        "columns": [{"id": "to-do", "name": "To do", "limit": None}, {"id": "doing", "name": "Doing", "limit": 2},
                    {"id": "archived", "name": "Archived", "limit": None},
                    {"id": "settings", "name": "Board settings", "limit": None},
                    {"id": "odd", "name": "Column: odd", "limit": 3}],
        "groups": [{"id": "web", "name": "Web: app", "prefix": "WEB", "color": "teal", "link": "https://example.com/web",
                    "details": [{"label": "Owner", "value": "Front end"}]}],
        "labels": [{"id": "bug", "name": "Bug", "color": "rose"}, {"id": "front", "name": "Front end, web", "color": "teal"},
                   {"id": "path", "name": "C:\\path\\", "color": "amber"}],
        "properties": props,
        "views": [{"id": "open-bugs", "name": "Open bugs", "query": "label:Bug", "sort": ""}],
        "settings": {"cardFace": ["group", "labels", "size-points"], "limitPolicy": "warn", "defaultView": "open-bugs"},
        "checklists": [{"id": "merge", "name": "Before merge", "items": [
            {"id": "code", "text": "Code", "heading": True}, {"id": "tests", "text": "Tests pass", "heading": False},
            {"id": "tests-2", "text": "Tests pass", "heading": False}, {"id": "docs", "text": "Docs", "heading": False}]}],
        "template": ["Problem", "Done when"],
        "counters": {"REL": 9, "WEB": 3},
        "cards": [
            card("REL-1", "Ship the ### hashes and `code` card", "to-do", rank=2, group="web", labels=["bug", "front"],
                 blocked="Waiting on: design, #12", summary="A summary with > and # in it",
                 properties={"size-points": 2.5, "priority": "high", "platforms": ["ios", "web"], "reviewed": True,
                             "spec": "https://example.com/spec?a=1,2", "confidence": 4, "owner": "Ann, Bo: lead"},
                 fields=[{"label": "Size: est", "value": "1 day\n\n  indented two\n### not a heading\n- [x] not a task\n```"},
                         {"label": "Link", "value": "https://example.com/x"}],
                 tasks=[{"id": "a", "text": "Write [x] tests", "done": True}, {"id": "b", "text": "- [ ] looks nested", "done": False},
                        {"id": "c", "text": "#### Notes", "done": False}],
                 checks=["tests-2", "docs"],
                 sections=[{"label": "Notes", "body": "A section called Notes."},
                           {"label": "Section: inner", "body": "x"},
                           {"label": "Checklist: fake", "body": "y"},
                           {"label": "Problem", "body": TRICKY},
                           {"label": "Done when", "body": "\nStarts with a blank line\n  and an indent"},
                           {"label": "Empty", "body": ""}],
                 notes=TRICKY),
            card("REL-6", "First in To do", "to-do", rank=1, properties={"size-points": 0.5}),
            card("REL-2", "Doing this", "doing"),
            card("REL-3", "In the column called Archived", "archived", notes="```\nnever closed"),
            card("REL-4", "In the column called Board settings", "settings", checks=["tests"]),
            card("REL-7", "In the odd column", "odd", group="web"),
            card("WEB-1", "Archived earlier", "doing", group="web", labels=["path"], archived="2026-03-01T10:00:00Z",
                 checks=["tests"], tasks=[{"id": "a", "text": "Old", "done": True}]),
            card("REL-5", "Archived later", "to-do", archived="2026-04-01T10:00:00Z", properties={"confidence": 1}),
        ],
    }


def expected_import(board):
    """The import shape that reading a Markdown export of `board` must give back: every part of the
    board's structure as stored, and each card's content, with columns, groups and labels by name."""
    columns = {c["id"]: c["name"] for c in board["columns"]}
    groups = {g["id"]: g["name"] for g in board.get("groups") or []}
    labels = {lab["id"]: lab["name"] for lab in board.get("labels") or []}
    out = {"name": board["name"], "description": board.get("description", ""), "properties": board.get("properties") or []}
    out.update({key: board[key] for key in markdown.STRUCTURE if key in board})
    cards = {}
    for c in board["cards"]:
        made = {"title": c["title"], "key": c["key"], "column": columns[c["column"]]}
        if c.get("archived"):
            made["archived"] = c["archived"]
        if c.get("group"):
            made["group"] = groups[c["group"]]
        if c.get("labels"):
            made["labels"] = [labels[i] for i in c["labels"]]
        for part in ("blocked", "summary", "fields", "sections", "notes"):
            if c.get(part):
                made[part] = c[part]
        if c.get("tasks"):
            made["tasks"] = [{"text": t["text"], "done": t["done"]} for t in c["tasks"]]
        values = {k: v for k, v in (c.get("properties") or {}).items() if v is not None}
        if values:
            made["properties"] = values
        tickable = {i["id"] for cl in board.get("checklists") or [] for i in cl["items"] if not i.get("heading")}
        ticks = sorted(set(c.get("checks") or []) & tickable)
        if ticks:
            made["checks"] = ticks
        cards[c["key"]] = made
    return out, cards


def split_import(board):
    """An imported board as (board without cards, {key: card}), with ticks sorted (their order isn't kept)."""
    board = copy.deepcopy(board)
    cards = {}
    for c in board.pop("cards"):
        if "checks" in c:
            c["checks"] = sorted(c["checks"])
        cards[c["key"]] = c
    return board, cards


class Registry(unittest.TestCase):
    def test_the_formats_and_which_way_they_go(self):
        self.assertEqual(list(formats.FORMATS), ["laneboard", "markdown", "html", "text", "trello", "github"])
        ways = {name: (f.info()["export"], f.info()["import"]) for name, f in formats.FORMATS.items()}
        self.assertEqual(ways, {"laneboard": (True, True), "markdown": (True, True), "html": (True, False),
                                "text": (True, False), "trello": (False, True), "github": (False, True)})
        self.assertEqual(formats.FORMATS["html"].info(), {"name": "html", "title": "Web page", "extension": ".html",
                                                          "mime": "text/html; charset=utf-8", "export": True, "import": False})
        for f in formats.FORMATS.values():
            self.assertTrue(f.extension.startswith("."))
            self.assertIn("charset=utf-8", f.mime)
            self.assertIsInstance(f, formats.Format)

    def test_get_format(self):
        self.assertIs(formats.get_format(" Markdown "), formats.FORMATS["markdown"])
        self.assertIs(formats.get_format("trello", "import"), formats.FORMATS["trello"])
        self.assertEqual(error(self, formats.get_format, "csv"),
                         'There\'s no format called "csv". The formats are laneboard, markdown, html, text, trello, github.')
        self.assertIn('no format called "that"', error(self, formats.get_format, "x" * 41))
        self.assertIn('no format called "that"', error(self, formats.get_format, None))
        self.assertEqual(error(self, formats.get_format, "html", "import"),
                         "Web page files can be exported, not imported. Formats to import from: laneboard, markdown, "
                         "trello, github.")
        self.assertEqual(error(self, formats.get_format, "github", "export"),
                         "GitHub issue list files can be imported, not exported. Formats to export to: laneboard, "
                         "markdown, html, text.")

    def test_import_file(self):
        name, result = formats.import_file(fixture("trello-board.json"), filename="board.json")
        self.assertEqual(name, "trello")
        self.assertIsInstance(result, formats.ImportResult)
        self.assertEqual(result._fields, ("boards", "warnings"))
        name, result = formats.import_file(b"- [ ] One", "markdown")
        self.assertEqual((name, result.boards[0]["cards"][0]["title"]), ("markdown", "One"))
        self.assertIn("can be exported, not imported", error(self, formats.import_file, b"x", "text"))


class Detection(unittest.TestCase):
    def detect(self, raw, filename=None):
        return formats.detect_format(raw, filename)

    def test_each_format_by_its_content(self):
        board = rich_board()
        with TempStore() as store:
            store.import_board(samples.load("garden"))
            with open(store.path, "rb") as f:
                boards_file = f.read()
        self.assertEqual(self.detect(boards_file), "laneboard")
        self.assertEqual(self.detect(native.export(board), "board.txt"), "laneboard")
        self.assertEqual(self.detect(to_bytes({"name": "Hand written"})), "laneboard")
        self.assertEqual(self.detect(markdown.export(board)), "markdown")
        self.assertEqual(self.detect(b"## To do\n\n- [ ] One\n"), "markdown")
        self.assertEqual(self.detect(b"# Notes\n\nSome words.\n"), "markdown")
        self.assertEqual(self.detect(fixture("trello-board.json"), "export.json"), "trello")
        self.assertEqual(self.detect(fixture("github-issues.json"), "issues.json"), "github")
        self.assertEqual(self.detect(b"[]"), "github")

    def test_the_extension_is_a_hint(self):
        prose = b"Some words, and nothing else.\n"
        self.assertIn("can't tell what kind of file", error(self, self.detect, prose))
        self.assertEqual(self.detect(prose, "notes.md"), "markdown")
        self.assertEqual(self.detect(prose, "NOTES.MARKDOWN"), "markdown")
        md = formats.FORMATS["markdown"]
        plain = b"# Notes\n\nSome words.\n"
        self.assertGreater(md.detect(plain, "a.md"), md.detect(plain))
        self.assertEqual(md.detect(to_bytes({"name": "x"}), "a.md"), 0)  # JSON is never Markdown
        self.assertEqual(formats.FORMATS["trello"].detect(plain, "a.json"), 0)  # a hint alone is not enough
        for f in formats.FORMATS.values():
            for raw in (plain, fixture("trello-board.json"), b"\xff\xfe", b""):
                self.assertTrue(0 <= f.detect(raw, "x.json") <= 1)
        self.assertEqual(formats.FORMATS["html"].detect(b"<!doctype html>", "a.html"), 0)  # export only
        self.assertEqual(formats.FORMATS["text"].detect(b"Board", "a.txt"), 0)

    def test_plain_messages_when_it_cant_tell(self):
        self.assertEqual(error(self, self.detect, to_bytes({"foo": 1})),
                         "Laneboard can't tell what that JSON file is. It reads Laneboard boards, Trello board exports "
                         "and GitHub issue lists. Choose the format to see what doesn't fit.")
        self.assertIn("can't tell what that JSON file is", error(self, self.detect, b"[1, 2]"))
        self.assertIn("can't tell what kind of file", error(self, self.detect, b'"text"'))
        self.assertRegex(error(self, self.detect, b'{"name": "x",'), r"^That file isn't valid JSON: .* at line 1, column \d+\.$")
        self.assertEqual(error(self, self.detect, b""), "That file is empty.")
        self.assertEqual(error(self, self.detect, b"  \n\t"), "That file is empty.")
        self.assertIn("isn't UTF-8 text", error(self, self.detect, b"\x89PNG\r\n\x1a\n\x00\x00"))
        self.assertEqual(error(self, self.detect, b"GIF89a\x00\x01"), "That file isn't a text file, so it can't be imported.")


class Encodings(unittest.TestCase):
    def files(self):
        return [("laneboard", native.export(rich_board())), ("markdown", markdown.export(rich_board())),
                ("markdown", b"# Week\n\n- [ ] Caf\xc3\xa9 \xe2\x9c\x93\n"),
                ("trello", fixture("trello-board.json")), ("github", fixture("github-issues.json"))]

    def test_utf8_with_a_bom_and_utf16_with_one_read_the_same(self):
        for name, raw in self.files():
            fmt, text = formats.FORMATS[name], raw.decode("utf-8")
            plain = fmt.import_(raw)
            for encoded in (b"\xef\xbb\xbf" + raw, text.encode("utf-16"), b"\xff\xfe" + text.encode("utf-16-le"),
                            b"\xfe\xff" + text.encode("utf-16-be")):
                with self.subTest(name=name, start=encoded[:3]):
                    self.assertEqual(fmt.import_(encoded), plain)
                    self.assertEqual(formats.detect_format(encoded), name)

    def test_other_encodings_are_refused_plainly(self):
        latin = "# Caf\u00e9\n\n- [ ] One\n".encode("latin-1")
        self.assertEqual(error(self, markdown.import_, latin),
                         "That file isn't UTF-8 text (invalid continuation byte at byte 5). Save it as UTF-8 and try again.")
        no_bom = "- [ ] One".encode("utf-16-le")
        self.assertEqual(error(self, markdown.import_, no_bom), "That file isn't a text file, so it can't be imported.")
        self.assertIn("isn't valid JSON", error(self, trello.import_, no_bom))
        for fmt in (native, trello, github):
            self.assertIn("isn't UTF-8 text", error(self, fmt.import_, b'{"name": "Caf\xe9"}'))

    def test_bytes_like_values_are_read(self):
        raw = b"- [ ] One"
        self.assertEqual(markdown.import_(bytearray(raw)), markdown.import_(memoryview(raw)))
        with self.assertRaises(TypeError):
            markdown.import_("- [ ] One")


class Limits(unittest.TestCase):
    def test_too_large(self):
        big = b" " * (common.MAX_IMPORT_BYTES + 1)
        message = "That file is too large to import (64 MB; the limit is 64 MB)."
        self.assertEqual(error(self, formats.detect_format, big), message)
        for f in formats.FORMATS.values():
            if f.import_:
                with self.subTest(f.name):
                    self.assertEqual(error(self, f.import_, big), message)

    def test_too_deep(self):
        deep = b"[" * 100000 + b"]" * 100000
        for call in (native.import_, trello.import_, github.import_, formats.detect_format):
            with self.subTest(call.__module__):
                self.assertEqual(error(self, call, deep), "That file is nested too deeply to be a board file.")
        nested = {"lists": [[[[1]]]], "cards": []}
        self.assertEqual(error(self, trello.import_, to_bytes(nested)),
                         "That Trello export: entry 1 of lists is a list, not an object.")
        md = b"# B\n\n## Board settings\n\n```json\n" + b'{"a":' * 50000 + b"1" + b"}" * 50000 + b"\n```\n"
        self.assertEqual(error(self, markdown.import_, md),
                         "The Board settings part of that file is nested too deeply to be a board file.")

    def test_numbers_json_does_not_allow(self):
        self.assertIn("NaN isn't allowed", error(self, native.import_, b'{"name": "x", "rank": NaN}'))

    def test_huge_strings_are_cut_and_said(self):
        data = fixture_json("trello-board.json")
        data["name"] = "N" * 5000
        data["cards"][0]["name"] = "T" * 300000
        data["cards"][0]["desc"] = "d" * 30000
        result = trello.import_(to_bytes(data))
        board = result.boards[0]
        card = next(c for c in board["cards"] if c["title"].startswith("T"))
        self.assertEqual(len(board["name"]), MAX_NAME)
        self.assertEqual(len(card["title"]), MAX_TITLE)
        self.assertTrue(card["title"].endswith("..."))
        self.assertEqual(len(card["notes"]), 20000)
        self.assertTrue(card["notes"].startswith("T" * 1000))
        self.assertIn(f"Shortened 1 card title to {MAX_TITLE} characters. The full title is at the top of the card's notes.",
                      result.warnings)
        self.assertIn("Shortened 1 card's notes to 20,000 characters.", result.warnings)
        self.assertIn(f"Shortened 1 board name to {MAX_NAME} characters.", result.warnings)
        with TempStore() as store:
            store.import_data(board)

    def test_too_many_columns(self):
        lists = [{"id": f"l{n}", "name": f"List {n}", "closed": False, "pos": n} for n in range(MAX_COLUMNS + 1)]
        self.assertEqual(error(self, trello.import_, to_bytes({"name": "B", "lists": lists, "cards": []})),
                         f"That Trello board has {MAX_COLUMNS + 1} open lists, and a Laneboard board can have at most "
                         f"{MAX_COLUMNS} columns. Archive or join some lists in Trello, then export it again.")
        md = "\n".join(f"## Heading {n}\n- Card {n}" for n in range(MAX_COLUMNS + 1)).encode()
        self.assertEqual(error(self, markdown.import_, md),
                         f"That Markdown file has {MAX_COLUMNS + 1} headings, and a board can have at most "
                         f"{MAX_COLUMNS} columns. Join some of the headings together and try again.")

    def test_too_many_tasks_and_labels(self):
        md = ("- Card\n" + "".join(f"  - [ ] Task {n}\n" for n in range(150))).encode()
        result = markdown.import_(md)
        self.assertEqual(len(result.boards[0]["cards"][0]["tasks"]), 100)
        self.assertIn("Kept the first 100 tasks on 1 card and left out the rest.", result.warnings)
        issues = [{"number": 1, "title": "Many labels", "labels": [{"name": f"label {n}", "color": "d73a4a"} for n in range(60)]}]
        result = github.import_(to_bytes(issues))
        self.assertEqual(len(result.boards[0]["labels"]), 50)
        self.assertEqual(len(result.boards[0]["cards"][0]["labels"]), 20)
        self.assertIn("Kept the first 50 labels and left out the rest.", result.warnings)
        self.assertIn("Kept the first 20 labels on 1 card and left out the rest.", result.warnings)


class NativeFormat(unittest.TestCase):
    def test_export_is_todays_bytes_with_every_field(self):
        for board in (rich_board(), sample_board()):
            raw = native.export(board)
            self.assertEqual(raw, encode_data(board))
            self.assertEqual(json.loads(raw.decode("utf-8")), board)
        self.assertEqual(native.FORMAT.extension, ".json")

    def test_import_hands_boards_on_unchanged(self):
        board = rich_board()
        self.assertEqual(native.import_(native.export(board)), formats.ImportResult([board], []))
        whole = {"version": SCHEMA_VERSION, "boards": [board, sample_board()]}
        self.assertEqual(native.import_(to_bytes(whole)).boards, whole["boards"])

    def test_same_messages_as_the_store(self):
        for bad in ([], {"boards": {}}, {"version": SCHEMA_VERSION + 1, "boards": [{}]}, {"boards": []}):
            with self.subTest(bad=bad), TempStore() as store:
                self.assertEqual(error(self, native.import_, to_bytes(bad)), error(self, store.import_data, bad))
        self.assertEqual(error(self, native.import_, to_bytes({"boards": []})), "That file has no boards in it.")

    def test_a_round_trip_through_the_store(self):
        board = sample_board()
        with TempStore() as store:
            again = store.import_data(native.import_(native.export(board)).boards[0])[0]
        for key in ("name", "description", "prefix", "groupLabel", "columns", "groups", "checklists", "template", "counters"):
            self.assertEqual(again[key], board[key])
        self.assertEqual([{k: v for k, v in c.items() if k not in ("created", "updated")} for c in again["cards"]],
                         [{k: v for k, v in c.items() if k not in ("created", "updated")} for c in board["cards"]])


class MarkdownRoundTrip(unittest.TestCase):
    def check(self, board):
        raw = markdown.export(board)
        result = markdown.import_(raw)
        self.assertEqual(result.warnings, [])
        self.assertEqual(len(result.boards), 1)
        got_board, got_cards = split_import(result.boards[0])
        want_board, want_cards = expected_import(board)
        self.assertEqual(got_board, want_board)
        self.assertEqual(sorted(got_cards), sorted(want_cards))
        for key, want in want_cards.items():
            self.assertEqual(got_cards[key], want, key)
        return raw, result

    def test_a_board_with_every_part(self):
        raw, result = self.check(rich_board())
        self.assertEqual([c["key"] for c in result.boards[0]["cards"]],
                         ["REL-6", "REL-1", "REL-2", "REL-3", "REL-4", "REL-7", "REL-5", "WEB-1"])
        text = raw.decode("utf-8")
        self.assertIn("\n## Column: Archived\n", text)
        self.assertIn("\n## Column: Board settings\n", text)
        self.assertIn("\n## Column: Column: odd\n", text)
        self.assertIn("\n#### Section: Notes\n", text)
        self.assertIn("- Labels: Bug, Front end\\, web\n", text)
        self.assertIn("- Size\\: points: 2.5 pt\n", text)
        self.assertIn("- Platforms: iOS, Web\n", text)
        self.assertIn("\n#### Checklist: Before merge\n\n- [ ] Tests pass\n- [x] Tests pass\n- [x] Docs\n", text)
        self.assertIn("\n\\## Not a column\n\\### REL-99 Not a card\n", text)
        self.assertIn("\n```python\nprint(\"# fine\")\n## inside a closed fence\n```\n\\````\n", text)
        self.assertIn("\n## Archived\n\n### REL-5 Archived later\n\n- Column: To do\n- Archived: 2026-04-01T10:00:00Z\n", text)
        self.assertTrue(text.endswith("```\n"))

    def test_the_board_settings_block_is_json_with_one_entry_per_line(self):
        raw = markdown.export(rich_board()).decode("utf-8")
        block = raw.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
        structure = json.loads(block)
        board = rich_board()
        self.assertEqual(structure, {key: board[key] for key in markdown.STRUCTURE})
        self.assertIn('\n  {"id": "to-do", "name": "To do", "limit": null},\n', block)

    def test_the_sample_boards(self):
        for name in ("software-release", "garden"):
            with self.subTest(name):
                board = sample_board(name)
                _, result = self.check(board)
                with TempStore() as store:
                    made = store.import_data(result.boards[0])[0]

                def content(cards):
                    parts = ("title", "column", "group", "summary", "blocked", "fields", "sections", "notes", "archived")
                    return {c["key"]: [c[p] for p in parts] + [sorted(c["checks"])] for c in cards}

                self.assertEqual(content(made["cards"]), content(board["cards"]))
                for key in ("name", "description", "prefix", "groupLabel", "columns", "groups", "checklists", "template"):
                    self.assertEqual(made[key], board[key])

    def test_an_old_board_without_version_3_parts(self):
        old = {"id": "b-old", "name": "Old", "columns": [{"id": "a", "name": "A", "limit": None}],
               "cards": [{"key": "T-1", "title": "One", "column": "a", "rank": 1, "checks": [], "archived": None}]}
        raw, result = self.check(old)
        self.assertNotIn(b"#### ", raw)
        self.assertEqual(result.boards[0]["properties"], [])

    def test_the_rich_board_imports_into_the_store(self):
        result = markdown.import_(markdown.export(rich_board()))
        with TempStore() as store:
            made = store.import_data(result.boards[0])[0]
        self.assertEqual([c["key"] for c in made["cards"]], [c["key"] for c in result.boards[0]["cards"]])
        self.assertEqual(next(c for c in made["cards"] if c["key"] == "REL-3")["column"], "archived")
        self.assertEqual(next(c for c in made["cards"] if c["key"] == "REL-1")["notes"], TRICKY.rstrip())  # as the store keeps it


class MarkdownImport(unittest.TestCase):
    def test_a_plain_task_list(self):
        raw = "\n".join([
            "# Weekend", "", "Things to do before Monday.", "",
            "## To do", "",
            "- [ ] Buy paint", "  - [x] Pick a colour", "  - [ ] Check the tin size", "",
            "  Notes about the paint,", "  on two lines.", "", "  A second paragraph.",
            "- Fix the gate latch, which has been", "  loose since spring",
            "* Plant the bulbs", "- [x] Order the seeds", "",
            "## Doing", "", "1. Clean the gutters", "2) Sweep the path", "",
            "## Done", "", "- [X] Mow the lawn", "",
            "## To do", "", "+ Water the plants", "",
        ]).encode()
        result = markdown.import_(raw)
        self.assertEqual(result.warnings, [])
        board = result.boards[0]
        self.assertEqual((board["name"], board["description"]), ("Weekend", "Things to do before Monday."))
        self.assertEqual(board["columns"], [{"name": "To do", "limit": None}, {"name": "Doing", "limit": None},
                                            {"name": "Done", "limit": None}])
        self.assertEqual([(c["title"], c["column"]) for c in board["cards"]], [
            ("Buy paint", "To do"), ("Fix the gate latch, which has been loose since spring", "To do"),
            ("Plant the bulbs", "To do"), ("Order the seeds", "Done"), ("Clean the gutters", "Doing"),
            ("Sweep the path", "Doing"), ("Mow the lawn", "Done"), ("Water the plants", "To do")])
        paint = board["cards"][0]
        self.assertEqual(paint["tasks"], [{"text": "Pick a colour", "done": True}, {"text": "Check the tin size", "done": False}])
        self.assertEqual(paint["notes"], "Notes about the paint,\non two lines.\n\nA second paragraph.")
        with TempStore() as store:
            store.import_data(board)

    def test_a_done_column_is_added_and_odd_lines_are_counted(self):
        result = markdown.import_(b"## Groceries\n- [ ] Milk\n- [x] Bread\n-\n\nsome stray text\n")
        board = result.boards[0]
        self.assertEqual(board["name"], markdown.DEFAULT_NAME)
        self.assertEqual([c["name"] for c in board["columns"]], ["Groceries", "Done"])
        self.assertEqual([(c["title"], c["column"]) for c in board["cards"]], [("Milk", "Groceries"), ("Bread", "Done")])
        self.assertEqual(result.warnings, [
            "The file has no # heading with a board name, so the board is called Imported tasks.",
            "Left out 1 list item with no text.", "Left out 1 line that was not a heading or a list item."])
        result = markdown.import_(b"- [ ] One\n- [x] Two\n")
        self.assertEqual([c["name"] for c in result.boards[0]["columns"]], ["To do", "Done"])

    def test_code_blocks_in_a_task_list(self):
        raw = b"# B\n\n- Card\n\n  ```\n  - not a card\n  ## not a column\n  ```\n\n```\n- outside\n```\n- Next\n"
        result = markdown.import_(raw)
        board = result.boards[0]
        self.assertEqual([c["title"] for c in board["cards"]], ["Card", "Next"])
        self.assertEqual(board["cards"][0]["notes"], "```\n- not a card\n## not a column\n```")
        self.assertEqual([c["name"] for c in board["columns"]], ["To do"])
        self.assertEqual(result.warnings, ["Left out 3 lines that were not headings or list items."])

    def test_nothing_to_make_a_board_from(self):
        self.assertEqual(error(self, markdown.import_, b"# Title\n\nJust words.\n"),
                         "That Markdown file has no headings or list items to make a board from.")
        self.assertEqual(error(self, markdown.import_, b""), "That file is empty.")

    def test_a_long_title_is_cut(self):
        result = markdown.import_(("- " + "word " * 100).encode())
        card = result.boards[0]["cards"][0]
        self.assertEqual(len(card["title"]), MAX_TITLE)
        self.assertEqual(card["notes"], ("word " * 100).strip())

    def test_a_laneboard_file_without_settings(self):
        raw = "\n".join(["# Board", "", "## To do", "", "### T-1 A card", "", "- Labels: X, Y\\, z", "stray here", "",
                         "#### Properties", "", "- Points: 3", "", "#### Checklist: Missing", "", "- [x] Something",
                         "", "### Untitled key-less card", ""]).encode()
        result = markdown.import_(raw)
        board = result.boards[0]
        self.assertEqual(board["columns"], [{"name": "To do", "limit": None}])
        self.assertEqual([(p["id"], p["name"], p["type"]) for p in board["properties"]], [("points", "Points", "text")])
        self.assertEqual(board["cards"], [
            {"title": "A card", "key": "T-1", "column": "To do", "labels": ["X", "Y, z"], "properties": {"points": "3"}},
            {"title": "Untitled key-less card", "column": "To do"}])
        self.assertEqual(result.warnings, [
            "The file has no Board settings part, so its properties were imported as text.",
            "Left out 1 line that was not in a part of the file Laneboard reads.",
            "Left out 1 checklist line that didn't match a checklist on the board."])

    def test_errors_name_the_card(self):
        board = rich_board()
        text = markdown.export(board).decode("utf-8")
        unknown = text.replace("- Owner: Ann, Bo: lead", "- Nobody: x")
        self.assertEqual(error(self, markdown.import_, unknown.encode()),
                         'Card REL-1: there\'s no property called "Nobody" on this board.')
        bad = text.replace("- Size\\: points: 2.5 pt", "- Size\\: points: lots")
        self.assertTrue(error(self, markdown.import_, bad.encode()).startswith("Card REL-1: "))
        no_json = text.split("```json")[0]
        self.assertEqual(error(self, markdown.import_, no_json.encode()),
                         "The Board settings part of that file has no JSON block in it.")
        listed = text.split("```json")[0] + "```json\n[1]\n```\n"
        self.assertEqual(error(self, markdown.import_, listed.encode()),
                         "The Board settings part of that file must hold a JSON object.")

    def test_windows_line_endings(self):
        raw = markdown.export(rich_board())
        self.assertEqual(markdown.import_(raw.replace(b"\n", b"\r\n")), markdown.import_(raw))

    def test_escaping_alone(self):
        for text in (TRICKY, "", "#", "\\#", "```", "```\n```", "~~~ a\n~~~", "````\n```\n", "\\\\\\# x"):
            with self.subTest(text=text):
                self.assertEqual(markdown.unescape_text(markdown.escape_text(text)), text.split("\n"))
        # Closed code blocks are kept as they are; everything else that could start a part gets a backslash.
        self.assertEqual(markdown.escape_text("# a\n```\n# b\n```\n````\n# c\n\\# d"),
                         ["\\# a", "```", "# b", "```", "\\````", "\\# c", "\\\\# d"])


class Hostile(HTMLParser):
    """Collects what a browser would see in a page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.attrs, self.text, self.styles, self._in_style = [], [], [], [], False

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += [(tag, name, value) for name, value in attrs]
        self._in_style = tag == "style"

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        self._in_style = False

    def handle_data(self, data):
        (self.styles if self._in_style else self.text).append(data)


SCRIPT = "<script>alert(1)</script>"
BREAK = '"><img src=x onerror=alert(1)>'
SINGLE = "' onmouseover='alert(1)"
JS = "javascript:alert(1)"
PAGE_TAGS = {"html", "head", "meta", "title", "style", "body", "header", "h1", "h2", "h3", "h4", "p", "section", "dl",
             "div", "dt", "dd", "table", "thead", "tbody", "tr", "th", "td", "span", "ul", "li", "main", "article",
             "code", "a", "br", "footer"}


def hostile_board():
    props = clean_definitions({"properties": [], "cards": []}, [
        {"name": "<b>Points</b>", "type": "number"},
        {"name": "Kind" + SINGLE, "type": "choice", "choices": ["<i>x</i>", '"quoted"']},
        {"name": "Link", "type": "url"},
    ])
    return {
        "id": "b-x", "name": SCRIPT, "description": BREAK + " `" + SCRIPT + "` " + JS, "prefix": "X",
        "groupLabel": SCRIPT,
        "columns": [{"id": "a", "name": "</style><script>alert(1)</script>", "limit": 1},
                    {"id": "b", "name": BREAK, "limit": None}],
        "groups": [{"id": "g", "name": SINGLE, "prefix": "G", "color": 'teal" onclick="alert(1)', "link": JS,
                    "details": [{"label": BREAK, "value": JS + " https://example.com/\"onmouseover=\"alert(1)"}]},
                   {"id": "h", "name": "Quoted link", "prefix": "H", "color": "blue",
                    "link": "https://example.com/\"onmouseover=alert(1)", "details": []}],
        "labels": [{"id": "l", "name": BREAK, "color": 'x" onclick="alert(1)'}],
        "properties": props,
        "checklists": [{"id": "c", "name": SCRIPT, "items": [{"id": "i", "text": SCRIPT, "heading": False}]}],
        "cards": [
            {"key": "X-1", "title": SCRIPT, "column": "a", "rank": 1, "group": "g", "labels": ["l"], "blocked": BREAK,
             "summary": "See https://example.com/a\"b and " + JS, "checks": ["i"], "archived": None,
             "fields": [{"label": BREAK, "value": "[x](javascript:alert(1)) <a href=\"javascript:alert(1)\">x</a>"}],
             "sections": [{"label": SCRIPT, "body": "data:text/html,<script>alert(1)</script>"}],
             "notes": "Read https://example.com/docs. Then `<b>code</b>` and JAVASCRIPT:alert(1) and HTTPS://EXAMPLE.COM/UP",
             "tasks": [{"id": "t", "text": SCRIPT, "done": True}, {"id": "u", "text": BREAK, "done": False}],
             "properties": {props[0]["id"]: 3, props[1]["id"]: props[1]["choices"][1]["id"], "link": JS}},
            {"key": "X-2", "title": BREAK, "column": "a", "rank": 2, "group": None, "archived": None},
            {"key": "X-3", "title": SINGLE, "column": "b", "rank": 1, "group": None, "archived": "2026-01-01T00:00:00Z"},
        ],
    }


class HtmlExport(unittest.TestCase):
    def parse(self, board):
        raw = htmlpage.export(board)
        page = Hostile()
        page.feed(raw.decode("utf-8"))
        page.close()
        return raw.decode("utf-8"), page

    def test_hostile_text_is_only_ever_text(self):
        html, page = self.parse(hostile_board())
        self.assertLessEqual(set(page.tags), PAGE_TAGS)
        self.assertEqual(page.tags.count("style"), 1)
        self.assertEqual(page.styles, [f"\n{htmlpage.CSS}\n"])
        self.assertNotIn("<script", html.lower())
        for tag, name, value in page.attrs:
            self.assertFalse(name.startswith("on"), (tag, name))
            self.assertNotIn("javascript:", (value or "").lower())
            if name == "href":
                self.assertRegex(value, r"(?i)^https?://[^\s<>]*$")
            if name == "class":
                self.assertRegex(value, r"^[a-z0-9 -]+$")
        text = "".join(page.text)
        for bad in (SCRIPT, BREAK, SINGLE, "<b>code</b>", "<i>x</i>"):
            self.assertIn(bad, text)  # shown as it was written
        hrefs = [value for _, name, value in page.attrs if name == "href"]
        self.assertIn("https://example.com/docs", hrefs)  # the sentence's period is not part of it
        self.assertIn("https://example.com/a", hrefs)
        self.assertNotIn("HTTPS://EXAMPLE.COM/UP", hrefs)  # as on the board, only lower case addresses become links
        self.assertIn('https://example.com/"onmouseover=alert(1)', hrefs)  # quote and all, inside the attribute
        self.assertNotIn(JS, hrefs)

    def test_a_page_that_stands_alone(self):
        html, page = self.parse(rich_board())
        self.assertTrue(html.startswith("<!doctype html>\n<html lang=\"en\">"))
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', html)
        self.assertIn("default-src 'none'", html)
        self.assertIn("@media print", html)
        for outside in ("<link", "src=", "url(", "@import", "<script"):
            self.assertNotIn(outside, html)
        self.assertLessEqual(set(page.tags), PAGE_TAGS)
        self.assertEqual(formats.FORMATS["html"].mime, "text/html; charset=utf-8")

    def test_the_page_holds_the_summary_and_every_card(self):
        board = rich_board()
        html, page = self.parse(board)
        text = "".join(page.text)
        self.assertIn('<h2 id="summary">Summary</h2>', html)
        self.assertIn("<div><dt>Cards</dt><dd>6</dd></div>", html)
        self.assertIn("<div><dt>Archived</dt><dd>2</dd></div>", html)
        self.assertIn("<div><dt>Checklist ticks</dt><dd>3 of 18</dd></div>", html)
        self.assertIn('<td class="n">3 pt</td>', html)  # the To do column's points: 2.5 + 0.5
        self.assertIn("Total 3 pt, average 1.5 pt, from 0.5 pt to 2.5 pt, on 2 cards", text)
        self.assertIn("Average 4 out of 5, on 1 card", text)
        self.assertIn("Low 0, High 1", text)
        for card in board["cards"]:
            self.assertIn(card["title"], text)
        self.assertIn("Before merge: 2 of 3 ticked", text)
        self.assertEqual(htmlpage.rich("Run `make <all>` at https://example.com/a?b=1&c=2. Then `this"),
                         'Run <code>make &lt;all&gt;</code> at <a href="https://example.com/a?b=1&amp;c=2" '
                         'rel="noopener noreferrer">https://example.com/a?b=1&amp;c=2</a>. Then `this')
        self.assertIn("Done: Write [x] tests", text)

    def test_an_old_board(self):
        board = sample_board()
        html, page = self.parse(board)
        text = "".join(page.text)
        for card in board["cards"]:
            self.assertIn(card["title"], text)
        self.assertIn("Component details", text)


class TextExport(unittest.TestCase):
    def test_columns_and_cards(self):
        def card(key, title, column, rank=1, **extra):
            return {"key": key, "title": title, "column": column, "rank": rank, "archived": None, **extra}

        board = {"name": "Errands", "description": "This week", "labels": [
            {"id": "home", "name": "Home", "color": "teal"}, {"id": "car", "name": "Car", "color": "rose"}],
            "columns": [{"id": "a", "name": "To do", "limit": None}, {"id": "b", "name": "Doing", "limit": 1},
                        {"id": "c", "name": "Done", "limit": 3}],
            "cards": [card("T-1", "Post the parcel", "a", 2, labels=["home", "car", "gone"]), card("T-10", "Buy stamps", "a"),
                      card("T-2", "Wash the car", "b"), card("T-3", "Fix the gate", "b", 2),
                      card("T-4", "Old errand", "c", archived="2026-01-01T00:00:00Z")]}
        self.assertEqual(plaintext.export(board).decode("utf-8"), "\n".join([
            "Errands", "This week", "", "To do (2)", "T-10  Buy stamps", "T-1   Post the parcel [Home, Car]", "",
            "Doing (2/1, over the limit)", "T-2   Wash the car", "T-3   Fix the gate", "", "Done (0/3)", ""]))

    def test_the_sample_board(self):
        out = plaintext.export(sample_board()).decode("utf-8")
        self.assertTrue(out.startswith("Software release\n"))
        self.assertIn("\nDoing (", out)
        self.assertTrue(out.endswith("\n"))


class TrelloImport(unittest.TestCase):
    def setUp(self):
        self.result = trello.import_(fixture("trello-board.json"))
        self.board = self.result.boards[0]

    def card(self, title):
        return next(c for c in self.board["cards"] if c["title"] == title)

    def test_lists_labels_and_fields(self):
        b = self.board
        self.assertEqual((b["name"], b["description"]),
                         ("Kitchen refit", "A made-up board for testing imports: planning a small kitchen refit."))
        self.assertEqual([c["name"] for c in b["columns"]], ["Ideas", "To do", "Doing", "Done"])
        self.assertEqual([(lab["name"], lab["color"]) for lab in b["labels"]], [
            ("Quick", "green"), ("Waiting", "amber"), ("Urgent", "rose"), ("Design", "violet"), ("Shopping", "teal"),
            ("Orange", "amber"), ("Electrics", "blue")])
        self.assertEqual([(p["name"], p["type"]) for p in b["properties"]],
                         [("Supplier", "text"), ("Cost", "number"), ("Ordered", "checkbox"), ("Room", "choice")])
        cost = b["properties"][1]
        self.assertEqual(cost["decimals"], 2)
        room = b["properties"][3]
        self.assertEqual([(c["name"], c["color"]) for c in room["choices"]],
                         [("Kitchen", "green"), ("Hall upstairs", "blue"), ("Garage", "slate")])

    def test_cards(self):
        self.assertEqual([(c["title"], c["column"], bool(c.get("archived"))) for c in self.board["cards"]], [
            ("Choose the cupboard handles", "Ideas", False), ("Pick a tile colour", "Ideas", False),
            ("Look at second hand ovens", "Ideas", True), ("Book the electrician", "To do", False),
            ("Measure the wall for the new shelves", "To do", False), ("Order the worktop", "Doing", False),
            ("Take the old cupboards to the tip", "Done", False), ("Paint the ceiling", "Done", False),
            ("Move the sink to the window", "Ideas", True)])
        worktop = self.card("Order the worktop")
        self.assertEqual(worktop["labels"], ["Urgent", "Shopping"])
        self.assertEqual(worktop["properties"], {"supplier": "Example Timber Yard", "cost": 420.5, "ordered": True})
        self.assertEqual(worktop["tasks"], [{"text": "Check the length again", "done": True},
                                            {"text": "Ask about the edge finish", "done": False}])
        tiles = self.card("Pick a tile colour")
        self.assertEqual(tiles["tasks"], [{"text": "Samples: White gloss", "done": True},
                                          {"text": "Samples: Sage green", "done": False},
                                          {"text": "Grout: Grey or white", "done": False}])
        self.assertEqual(tiles["properties"], {"room": "hall-upstairs", "cost": 35})
        self.assertEqual(tiles["labels"], ["Design", "Orange"])
        self.assertEqual(self.card("Measure the wall for the new shelves")["notes"], "Both alcoves.\n\nWrite the numbers in the notes.")
        self.assertNotIn("properties", self.card("Book the electrician"))  # an unticked box is no value
        for c in self.board["cards"]:
            self.assertLessEqual(set(c), {"title", "column", "notes", "archived", "labels", "tasks", "properties"})

    def test_warnings(self):
        self.assertEqual(self.result.warnings, [
            "Skipped 1 archived list (Old plans). The 1 card in it came in as an archived card in Ideas.",
            "Left out 1 custom field (Delivery) because Laneboard has no property of that kind.",
            "Took the commas out of 1 choice name, since a choice can't have one in its name.",
            "Left out the due dates on 1 card.",
            "Left out members on 1 card, attachments on 1 card, votes on 2 cards, stickers on 1 card and 1 comment, "
            "which Laneboard doesn't keep."])

    def test_the_store_takes_it(self):
        with TempStore() as store:
            made = store.import_data(self.board)[0]
        self.assertEqual([c["name"] for c in made["columns"]], ["Ideas", "To do", "Doing", "Done"])
        self.assertEqual(len(made["cards"]), 9)
        self.assertEqual(sum(1 for c in made["cards"] if c["archived"]), 2)

    def test_odd_exports(self):
        lists_only = {"name": "", "lists": [{"id": "x", "name": "Old", "closed": True}], "cards": [
            {"name": "Lost", "idList": "nowhere"}, {"name": "Kept", "idList": "x"}]}
        result = trello.import_(to_bytes(lists_only))
        board = result.boards[0]
        self.assertEqual((board["name"], board["columns"]), ("Trello board", [{"name": "To do", "limit": None}]))
        self.assertEqual([(c["title"], c["column"], c.get("archived")) for c in board["cards"]],
                         [("Lost", "To do", None), ("Kept", "To do", True)])
        self.assertEqual(result.warnings, [
            "Skipped 1 archived list (Old). The 1 card in it came in as an archived card in To do.",
            "1 card named a list that isn't in the export, so it is in To do."])
        clashing = {"name": "B", "lists": [{"id": "1", "name": "Same"}, {"id": "2", "name": "same"}], "cards": [],
                    "labels": [{"id": None, "name": "No id", "color": "green"}],
                    "checklists": [{"idCard": None, "checkItems": [{"name": "x"}]}]}
        result = trello.import_(to_bytes(clashing))
        self.assertEqual([c["name"] for c in result.boards[0]["columns"]], ["Same", "same 2"])
        self.assertIn("Renamed 1 column name so that each name is different and at most 40 characters long.", result.warnings)

    def test_wrong_types(self):
        def bad(change):
            data = fixture_json("trello-board.json")
            change(data)
            return error(self, trello.import_, to_bytes(data))

        self.assertEqual(error(self, trello.import_, b"[]"),
                         "That isn't a Trello board export: it needs the lists and cards that Trello's Export as JSON writes.")
        self.assertEqual(bad(lambda d: d.update(cards=[1])), "That Trello export: entry 1 of cards is a number, not an object.")
        self.assertEqual(bad(lambda d: d["cards"][0].update(name=5)),
                         'Card 1 in that Trello export has a "name" that is a number, not text.')
        self.assertEqual(bad(lambda d: d["cards"][0].update(idLabels="x")),
                         'Card 1 in that Trello export has a "idLabels" that is text, not a list.')
        self.assertEqual(bad(lambda d: d["lists"][0].update(closed="no")),
                         'List 1 in that Trello export has a "closed" that is text, not true or false.')
        self.assertEqual(bad(lambda d: d.update(labels={})), "That Trello export: labels must be a list, not an object.")
        self.assertEqual(bad(lambda d: d["customFields"][3].update(options=[None])),
                         "Custom field 4 in that Trello export: entry 1 of options is empty, not an object.")
        for value, kind in ((5, "a number"), (True, "true or false")):
            self.assertEqual(bad(lambda d: d["cards"][1].update(customFieldItems=value)),
                             f"Card 2 in that Trello export: customFieldItems must be a list, not {kind}.")
        data = fixture_json("trello-board.json")
        data["cards"][0]["idLabels"] = [["unhashable"], None, 7]
        data["cards"][1]["customFieldItems"][1]["value"] = {"number": "lots"}
        data["cards"][1]["customFieldItems"][0]["value"] = {"text": ["not text"]}
        result = trello.import_(to_bytes(data))
        self.assertIn("Left out 2 custom field values that couldn't be read.", result.warnings)
        self.assertNotIn("labels", next(c for c in result.boards[0]["cards"] if c["title"].startswith("Measure")))


class GithubImport(unittest.TestCase):
    def setUp(self):
        self.result = github.import_(fixture("github-issues.json"))
        self.board = self.result.boards[0]

    def card(self, key):
        return next(c for c in self.board["cards"] if c.get("key") == key)

    def test_the_board(self):
        b = self.board
        self.assertEqual(b["name"], "example-org/example-repo")
        self.assertEqual([c["name"] for c in b["columns"]], ["Backlog", "Ready", "In progress", "Review", "Done"])
        self.assertEqual([(lab["name"], lab["color"]) for lab in b["labels"]], [
            ("bug", "rose"), ("enhancement", "teal"), ("documentation", "blue"), ("good first issue", "violet"),
            ("help wanted", "teal"), ("wontfix", "slate")])
        self.assertEqual([(p["name"], p["type"], [c["name"] for c in p["choices"]]) for p in b["properties"]], [
            ("Milestone", "choice", ["v1.2", "v2.0"]),
            ("Assignees", "multi", ["example-user", "another-user", "third-example"])])
        self.assertEqual(self.result.warnings, [])

    def test_issues(self):
        self.assertEqual([(c["key"], c["column"]) for c in self.board["cards"]], [
            ("GH-12", "Backlog"), ("GH-15", "Backlog"), ("GH-9", "Done"), ("GH-21", "Backlog"), ("GH-18", "Backlog"),
            ("GH-4", "Done"), ("GH-7", "Done")])
        nine = self.card("GH-9")
        self.assertEqual(nine["title"], "Fix the install steps in the readme")
        self.assertEqual(nine["notes"], "The install section still names the old command.")
        self.assertEqual(nine["fields"], [{"label": "Link", "value": "https://github.com/example-org/example-repo/issues/9"}])
        self.assertEqual(nine["labels"], ["documentation", "good first issue"])
        self.assertEqual(nine["properties"], {"milestone": "v1-2", "assignees": ["example-user", "another-user"]})
        self.assertNotIn("notes", self.card("GH-21"))
        self.assertNotIn("properties", self.card("GH-21"))
        self.assertIn("```\nError: out of range", self.card("GH-18")["notes"])

    def test_the_store_takes_it(self):
        with TempStore() as store:
            made = store.import_data(self.board)[0]
        self.assertEqual(sorted(c["key"] for c in made["cards"]), sorted(c["key"] for c in self.board["cards"]))
        self.assertEqual(made["counters"]["GH"], 21)

    def test_odd_lists(self):
        issues = [{"number": 5, "title": "One", "state": "MERGED", "url": JS},
                  {"number": 5, "title": "Two", "milestone": {"title": "v1, beta"}},
                  {"title": "No number", "url": "https://github.com/a/b/pull/3"},
                  {"number": 10 ** 7, "title": "Too big a number"}]
        result = github.import_(to_bytes(issues))
        board = result.boards[0]
        self.assertEqual(board["name"], "a/b")
        self.assertEqual([(c["title"], c.get("key"), c["column"]) for c in board["cards"]], [
            ("One", "GH-5", "Done"), ("Two", None, "Backlog"), ("No number", None, "Backlog"),
            ("Too big a number", None, "Backlog")])
        self.assertNotIn("fields", board["cards"][0])
        self.assertEqual(board["properties"][0]["choices"][0]["name"], "v1 beta")
        self.assertEqual(result.warnings, ["Took the commas out of 1 milestone name, since a choice can't have one in its name.",
                                           "1 issue had the same number as another, so it got a new key."])
        with TempStore() as store:
            made = store.import_data(board)[0]
        self.assertEqual(len({c["key"] for c in made["cards"]}), 4)

    def test_wrong_types(self):
        self.assertEqual(error(self, github.import_, b"{}"), "That isn't a GitHub issue list. Make one with: gh issue list "
                                                              "--json number,title,body,labels,state,url,milestone,assignees")
        self.assertEqual(error(self, github.import_, b"[]"), "That issue list is empty.")
        self.assertEqual(error(self, github.import_, b"[1]"), "That issue list: entry 1 of issues is a number, not an object.")
        self.assertEqual(error(self, github.import_, b'[{"number": 1}]'), 'Issue 1 in that list has no "title".')
        self.assertEqual(error(self, github.import_, b'[{"title": "x", "number": "1"}]'),
                         'Issue 1 in that list has a "number" that is text, not a whole number.')
        self.assertEqual(error(self, github.import_, b'[{"title": "x", "number": true}]'),
                         'Issue 1 in that list has a "number" that is true or false, not a whole number.')
        self.assertEqual(error(self, github.import_, b'[{"title": "x", "labels": "bug"}]'),
                         "Issue 1 in that list: labels must be a list, not text.")
        self.assertEqual(error(self, github.import_, b'[{"title": "x", "labels": [{"name": 3}]}]'),
                         'Issue 1 in that list, label 1 has a "name" that is a number, not text.')
        self.assertEqual(error(self, github.import_, b'[{"title": "x", "milestone": "v1"}]'),
                         'Issue 1 in that list has a "milestone" that is text, not an object.')

    def test_colors(self):
        for hex_color, name in (("d73a4a", "rose"), ("0075ca", "blue"), ("a2eeef", "teal"), ("7057ff", "violet"),
                                ("008672", "teal"), ("e4e669", "amber"), ("0e8a16", "green"), ("ffffff", "slate"),
                                ("000000", "slate"), ("cfd3d7", "slate"), ("#fbca04", "amber"), ("nonsense", "slate"),
                                (None, "slate"), ("e99695", "rose"), ("c5def5", "blue")):
            with self.subTest(hex_color):
                self.assertEqual(github.color(hex_color), name)


class DatesStayOut(unittest.TestCase):
    def test_no_dates_are_imported_and_only_trello_due_dates_are_mentioned(self):
        trello_result = trello.import_(fixture("trello-board.json"))
        github_result = github.import_(fixture("github-issues.json"))
        for result in (trello_result, github_result):
            for board in result.boards:
                self.assertLessEqual({p["type"] for p in board["properties"]}, {"text", "number", "checkbox", "choice", "multi"})
                text = json.dumps(board)
                self.assertNotIn("2026-12-", text)  # no due date, delivery date or milestone date came in
        self.assertEqual([w for w in trello_result.warnings if "due" in w], ["Left out the due dates on 1 card."])
        self.assertFalse([w for w in github_result.warnings if "date" in w or "due" in w])


if __name__ == "__main__":
    unittest.main()
