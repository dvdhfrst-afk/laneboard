"""A small HTTP server for the board: the page and its static files, plus a JSON API.

It only answers requests addressed to this machine (localhost, 127.0.0.1 or ::1), and refuses
changes sent from other web pages, so a site open in another tab can't edit your boards.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from . import __version__
from .store import Store, StoreError

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MAX_BODY = 1_000_000
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")

ROUTES = [
    ("GET", re.compile(r"^/api/health$"), "health"),
    ("GET", re.compile(r"^/api/boards$"), "list_boards"),
    ("POST", re.compile(r"^/api/boards$"), "create_board"),
    ("GET", re.compile(r"^/api/boards/(?P<board>[\w-]+)$"), "get_board"),
    ("PATCH", re.compile(r"^/api/boards/(?P<board>[\w-]+)$"), "update_board"),
    ("DELETE", re.compile(r"^/api/boards/(?P<board>[\w-]+)$"), "delete_board"),
    ("POST", re.compile(r"^/api/boards/(?P<board>[\w-]+)/cards$"), "create_card"),
    ("PATCH", re.compile(r"^/api/boards/(?P<board>[\w-]+)/cards/(?P<key>[A-Z][A-Z0-9]*-\d+)$"), "update_card"),
    ("DELETE", re.compile(r"^/api/boards/(?P<board>[\w-]+)/cards/(?P<key>[A-Z][A-Z0-9]*-\d+)$"), "delete_card"),
]


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Handler(BaseHTTPRequestHandler):
    server_version = "laneboard/" + __version__
    protocol_version = "HTTP/1.1"

    @property
    def store(self) -> Store:
        return self.server.store

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")

    # ---- request handling ----------------------------------------------------------------------

    def _handle(self, method):
        try:
            self._check_host()
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                if method != "GET":
                    self._check_origin()
                self._api(method, path)
            elif method == "GET":
                self._static(path)
            else:
                raise HttpError(405, "Only GET works outside /api/.")
        except HttpError as e:
            self._json(e.status, {"error": str(e)})
        except StoreError as e:
            self._json(e.status, {"error": str(e)})

    def _check_host(self):
        host = (self.headers.get("Host") or "").lower()
        name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        if name not in LOCAL_HOSTS:
            raise HttpError(403, "This server only answers requests for localhost.")

    def _check_origin(self):
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc.lower() != (self.headers.get("Host") or "").lower():
            raise HttpError(403, "Changes are only accepted from the board's own page.")

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise HttpError(400, "Content-Length must be a number.")
        if length > MAX_BODY:
            # Read a moderately oversized body so the client sees the 413 instead of a reset connection.
            remaining = length if length <= 8 * MAX_BODY else 0
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
            raise HttpError(413, "That request is too large.")
        if not length:
            return {}
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            raise HttpError(415, "Send changes as application/json.")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise HttpError(400, "The request body isn't valid JSON.")
        if not isinstance(body, dict):
            raise HttpError(400, "The request body must be a JSON object.")
        return body

    def _rev(self):
        value = self.headers.get("If-Match")
        if value is None:
            return None
        try:
            return int(value.strip('"'))
        except ValueError:
            raise HttpError(400, "If-Match must be the board's revision number.")

    def _api(self, method, path):
        matched = False
        for route_method, pattern, name in ROUTES:
            m = pattern.match(path)
            if not m:
                continue
            matched = True
            if route_method == method:
                status, payload = getattr(self, "api_" + name)(**m.groupdict())
                self._json(status, payload)
                return
        if matched:
            raise HttpError(405, f"{method} isn't allowed on {path}.")
        raise HttpError(404, "No such API route.")

    # ---- API -----------------------------------------------------------------------------------

    def api_health(self):
        return 200, {"app": "laneboard", "version": __version__}

    def api_list_boards(self):
        return 200, {"boards": self.store.list_boards()}

    def api_create_board(self):
        body = self._body()
        return 201, {"board": self.store.create_board(body.get("name"), body.get("description", ""))}

    def api_get_board(self, board):
        return 200, {"board": self.store.get_board(board)}

    def api_update_board(self, board):
        return 200, {"board": self.store.update_board(board, self._body(), rev=self._rev())}

    def api_delete_board(self, board):
        self.store.delete_board(board, rev=self._rev())
        return 200, {"deleted": board}

    def api_create_card(self, board):
        updated, key = self.store.create_card(board, self._body(), rev=self._rev())
        return 201, {"board": updated, "key": key}

    def api_update_card(self, board, key):
        return 200, {"board": self.store.update_card(board, key, self._body(), rev=self._rev())}

    def api_delete_card(self, board, key):
        return 200, {"board": self.store.delete_card(board, key, rev=self._rev())}

    # ---- responses -----------------------------------------------------------------------------

    def _static(self, path):
        name = "index.html" if path in ("/", "/index.html") else unquote(path[len("/static/"):]) if path.startswith("/static/") else None
        if not name or not re.match(r"^[a-z0-9][a-z0-9_.-]*$", name):
            raise HttpError(404, "Not found.")
        full = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(full):
            raise HttpError(404, "Not found.")
        with open(full, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "text/javascript"):
            ctype += "; charset=utf-8"
        self._send(200, data, ctype, extra={"Cache-Control": "no-cache"})

    def _json(self, status, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8",
                   extra={"Cache-Control": "no-store"})

    def _send(self, status, data, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        if status >= 400:
            # The request body may be unread; don't reuse the connection.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


class BoardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store: Store, verbose=False):
        super().__init__(address, Handler)
        self.store = store
        self.verbose = verbose
