"""A small HTTP server for the board: the page and its static files, plus a JSON API.

There is no sign-in, so the server has to be sure a request really comes from the person at this
computer. It listens on 127.0.0.1 only, answers only requests addressed to this machine (which stops
DNS rebinding), and refuses API calls sent from other web pages (which stops cross-site requests).
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, quote, urlsplit

from . import __version__, samples, storage
from .query import QueryError, run, search_boards
from .schema import SCHEMA_VERSION, slug
from .store import Store, StoreError

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MAX_BODY = 4_000_000
MAX_IMPORT = 64_000_000
MAX_DRAIN = 2 * MAX_IMPORT
MAX_PARAMS = 20  # in a query string
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")
CONTENT_TYPES = {  # not mimetypes: on Windows it reads the registry, which can call .js text/plain, and the page dies
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
INTERNAL_ERROR = ("Laneboard hit an internal error and this request didn't finish. Reload the page to see what "
                  "was saved. The terminal running Laneboard has the details.")
BOARD = r"(?P<board>[\w-]+)"
KEY = r"(?P<key>[A-Z][A-Z0-9]*-\d+)"
TASK = r"(?P<task>[a-z0-9][a-z0-9-]{0,39})"

ROUTES = [
    ("GET", re.compile(r"/api/health"), "health"),
    ("GET", re.compile(r"/api/boards"), "list_boards"),
    ("POST", re.compile(r"/api/boards"), "create_board"),
    ("GET", re.compile(r"/api/export"), "export_all"),
    ("POST", re.compile(r"/api/import"), "import_file"),
    ("GET", re.compile(r"/api/search"), "search_all"),
    ("GET", re.compile(r"/api/samples"), "list_samples"),
    ("POST", re.compile(r"/api/samples/(?P<name>[a-z0-9-]+)"), "add_sample"),
    ("GET", re.compile(rf"/api/boards/{BOARD}"), "get_board"),
    ("PATCH", re.compile(rf"/api/boards/{BOARD}"), "update_board"),
    ("DELETE", re.compile(rf"/api/boards/{BOARD}"), "delete_board"),
    ("GET", re.compile(rf"/api/boards/{BOARD}/export"), "export_board"),
    ("GET", re.compile(rf"/api/boards/{BOARD}/search"), "search_board"),
    ("POST", re.compile(rf"/api/boards/{BOARD}/cards"), "create_card"),
    ("PATCH", re.compile(rf"/api/boards/{BOARD}/cards/{KEY}"), "update_card"),
    ("DELETE", re.compile(rf"/api/boards/{BOARD}/cards/{KEY}"), "delete_card"),
    ("POST", re.compile(rf"/api/boards/{BOARD}/cards/{KEY}/tasks"), "add_task"),
    ("PATCH", re.compile(rf"/api/boards/{BOARD}/cards/{KEY}/tasks/{TASK}"), "update_task"),
    ("DELETE", re.compile(rf"/api/boards/{BOARD}/cards/{KEY}/tasks/{TASK}"), "delete_task"),
    ("POST", re.compile(rf"/api/boards/{BOARD}/cards/{KEY}/transfer"), "transfer_card"),
]


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def encode_json(payload, indent=None) -> bytes:
    # backslashreplace: a lone surrogate from a hand-edited file becomes its JSON escape instead of a crash.
    separators = None if indent else (",", ":")
    return json.dumps(payload, ensure_ascii=False, indent=indent, separators=separators).encode("utf-8", "backslashreplace")


class Handler(BaseHTTPRequestHandler):
    server_version = "laneboard/" + __version__
    protocol_version = "HTTP/1.1"
    timeout = 120  # a stalled connection shouldn't hold a thread forever

    @property
    def store(self) -> Store:
        return self.server.store

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False) and sys.stderr:
            super().log_message(fmt, *args)

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_PUT(self):
        self._handle("PUT")

    def do_OPTIONS(self):  # no CORS headers, so a browser's preflight from another site fails
        self._handle("OPTIONS")

    def send_error(self, code, message=None, explain=None):
        """The standard library's own errors (a malformed request, say) leave as JSON too."""
        self._json(code, {"error": message or self.responses.get(code, ("Request refused.",))[0]})

    # ---- request handling ----------------------------------------------------------------------

    def _handle(self, method):
        self._body_read = False
        try:
            self._check_host()
            parts = urlsplit(self.path)
            path, self._query = parts.path, parts.query
            if path.startswith("/api/"):
                self._check_fetch_site()
                if method not in ("GET", "HEAD"):
                    self._check_origin()
                self._api("GET" if method == "HEAD" else method, path)
            elif method in ("GET", "HEAD"):
                self._static(path)
            else:
                raise HttpError(405, "Only GET works outside /api/.")
        except HttpError as e:
            self._json(e.status, {"error": str(e)})
        except QueryError as e:  # a search, or a saved view's: say where, so the page can point at it
            self._json(e.status, {"error": str(e), "position": e.position, "part": e.part})
        except StoreError as e:
            self._json(e.status, {"error": str(e)})
        except (ConnectionError, socket.timeout):
            self.close_connection = True  # the browser went away; nobody to tell
        except Exception:
            if sys.stderr:
                traceback.print_exc()
            self._json(500, {"error": INTERNAL_ERROR})

    def _check_host(self):
        host = (self.headers.get("Host") or "").lower()
        name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        if name not in LOCAL_HOSTS:
            raise HttpError(403, "This server only answers requests for localhost.")

    def _check_fetch_site(self):
        # Browsers name where a request comes from. "same-site" isn't enough: every port on 127.0.0.1 is the same site.
        if (self.headers.get("Sec-Fetch-Site") or "same-origin").lower() not in ("same-origin", "none"):
            raise HttpError(403, "Only the board's own page can use this API.")

    def _check_origin(self):
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc.lower() != (self.headers.get("Host") or "").lower():
            raise HttpError(403, "Changes are only accepted from the board's own page.")

    def _body(self, limit=MAX_BODY):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise HttpError(400, "Content-Length must be a number.")
        if length < 0:
            raise HttpError(400, "Content-Length must be a number.")
        if length > limit:
            raise HttpError(413, f"That request is too large (the limit is {limit // 1_000_000} MB).")
        if not length:
            return {}
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            raise HttpError(415, "Send changes as application/json.")
        try:
            data = self.rfile.read(length)
            self._body_read = True
            body = json.loads(data.decode("utf-8-sig"), parse_constant=self._no_constant)
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise HttpError(400, "The request body isn't valid JSON.")
        if not isinstance(body, dict):
            raise HttpError(400, "The request body must be a JSON object.")
        return body

    @staticmethod
    def _no_constant(name):
        raise ValueError(name)

    def _param(self, name, default=""):
        """One value from the query string, decoded. A name given twice is refused rather than guessed at."""
        try:
            values = parse_qs(self._query, keep_blank_values=True, max_num_fields=MAX_PARAMS).get(name)
        except ValueError:
            raise HttpError(400, f"That address has too many parameters (the limit is {MAX_PARAMS}).")
        if not values:
            return default
        if len(values) > 1:
            raise HttpError(400, f"Give {name} only once.")
        return values[0]

    def _search_params(self):
        """q, sort and archived, as the search routes take them."""
        archived = self._param("archived", "0").strip().lower()
        if archived not in ("0", "1", "false", "true", ""):
            raise HttpError(400, "archived must be 0 (leave archived cards out) or 1 (include them).")
        return self._param("q"), self._param("sort"), archived in ("1", "true")

    def _rev(self):
        value = self.headers.get("If-Match")
        if value is None:
            return None
        try:
            return int(value.strip().strip('"'))
        except ValueError:
            raise HttpError(400, "If-Match must be the board's revision number.")

    def _api(self, method, path):
        matched = False
        for route_method, pattern, name in ROUTES:
            m = pattern.fullmatch(path)
            if not m:
                continue
            matched = True
            if route_method == method:
                status, payload, *extra = getattr(self, "api_" + name)(**m.groupdict())
                self._json(status, payload, *extra)
                return
        if matched:
            raise HttpError(405, f"{method} isn't allowed on {path}.")
        raise HttpError(404, "No such API route.")

    # ---- API -----------------------------------------------------------------------------------

    def api_health(self):
        # Doesn't touch the boards file: this has to answer even when the file is broken.
        path = self.store.path
        return 200, {"app": "laneboard", "version": __version__, "schema": SCHEMA_VERSION, "data": path,
                     "backups": storage.backups_folder(path)}

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

    def api_add_task(self, board, key):
        updated, task = self.store.add_task(board, key, self._body(), rev=self._rev())
        return 201, {"board": updated, "task": task}

    def api_update_task(self, board, key, task):
        return 200, {"board": self.store.update_task(board, key, task, self._body(), rev=self._rev())}

    def api_delete_task(self, board, key, task):
        return 200, {"board": self.store.delete_task(board, key, task, rev=self._rev())}

    def api_transfer_card(self, board, key):
        body = self._body()
        unknown = set(body) - {"board", "column", "targetRev"}
        if unknown:
            raise HttpError(400, f"Moving a card to another board takes board, column and targetRev, not {', '.join(sorted(unknown))}.")
        target = body.get("board")
        if not isinstance(target, str) or not target:
            raise HttpError(400, "Say which board to move the card to, as board: its id.")
        target_rev = body.get("targetRev")
        if target_rev is not None and (isinstance(target_rev, bool) or not isinstance(target_rev, int)):
            raise HttpError(400, "targetRev must be the other board's revision number.")
        done = self.store.transfer_card(board, key, target, body.get("column"), rev=self._rev(), target_rev=target_rev)
        return 200, done

    def _download(self, payload, name):
        filename = (slug(name, ()) or "board") + ".json"
        disposition = f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
        return 200, payload, {"Content-Disposition": disposition}, 1

    def api_export_board(self, board):
        found = self.store.get_board(board)
        return self._download(found, found["name"])

    def api_export_all(self):
        return self._download(self.store.all_data(), "all-boards")

    def api_import_file(self):
        boards = self.store.import_data(self._body(limit=MAX_IMPORT))
        return 201, {"boards": [{"id": b["id"], "name": b["name"], "cards": len(b["cards"])} for b in boards]}

    def api_search_board(self, board):
        text, sort, archived = self._search_params()
        keys, considered = run(self.store.get_board(board), text, sort, archived)
        return 200, {"keys": keys, "total": considered, "matched": len(keys)}

    def api_search_all(self):
        text, sort, archived = self._search_params()
        results = search_boards(self.store.all_data()["boards"], text, sort, archived)
        return 200, {"results": [{"board": b["id"], "name": b["name"], "keys": keys, "total": considered}
                                 for b, keys, considered in results]}

    def api_list_samples(self):
        return 200, {"samples": samples.list_samples()}

    def api_add_sample(self, name):
        board = self.store.import_board(samples.load(name))
        return 201, {"board": board}

    # ---- responses -----------------------------------------------------------------------------

    def _static(self, path):
        name = "index.html" if path in ("/", "/index.html") else path[len("/static/"):] if path.startswith("/static/") else None
        # The name is matched as sent, percent signs included, so an encoded ../ can't get through.
        if not name or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*\.[a-z0-9]+", name):
            raise HttpError(404, "Not found.")
        ctype = CONTENT_TYPES.get(os.path.splitext(name)[1])
        full = os.path.join(STATIC_DIR, name)
        if not ctype or not os.path.isfile(full):
            raise HttpError(404, "Not found.")
        with open(full, "rb") as f:
            data = f.read()
        self._send(200, data, ctype, extra={"Cache-Control": "no-cache"})

    def _json(self, status, payload, extra=None, indent=None):
        headers = {"Cache-Control": "no-store"}
        headers.update(extra or {})
        self._send(status, encode_json(payload, indent), "application/json; charset=utf-8", extra=headers)

    def _send(self, status, data, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        if status >= 400:
            # The request body may be unread; don't reuse the connection.
            self.send_header("Connection", "close")
            self.close_connection = True
            self._drain()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


    def _drain(self):
        """Read what the client sent. On Windows, closing with data unread can destroy our answer on the way out."""
        if getattr(self, "_body_read", True) or not getattr(self, "headers", None):
            return
        self._body_read = True
        try:
            remaining = int(self.headers.get("Content-Length") or 0)
            if remaining > MAX_DRAIN:
                return
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    return
                remaining -= len(chunk)
        except (ValueError, OSError):
            pass


class BoardServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second program bind the same port and steal requests at random.
    allow_reuse_address = os.name != "nt"

    def __init__(self, address, store: Optional[Store] = None, verbose=False):
        self.store = store
        self.verbose = verbose
        super().__init__(address, Handler)

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        # A browser closing a connection mid-request isn't worth a traceback.
        if not isinstance(sys.exc_info()[1], (ConnectionError, socket.timeout)) and sys.stderr:
            traceback.print_exc()
