"""Board storage: every board, column, group and card lives in one JSON file.

All changes go through Store, which validates them, bumps the board's revision number and
writes the file atomically (temp file, then rename), keeping the previous version as .bak.
"""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
from datetime import datetime, timezone

SCHEMA_VERSION = 1
DEFAULT_COLUMNS = [("Backlog", None), ("Ready", None), ("In progress", 1), ("Review", None), ("Done", None)]
COLORS = ("blue", "teal", "violet", "amber", "rose", "green", "slate")
PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9]{0,7}$")
KEY_RE = re.compile(r"^([A-Z][A-Z0-9]{0,7})-([1-9][0-9]{0,5})$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

MAX_NAME = 120
MAX_TITLE = 200
MAX_SUMMARY = 1000
MAX_TEXT = 20000
MAX_COLUMNS = 20
MAX_GROUPS = 50
MAX_ROWS = 30  # fields, sections, details
MAX_CHECKLISTS = 10
MAX_CHECK_ITEMS = 100

CARD_FIELDS = ("title", "group", "column", "summary", "blocked", "fields", "sections", "notes", "rank", "checks")
BOARD_FIELDS = ("name", "description", "prefix", "groupLabel", "columns", "groups", "checklists", "template")


class StoreError(Exception):
    """A change that can't be applied. `status` is the matching HTTP status code."""
    status = 400


class NotFound(StoreError):
    status = 404


class Conflict(StoreError):
    status = 409


class StaleRevision(StoreError):
    """The caller's copy of the board is out of date (HTTP 412, from If-Match)."""
    status = 412


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_data_path() -> str:
    env = os.environ.get("LANEBOARD_DATA")
    if env:
        return env
    if os.name == "nt" and os.environ.get("APPDATA"):
        base = os.path.join(os.environ["APPDATA"], "laneboard")
    else:
        base = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "laneboard")
    return os.path.join(base, "boards.json")


def _text(value, label, limit, required=False, multiline=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise StoreError(f"{label} must be text.")
    value = value.rstrip() if multiline else value.strip()
    if required and not value:
        raise StoreError(f"{label} can't be empty.")
    if len(value) > limit:
        raise StoreError(f"{label} is too long ({len(value)} characters; the limit is {limit}).")
    return value


def _rows(value, label, value_key, value_limit):
    """A list of {label, <value_key>} rows, as used by card fields, card sections and group details."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise StoreError(f"{label} must be a list.")
    if len(value) > MAX_ROWS:
        raise StoreError(f"{label} can have at most {MAX_ROWS} entries.")
    rows = []
    for row in value:
        if not isinstance(row, dict):
            raise StoreError(f"Each entry in {label} needs a label and a {value_key}.")
        rows.append({"label": _text(row.get("label"), f"A label in {label}", 60, required=True),
                     value_key: _text(row.get(value_key), f"{label} text", value_limit, multiline=True)})
    return rows


def _slug(name, taken):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "item"
    slug, n = base[:40], 2
    while slug in taken:
        slug = f"{base[:36]}-{n}"
        n += 1
    return slug


class Store:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self._lock = threading.Lock()
        self._stamp = None
        self.data = self._load()

    # ---- file handling -------------------------------------------------------------------------

    def _load(self):
        if not os.path.exists(self.path):
            data = {"version": SCHEMA_VERSION, "boards": []}
            data["boards"].append(self._new_board("My board"))
            self._write(data)
            return data
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise StoreError(f"Can't read {self.path}: {e}. The previous version is in {self.path}.bak.") from e
        if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION or not isinstance(data.get("boards"), list):
            raise StoreError(f"{self.path} isn't a laneboard file (schema version {SCHEMA_VERSION}).")
        for board in data["boards"]:  # files written before checklists and templates existed
            board.setdefault("checklists", [])
            board.setdefault("template", [])
            for card in board["cards"]:
                card.setdefault("checks", [])
        self._stamp = self._file_stamp()
        return data

    def _file_stamp(self):
        st = os.stat(self.path)
        return st.st_mtime_ns, st.st_size

    def _refresh(self):
        """Pick up edits made to the file by something else (an editor, a script) since we last read or wrote it."""
        try:
            changed = self._file_stamp() != self._stamp
        except OSError:
            return
        if changed:
            self.data = self._load()

    def _write(self, data):
        folder = os.path.dirname(self.path)
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".boards-", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(self.path):
                shutil.copy2(self.path, self.path + ".bak")
            os.replace(tmp, self.path)
            self._stamp = self._file_stamp()
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    # ---- reads ---------------------------------------------------------------------------------

    def list_boards(self):
        with self._lock:
            self._refresh()
            return [{"id": b["id"], "name": b["name"], "cards": len(b["cards"])} for b in self.data["boards"]]

    def get_board(self, board_id):
        with self._lock:
            self._refresh()
            return copy.deepcopy(self._find(board_id)[1])

    def _find(self, board_id):
        for i, b in enumerate(self.data["boards"]):
            if b["id"] == board_id:
                return i, b
        raise NotFound("That board doesn't exist. It may have been deleted.")

    # ---- boards --------------------------------------------------------------------------------

    def _new_board(self, name, columns=None, description="", prefix="T", group_label="Group"):
        taken = set()
        cols = []
        for col_name, limit in columns or DEFAULT_COLUMNS:
            cid = _slug(col_name, taken)
            taken.add(cid)
            cols.append({"id": cid, "name": col_name, "limit": limit})
        board = {"id": "b-" + secrets.token_hex(4), "name": name, "description": description, "prefix": prefix,
                 "groupLabel": group_label, "rev": 1, "created": now(), "updated": now(),
                 "columns": cols, "groups": [], "checklists": [], "template": [], "counters": {}, "cards": []}
        self._check_board(board)
        return board

    def create_board(self, name, description=""):
        board = self._new_board(_text(name, "Board name", MAX_NAME, required=True),
                                description=_text(description, "Description", MAX_SUMMARY))
        with self._lock:
            self._refresh()
            data = copy.deepcopy(self.data)
            data["boards"].append(board)
            self._write(data)
            self.data = data
        return copy.deepcopy(board)

    def update_board(self, board_id, changes, rev=None):
        unknown = set(changes) - set(BOARD_FIELDS)
        if unknown:
            raise StoreError(f"Boards have no {', '.join(sorted(unknown))} setting.")

        def apply(board):
            if "name" in changes:
                board["name"] = _text(changes["name"], "Board name", MAX_NAME, required=True)
            if "description" in changes:
                board["description"] = _text(changes["description"], "Description", MAX_SUMMARY)
            if "prefix" in changes:
                board["prefix"] = self._prefix(changes["prefix"], "The board's key prefix")
            if "groupLabel" in changes:
                board["groupLabel"] = _text(changes["groupLabel"], "Group label", 30, required=True)
            if "columns" in changes:
                board["columns"] = self._columns(board, changes["columns"])
            if "groups" in changes:
                board["groups"] = self._groups(board, changes["groups"])
            if "template" in changes:
                board["template"] = self._template(changes["template"])
            if "checklists" in changes:
                board["checklists"] = self._checklists(changes["checklists"])
                items = self._check_ids(board)
                for card in board["cards"]:
                    card["checks"] = [i for i in card["checks"] if i in items]
            return None

        return self._mutate(board_id, rev, apply)

    def delete_board(self, board_id, rev=None):
        with self._lock:
            self._refresh()
            i, board = self._find(board_id)
            self._check_rev(board, rev)
            data = copy.deepcopy(self.data)
            del data["boards"][i]
            self._write(data)
            self.data = data

    def import_board(self, board):
        """Add a board from an exported JSON object. It gets a new id; card keys are kept."""
        if not isinstance(board, dict):
            raise StoreError("An imported board must be a JSON object.")
        draft = self._new_board(_text(board.get("name"), "Board name", MAX_NAME, required=True),
                                description=_text(board.get("description"), "Description", MAX_SUMMARY))
        if board.get("prefix") is not None:
            draft["prefix"] = self._prefix(board["prefix"], "The board's key prefix")
        if board.get("groupLabel") is not None:
            draft["groupLabel"] = _text(board["groupLabel"], "Group label", 30, required=True)
        if board.get("columns"):
            draft["columns"] = self._columns(draft, board["columns"])
        if board.get("groups"):
            draft["groups"] = self._groups(draft, board["groups"])
        if board.get("checklists"):
            draft["checklists"] = self._checklists(board["checklists"])
        if board.get("template"):
            draft["template"] = self._template(board["template"])
        cards = board.get("cards") or []
        if not isinstance(cards, list):
            raise StoreError("cards must be a list.")
        for raw in cards:
            if not isinstance(raw, dict):
                raise StoreError("Each card must be a JSON object.")
            card = self._card(draft, {k: v for k, v in raw.items() if k in CARD_FIELDS}, new=True)
            key = raw.get("key")
            if key is not None:
                m = KEY_RE.match(str(key))
                if not m:
                    raise StoreError(f"{key!r} isn't a card key like ABC-12.")
                if any(c["key"] == key for c in draft["cards"]):
                    raise StoreError(f"Two cards share the key {key}.")
                card["key"] = key
                prefix, number = m.group(1), int(m.group(2))
                draft["counters"][prefix] = max(draft["counters"].get(prefix, 0), number)
            else:
                card["key"] = self._next_key(draft, card["group"])
            for stamp in ("created", "updated"):
                if isinstance(raw.get(stamp), str):
                    card[stamp] = raw[stamp][:40]
            draft["cards"].append(card)
        self._check_board(draft)
        with self._lock:
            self._refresh()
            data = copy.deepcopy(self.data)
            data["boards"].append(draft)
            self._write(data)
            self.data = data
        return copy.deepcopy(draft)

    # ---- cards ---------------------------------------------------------------------------------

    def create_card(self, board_id, values, rev=None):
        """Add a card and return (board, new card key)."""
        unknown = set(values) - set(CARD_FIELDS)
        if unknown:
            raise StoreError(f"Cards have no {', '.join(sorted(unknown))} field.")

        def apply(board):
            card = self._card(board, values, new=True)
            card["key"] = self._next_key(board, card["group"])
            board["cards"].append(card)
            return card["key"]

        return self._mutate(board_id, rev, apply, with_result=True)

    def update_card(self, board_id, key, changes, rev=None):
        unknown = set(changes) - set(CARD_FIELDS)
        if unknown:
            raise StoreError(f"Cards have no {', '.join(sorted(unknown))} field.")

        def apply(board):
            i = self._card_index(board, key)
            merged = {**board["cards"][i], **changes}
            card = self._card(board, {k: merged.get(k) for k in CARD_FIELDS}, new=False)
            card.update(key=key, created=board["cards"][i]["created"])
            board["cards"][i] = card
            return key

        return self._mutate(board_id, rev, apply)

    def delete_card(self, board_id, key, rev=None):
        def apply(board):
            del board["cards"][self._card_index(board, key)]
            return key

        return self._mutate(board_id, rev, apply)

    # ---- internals -----------------------------------------------------------------------------

    def _mutate(self, board_id, rev, apply, with_result=False):
        """Apply a change to a copy of the board, validate, save, then swap it in."""
        with self._lock:
            self._refresh()
            i, board = self._find(board_id)
            self._check_rev(board, rev)
            draft = copy.deepcopy(board)
            result = apply(draft)
            draft["rev"] += 1
            draft["updated"] = now()
            self._check_board(draft)
            data = copy.deepcopy(self.data)
            data["boards"][i] = draft
            self._write(data)
            self.data = data
            board = copy.deepcopy(draft)
            return (board, result) if with_result else board

    @staticmethod
    def _check_rev(board, rev):
        if rev is not None and rev != board["rev"]:
            raise StaleRevision("This board changed since you loaded it. Reload to see the latest version.")

    @staticmethod
    def _card_index(board, key):
        for i, c in enumerate(board["cards"]):
            if c["key"] == key:
                return i
        raise NotFound(f"There's no card {key} on this board.")

    @staticmethod
    def _prefix(value, label):
        if not isinstance(value, str) or not PREFIX_RE.match(value):
            raise StoreError(f"{label} must be 1 to 8 capital letters or digits, starting with a letter (like VM).")
        return value

    def _columns(self, board, columns):
        if not isinstance(columns, list) or not columns:
            raise StoreError("A board needs at least one column.")
        if len(columns) > MAX_COLUMNS:
            raise StoreError(f"A board can have at most {MAX_COLUMNS} columns.")
        existing = {c["id"]: c for c in board["columns"]}
        taken = {c.get("id") for c in columns if isinstance(c, dict) and c.get("id") in existing}
        out, names = [], set()
        for col in columns:
            if not isinstance(col, dict):
                raise StoreError("Each column needs a name.")
            name = _text(col.get("name"), "Column name", 40, required=True)
            if name.lower() in names:
                raise StoreError(f"There are two columns named {name}.")
            names.add(name.lower())
            limit = col.get("limit")
            if limit in ("", None):
                limit = None
            elif isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 999:
                raise StoreError(f"The limit for {name} must be a whole number from 1 to 999, or empty.")
            cid = col.get("id")
            if cid not in existing:
                cid = _slug(name, taken)
                taken.add(cid)
            out.append({"id": cid, "name": name, "limit": limit})
        kept = {c["id"] for c in out}
        for cid, col in existing.items():
            n = sum(1 for card in board["cards"] if card["column"] == cid)
            if cid not in kept and n:
                raise Conflict(f"Move the {n} card{'s' if n > 1 else ''} out of {col['name']} before removing it.")
        return out

    def _groups(self, board, groups):
        if not isinstance(groups, list):
            raise StoreError("Groups must be a list.")
        if len(groups) > MAX_GROUPS:
            raise StoreError(f"A board can have at most {MAX_GROUPS} groups.")
        existing = {g["id"]: g for g in board["groups"]}
        taken = {g.get("id") for g in groups if isinstance(g, dict) and g.get("id") in existing}
        out, names = [], set()
        for g in groups:
            if not isinstance(g, dict):
                raise StoreError("Each group needs a name.")
            name = _text(g.get("name"), "Group name", 60, required=True)
            if name.lower() in names:
                raise StoreError(f"There are two groups named {name}.")
            names.add(name.lower())
            color = g.get("color") or "slate"
            if color not in COLORS:
                raise StoreError(f"{color} isn't one of the colors: {', '.join(COLORS)}.")
            link = _text(g.get("link"), "Link", 500)
            if link and not re.match(r"^https?://", link):
                raise StoreError("Links must start with http:// or https://.")
            gid = g.get("id")
            if gid not in existing:
                gid = _slug(name, taken)
                taken.add(gid)
            out.append({"id": gid, "name": name, "prefix": self._prefix(g.get("prefix"), f"The key prefix for {name}"),
                        "color": color, "link": link,
                        "details": _rows(g.get("details"), f"{name} details", "value", 500)})
        kept = {g["id"] for g in out}
        for gid, g in existing.items():
            n = sum(1 for card in board["cards"] if card["group"] == gid)
            if gid not in kept and n:
                raise Conflict(f"{n} card{'s' if n > 1 else ''} still belong{'s' if n == 1 else ''} to {g['name']}. "
                               "Move or delete them before removing it.")
        return out

    @staticmethod
    def _template(names):
        """Section names a new card starts with, in order."""
        if not isinstance(names, list):
            raise StoreError("New card sections must be a list of names.")
        if len(names) > MAX_ROWS:
            raise StoreError(f"A new card can start with at most {MAX_ROWS} sections.")
        out = []
        for name in names:
            name = _text(name, "A new card section name", 60, required=True)
            if name.lower() in (n.lower() for n in out):
                raise StoreError(f"{name} is listed twice in new card sections.")
            out.append(name)
        return out

    def _checklists(self, checklists):
        """Checklists every card on the board can tick through. Items keep their ids, so ticks survive edits."""
        if not isinstance(checklists, list):
            raise StoreError("Checklists must be a list.")
        if len(checklists) > MAX_CHECKLISTS:
            raise StoreError(f"A board can have at most {MAX_CHECKLISTS} checklists.")
        out, names, taken = [], set(), set()

        def new_id(given, make):
            if isinstance(given, str) and ID_RE.match(given) and given not in taken:
                taken.add(given)
                return given
            made = make()
            taken.add(made)
            return made

        for cl in checklists:
            if not isinstance(cl, dict):
                raise StoreError("Each checklist needs a name.")
            name = _text(cl.get("name"), "Checklist name", 60, required=True)
            if name.lower() in names:
                raise StoreError(f"There are two checklists named {name}.")
            names.add(name.lower())
            items = cl.get("items") or []
            if not isinstance(items, list):
                raise StoreError(f"The items in {name} must be a list.")
            if len(items) > MAX_CHECK_ITEMS:
                raise StoreError(f"{name} can have at most {MAX_CHECK_ITEMS} lines.")
            rows = []
            for item in items:
                if not isinstance(item, dict):
                    raise StoreError(f"Each line in {name} needs text.")
                rows.append({"id": new_id(item.get("id"), lambda: "i" + secrets.token_hex(4)),
                             "text": _text(item.get("text"), f"A line in {name}", 500, required=True),
                             "heading": bool(item.get("heading"))})
            out.append({"id": new_id(cl.get("id"), lambda: _slug(name, taken)), "name": name, "items": rows})
        return out

    @staticmethod
    def _check_ids(board):
        return {i["id"] for cl in board["checklists"] for i in cl["items"] if not i["heading"]}

    def _card(self, board, values, new):
        column = values.get("column") or board["columns"][0]["id"]
        if column not in {c["id"] for c in board["columns"]}:
            raise StoreError("That column doesn't exist on this board.")
        group = values.get("group") or None
        if group is not None and group not in {g["id"] for g in board["groups"]}:
            raise StoreError("That group doesn't exist on this board.")
        rank = values.get("rank")
        if new and rank is None:
            rank = max((c["rank"] for c in board["cards"]), default=0) + 1
        if isinstance(rank, bool) or not isinstance(rank, (int, float)):
            raise StoreError("Rank must be a number.")
        checks = values.get("checks") or []
        if not isinstance(checks, list) or not all(isinstance(c, str) for c in checks):
            raise StoreError("Checks must be a list of checklist item ids.")
        items = self._check_ids(board)
        checks = [c for c in dict.fromkeys(checks) if c in items]  # drop ticks for lines that no longer exist
        stamp = now()
        return {"key": None, "title": _text(values.get("title"), "Title", MAX_TITLE, required=True),
                "group": group, "column": column, "rank": rank,
                "summary": _text(values.get("summary"), "Summary", MAX_SUMMARY, multiline=True),
                "blocked": _text(values.get("blocked"), "Blocked by", 500),
                "fields": _rows(values.get("fields"), "Fields", "value", 500),
                "sections": _rows(values.get("sections"), "Sections", "body", MAX_TEXT),
                "notes": _text(values.get("notes"), "Notes", MAX_TEXT, multiline=True),
                "checks": checks, "created": stamp, "updated": stamp}

    @staticmethod
    def _next_key(board, group_id):
        prefix = next((g["prefix"] for g in board["groups"] if g["id"] == group_id), board["prefix"])
        taken = {c["key"] for c in board["cards"]}
        n = board["counters"].get(prefix, 0) + 1
        while f"{prefix}-{n}" in taken:
            n += 1
        board["counters"][prefix] = n
        return f"{prefix}-{n}"

    @staticmethod
    def _check_board(board):
        keys = [c["key"] for c in board["cards"]]
        if len(keys) != len(set(keys)):
            raise StoreError("Two cards share a key.")
        if not board["columns"]:
            raise StoreError("A board needs at least one column.")
