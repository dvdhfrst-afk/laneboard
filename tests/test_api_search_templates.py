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


if __name__ == "__main__":
    unittest.main()
