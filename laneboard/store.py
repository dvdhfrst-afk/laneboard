"""Board storage: every board, column, group and card lives in one JSON file.

All changes go through Store, which validates them, bumps the board's revision number and writes
the file atomically, keeping the previous version as .bak.

Boards handed out by Store are shared, never copied, and Store never edits one in place: a change
builds a new board and swaps it in. Callers must treat what they get as read-only.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from contextlib import contextmanager

from . import schema, storage
from .schema import (BadFile, Conflict, NotFound, StaleRevision, StoreError, Unavailable,  # noqa: F401 (re-exported)
                     COLORS, ID_RE, KEY_RE, LINK_RE, MAX_BOARDS, MAX_CARDS, MAX_CHECK_ITEMS, MAX_CHECKLISTS,
                     MAX_COLUMNS, MAX_GROUPS, MAX_NAME, MAX_ROWS, MAX_SUMMARY, MAX_TEXT, MAX_TITLE, PREFIX_RE,
                     SCHEMA_VERSION, key_sort, now, rows, slug, text)

DEFAULT_COLUMNS = [("Backlog", None), ("Ready", None), ("In progress", 1), ("Review", None), ("Done", None)]

CARD_INPUT = ("title", "group", "column", "summary", "blocked", "fields", "sections", "notes", "rank", "checks")
CARD_FIELDS = CARD_INPUT + ("archived",)
BOARD_FIELDS = ("name", "description", "prefix", "groupLabel", "columns", "groups", "checklists", "template", "moveCards")
MIN_RANK_GAP = 1e-6


def default_data_path() -> str:
    env = os.environ.get("LANEBOARD_DATA")
    if env:
        return env
    if os.name == "nt" and os.environ.get("APPDATA"):
        base = os.path.join(os.environ["APPDATA"], "laneboard")
    else:
        base = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "laneboard")
    return os.path.join(base, "boards.json")


def resolve_path(path) -> str:
    # realpath: replacing a symlink would silently cut the link to a file kept somewhere else.
    return os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))


def encode_data(data) -> bytes:
    # backslashreplace turns a lone surrogate from a hand-edited file back into its JSON \uXXXX escape.
    return json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8", "backslashreplace")


def _read_bytes(path):
    """Read the file, waiting out a moment when Windows has it locked for a replace."""
    for attempt in range(5):
        try:
            with open(path, "rb") as f:
                return f.read()
        except PermissionError:
            if os.name != "nt" or attempt == 4:
                raise
            time.sleep(0.03)


def restore_backup(path, name):
    """Make a listed backup the live file. The file it replaces is kept as a backup of its own."""
    path = resolve_path(path)
    match = next((b for b in storage.list_backups(path) if b["name"] == name), None)
    if match is None:
        raise NotFound(f"There's no backup called {name!r}. Run the backups command to list them.")
    try:
        with storage.FileLock(path + ".lock"):
            raw = _read_bytes(match["path"])
            data, _ = schema.load_bytes(raw, f"The backup {name}")
            kept = storage.tagged_snapshot(path, "before-restore")
            storage.write_atomic(path, raw)
    except OSError as e:
        raise Unavailable(f"Can't restore over {path}: {storage.reason(e)}. Nothing was changed.") from e
    return {"boards": len(data["boards"]), "cards": sum(len(b["cards"]) for b in data["boards"]), "kept": kept}


class Store:
    def __init__(self, path, create=True, starter=True):
        """With starter=False a new file begins empty and isn't written until something is saved."""
        self.path = resolve_path(path)
        self._lock = threading.RLock()
        self._stamp = None
        self._upgraded_from = None
        self.created = False
        self.data = self._open(create, starter)
        storage.remove_stale_temp_files(self.path)

    # ---- file handling -------------------------------------------------------------------------

    def _open(self, create, starter):
        if os.path.isdir(self.path):
            raise Unavailable(f"{self.path} is a folder. Give the name of a boards file, like {os.path.join(self.path, 'boards.json')}.")
        if os.path.exists(self.path):
            return self._read()
        if not create:
            raise NotFound(f"There's no boards file at {self.path} yet.")
        if not starter:
            return {"version": SCHEMA_VERSION, "boards": []}
        try:
            with storage.FileLock(self.path + ".lock"):
                if os.path.exists(self.path):  # another program got here first
                    return self._read()
                data = {"version": SCHEMA_VERSION, "boards": [self._new_board("My board")]}
                storage.write_atomic(self.path, encode_data(data))
                self._stamp = self._file_stamp()
                self.created = True
                return data
        except OSError as e:
            raise Unavailable(f"Can't create {self.path}: {storage.reason(e)}.") from e

    def _read(self):
        try:
            raw = _read_bytes(self.path)
            stamp = self._file_stamp()
        except OSError as e:
            raise Unavailable(f"Can't read {self.path}: {storage.reason(e)}.") from e
        try:
            data, start = schema.load_bytes(raw, self.path)
        except BadFile as e:
            raise BadFile(f"{e} Your file was not changed. Fix it by hand, or run the backups command to see "
                          "the copies you can go back to.") from e
        self._upgraded_from = start if start != SCHEMA_VERSION else None
        self._stamp = stamp
        return data

    def _file_stamp(self):
        st = os.stat(self.path)
        return st.st_mtime_ns, st.st_size, st.st_ino

    def _refresh(self):
        """Pick up edits made to the file by something else (an editor, a script, the command line)."""
        try:
            changed = self._file_stamp() != self._stamp
        except OSError:
            return  # deleted or moved while running: keep what's in memory, the next save writes it back
        if not changed:
            return
        old = {b["id"]: b for b in self.data["boards"]}
        data = self._read()
        for board in data["boards"]:
            before = old.get(board["id"])
            # A hand edit doesn't touch rev, but pages holding the old copy still have to notice.
            if before is not None and board != before and board["rev"] <= before["rev"]:
                board["rev"] = before["rev"] + 1
        self.data = data

    def _save(self, data):
        """Write a new version of the file, then adopt it. A failure changes nothing."""
        try:
            if os.path.exists(self.path):
                if self._upgraded_from is not None:
                    keep = f"{self.path}.v{self._upgraded_from}.bak"
                    if not os.path.exists(keep):
                        storage.copy_atomic(self.path, keep)
                storage.daily_snapshot(self.path)
                storage.copy_atomic(self.path, storage.previous_path(self.path))
            storage.write_atomic(self.path, encode_data(data))
            self._stamp = self._file_stamp()
        except OSError as e:
            raise Unavailable(f"Can't save {self.path}: {storage.reason(e)}. Your change was not saved.") from e
        self._upgraded_from = None
        self.data = data

    @contextmanager
    def _changing(self):
        with self._lock:
            try:
                with storage.FileLock(self.path + ".lock"):
                    self._refresh()
                    yield
            except OSError as e:
                raise Unavailable(f"Can't save {self.path}: {storage.reason(e)}. Your change was not saved.") from e

    # ---- reads ---------------------------------------------------------------------------------

    def list_boards(self):
        with self._lock:
            self._refresh()
            out = []
            for b in self.data["boards"]:
                archived = sum(1 for c in b["cards"] if c["archived"])
                out.append({"id": b["id"], "name": b["name"], "rev": b["rev"],
                            "cards": len(b["cards"]) - archived, "archived": archived})
            return out

    def get_board(self, board_id):
        with self._lock:
            self._refresh()
            return self._find(board_id)[1]

    def all_data(self):
        with self._lock:
            self._refresh()
            return self.data

    def _find(self, board_id):
        for i, b in enumerate(self.data["boards"]):
            if b["id"] == board_id:
                return i, b
        raise NotFound("That board doesn't exist. It may have been deleted.")

    def status(self):
        """What a health check shows."""
        with self._lock:
            self._refresh()
            boards = self.data["boards"]
            return {"schema": SCHEMA_VERSION, "upgradedFrom": self._upgraded_from, "boards": len(boards),
                    "cards": sum(len(b["cards"]) for b in boards), "path": self.path}

    # ---- boards --------------------------------------------------------------------------------

    def _new_board(self, name, columns=None, description="", prefix="T", group_label="Group"):
        taken = set()
        cols = []
        for col_name, limit in columns or DEFAULT_COLUMNS:
            cid = slug(col_name, taken)
            taken.add(cid)
            cols.append({"id": cid, "name": col_name, "limit": limit})
        stamp = now()
        return {"id": "b-" + secrets.token_hex(4), "name": name, "description": description, "prefix": prefix,
                "groupLabel": group_label, "rev": 1, "created": stamp, "updated": stamp,
                "columns": cols, "groups": [], "checklists": [], "template": [], "counters": {}, "cards": []}

    def _add_boards(self, boards):
        with self._changing():
            if len(self.data["boards"]) + len(boards) > MAX_BOARDS:
                raise Conflict(f"A file can hold at most {MAX_BOARDS} boards.")
            self._save({**self.data, "boards": self.data["boards"] + boards})

    def create_board(self, name, description=""):
        board = self._new_board(text(name, "Board name", MAX_NAME, required=True),
                                description=text(description, "Description", MAX_SUMMARY))
        self._add_boards([board])
        return board

    def update_board(self, board_id, changes, rev=None):
        unknown = set(changes) - set(BOARD_FIELDS)
        if unknown:
            raise StoreError(f"Boards have no {', '.join(sorted(unknown))} setting.")
        if "moveCards" in changes and "columns" not in changes:
            raise StoreError("moveCards only goes with a new list of columns.")

        def apply(board):
            if "name" in changes:
                board["name"] = text(changes["name"], "Board name", MAX_NAME, required=True)
            if "description" in changes:
                board["description"] = text(changes["description"], "Description", MAX_SUMMARY)
            if "prefix" in changes:
                board["prefix"] = self._prefix(changes["prefix"], "The board's key prefix")
            if "groupLabel" in changes:
                board["groupLabel"] = text(changes["groupLabel"], "Group label", 30, required=True)
            if "columns" in changes:
                self._apply_columns(board, changes["columns"], changes.get("moveCards"))
            if "groups" in changes:
                self._apply_groups(board, changes["groups"])
            if "template" in changes:
                board["template"] = self._template(changes["template"])
            if "checklists" in changes:
                board["checklists"] = self._checklists(changes["checklists"])
                items = self._check_ids(board)
                board["cards"] = [c if all(i in items for i in c["checks"])
                                  else {**c, "checks": [i for i in c["checks"] if i in items]} for c in board["cards"]]
            return None

        return self._mutate(board_id, rev, apply)[0]

    def delete_board(self, board_id, rev=None):
        with self._changing():
            i, board = self._find(board_id)
            self._check_rev(board, rev)
            storage.tagged_snapshot(self.path, "before-delete")
            self._save({**self.data, "boards": self.data["boards"][:i] + self.data["boards"][i + 1:]})

    def import_data(self, data):
        """Add boards from parsed JSON: one exported board, or a whole boards file. Adds all or nothing."""
        if not isinstance(data, dict):
            raise StoreError("An imported file must hold a JSON object: one board, or a whole boards file.")
        if "boards" in data:
            if not isinstance(data["boards"], list):
                raise StoreError("boards must be a list.")
            version = data.get("version")
            if isinstance(version, int) and version > SCHEMA_VERSION:
                raise StoreError(f"That file was written by a newer Laneboard (file format {version}; this one "
                                 f"reads up to {SCHEMA_VERSION}). Update Laneboard to import it.")
            incoming = data["boards"]
            if not incoming:
                raise StoreError("That file has no boards in it.")
        else:
            incoming = [data]
        boards = []
        for i, raw in enumerate(incoming):
            try:
                boards.append(self._imported_board(raw))
            except StoreError as e:
                if len(incoming) == 1:
                    raise
                raise StoreError(f"Board number {i + 1}: {e}") from e
        self._add_boards(boards)
        return boards

    def import_board(self, board):
        """Add a board from an exported JSON object. It gets a new id; card keys are kept."""
        made = self._imported_board(board)
        self._add_boards([made])
        return made

    def _imported_board(self, board):
        """Build a board from untrusted JSON. Everything is validated; only known fields are kept."""
        if not isinstance(board, dict):
            raise StoreError("An imported board must be a JSON object.")
        draft = self._new_board(text(board.get("name"), "Board name", MAX_NAME, required=True),
                                description=text(board.get("description"), "Description", MAX_SUMMARY))
        if board.get("prefix") is not None:
            draft["prefix"] = self._prefix(board["prefix"], "The board's key prefix")
        if board.get("groupLabel") is not None:
            draft["groupLabel"] = text(board["groupLabel"], "Group label", 30, required=True)
        if board.get("columns"):
            draft["columns"] = self._columns({"columns": [], "cards": []}, board["columns"], keep_ids=True)[0]
        if board.get("groups"):
            draft["groups"] = self._groups({"groups": [], "cards": []}, board["groups"], keep_ids=True)
        if board.get("checklists"):
            draft["checklists"] = self._checklists(board["checklists"])
        if board.get("template"):
            draft["template"] = self._template(board["template"])
        cards = board.get("cards") or []
        if not isinstance(cards, list):
            raise StoreError("cards must be a list.")
        if len(cards) > MAX_CARDS:
            raise StoreError(f"A board can have at most {MAX_CARDS} cards. This one has {len(cards)}.")
        counters = board.get("counters")
        if isinstance(counters, dict):  # keeps keys of deleted cards from coming back
            for prefix, number in counters.items():
                if isinstance(prefix, str) and PREFIX_RE.fullmatch(prefix) and type(number) is int and 0 < number < 10**6:
                    draft["counters"][prefix] = number
        items = self._check_ids(draft)
        column_names = {c["name"].lower(): c["id"] for c in draft["columns"]}
        group_names = {g["name"].lower(): g["id"] for g in draft["groups"]}
        seen, top_rank, built = set(), 0, []
        for n, raw in enumerate(cards, 1):
            if not isinstance(raw, dict):
                raise StoreError(f"Card number {n} isn't a JSON object.")
            try:
                values = {k: raw[k] for k in CARD_INPUT if k in raw}
                # A hand-written file may say "column": "In progress" instead of using the id.
                if isinstance(values.get("column"), str):
                    values["column"] = column_names.get(values["column"].lower(), values["column"])
                if isinstance(values.get("group"), str):
                    values["group"] = group_names.get(values["group"].lower(), values["group"])
                if values.get("rank") is None:
                    values["rank"] = top_rank + 1
                card = self._new_card(draft, values, items)
                top_rank = max(top_rank, card["rank"])
                key = raw.get("key")
                if key is not None:
                    if not isinstance(key, str) or not KEY_RE.fullmatch(key):
                        raise StoreError(f"{key!r} isn't a card key like ABC-12.")
                    if key in seen:
                        raise StoreError(f"Two cards share the key {key}.")
                    prefix, number = KEY_RE.fullmatch(key).groups()
                    draft["counters"][prefix] = max(draft["counters"].get(prefix, 0), int(number))
                    card["key"] = key
                    seen.add(key)
                for stamp in ("created", "updated"):
                    if isinstance(raw.get(stamp), str):
                        card[stamp] = text(raw[stamp], stamp, 40)
                archived = raw.get("archived")
                if archived:
                    card["archived"] = text(archived, "archived", 40) if isinstance(archived, str) else now()
            except StoreError as e:
                raise StoreError(f"Card number {n}: {e}") from e
            built.append(card)
        for card in built:  # keys come last, so a card with no key can't take one a later card asks for
            if card["key"] is None:
                prefix = next((g["prefix"] for g in draft["groups"] if g["id"] == card["group"]), draft["prefix"])
                n = draft["counters"].get(prefix, 0) + 1
                while f"{prefix}-{n}" in seen:
                    n += 1
                draft["counters"][prefix] = n
                card["key"] = f"{prefix}-{n}"
                seen.add(card["key"])
        draft["cards"] = built
        return draft

    # ---- cards ---------------------------------------------------------------------------------

    def create_card(self, board_id, values, rev=None):
        """Add a card and return (board, new card key)."""
        unknown = set(values) - set(CARD_INPUT)
        if unknown:
            raise StoreError(f"Cards have no {', '.join(sorted(unknown))} field.")

        def apply(board):
            if len(board["cards"]) >= MAX_CARDS:
                raise Conflict(f"A board can have at most {MAX_CARDS} cards. Delete or move some first.")
            card = self._new_card(board, values, self._check_ids(board))
            if card["rank"] is None:
                card["rank"] = max((c["rank"] for c in board["cards"]), default=0) + 1
            card["key"] = self._next_key(board, card["group"])
            board["cards"].append(card)
            self._tidy_ranks(board, card["column"])
            return card["key"]

        return self._mutate(board_id, rev, apply)

    def update_card(self, board_id, key, changes, rev=None):
        unknown = set(changes) - set(CARD_FIELDS)
        if unknown:
            raise StoreError(f"Cards have no {', '.join(sorted(unknown))} field.")

        def apply(board):
            i = self._card_index(board, key)
            card = {**board["cards"][i], **self._card_changes(board, changes)}
            if "archived" in changes:
                was = board["cards"][i]["archived"]
                if changes["archived"] not in (True, False, None):
                    raise StoreError("archived must be true or false.")
                card["archived"] = (was or now()) if changes["archived"] else None
                if was and not card["archived"] and "rank" not in changes:  # comes back at the bottom of its column
                    card["rank"] = max((c["rank"] for c in board["cards"]), default=0) + 1
            card["updated"] = now()
            board["cards"][i] = card
            if "rank" in changes or "column" in changes:
                self._tidy_ranks(board, card["column"])
            return key

        return self._mutate(board_id, rev, apply)[0]

    def delete_card(self, board_id, key, rev=None):
        def apply(board):
            del board["cards"][self._card_index(board, key)]
            return key

        return self._mutate(board_id, rev, apply)[0]

    # ---- internals -----------------------------------------------------------------------------

    def _mutate(self, board_id, rev, apply):
        """Apply a change to a new copy of the board, save it, then swap it in. Returns (board, result)."""
        with self._changing():
            i, board = self._find(board_id)
            self._check_rev(board, rev)
            draft = dict(board)
            draft["cards"] = list(board["cards"])
            draft["counters"] = dict(board["counters"])
            result = apply(draft)
            draft["rev"] += 1
            draft["updated"] = now()
            boards = list(self.data["boards"])
            boards[i] = draft
            self._save({**self.data, "boards": boards})
            return draft, result

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
        if not isinstance(value, str) or not PREFIX_RE.fullmatch(value):
            raise StoreError(f"{label} must be 1 to 8 capital letters or digits, starting with a letter (like VM).")
        return value

    def _columns(self, board, columns, keep_ids=False):
        """Validate a new column list. Returns (columns, ids that no longer exist)."""
        if not isinstance(columns, list) or not columns:
            raise StoreError("A board needs at least one column.")
        if len(columns) > MAX_COLUMNS:
            raise StoreError(f"A board can have at most {MAX_COLUMNS} columns.")
        existing = {c["id"] for c in board["columns"]}
        usable = lambda cid: isinstance(cid, str) and (cid in existing or (keep_ids and ID_RE.fullmatch(cid)))  # noqa: E731
        taken = set()
        for col in columns:
            if isinstance(col, dict) and usable(col.get("id")) and col["id"] in existing:
                taken.add(col["id"])
        out, names = [], set()
        for col in columns:
            if not isinstance(col, dict):
                raise StoreError("Each column needs a name.")
            name = text(col.get("name"), "Column name", 40, required=True)
            if name.lower() in names:
                raise StoreError(f"There are two columns named {name}.")
            names.add(name.lower())
            limit = col.get("limit")
            if limit in ("", None):
                limit = None
            elif isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 999:
                raise StoreError(f"The limit for {name} must be a whole number from 1 to 999, or empty.")
            cid = col.get("id")
            if not usable(cid) or (cid not in existing and cid in taken):
                cid = slug(name, taken)
            taken.add(cid)
            out.append({"id": cid, "name": name, "limit": limit})
        kept = {c["id"] for c in out}
        return out, existing - kept

    def _apply_columns(self, board, columns, move_cards):
        """Set the columns. Cards in a removed column go where moveCards says; otherwise it's refused."""
        if move_cards is None:
            move_cards = {}
        if not isinstance(move_cards, dict):
            raise StoreError("moveCards must map a removed column to the column its cards go to.")
        out, removed = self._columns(board, columns)
        if not removed:
            board["columns"] = out
            return
        kept = {c["id"] for c in out}
        names = {c["id"]: c["name"] for c in board["columns"]}
        top = max((c["rank"] for c in board["cards"]), default=0)
        cards = list(board["cards"])
        for cid in removed:
            target = move_cards.get(cid)
            if target is not None and (not isinstance(target, str) or target not in kept):
                raise StoreError(f"The cards in {names[cid]} can't move to a column that isn't staying.")
            leaving = [i for i, c in enumerate(cards) if c["column"] == cid]
            active = sum(1 for i in leaving if not cards[i]["archived"])
            if active and target is None:
                raise Conflict(f"Move the {active} card{'s' if active > 1 else ''} out of {names[cid]} before removing it.")
            for i in sorted(leaving, key=lambda i: (cards[i]["rank"], key_sort(cards[i]["key"]))):
                if cards[i]["archived"] and target is None:
                    # An archived card still has to name a column, for when it comes back.
                    cards[i] = {**cards[i], "column": out[0]["id"]}
                else:
                    top += 1
                    cards[i] = {**cards[i], "column": target, "rank": top}
        board["cards"] = cards
        board["columns"] = out

    def _groups(self, board, groups, keep_ids=False):
        if not isinstance(groups, list):
            raise StoreError("Groups must be a list.")
        if len(groups) > MAX_GROUPS:
            raise StoreError(f"A board can have at most {MAX_GROUPS} groups.")
        existing = {g["id"] for g in board["groups"]}
        usable = lambda gid: isinstance(gid, str) and (gid in existing or (keep_ids and ID_RE.fullmatch(gid)))  # noqa: E731
        taken = {g["id"] for g in groups if isinstance(g, dict) and isinstance(g.get("id"), str) and g["id"] in existing}
        out, names = [], set()
        for g in groups:
            if not isinstance(g, dict):
                raise StoreError("Each group needs a name.")
            name = text(g.get("name"), "Group name", 60, required=True)
            if name.lower() in names:
                raise StoreError(f"There are two groups named {name}.")
            names.add(name.lower())
            color = g.get("color") or "slate"
            if not isinstance(color, str) or color not in COLORS:
                raise StoreError(f"That isn't one of the colors: {', '.join(COLORS)}.")
            link = text(g.get("link"), "Link", 500)
            if link and not LINK_RE.fullmatch(link):
                raise StoreError("Links must start with http:// or https:// and have no spaces.")
            gid = g.get("id")
            if not usable(gid) or (gid not in existing and gid in taken):
                gid = slug(name, taken)
            taken.add(gid)
            out.append({"id": gid, "name": name, "prefix": self._prefix(g.get("prefix"), f"The key prefix for {name}"),
                        "color": color, "link": link, "details": rows(g.get("details"), f"{name} details", "value", 500)})
        return out

    def _apply_groups(self, board, groups):
        out = self._groups(board, groups)
        kept = {g["id"] for g in out}
        names = {g["id"]: g["name"] for g in board["groups"]}
        cards = None
        for gid in {g["id"] for g in board["groups"]} - kept:
            active = sum(1 for c in board["cards"] if c["group"] == gid and not c["archived"])
            if active:
                raise Conflict(f"{active} card{'s' if active > 1 else ''} still belong{'s' if active == 1 else ''} to "
                               f"{names[gid]}. Move or delete them before removing it.")
            cards = [({**c, "group": None} if c["group"] == gid else c) for c in (cards or board["cards"])]
        if cards is not None:
            board["cards"] = cards
        board["groups"] = out

    @staticmethod
    def _template(names):
        """Section names a new card starts with, in order."""
        if not isinstance(names, list):
            raise StoreError("New card sections must be a list of names.")
        if len(names) > MAX_ROWS:
            raise StoreError(f"A new card can start with at most {MAX_ROWS} sections.")
        out = []
        for name in names:
            name = text(name, "A new card section name", 60, required=True)
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
            if isinstance(given, str) and ID_RE.fullmatch(given) and given not in taken:
                taken.add(given)
                return given
            made = make()
            taken.add(made)
            return made

        for cl in checklists:
            if not isinstance(cl, dict):
                raise StoreError("Each checklist needs a name.")
            name = text(cl.get("name"), "Checklist name", 60, required=True)
            if name.lower() in names:
                raise StoreError(f"There are two checklists named {name}.")
            names.add(name.lower())
            items = cl.get("items") or []
            if not isinstance(items, list):
                raise StoreError(f"The items in {name} must be a list.")
            if len(items) > MAX_CHECK_ITEMS:
                raise StoreError(f"{name} can have at most {MAX_CHECK_ITEMS} lines.")
            lines = []
            for item in items:
                if not isinstance(item, dict):
                    raise StoreError(f"Each line in {name} needs text.")
                lines.append({"id": new_id(item.get("id"), lambda: "i" + secrets.token_hex(4)),
                              "text": text(item.get("text"), f"A line in {name}", 500, required=True),
                              "heading": bool(item.get("heading"))})
            out.append({"id": new_id(cl.get("id"), lambda: slug(name, taken)), "name": name, "items": lines})
        return out

    @staticmethod
    def _check_ids(board):
        return {i["id"] for cl in board["checklists"] for i in cl["items"] if not i["heading"]}

    @staticmethod
    def _column_id(board, value):
        if not isinstance(value, str) or value not in {c["id"] for c in board["columns"]}:
            raise StoreError("That column doesn't exist on this board.")
        return value

    @staticmethod
    def _group_id(board, value):
        if value in (None, ""):
            return None
        if not isinstance(value, str) or value not in {g["id"] for g in board["groups"]}:
            raise StoreError("That group doesn't exist on this board.")
        return value

    def _card_changes(self, board, values):
        """Validate only the fields being changed, so an old oversized field can't block a simple move."""
        out = {}
        if "title" in values:
            out["title"] = text(values["title"], "Title", MAX_TITLE, required=True)
        if "column" in values:
            out["column"] = self._column_id(board, values["column"])
        if "group" in values:
            out["group"] = self._group_id(board, values["group"])
        if "rank" in values:
            out["rank"] = schema.rank(values["rank"])
        if "summary" in values:
            out["summary"] = text(values["summary"], "Summary", MAX_SUMMARY, multiline=True)
        if "blocked" in values:
            out["blocked"] = text(values["blocked"], "Blocked by", 500)
        if "fields" in values:
            out["fields"] = rows(values["fields"], "Fields", "value", 500)
        if "sections" in values:
            out["sections"] = rows(values["sections"], "Sections", "body", MAX_TEXT)
        if "notes" in values:
            out["notes"] = text(values["notes"], "Notes", MAX_TEXT, multiline=True)
        if "checks" in values:
            out["checks"] = self._checks(board, values["checks"])
        return out

    def _checks(self, board, checks, items=None):
        if checks is None:
            checks = []
        if not isinstance(checks, list) or not all(isinstance(c, str) for c in checks):
            raise StoreError("Checks must be a list of checklist item ids.")
        items = self._check_ids(board) if items is None else items
        return [c for c in dict.fromkeys(checks) if c in items]  # drop ticks for lines that no longer exist

    def _new_card(self, board, values, items):
        column = values.get("column") or board["columns"][0]["id"]
        rank = values.get("rank")
        stamp = now()
        return {"key": None, "title": text(values.get("title"), "Title", MAX_TITLE, required=True),
                "group": self._group_id(board, values.get("group")), "column": self._column_id(board, column),
                "rank": None if rank is None else schema.rank(rank),
                "summary": text(values.get("summary"), "Summary", MAX_SUMMARY, multiline=True),
                "blocked": text(values.get("blocked"), "Blocked by", 500),
                "fields": rows(values.get("fields"), "Fields", "value", 500),
                "sections": rows(values.get("sections"), "Sections", "body", MAX_TEXT),
                "notes": text(values.get("notes"), "Notes", MAX_TEXT, multiline=True),
                "checks": self._checks(board, values.get("checks"), items), "archived": None,
                "created": stamp, "updated": stamp}

    @staticmethod
    def _tidy_ranks(board, column):
        """Moves pick a rank halfway between two neighbours, and halving runs out. Renumber before it does."""
        members = sorted((c for c in board["cards"] if c["column"] == column), key=lambda c: (c["rank"], key_sort(c["key"])))
        if all(b["rank"] - a["rank"] >= MIN_RANK_GAP for a, b in zip(members, members[1:])):
            return
        ranks = {c["key"]: float(n) for n, c in enumerate(members, 1)}
        board["cards"] = [({**c, "rank": ranks[c["key"]]} if c["key"] in ranks else c) for c in board["cards"]]

    @staticmethod
    def _next_key(board, group_id):
        prefix = next((g["prefix"] for g in board["groups"] if g["id"] == group_id), board["prefix"])
        taken = {c["key"] for c in board["cards"]}
        n = board["counters"].get(prefix, 0) + 1
        while f"{prefix}-{n}" in taken:
            n += 1
        board["counters"][prefix] = n
        return f"{prefix}-{n}"
