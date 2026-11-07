"""The server has no sign-in, so these tests check who it talks to and what it does with odd requests."""
import http.client
import json
import os
import random
import socket
import tempfile
import threading
import unittest
from unittest import mock

from laneboard import server as server_module
from laneboard.schema import check_data
from laneboard.server import MAX_BODY, MAX_IMPORT, BoardServer
from laneboard.store import Store


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "boards.json")
        cls.store = Store(cls.path)
        cls.server = BoardServer(("127.0.0.1", 0), cls.store)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.board = cls.store.list_boards()[0]["id"]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Host": f"127.0.0.1:{self.port}"}
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        conn.request(method, path, body=data, headers=hdrs)
        res = conn.getresponse()
        payload = res.read()
        conn.close()
        ctype = res.getheader("Content-Type") or ""
        return res.status, (json.loads(payload) if ctype.startswith("application/json") and payload else payload), res


class CrossSiteTest(ServerCase):
    def boards(self):
        return len(self.store.list_boards())

    def test_other_sites_cannot_change_anything(self):
        before = self.boards()
        for origin in ("https://evil.example", "http://127.0.0.1:1", "http://localhost.evil.example", "null"):
            for method, path, body in (("POST", "/api/boards", {"name": "Sneaky"}),
                                       ("PATCH", f"/api/boards/{self.board}", {"name": "Sneaky"}),
                                       ("DELETE", f"/api/boards/{self.board}", None),
                                       ("POST", "/api/import", {"name": "Sneaky"}),
                                       ("POST", "/api/samples/garden", None)):
                status, _, _ = self.request(method, path, body, {"Origin": origin})
                self.assertEqual(status, 403, (origin, method, path))
        self.assertEqual(self.boards(), before)

    def test_browsers_saying_cross_site_are_refused_even_for_reads(self):
        for site in ("cross-site", "same-site"):
            for method, path in (("GET", "/api/boards"), ("GET", f"/api/boards/{self.board}"), ("GET", "/api/export"),
                                 ("GET", f"/api/boards/{self.board}/export")):
                status, _, _ = self.request(method, path, headers={"Sec-Fetch-Site": site})
                self.assertEqual(status, 403, (site, path))

    def test_own_page_and_typed_urls_work(self):
        for site in ("same-origin", "none"):
            self.assertEqual(self.request("GET", "/api/boards", headers={"Sec-Fetch-Site": site})[0], 200)
        status, _, _ = self.request("GET", "/", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 200, "following a link to the page from elsewhere is fine; it carries no data")

    def test_form_posts_are_refused(self):
        for ctype in ("application/x-www-form-urlencoded", "multipart/form-data; boundary=x", "text/plain"):
            status, _, _ = self.request("POST", "/api/boards", raw=b'{"name":"Sneaky"}', headers={"Content-Type": ctype})
            self.assertEqual(status, 415, ctype)

    def test_no_cors_headers_and_preflight_fails(self):
        status, _, res = self.request("OPTIONS", "/api/boards", headers={
            "Origin": "https://evil.example", "Access-Control-Request-Method": "PATCH"})
        self.assertGreaterEqual(status, 400)
        self.assertIsNone(res.getheader("Access-Control-Allow-Origin"))
        _, _, res = self.request("GET", "/api/boards")
        self.assertIsNone(res.getheader("Access-Control-Allow-Origin"))


class DnsRebindingTest(ServerCase):
    def test_other_host_names_are_refused_everywhere(self):
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1.evil.example", "localhost.evil.example:80",
                     f"192.168.1.5:{self.port}", "0.0.0.0", "[::ffff:127.0.0.1]", ""):
            for path in ("/", "/static/app.js", "/api/health", "/api/boards"):
                status, _, _ = self.request("GET", path, headers={"Host": host})
                self.assertEqual(status, 403, (host, path))

    def test_this_machine_works(self):
        for host in ("127.0.0.1", f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"LOCALHOST:{self.port}", f"[::1]:{self.port}", "[::1]"):
            self.assertEqual(self.request("GET", "/api/health", headers={"Host": host})[0], 200, host)

    def test_it_only_listens_on_loopback(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")


class ResponseHeaderTest(ServerCase):
    def test_every_response_carries_the_protections(self):
        for path in ("/", "/static/app.js", "/api/boards", "/api/nothing", "/nothing"):
            _, _, res = self.request("GET", path)
            csp = res.getheader("Content-Security-Policy")
            self.assertIn("default-src 'none'", csp, path)
            self.assertIn("script-src 'self'", csp, path)
            self.assertNotIn("unsafe", csp, path)
            self.assertIn("frame-ancestors 'none'", csp, path)
            self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff", path)
            self.assertEqual(res.getheader("X-Frame-Options"), "DENY", path)
            self.assertEqual(res.getheader("Referrer-Policy"), "no-referrer", path)
            self.assertEqual(res.getheader("Cross-Origin-Resource-Policy"), "same-origin", path)

    def test_content_types_do_not_depend_on_the_system(self):
        with mock.patch("mimetypes.guess_type", return_value=("text/plain", None)):
            for name, want in (("app.js", "text/javascript"), ("app.css", "text/css"), ("icon.svg", "image/svg+xml")):
                _, _, res = self.request("GET", "/static/" + name)
                self.assertTrue(res.getheader("Content-Type").startswith(want), name)

    def test_api_answers_are_not_cached(self):
        _, _, res = self.request("GET", "/api/boards")
        self.assertEqual(res.getheader("Cache-Control"), "no-store")


class PathTest(ServerCase):
    def test_nothing_outside_the_static_folder_is_served(self):
        for path in ("/static/../store.py", "/static/%2e%2e/store.py", "/static/..%2fstore.py", "/static/..%5cstore.py",
                     "/static/....//store.py", "/static/.htaccess", "/static/app.js%00.png", "/static/app.js%0a",
                     "/static/", "/static/app", "/store.py", "/laneboard/store.py", "/%2e%2e/%2e%2e/LICENSE",
                     "//evil.example/x", "/static/con.js", "/static/aux", "/static//app.js",
                     "/static/APP.JS", "/static/__init__.py"):
            status, body, _ = self.request("GET", path)
            self.assertEqual(status, 404, path)
            self.assertNotIn(b"import", body if isinstance(body, bytes) else b"", path)

    def test_routes_match_whole_paths(self):
        for path in (f"/api/boards/{self.board}/", f"/api/boards/{self.board}/cards/", "/api/boards/../boards",
                     f"/api/boards/{self.board}%0a", "/api/boards/a/b/c/d"):
            self.assertIn(self.request("GET", path)[0], (404, 405), path)

    def test_export_file_name_cannot_carry_headers(self):
        board = self.store.create_board("Evil\r\nX-Injected: yes\r\n\"name;.json")
        status, data, res = self.request("GET", f"/api/boards/{board['id']}/export")
        self.assertEqual(status, 200)
        self.assertIsNone(res.getheader("X-Injected"))
        self.assertRegex(res.getheader("Content-Disposition"), r'^attachment; filename="[a-z0-9-]+\.json"')
        self.assertEqual(data["name"], "Evil X-Injected: yes \"name;.json")
        self.store.delete_board(board["id"])


class BadRequestTest(ServerCase):
    def raw(self, data: bytes) -> bytes:
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            s.sendall(data)
            s.shutdown(socket.SHUT_WR)
            out = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    return out
                out += chunk

    def test_malformed_requests_get_a_plain_answer(self):
        for data in (b"NONSENSE\r\n\r\n", b"GET / HTTP/9.9\r\n\r\n", b"GET\r\n\r\n", b"\xff\xfe\x00garbage\r\n\r\n",
                     b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\n\r\n"):
            out = self.raw(data)
            self.assertIn(b"error", out, data[:20])
            self.assertNotIn(b"Traceback", out)

    def test_odd_content_lengths(self):
        status, _, _ = self.request("POST", "/api/boards", raw=b"", headers={"Content-Length": "abc"})
        self.assertEqual(status, 400)
        status, _, _ = self.request("POST", "/api/boards", raw=b"", headers={"Content-Length": "-5"})
        self.assertEqual(status, 400)

    def test_json_that_is_not_what_we_want(self):
        ctype = {"Content-Type": "application/json"}
        for raw in (b"{nope", b"[1]", b'"text"', b"5", b"null", b"\xff\xfe", b'{"name": NaN}', b'{"name": Infinity}',
                    b"[" * 100000, b'{"a":' * 50000):
            status, data, _ = self.request("POST", "/api/boards", raw=raw, headers=ctype)
            self.assertEqual(status, 400, raw[:20])
            self.assertIn("error", data)

    def test_limits(self):
        ctype = {"Content-Type": "application/json"}
        self.assertEqual(self.request("POST", "/api/boards", raw=b"x" * (MAX_BODY + 1), headers=ctype)[0], 413)
        self.assertGreater(MAX_IMPORT, MAX_BODY)
        with mock.patch.object(server_module, "MAX_IMPORT", 1000):
            status, data, _ = self.request("POST", "/api/import", raw=b"x" * 2000, headers=ctype)
        self.assertEqual(status, 413)
        self.assertIn("too large", data["error"])

    def test_an_import_can_be_bigger_than_a_normal_change(self):
        cards = [{"title": f"Card {n}", "notes": "n" * 4000} for n in range(1200)]
        status, data, _ = self.request("POST", "/api/import", {"name": "Larger than a normal change", "cards": cards})
        self.assertEqual(status, 201)
        self.assertEqual(data["boards"][0]["cards"], 1200)
        self.store.delete_board(data["boards"][0]["id"])

    def test_lone_surrogates_in_a_request(self):
        raw = b'{"title": "half \\ud83d emoji"}'
        status, data, _ = self.request("POST", f"/api/boards/{self.board}/cards", raw=raw, headers={"Content-Type": "application/json"})
        self.assertEqual(status, 201)
        key = data["key"]
        self.assertEqual(next(c for c in data["board"]["cards"] if c["key"] == key)["title"], "half � emoji")
        self.request("DELETE", f"/api/boards/{self.board}/cards/{key}")

    def test_an_unexpected_failure_is_a_plain_500_with_no_details(self):
        with mock.patch.object(self.store, "list_boards", side_effect=ValueError("secret detail")), \
                mock.patch("traceback.print_exc"):
            status, data, _ = self.request("GET", "/api/boards")
        self.assertEqual(status, 500)
        self.assertIn("internal error", data["error"])
        self.assertNotIn("secret", data["error"])

    def test_junk_sent_to_every_route_never_breaks_the_file(self):
        rng = random.Random(7)
        atoms = [None, True, 0, -1, 1.5, "", "x", "<b>", [], {}, ["x"], {"a": 1}, [{}], 1e20]
        fields = ["name", "columns", "groups", "checklists", "template", "cards", "title", "column", "group", "rank", "notes",
                  "fields", "sections", "checks", "items", "text", "id", "limit", "color", "link", "details", "label", "archived",
                  "moveCards", "prefix", "groupLabel"]

        def junk(depth=0):
            if depth > 2 or rng.random() < 0.4:
                return rng.choice(atoms)
            if rng.random() < 0.3:
                return [junk(depth + 1) for _ in range(rng.randint(0, 3))]
            return {rng.choice(fields): junk(depth + 1) for _ in range(rng.randint(1, 4))}

        board = self.store.create_board("Junk target")
        path = f"/api/boards/{board['id']}"
        _, data, _ = self.request("POST", path + "/cards", {"title": "Target"})
        key = data["key"]
        routes = [("PATCH", path), ("POST", path + "/cards"), ("PATCH", f"{path}/cards/{key}"), ("POST", "/api/import"), ("POST", "/api/boards")]
        for _ in range(150):
            for method, route in routes:
                status, _, _ = self.request(method, route, junk() if rng.random() < 0.9 else {"x": 1})
                self.assertLess(status, 500, route)
        for b in self.store.list_boards():
            if b["id"] not in (self.board, board["id"]):
                self.store.delete_board(b["id"])
        with open(self.path, encoding="utf-8") as f:
            check_data(json.load(f), "after the junk")
        self.store.delete_board(board["id"])


class BrokenFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "boards.json")
        self.store = Store(self.path)
        self.server = BoardServer(("127.0.0.1", 0), self.store)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_a_broken_file_gets_a_503_and_the_health_check_still_answers(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ nope")
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        conn.request("GET", "/api/boards", headers={"Host": "localhost"})
        res = conn.getresponse()
        data = json.loads(res.read())
        self.assertEqual(res.status, 503)
        self.assertIn("isn't valid JSON", data["error"])
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        conn.request("GET", "/api/health", headers={"Host": "localhost"})
        res = conn.getresponse()
        data = json.loads(res.read())
        self.assertEqual((res.status, data["app"]), (200, "laneboard"))
        self.assertEqual(data["data"], self.store.path)
        conn.close()


if __name__ == "__main__":
    unittest.main()
