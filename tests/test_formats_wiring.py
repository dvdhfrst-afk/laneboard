"""Formats and stats through the server and the command line: exports as downloads, imports through the
format registry into the store's all-or-nothing import, previews, summaries, and hostile files refused."""
import http.client
import json
import os
import re
import unittest
from unittest import mock

from laneboard import formats, stats
from laneboard import server as server_module
from laneboard.formats import common
from laneboard.server import encode_json
from laneboard.store import encode_data, outline
from tests.test_api_search_templates import ApiCase
from tests.test_cli_board_parts import Case
from tests.test_docs import blocks
from tests.test_formats import BREAK, JS, PAGE_TAGS, SCRIPT, SINGLE, Hostile, TempStore, fixture, rich_board, to_bytes

TRELLO = fixture("trello-board.json")
GITHUB = fixture("github-issues.json")
TRELLO_WARNINGS = formats.import_file(TRELLO)[1].warnings
SAFE_NAME = re.compile(r'^attachment; filename="[a-z0-9-]+\.[a-z]+"; filename\*=UTF-8\'\'[a-z0-9-]+\.[a-z]+$')


def structure(board):
    """What a board is, apart from its cards and the stamps a new board gets."""
    return {key: board[key] for key in ("name", "description", "prefix", "groupLabel", "columns", "groups", "labels",
                                        "properties", "views", "settings", "checklists", "template")}


def content(board):
    """Each card's content by key, with its column by name, and the order of the cards on the board in each column."""
    columns = {c["id"]: c["name"] for c in board["columns"]}
    cards = {}
    for c in board["cards"]:
        made = {k: v for k, v in c.items() if k not in ("created", "updated", "rank", "tasks", "checks", "column")}
        made.update(column=columns[c["column"]], tasks=[(t["text"], t["done"]) for t in c["tasks"]], checks=sorted(c["checks"]))
        cards[c["key"]] = made
    on_board = sorted((c for c in board["cards"] if not c["archived"]), key=lambda c: c["rank"])
    order = {name: [c["key"] for c in on_board if columns[c["column"]] == name] for name in columns.values()}
    return cards, order


def hostile_import():
    """A board in the import shape whose every piece of text tries to be markup, for the store to take in."""
    return {
        "name": SCRIPT, "description": BREAK + " `" + SCRIPT + "` " + JS, "prefix": "X", "groupLabel": SCRIPT,
        "columns": ["</style><script>alert(1)</script>", {"name": BREAK, "limit": 1}],
        "groups": [{"name": SINGLE, "prefix": "G", "link": 'https://example.com/"onmouseover=alert(1)',
                    "details": [{"label": BREAK, "value": JS + ' https://example.com/"onmouseover="alert(1)'}]}],
        "labels": [BREAK],
        "properties": [{"name": "<b>Points</b>", "type": "number"},
                       {"name": "Kind" + SINGLE, "type": "choice", "choices": ["<i>x</i>", '"quoted"']},
                       {"name": "Link", "type": "url"}],
        "checklists": [{"id": "c", "name": SCRIPT, "items": [{"id": "i", "text": SCRIPT, "heading": False}]}],
        "cards": [
            {"title": SCRIPT, "column": "</style><script>alert(1)</script>", "group": SINGLE, "labels": [BREAK],
             "blocked": BREAK, "summary": 'See https://example.com/a"b and ' + JS, "checks": ["i"],
             "fields": [{"label": BREAK, "value": '[x](javascript:alert(1)) <a href="javascript:alert(1)">x</a>'}],
             "sections": [{"label": SCRIPT, "body": "data:text/html,<script>alert(1)</script>"}],
             "notes": "Read https://example.com/docs. Then `<b>code</b>` and JAVASCRIPT:alert(1)",
             "tasks": [{"text": SCRIPT, "done": True}, BREAK],
             "properties": {"<b>Points</b>": "3", "Kind" + SINGLE: '"quoted"', "Link": 'https://example.com/"x'}},
            {"title": BREAK, "column": BREAK},
            {"title": SINGLE, "archived": True},
        ],
    }


class Server(ApiCase):
    def setUp(self):
        super().setUp()
        self.board = self.store.import_data(rich_board())[0]["id"]

    def fetch(self, method, path, raw=None, ctype="application/octet-stream", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        sent = {"Host": f"localhost:{self.port}"}
        if raw is not None:
            sent["Content-Type"] = ctype
        sent.update(headers or {})
        conn.request(method, path, body=raw, headers=sent)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res.status, res, data

    def answer(self, method, path, raw=None, status=200, **options):
        got, _, data = self.fetch(method, path, raw, **options)
        payload = json.loads(data)
        self.assertEqual(got, status, payload)
        return payload

    def file_bytes(self):
        with open(self.path, "rb") as f:
            return f.read()

    def refused(self, method, path, raw=None, status=400, says="", **options):
        before = self.file_bytes()
        payload = self.answer(method, path, raw, status, **options)
        self.assertIn(says, payload["error"])
        self.assertEqual(self.file_bytes(), before, "a refused request changes nothing")
        return payload["error"]


class FormatList(Server):
    def test_every_format_and_which_way_it_goes(self):
        listed = self.answer("GET", "/api/formats")["formats"]
        self.assertEqual(listed, [f.info() for f in formats.FORMATS.values()])
        self.assertEqual([f["name"] for f in listed], ["laneboard", "markdown", "html", "text", "trello", "github"])
        self.assertEqual(listed[2], {"name": "html", "title": "Web page", "extension": ".html",
                                     "mime": "text/html; charset=utf-8", "export": True, "import": False})


class Exports(Server):
    def path_for(self, query=""):
        return f"/api/boards/{self.board}/export" + (f"?{query}" if query else "")

    def test_the_default_download_is_todays(self):
        board = self.store.get_board(self.board)
        for query in ("", "format=laneboard", "format=LANEBOARD", "format="):
            with self.subTest(query=query):
                status, res, data = self.fetch("GET", self.path_for(query))
                self.assertEqual(status, 200)
                self.assertEqual(data, encode_json(board, 1))  # what the route has always sent
                self.assertEqual(data, encode_data(board))
                self.assertEqual(res.getheader("Content-Type"), "application/json; charset=utf-8")
                self.assertEqual(res.getheader("Content-Disposition"), "attachment; filename=\"release-3-the-big-one.json\"; "
                                                                      "filename*=UTF-8''release-3-the-big-one.json")

    def test_every_format_is_a_download(self):
        board = self.store.get_board(self.board)
        for fmt in formats.FORMATS.values():
            if fmt.export is None:
                continue
            with self.subTest(fmt.name):
                status, res, data = self.fetch("GET", self.path_for(f"format={fmt.name}"))
                self.assertEqual(status, 200)
                self.assertEqual(data, fmt.export(board))
                self.assertEqual(res.getheader("Content-Type"), fmt.mime)
                self.assertEqual(res.getheader("Content-Disposition"),
                                 f"attachment; filename=\"release-3-the-big-one{fmt.extension}\"; "
                                 f"filename*=UTF-8''release-3-the-big-one{fmt.extension}")
                self.assertIn("sandbox", res.headers.get_all("Content-Security-Policy"))
                self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff")
                self.assertEqual(res.getheader("Cache-Control"), "no-store")
        status, res, data = self.fetch("HEAD", self.path_for("format=html"))
        self.assertEqual((status, data, res.getheader("Content-Type")), (200, b"", "text/html; charset=utf-8"))

    def test_the_file_name_is_made_safe(self):
        made = self.store.import_data({"name": '../..\\evil "name"; x=1.html\r\nSet-Cookie: a=b'})[0]
        for name in ("markdown", "html"):
            status, res, _ = self.fetch("GET", f"/api/boards/{made['id']}/export?format={name}")
            self.assertEqual(status, 200)
            self.assertRegex(res.getheader("Content-Disposition"), SAFE_NAME)
            self.assertIsNone(res.getheader("Set-Cookie"))

    def test_bad_requests(self):
        for query, status, says in (("format=nope", 400, 'There\'s no format called "nope".'),
                                    ("format=trello", 400, "can be imported, not exported"),
                                    ("format=html&format=text", 400, "Give format only once.")):
            with self.subTest(query=query):
                self.refused("GET", self.path_for(query), status=status, says=says)
        self.refused("GET", "/api/boards/b-nothere/export?format=html", status=404)

    def test_the_whole_file_is_only_laneboard_json(self):
        for query in ("", "?format=laneboard"):
            status, res, data = self.fetch("GET", "/api/export" + query)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(data), self.store.all_data())
            self.assertIn('filename="all-boards.json"', res.getheader("Content-Disposition"))
        self.refused("GET", "/api/export?format=markdown", says="The whole file exports only as Laneboard JSON.")


class Imports(Server):
    def test_a_trello_export(self):
        payload = self.answer("POST", "/api/import", TRELLO, 201)
        self.assertEqual((payload["format"], payload["warnings"]), ("trello", TRELLO_WARNINGS))
        self.assertIn("Left out the due dates on 1 card.", payload["warnings"])
        made = payload["boards"]
        self.assertEqual([(b["name"], b["cards"]) for b in made], [("Kitchen refit", 9)])
        board = self.store.get_board(made[0]["id"])
        self.assertEqual([c["name"] for c in board["columns"]], ["Ideas", "To do", "Doing", "Done"])
        self.assertEqual(sum(1 for c in board["cards"] if c["archived"]), 2)

    def test_a_github_issue_list_by_name(self):
        payload = self.answer("POST", "/api/import?format=github&filename=issues.json", GITHUB, 201, ctype="application/json")
        self.assertEqual((payload["format"], payload["warnings"], payload["boards"][0]["cards"]), ("github", [], 7))
        board = self.store.get_board(payload["boards"][0]["id"])
        self.assertEqual(board["counters"]["GH"], 21)

    def test_todays_import_still_works(self):
        payload = self.answer("POST", "/api/import", to_bytes({"name": "Plain", "cards": [{"title": "One"}]}), 201,
                              ctype="application/json")
        self.assertEqual(payload, {"boards": [{"id": payload["boards"][0]["id"], "name": "Plain", "cards": 1}],
                                   "format": "laneboard", "warnings": []})
        whole = {"version": 3, "boards": [{"name": "A"}, {"name": "B"}]}
        self.assertEqual([b["name"] for b in self.answer("POST", "/api/import", to_bytes(whole), 201)["boards"]], ["A", "B"])

    def test_a_markdown_task_list(self):
        raw = "# Weekend\n\n## To do\n\n- [ ] Mow the lawn\n- [x] Buy bread\n".encode("utf-8")
        payload = self.answer("POST", "/api/import?filename=weekend.md", raw, 201, ctype="text/markdown; charset=utf-8")
        self.assertEqual(payload["format"], "markdown")
        board = self.store.get_board(payload["boards"][0]["id"])
        self.assertEqual([(c["title"], board["columns"][[x["id"] for x in board["columns"]].index(c["column"])]["name"])
                          for c in board["cards"]], [("Mow the lawn", "To do"), ("Buy bread", "Done")])

    def test_a_preview_adds_nothing(self):
        before = self.file_bytes()
        payload = self.answer("POST", "/api/import?preview=1", TRELLO)
        self.assertEqual(payload, {"format": "trello", "warnings": TRELLO_WARNINGS, "boards": [{
            "name": "Kitchen refit",
            "columns": [{"name": "Ideas", "cards": 2}, {"name": "To do", "cards": 2}, {"name": "Doing", "cards": 1},
                        {"name": "Done", "cards": 2}],
            "cards": 9, "archived": 2,
            "labels": ["Quick", "Waiting", "Urgent", "Design", "Shopping", "Orange", "Electrics"],
            "properties": [{"name": "Supplier", "type": "text"}, {"name": "Cost", "type": "number"},
                           {"name": "Ordered", "type": "checkbox"}, {"name": "Room", "type": "choice"}]}]})
        self.assertEqual(self.file_bytes(), before)
        self.assertEqual(self.answer("POST", "/api/import?preview=0", TRELLO, 201)["format"], "trello")

    def test_the_answers_shown_in_the_api_doc(self):
        garage = next(b for b in blocks("docs/examples.md", ("json",)) if '"lists"' in b).encode("utf-8")
        added, preview = [json.loads(b) for b in blocks("docs/api.md", ("json",)) if "Garage clear-out" in b]
        self.assertEqual(self.answer("POST", "/api/import?preview=1&filename=garage.json", garage), preview)
        got = self.answer("POST", "/api/import", garage, 201)
        self.assertRegex(got["boards"][0]["id"], r"^b-[0-9a-f]{8}$")
        got["boards"][0]["id"] = added["boards"][0]["id"]
        self.assertEqual(got, added)

    def test_a_preview_refuses_what_the_import_would(self):
        bad = to_bytes({"name": "Links", "groups": [{"name": "Web", "link": "javascript:alert(1)"}]})
        said = self.refused("POST", "/api/import?preview=1", bad)
        self.assertEqual(said, self.refused("POST", "/api/import", bad))

    def test_bad_requests(self):
        for query, raw, options, status, says in (
                ("?preview=2", TRELLO, {}, 400, "preview must be 1"),
                ("?format=html", TRELLO, {}, 400, "Web page files can be exported, not imported."),
                ("?format=nope", TRELLO, {}, 400, 'There\'s no format called "nope".'),
                ("?format=markdown", TRELLO, {}, 400, ""),
                ("", TRELLO, {"ctype": "text/plain"}, 415, "Send the file as application/json"),
                ("", TRELLO, {"ctype": "multipart/form-data; boundary=x"}, 415, "Send the file as"),
                ("", TRELLO, {"ctype": "application/x-www-form-urlencoded"}, 415, "Send the file as"),
                ("", TRELLO, {"headers": {"Origin": "http://evil.example"}}, 403, "own page"),
                ("", TRELLO, {"headers": {"Sec-Fetch-Site": "cross-site"}}, 403, "own page"),
                ("", b"", {}, 400, "That file is empty.")):
            with self.subTest(query=query, options=options):
                self.refused("POST", "/api/import" + query, raw, status, says, **options)
        with mock.patch.object(server_module, "MAX_IMPORT", 1000):
            self.refused("POST", "/api/import", TRELLO, 413, "too large")

    def test_hostile_files_add_nothing(self):
        trello_wrong = json.loads(TRELLO)
        trello_wrong["cards"][0]["name"] = 5
        github_wrong = json.loads(GITHUB)
        github_wrong[0]["title"] = {"text": "x"}
        too_many = "# Many\n\n" + "".join(f"## Column {n}\n\n- Card {n}\n\n" for n in range(25))
        for raw, query, says in (
                (b"[" * 100000, "", ""), (b'{"a":' * 50000, "", ""), (b"\x00" * 10, "", "isn't a text file"),
                (b"\xc3\x28 broken", "", "isn't UTF-8 text"), (b"\xff\xfe" + "\ud800".encode("utf-16-le", "surrogatepass"), "", ""),
                (to_bytes(trello_wrong), "", 'has a "name" that is a number, not text'),
                (to_bytes(github_wrong), "", ""),
                (too_many.encode("utf-8"), "?format=markdown", "a board can have at most 20"),
                (to_bytes({"boards": [{"name": "Fine"}, {"name": "Bad", "columns": "x"}]}), "", "Board number 2"),
                (to_bytes({"name": "X" * 5000}), "", "Board name is too long"),
                (to_bytes({"name": "Deep", "cards": [{"title": "x", "notes": [[[[["x"]]]]]}]}), "", "Notes must be text")):
            with self.subTest(raw=raw[:30]):
                self.refused("POST", "/api/import" + query, raw, says=says)
        self.assertEqual(len(self.store.list_boards()), 1)


class Summary(Server):
    def test_a_boards_summary(self):
        made = self.store.create_board("Work", template="software")
        self.assertEqual(self.answer("GET", f"/api/boards/{made['id']}/summary"), {"summary": stats.summarize(made)})
        self.assertEqual(self.answer("GET", f"/api/boards/{self.board}/summary")["summary"]["archived"], 2)
        self.refused("GET", "/api/boards/b-nothere/summary", status=404)
        self.refused("POST", f"/api/boards/{self.board}/summary", b"{}", status=405, ctype="application/json")


class EndToEnd(Server):
    def test_markdown_out_and_back_in_keeps_the_board(self):
        original = self.store.get_board(self.board)
        status, res, raw = self.fetch("GET", f"/api/boards/{self.board}/export?format=markdown")
        self.assertEqual((status, res.getheader("Content-Type")), (200, "text/markdown; charset=utf-8"))
        payload = self.answer("POST", "/api/import?filename=release.md", raw, 201, ctype="text/markdown")
        self.assertEqual((payload["format"], payload["warnings"]), ("markdown", []))
        again = self.store.get_board(payload["boards"][0]["id"])
        self.assertEqual(structure(again), structure(original))
        self.assertEqual(content(again), content(original))
        self.assertEqual(len(again["cards"]), 8)

    def test_the_web_page_of_a_hostile_board_has_no_live_markup(self):
        board = self.store.import_data(hostile_import())[0]
        self.assertEqual(board["name"], SCRIPT)
        status, res, raw = self.fetch("GET", f"/api/boards/{board['id']}/export?format=html")
        self.assertEqual(status, 200)
        self.assertTrue(res.getheader("Content-Disposition").startswith('attachment; filename="script-alert-1-script.html"'))
        self.assertIn("sandbox", res.headers.get_all("Content-Security-Policy"))
        html = raw.decode("utf-8")
        page = Hostile()
        page.feed(html)
        page.close()
        self.assertLessEqual(set(page.tags), PAGE_TAGS)
        self.assertEqual(page.tags.count("style"), 1)
        self.assertNotIn("<script", html.lower())
        self.assertIn("default-src 'none'", html)
        for tag, name, value in page.attrs:
            self.assertFalse(name.startswith("on"), (tag, name))
            self.assertNotIn("javascript:", (value or "").lower())
            if name == "href":
                self.assertRegex(value, r"(?i)^https?://[^\s<>]*$")
            if name == "class":
                self.assertRegex(value, r"^[a-z0-9 -]+$")
        text = "".join(page.text)
        for shown in (SCRIPT, BREAK, SINGLE, "<b>code</b>", "<b>Points</b>", '"quoted"', "</style><script>alert(1)</script>"):
            self.assertIn(shown, text)


class CommandLine(Case):
    def setUp(self):
        super().setUp()
        self.run_cli("new", "Work", "--template", "software")

    def file_bytes(self):
        with open(self.data, "rb") as f:
            return f.read()

    def test_every_export_format(self):
        board = self.board()
        self.assertEqual(json.loads(self.run_cli("export", "Work")[0]), board)
        for fmt in formats.FORMATS.values():
            if fmt.export is None or fmt.name == "laneboard":
                continue
            with self.subTest(fmt.name):
                self.assertEqual(self.run_cli("export", "Work", "--format", fmt.name.upper())[0], fmt.export(board).decode("utf-8"))
                out = os.path.join(self.tmp.name, "work" + fmt.extension)
                self.assertEqual(self.lines("export", "Work", "--format", fmt.name, "-o", out), [f"Wrote {out}."])
                with open(out, "rb") as f:
                    self.assertEqual(f.read(), fmt.export(board))
                self.unchanged("export", "Work", "--format", fmt.name, "-o", out, says="already exists. Use --force")
                self.run_cli("export", "Work", "--format", fmt.name, "-o", out, "--force")
        self.unchanged("export", "--all", "--format", "markdown", code=2, says="--all writes the whole file as Laneboard JSON")
        self.unchanged("export", "Work", "--format", "trello", code=2, says="invalid choice: 'trello'")

    def test_a_trello_import_says_what_it_left_out(self):
        path = os.path.join(self.tmp.name, "kitchen.json")
        with open(path, "wb") as f:
            f.write(TRELLO)
        out = self.lines("import", path)
        self.assertRegex(out[0], r"^Imported Kitchen refit with 9 cards as b-[0-9a-f]{8}\.$")
        self.assertEqual(out[1:], [f"Warning: {w}" for w in TRELLO_WARNINGS])
        self.assertIn("Warning: Left out the due dates on 1 card.", out)

    def test_a_preview_adds_nothing(self):
        path = os.path.join(self.tmp.name, "kitchen.json")
        with open(path, "wb") as f:
            f.write(TRELLO)
        before = self.file_bytes()
        self.assertEqual(self.lines("import", path, "--preview"), [
            "Format: Trello board export",
            "Would add Kitchen refit: 9 cards, 2 of them archived",
            "  Columns: Ideas 2, To do 2, Doing 1, Done 2",
            "  Labels: Quick, Waiting, Urgent, Design, Shopping, Orange, Electrics",
            "  Properties: Supplier (text), Cost (number), Ordered (checkbox), Room (choice)",
            *[f"Warning: {w}" for w in TRELLO_WARNINGS],
            "Nothing was added. Run it again without --preview to add it."])
        self.assertEqual(self.file_bytes(), before)

    def test_markdown_out_and_back_in(self):
        path = os.path.join(self.tmp.name, "work.md")
        self.run_cli("export", "Work", "--format", "markdown", "-o", path)
        self.run_cli("settings", "Work", "--name", "Work before")
        self.assertRegex(self.lines("import", path)[0], r"^Imported Work with 5 cards as b-[0-9a-f]{8}\.$")
        before, after = self.board("Work before"), self.board("Work")
        self.assertEqual({**structure(after), "name": "Work before"}, structure(before))
        self.assertEqual(content(after), content(before))

    def test_import_errors_change_nothing(self):
        path = os.path.join(self.tmp.name, "kitchen.json")
        with open(path, "wb") as f:
            f.write(TRELLO)
        self.unchanged("import", path, "--format", "markdown", says="laneboard: ")
        self.unchanged("import", path, "--format", "html", code=2, says="invalid choice: 'html'")
        self.unchanged("import", os.path.join(self.tmp.name, "nothing.json"), says="Can't read")
        with mock.patch.object(common, "MAX_IMPORT_BYTES", 100):
            self.unchanged("import", path, says="That file is too large to import")


class Stats(Case):
    def setUp(self):
        super().setUp()
        for args in (("new", "Home"), ("properties", "Home", "--add", "Hours", "--type", "number", "--unit", "h"),
                     ("properties", "Home", "--add", "Size", "--type", "choice", "--choices", "Small, Big"),
                     ("labels", "Home", "--add", "Outside"),
                     ("add", "Home", "Paint the fence"), ("add", "Home", "Fix the tap", "--column", "In progress"),
                     ("add", "Home", "Clean the car", "--column", "In progress"), ("add", "Home", "Old job"),
                     ("edit", "Home", "T-1", "--label", "Outside", "--set", "Hours=3", "--set", "Size=big",
                      "--blocked", "Waiting for paint"),
                     ("edit", "Home", "T-2", "--set", "Hours=2"), ("archive", "Home", "T-4"),
                     ("task", "Home", "T-2", "--add", "Buy a washer", "--add", "Turn off the water"),
                     ("task", "Home", "T-2", "--done", "1")):
            self.run_cli(*args)

    def test_the_text_layout(self):
        self.assertEqual(self.lines("stats", "Home"), [
            "Home: 3 cards, 1 blocked, 1 archived",
            "Tasks: 1 of 2 done",
            "",
            "Column       Cards  Limit  Hours",
            "Backlog          1           3 h",
            "Ready            0           0 h",
            "In progress      2      1    2 h  over the limit",
            "Review           0           0 h",
            "Done             0           0 h  done",
            "",
            "Labels: Outside 1, no labels 2",
            "",
            "Properties:",
            "  Hours  Total 5 h, average 2.5 h, from 2 h to 3 h, on 2 cards",
            "  Size   Small 0, Big 1"])

    def test_json_is_the_summary(self):
        self.assertEqual(json.loads(self.run_cli("stats", "Home", "--json")[0]), stats.summarize(self.board("Home")))

    def test_a_board_with_nothing_on_it(self):
        self.run_cli("new", "Empty")
        self.assertEqual(self.lines("stats", "Empty"), [
            "Empty: 0 cards", "", "Column       Cards  Limit", "Backlog          0", "Ready            0",
            "In progress      0      1", "Review           0", "Done             0  done"])
        self.unchanged("stats", "Nope", says="There's no board called 'Nope'.")


class Outline(unittest.TestCase):
    def test_counts_leave_archived_cards_out_of_columns(self):
        with TempStore() as store:
            board = store.import_data({"name": "A", "columns": ["One", "Two"], "labels": ["L"],
                                       "properties": [{"name": "P", "type": "text"}],
                                       "cards": [{"title": "x", "column": "Two"}, {"title": "y", "archived": True}]})[0]
        self.assertEqual(outline(board), {"name": "A", "columns": [{"name": "One", "cards": 0}, {"name": "Two", "cards": 1}],
                                          "cards": 2, "archived": 1, "labels": ["L"],
                                          "properties": [{"name": "P", "type": "text"}]})
