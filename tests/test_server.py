import http.client
import json
import os
import tempfile
import threading
import unittest

from laneboard.server import MAX_BODY, BoardServer
from laneboard.store import Store


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(os.path.join(cls.tmp.name, "boards.json"))
        cls.server = BoardServer(("127.0.0.1", 0), cls.store)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = {"Host": f"localhost:{self.port}"}
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
        return res.status, (json.loads(payload) if ctype.startswith("application/json") else payload), res

    def new_board(self):
        status, data, _ = self.request("POST", "/api/boards", {"name": "Test board"})
        self.assertEqual(status, 201)
        return data["board"]

    def test_page_and_static_files(self):
        status, body, res = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Laneboard</title>", body)
        self.assertIn("default-src 'none'", res.getheader("Content-Security-Policy"))
        self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff")
        for name in ("app.js", "app.css", "icon.svg"):
            status, _, _ = self.request("GET", "/static/" + name)
            self.assertEqual(status, 200, name)

    def test_static_paths_cannot_leave_the_static_folder(self):
        for path in ("/static/../store.py", "/static/%2e%2e/store.py", "/static/..%5cstore.py", "/static/", "/store.py"):
            status, _, _ = self.request("GET", path)
            self.assertEqual(status, 404, path)

    def test_health(self):
        status, data, _ = self.request("GET", "/api/health")
        self.assertEqual((status, data["app"]), (200, "laneboard"))

    def test_board_and_card_round_trip(self):
        board = self.new_board()
        path = f"/api/boards/{board['id']}"
        status, data, _ = self.request("POST", path + "/cards", {"title": "First"}, {"If-Match": str(board["rev"])})
        self.assertEqual((status, data["key"]), (201, "T-1"))
        rev = data["board"]["rev"]
        status, data, _ = self.request("PATCH", path + "/cards/T-1", {"column": "done"}, {"If-Match": str(rev)})
        self.assertEqual(status, 200)
        self.assertEqual(data["board"]["cards"][0]["column"], "done")
        status, data, _ = self.request("GET", "/api/boards")
        self.assertIn(board["id"], [b["id"] for b in data["boards"]])
        status, data, _ = self.request("DELETE", path + "/cards/T-1")
        self.assertEqual((status, data["board"]["cards"]), (200, []))
        status, data, _ = self.request("DELETE", path)
        self.assertEqual((status, data["deleted"]), (200, board["id"]))

    def test_stale_revision_gets_412(self):
        board = self.new_board()
        path = f"/api/boards/{board['id']}"
        self.request("POST", path + "/cards", {"title": "Moves the revision on"})
        status, data, _ = self.request("POST", path + "/cards", {"title": "Late"}, {"If-Match": str(board["rev"])})
        self.assertEqual(status, 412)
        self.assertIn("changed", data["error"])

    def test_bad_if_match_is_400(self):
        board = self.new_board()
        status, _, _ = self.request("PATCH", f"/api/boards/{board['id']}", {"name": "X"}, {"If-Match": "abc"})
        self.assertEqual(status, 400)

    def test_validation_errors_are_400_with_a_message(self):
        board = self.new_board()
        status, data, _ = self.request("POST", f"/api/boards/{board['id']}/cards", {"title": ""})
        self.assertEqual(status, 400)
        self.assertIn("Title", data["error"])

    def test_removing_a_column_with_cards_is_409(self):
        board = self.new_board()
        path = f"/api/boards/{board['id']}"
        self.request("POST", path + "/cards", {"title": "Sits in Backlog"})
        status, _, _ = self.request("PATCH", path, {"columns": board["columns"][1:]})
        self.assertEqual(status, 409)

    def test_unknown_board_is_404(self):
        status, _, _ = self.request("GET", "/api/boards/b-nothere")
        self.assertEqual(status, 404)

    def test_unknown_route_and_wrong_method(self):
        self.assertEqual(self.request("GET", "/api/nothing")[0], 404)
        self.assertEqual(self.request("PUT", "/api/boards")[0], 405)
        self.assertEqual(self.request("DELETE", "/api/boards")[0], 405)
        self.assertEqual(self.request("POST", "/index.html", {"a": 1})[0], 405)

    def test_other_hosts_are_refused(self):
        for host in ("example.com", "localhost.example.com", f"192.168.1.5:{self.port}"):
            status, _, _ = self.request("GET", "/api/health", headers={"Host": host})
            self.assertEqual(status, 403, host)
        for host in ("127.0.0.1", f"[::1]:{self.port}"):
            status, _, _ = self.request("GET", "/api/health", headers={"Host": host})
            self.assertEqual(status, 200, host)

    def test_changes_from_other_origins_are_refused(self):
        status, _, _ = self.request("POST", "/api/boards", {"name": "Sneaky"}, {"Origin": "https://example.com"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/boards", {"name": "Fine"}, {"Origin": f"http://localhost:{self.port}"})
        self.assertEqual(status, 201)

    def test_changes_must_be_json(self):
        status, _, _ = self.request("POST", "/api/boards", raw=b"name=Form",
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)
        status, _, _ = self.request("POST", "/api/boards", raw=b"{nope", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        status, _, _ = self.request("POST", "/api/boards", raw=b"[1]", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)

    def test_large_bodies_are_refused(self):
        status, _, _ = self.request("POST", "/api/boards", raw=b"x" * (MAX_BODY + 1), headers={"Content-Type": "application/json"})
        self.assertEqual(status, 413)


if __name__ == "__main__":
    unittest.main()
