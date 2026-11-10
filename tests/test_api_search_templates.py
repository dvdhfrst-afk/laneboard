"""The JSON API for saved views, search and templates."""
import http.client
import json
import os
import tempfile
import threading
import unittest

from laneboard.server import BoardServer
from laneboard.store import Store


class ApiCase(unittest.TestCase):
    """A server on a free port with a file of its own, made fresh for each test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "boards.json")
        self.store = Store(self.path, starter=False)
        self.server = BoardServer(("127.0.0.1", 0), self.store)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        sent = {"Host": f"localhost:{self.port}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            sent["Content-Type"] = "application/json"
        sent.update(headers or {})
        conn.request(method, path, body=data, headers=sent)
        res = conn.getresponse()
        payload = json.loads(res.read())
        conn.close()
        return res.status, payload

    def ok(self, method, path, body=None, status=200, headers=None):
        got, payload = self.request(method, path, body, headers)
        self.assertEqual(got, status, payload)
        return payload

    def work_board(self):
        board = self.ok("POST", "/api/boards", {"name": "Work"}, 201)["board"]
        return self.ok("PATCH", f"/api/boards/{board['id']}", {
            "labels": ["Bug", "Docs"], "properties": [{"name": "Points", "type": "number"}]})["board"]


class SavedViews(ApiCase):
    def test_a_view_that_does_not_read_is_400_with_its_place(self):
        board = self.work_board()
        status, payload = self.request("PATCH", f"/api/boards/{board['id']}",
                                       {"views": [{"name": "Big", "query": "bug pionts:>3"}]}, {"If-Match": str(board["rev"])})
        self.assertEqual(status, 400)
        self.assertEqual(payload, {"error": 'The saved view "Big" can\'t be saved: There\'s no property called "pionts". '
                                            "Properties: Points.", "position": 4, "part": "query"})
        status, payload = self.request("PATCH", f"/api/boards/{board['id']}", {"views": [{"name": "Big", "sort": "-size"}]})
        self.assertEqual((status, payload["position"], payload["part"]), (400, 1, "sort"))
        self.assertEqual(self.store.get_board(board["id"])["rev"], board["rev"])

    def test_other_errors_keep_their_shape(self):
        board = self.work_board()
        status, payload = self.request("PATCH", f"/api/boards/{board['id']}", {"views": "x"})
        self.assertEqual((status, payload), (400, {"error": "Saved views must be a list."}))


class Search(ApiCase):
    def setUp(self):
        super().setUp()
        self.board = self.work_board()["id"]
        for title, values in (("Login page crashes", {"labels": ["Bug"], "properties": {"points": 5}}),
                              ("Write the guide", {"labels": ["Docs"], "properties": {"points": 2}}),
                              ("Old login bug", {"labels": ["Bug"]})):
            self.store.create_card(self.board, {"title": title, **values})
        self.store.update_card(self.board, "T-3", {"archived": True})
        self.other = self.store.create_board("Home")["id"]
        self.store.create_card(self.other, {"title": "Login to the bank"})

    def search(self, query="", board=None):
        return self.ok("GET", f"/api/boards/{board or self.board}/search" + (f"?{query}" if query else ""))

    def test_one_board(self):
        self.assertEqual(self.search("q=login"), {"keys": ["T-1"], "total": 2, "matched": 1})
        self.assertEqual(self.search(), {"keys": ["T-1", "T-2"], "total": 2, "matched": 2})
        self.assertEqual(self.search("q=login&archived=1"), {"keys": ["T-1", "T-3"], "total": 3, "matched": 2})
        self.assertEqual(self.search("q=is%3Aarchived"), {"keys": ["T-3"], "total": 3, "matched": 1})
        self.assertEqual(self.search("q=&sort=-points")["keys"], ["T-1", "T-2"])
        self.assertEqual(self.search("q=sort:points")["keys"], ["T-2", "T-1"])
        self.assertEqual(self.search("q=label%3Abug+OR+label%3Adocs&sort=title")["keys"], ["T-1", "T-2"])
        self.assertEqual(self.search("q=%22the+guide%22")["keys"], ["T-2"])
        self.assertEqual(self.search("q=%C3%A9crire")["keys"], [], "decoded as UTF-8")

    def test_errors_say_where(self):
        for query, position, part in (("q=bug+pionts%3A3", 4, "query"), ("q=%22open", 0, "query"),
                                      ("sort=title,-size", 7, "sort"), ("q=sort:-size", 6, "query")):
            with self.subTest(query=query):
                status, payload = self.request("GET", f"/api/boards/{self.board}/search?{query}")
                self.assertEqual(status, 400)
                self.assertEqual(set(payload), {"error", "position", "part"})
                self.assertEqual((payload["position"], payload["part"]), (position, part))
        status, payload = self.request("GET", f"/api/boards/{self.board}/search?q=bug+pionts%3A3")
        self.assertEqual(payload["error"], 'There\'s no property called "pionts". Properties: Points.')

    def test_bad_requests(self):
        for query, phrase in (("archived=2", "archived must be 0"), ("q=a&q=b", "Give q only once."),
                              ("&".join(f"x{n}=1" for n in range(30)), "too many parameters")):
            with self.subTest(query=query[:20]):
                status, payload = self.request("GET", f"/api/boards/{self.board}/search?{query}")
                self.assertEqual(status, 400)
                self.assertIn(phrase, payload["error"])
        status, _ = self.request("GET", "/api/boards/b-nothere/search?q=x")
        self.assertEqual(status, 404)
        status, _ = self.request("POST", f"/api/boards/{self.board}/search?q=x", {})
        self.assertEqual(status, 405)

    def test_every_board(self):
        self.assertEqual(self.ok("GET", "/api/search?q=login"), {"results": [
            {"board": self.board, "name": "Work", "keys": ["T-1"], "total": 2},
            {"board": self.other, "name": "Home", "keys": ["T-1"], "total": 1}]})
        self.assertEqual(self.ok("GET", "/api/search?q=label:bug"), {"results": [
            {"board": self.board, "name": "Work", "keys": ["T-1"], "total": 2}]})
        self.assertEqual(self.ok("GET", "/api/search?q=label:bug&archived=1")["results"][0]["keys"], ["T-1", "T-3"])
        self.assertEqual(self.ok("GET", "/api/search?q=nowhere"), {"results": []})
        self.assertEqual(self.ok("GET", "/api/search?q=&sort=-points")["results"][1]["keys"], ["T-1"])
        status, payload = self.request("GET", "/api/search?q=label:bgu")
        self.assertEqual((status, payload), (400, {"error": 'There\'s no label called "bgu". Labels: Bug, Docs.',
                                                   "position": 6, "part": "query"}))

    def test_search_needs_no_rev_and_changes_nothing(self):
        before = self.store.get_board(self.board)["rev"]
        self.search("q=login")
        self.ok("GET", "/api/search?q=login")
        self.assertEqual(self.store.get_board(self.board)["rev"], before)


if __name__ == "__main__":
    unittest.main()
