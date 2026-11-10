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
from .properties import TYPES, _assign_ids, _shown, clean_definitions, find_property, prune_values
from .query import QueryError, prepare
from .templates import (BUILTIN_IDS, board_from_template, builtin_templates, describe, find_template, get_template,
                        template_from_board)
from .schema import (BadFile, Conflict, NotFound, StaleRevision, StoreError, Unavailable,  # noqa: F401 (re-exported)
                     CARD_FACE_PARTS, COLORS, ID_RE, KEY_RE, LIMIT_POLICIES, LINK_RE, MAX_BOARDS, MAX_CARD_LABELS,
                     MAX_CARDS, MAX_CHECK_ITEMS, MAX_CHECK_TEXT, MAX_CHECKLIST_NAME, MAX_CHECKLISTS, MAX_COLUMN_LIMIT,
                     MAX_COLUMN_NAME, MAX_COLUMNS, MAX_GROUP_LABEL, MAX_GROUP_NAME, MAX_GROUPS, MAX_LABEL_NAME, MAX_LABELS,
                     MAX_NAME, MAX_QUERY, MAX_ROWS, MAX_SECTION_NAME, MAX_SORT, MAX_SUMMARY, MAX_TASK_TEXT, MAX_TASKS,
                     MAX_TEMPLATES, MAX_TEXT, MAX_TITLE, MAX_VIEW_NAME, MAX_VIEWS, PREFIX_RE, SCHEMA_VERSION, default_settings, key_sort,
                     now, rows, slug, text)

DEFAULT_COLUMNS = [("Backlog", None), ("Ready", None), ("In progress", 1), ("Review", None), ("Done", None)]

CARD_INPUT = ("title", "group", "column", "summary", "blocked", "fields", "sections", "notes", "rank", "checks",
              "labels", "properties", "tasks")
CARD_FIELDS = CARD_INPUT + ("archived",)
BOARD_FIELDS = ("name", "description", "prefix", "groupLabel", "columns", "groups", "checklists", "template",
                "labels", "properties", "views", "settings", "moveCards")
SETTINGS_FIELDS = ("cardFace", "limitPolicy", "defaultView")
TASK_FIELDS = ("text", "done")
MIN_RANK_GAP = 1e-6
NO_BOARD = "That board doesn't exist. It may have been deleted."


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
            return {"version": SCHEMA_VERSION, "boards": [], "templates": []}
        try:
            with storage.FileLock(self.path + ".lock"):
                if os.path.exists(self.path):  # another program got here first
                    return self._read()
                data = {"version": SCHEMA_VERSION, "boards": [self._new_board("My board")], "templates": []}
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

    def _find(self, board_id, missing=NO_BOARD):
        for i, b in enumerate(self.data["boards"]):
            if b["id"] == board_id:
                return i, b
        raise NotFound(missing)

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
                "columns": cols, "groups": [], "checklists": [], "template": [], "labels": [], "properties": [], "views": [],
                "settings": default_settings(), "counters": {}, "cards": []}

    def _add_boards(self, boards):
        with self._changing():
            if len(self.data["boards"]) + len(boards) > MAX_BOARDS:
                raise Conflict(f"A file can hold at most {MAX_BOARDS} boards.")
            self._save({**self.data, "boards": self.data["boards"] + boards})

    def create_board(self, name, description=None, template=None):
        """Add a board. With `template` (a template's id or name) it gets that template's structure and starter
        cards, named `name` or, when that is empty, after the template, and described as the template is unless
        `description` is given."""
        if template is None:
            board = self._new_board(text(name, "Board name", MAX_NAME, required=True),
                                    description=text(description, "Description", MAX_SUMMARY))
        else:
            if not isinstance(template, str):
                raise StoreError("Say which template to use by its id or name.")
            with self._lock:
                self._refresh()
                chosen = get_template(self.data["templates"], template)
            name = text(name, "Board name", MAX_NAME) or None
            description = None if description is None else text(description, "Description", MAX_SUMMARY)
            board = self._imported_board(board_from_template(chosen, name, description))
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
                board["groupLabel"] = text(changes["groupLabel"], "Group label", MAX_GROUP_LABEL, required=True)
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
            # Settings last: the card face and the default view can name properties and views added in the same change.
            if "labels" in changes:
                board["labels"] = self._labels(board, changes["labels"])
                self._fit_card_labels(board)
            if "properties" in changes:
                self._apply_properties(board, changes["properties"])
            if "views" in changes:
                self._apply_views(board, changes["views"])
            if "settings" in changes:
                self._apply_settings(board, changes["settings"])
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
            boards.append(self._imported_board(raw, i + 1 if len(incoming) > 1 else None))
        self._add_boards(boards)
        return boards

    def import_board(self, board):
        """Add a board from an exported JSON object. It gets a new id; card keys are kept."""
        made = self._imported_board(board)
        self._add_boards([made])
        return made

    def _imported_board(self, board, number=None):
        """Build a board from untrusted JSON. Everything is validated; only known fields are kept.

        Takes the loose import shape (see docs/file-format.md) as well as a board exactly as exported.
        `number` is the board's place in a file of several. Errors name the board, and the card and part.
        """
        label = f"Board number {number}" if number else None
        try:
            if not isinstance(board, dict):
                raise StoreError("An imported board must be a JSON object.")
            name = text(board.get("name"), "Board name", MAX_NAME, required=True)
        except StoreError as e:
            raise type(e)(f"{label}: {e}" if label else str(e)) from e
        where = f'{label} ("{_shown(name)}")' if label else f'Board "{_shown(name)}"'
        try:
            draft = self._imported_parts(board, name)
        except StoreError as e:
            raise type(e)(f"{where}: {e}") from e
        items = self._check_ids(draft)
        column_names = {c["name"].lower(): c["id"] for c in draft["columns"]}
        group_names = {g["name"].lower(): g["id"] for g in draft["groups"]}
        seen, top_rank, built = set(), 0, []
        for n, raw in enumerate(board.get("cards") or [], 1):
            try:
                if not isinstance(raw, dict):
                    raise StoreError("It isn't a JSON object.")
                values = {k: raw[k] for k in CARD_INPUT if k in raw}
                # A hand-written file may say "column": "In progress" instead of using the id.
                if isinstance(values.get("column"), str):
                    values["column"] = column_names.get(values["column"].lower(), values["column"])
                if isinstance(values.get("group"), str):
                    values["group"] = group_names.get(values["group"].lower(), values["group"])
                if values.get("rank") is None:
                    values["rank"] = top_rank + 1
                if "labels" in values:
                    values["labels"] = self._imported_labels(draft, values["labels"])
                if "properties" in values:
                    values["properties"] = self._imported_values(draft, values["properties"])
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
                title = raw.get("title") if isinstance(raw, dict) else None
                title = f' ("{_shown(title.strip())}")' if isinstance(title, str) and title.strip() else ""
                raise type(e)(f"{where}, card number {n}{title}: {e}") from e
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
        try:
            self._check_views(draft)  # with the cards in: a label a card brought can be in a view
            self._check_limits(draft, {c["id"] for c in draft["columns"]})
        except QueryError as e:  # a plain error: an import has no search box to point into
            raise StoreError(f"{where}: {e}") from e
        except StoreError as e:
            raise type(e)(f"{where}: {e}") from e
        return draft

    def _imported_parts(self, board, name):
        """Everything on an imported board except its cards, as a new board with no cards yet."""
        draft = self._new_board(name, description=text(board.get("description"), "Description", MAX_SUMMARY))
        if board.get("prefix") is not None:
            draft["prefix"] = self._prefix(board["prefix"], "The board's key prefix")
        if board.get("groupLabel") is not None:
            draft["groupLabel"] = text(board["groupLabel"], "Group label", MAX_GROUP_LABEL, required=True)
        columns = board.get("columns")
        if columns:
            if isinstance(columns, list):  # a hand-written file may list just the names
                columns = [{"name": c} if isinstance(c, str) else c for c in columns]
            draft["columns"] = self._columns({"columns": [], "cards": []}, columns, keep_ids=True)[0]
        groups = board.get("groups")
        if groups:
            if isinstance(groups, list):  # a group with no key prefix of its own uses the board's
                groups = [{**g, "prefix": draft["prefix"]} if isinstance(g, dict) and g.get("prefix") is None else g
                          for g in groups]
            draft["groups"] = self._groups({"groups": [], "cards": []}, groups, keep_ids=True)
        if board.get("checklists"):
            draft["checklists"] = self._checklists(board["checklists"])
        if board.get("template"):
            draft["template"] = self._template(board["template"])
        if board.get("labels") is not None:
            draft["labels"] = self._labels(draft, board["labels"], keep_ids=True)
        if board.get("properties") is not None:
            self._apply_properties(draft, board["properties"], keep_ids=True)
        if board.get("views") is not None:
            self._apply_views(draft, board["views"], keep_ids=True, check=False)
        if board.get("settings") is not None:
            self._apply_settings(draft, board["settings"])
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
        return draft

    def _imported_labels(self, draft, value):
        """Card labels from an import, by id or name. A name the board doesn't have yet becomes a new label."""
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise StoreError("Labels must be a list of label names or ids.")
        for given in value:
            wanted = given.strip().casefold()
            if not any(lab["id"] == given or lab["name"].casefold() == wanted for lab in draft["labels"]):
                draft["labels"] = self._labels(draft, draft["labels"] + [{"name": given}], keep_ids=True)
        return self._card_labels(draft, value)

    @staticmethod
    def _imported_values(draft, value):
        """Card property values from an import, keyed by property name or id. Each value is tried as a stored
        value first, then as the text form people type (so "3", "yes" and "iOS, Web" all work)."""
        if not isinstance(value, dict):
            raise StoreError("Properties must map property names to values.")
        out = {}
        for given, raw in value.items():
            prop = find_property(draft, given)
            if prop is None:
                raise StoreError(f'There\'s no property called "{_shown(given)}". {_listed("Properties", draft["properties"])}')
            kind = TYPES[prop["type"]]
            try:
                cleaned = kind.clean_value(raw, prop)
            except StoreError:
                if not isinstance(raw, str):
                    raise
                cleaned = kind.parse_text(raw, prop)
            if cleaned is None:
                out.pop(prop["id"], None)
            else:
                out[prop["id"]] = cleaned
        return out

    # ---- templates -----------------------------------------------------------------------------
    #
    # Saved templates live in the file's top-level list, saved with the same lock and backups as boards.
    # Changing one never touches a board, so no board's rev moves.

    def list_templates(self):
        """The built-in templates, then the saved ones, as listings: {id, name, description, builtIn, columns, cards}."""
        return [describe(t) for t in builtin_templates() + self.saved_templates()]

    def saved_templates(self):
        with self._lock:
            self._refresh()
            return self.data["templates"]

    def save_template(self, board_id, name, description="", cards=False):
        """Save a board's structure as a template, with its cards as starter cards if `cards`. Returns the template."""
        if not isinstance(cards, bool):
            raise StoreError("cards must be true or false: whether the template keeps the board's cards.")
        with self._changing():
            _, board = self._find(board_id)
            saved = self.data["templates"]
            if len(saved) >= MAX_TEMPLATES:
                raise Conflict(f"A file can hold at most {MAX_TEMPLATES} saved templates. Delete one you don't use first.")
            template = template_from_board(board, name, description, include_cards=cards, taken_ids=[t["id"] for t in saved])
            self._unique_template_name(template["name"], saved)
            self._save({**self.data, "templates": saved + [template]})
        return template

    def update_template(self, template_id, changes):
        """Rename a saved template or change its description. Built-in templates can't be changed."""
        self._only(changes, ("name", "description"), "Templates")
        with self._changing():
            i, template = self._saved_template(template_id)
            template = dict(template)
            if "name" in changes:
                template["name"] = text(changes["name"], "The template name", MAX_NAME, required=True)
                self._unique_template_name(template["name"], self.data["templates"], template_id)
            if "description" in changes:
                template["description"] = text(changes["description"], "The template description", MAX_SUMMARY)
            templates = list(self.data["templates"])
            templates[i] = template
            self._save({**self.data, "templates": templates})
        return template

    def delete_template(self, template_id):
        """Delete a saved template, keeping a backup of the file first. Built-in templates can't be deleted."""
        with self._changing():
            i, template = self._saved_template(template_id)
            storage.tagged_snapshot(self.path, "before-delete")
            templates = self.data["templates"]
            self._save({**self.data, "templates": templates[:i] + templates[i + 1:]})
        return template

    def _saved_template(self, template_id):
        if template_id in BUILTIN_IDS:
            name = find_template([], template_id)["name"]
            raise Conflict(f"{name} is a built-in template, so it can't be changed or deleted. Save a board as a "
                           "template of your own instead.")
        for i, template in enumerate(self.data["templates"]):
            if template["id"] == template_id:
                return i, template
        raise NotFound("There's no saved template with that id. It may have been deleted.")

    @staticmethod
    def _unique_template_name(name, saved, own_id=None):
        for template in builtin_templates() + saved:
            if template["id"] != own_id and template["name"].strip().casefold() == name.casefold():
                raise Conflict(f"There's already a template called {template['name']}. Choose another name.")

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
            self._check_room(board, card["column"])
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
            old = board["cards"][i]
            card = {**old, **self._card_changes(board, changes, old)}
            if "archived" in changes:
                was = old["archived"]
                if changes["archived"] not in (True, False, None):
                    raise StoreError("archived must be true or false.")
                card["archived"] = (was or now()) if changes["archived"] else None
                if was and not card["archived"] and "rank" not in changes:  # comes back at the bottom of its column
                    card["rank"] = max((c["rank"] for c in board["cards"]), default=0) + 1
            if not card["archived"] and (card["column"] != old["column"] or old["archived"]):
                self._check_room(board, card["column"], moving=key)
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

    def transfer_card(self, board_id, key, target_id, column=None, rev=None, target_rev=None):
        """Move a card to another board, saving both boards in one step. Both revs are checked and both go up.

        The card gets a new key from the target's counters and goes to the bottom of `column` (an id or a
        name on the target), or else the column with the same name as its own, or else the first column.
        Its group, labels, property values and checklist ticks carry over by name where the target has
        them; `dropped` lists, as short text, each one that couldn't. Everything else on the card carries
        over as it is. Returns {"board": source, "target": target, "key": new key, "dropped": [...]}.
        """
        if target_id == board_id:
            raise StoreError("That card is already on this board. Choose another board to move it to.")

        def apply(drafts):
            source, target = drafts
            i = self._card_index(source, key)
            card, dropped = self._carried(source, target, source["cards"][i])
            card["column"] = self._target_column(target, column, source, source["cards"][i])
            if len(target["cards"]) >= MAX_CARDS:
                raise Conflict(f"{target['name']} already has {MAX_CARDS} cards, the most a board can hold.")
            if not card["archived"]:
                self._check_room(target, card["column"])
            card["rank"] = max((c["rank"] for c in target["cards"]), default=0) + 1
            card["key"] = self._next_key(target, card["group"])
            del source["cards"][i]
            target["cards"].append(card)
            self._tidy_ranks(target, card["column"])
            return card["key"], dropped

        (source, target), (new_key, dropped) = self._mutate_many(
            [(board_id, rev, NO_BOARD), (target_id, target_rev, "The board to move it to doesn't exist. It may have been deleted.")],
            apply)
        return {"board": source, "target": target, "key": new_key, "dropped": dropped}

    # ---- card tasks ----------------------------------------------------------------------------

    def add_task(self, board_id, key, values, rev=None):
        """Add a task to the end of a card's list. Returns (board, the new task's id)."""
        self._only(values, TASK_FIELDS, "Tasks")

        def apply(board):
            i = self._card_index(board, key)
            card = board["cards"][i]
            if len(card["tasks"]) >= MAX_TASKS:
                raise Conflict(f"A card can have at most {MAX_TASKS} tasks. Remove some first.")
            task = {"id": self._new_task_id({t["id"] for t in card["tasks"]}), **self._task(values)}
            board["cards"][i] = {**card, "tasks": card["tasks"] + [task], "updated": now()}
            return task["id"]

        return self._mutate(board_id, rev, apply)

    def update_task(self, board_id, key, task_id, changes, rev=None):
        """Change a task's text, or whether it's done. Only what is sent changes."""
        self._only(changes, TASK_FIELDS, "Tasks")

        def apply(board):
            i = self._card_index(board, key)
            card = board["cards"][i]
            j = self._task_index(card, task_id)
            tasks = list(card["tasks"])
            tasks[j] = {**tasks[j], **self._task(changes, partial=True)}
            board["cards"][i] = {**card, "tasks": tasks, "updated": now()}

        return self._mutate(board_id, rev, apply)[0]

    def delete_task(self, board_id, key, task_id, rev=None):
        def apply(board):
            i = self._card_index(board, key)
            card = board["cards"][i]
            j = self._task_index(card, task_id)
            board["cards"][i] = {**card, "tasks": card["tasks"][:j] + card["tasks"][j + 1:], "updated": now()}

        return self._mutate(board_id, rev, apply)[0]

    # ---- internals -----------------------------------------------------------------------------

    def _mutate(self, board_id, rev, apply):
        """Apply a change to a new copy of the board, save it, then swap it in. Returns (board, result)."""
        drafts, result = self._mutate_many([(board_id, rev, NO_BOARD)], lambda drafts: apply(drafts[0]))
        return drafts[0], result

    def _mutate_many(self, targets, apply):
        """Apply a change to new copies of one or more boards, save them in one write, then swap them in.

        `targets` lists (board id, rev or None, message if the board is missing). Every rev is checked
        before anything changes, and every board's rev goes up. Returns (drafts, result).
        """
        with self._changing():
            found = []
            for board_id, rev, missing in targets:
                i, board = self._find(board_id, missing)
                self._check_rev(board, rev)
                found.append(i)
            drafts = []
            for i in found:
                board = self.data["boards"][i]
                drafts.append({**board, "cards": list(board["cards"]), "counters": dict(board["counters"])})
            result = apply(drafts)
            stamp = now()
            boards = list(self.data["boards"])
            for i, draft in zip(found, drafts):
                draft["rev"] += 1
                draft["updated"] = stamp
                boards[i] = draft
            self._save({**self.data, "boards": boards})
            return drafts, result

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
            name = text(col.get("name"), "Column name", MAX_COLUMN_NAME, required=True)
            if name.lower() in names:
                raise StoreError(f"There are two columns named {name}.")
            names.add(name.lower())
            limit = col.get("limit")
            if limit in ("", None):
                limit = None
            elif isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_COLUMN_LIMIT:
                raise StoreError(f"The limit for {name} must be a whole number from 1 to {MAX_COLUMN_LIMIT}, or empty.")
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
        receiving = set()
        for cid in removed:
            target = move_cards.get(cid)
            if target is not None and (not isinstance(target, str) or target not in kept):
                raise StoreError(f"The cards in {names[cid]} can't move to a column that isn't staying.")
            leaving = [i for i, c in enumerate(cards) if c["column"] == cid]
            active = sum(1 for i in leaving if not cards[i]["archived"])
            if active and target is None:
                raise Conflict(f"Move the {active} card{'s' if active > 1 else ''} out of {names[cid]} before removing it.")
            if active:
                receiving.add(target)
            for i in sorted(leaving, key=lambda i: (cards[i]["rank"], key_sort(cards[i]["key"]))):
                if cards[i]["archived"] and target is None:
                    # An archived card still has to name a column, for when it comes back.
                    cards[i] = {**cards[i], "column": out[0]["id"]}
                else:
                    top += 1
                    cards[i] = {**cards[i], "column": target, "rank": top}
        board["cards"] = cards
        board["columns"] = out
        self._check_limits(board, receiving)

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
            name = text(g.get("name"), "Group name", MAX_GROUP_NAME, required=True)
            if name.lower() in names:
                raise StoreError(f"There are two groups named {name}.")
            names.add(name.lower())
            color = g.get("color") or COLORS[(len(names) - 1) % len(COLORS)]  # a hand-written file needn't choose
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
            name = text(name, "A new card section name", MAX_SECTION_NAME, required=True)
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
            name = text(cl.get("name"), "Checklist name", MAX_CHECKLIST_NAME, required=True)
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
                              "text": text(item.get("text"), f"A line in {name}", MAX_CHECK_TEXT, required=True),
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

    def _card_changes(self, board, values, card=None):
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
        if "labels" in values:
            out["labels"] = self._card_labels(board, values["labels"])
        if "properties" in values:
            out["properties"] = self._card_values(board, values["properties"], card["properties"] if card else None)
        if "tasks" in values:
            out["tasks"] = self._tasks(values["tasks"])
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
                "checks": self._checks(board, values.get("checks"), items),
                "labels": self._card_labels(board, values.get("labels")),
                "properties": self._card_values(board, values.get("properties")),
                "tasks": self._tasks(values.get("tasks")),
                "archived": None, "created": stamp, "updated": stamp}

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

    @staticmethod
    def _only(values, allowed, what):
        if not isinstance(values, dict):
            raise StoreError(f"{what} take an object of changes.")
        unknown = set(values) - set(allowed)
        if unknown:
            raise StoreError(f"{what} have no {', '.join(sorted(unknown))} field.")

    # ---- column limits -------------------------------------------------------------------------

    @staticmethod
    def _check_room(board, column_id, moving=None):
        """When the board refuses cards over a column's limit, a full column takes no more cards.

        `moving` is the key of a card already on the board, which doesn't count against its new column.
        Archived cards never count, as on the page.
        """
        if board["settings"]["limitPolicy"] != "refuse":
            return
        col = next(c for c in board["columns"] if c["id"] == column_id)
        if col["limit"] is None:
            return
        held = sum(1 for c in board["cards"] if c["column"] == column_id and not c["archived"] and c["key"] != moving)
        if held >= col["limit"]:
            raise Conflict(f"{col['name']} is full: its limit is {Store._cards(col['limit'])}. Move a card out of it "
                           "first, or raise the limit in the board's settings.")

    @staticmethod
    def _check_limits(board, column_ids):
        """After cards arrive in bulk (an import, or a removed column's cards), each of these columns must fit."""
        if board["settings"]["limitPolicy"] != "refuse":
            return
        for col in board["columns"]:
            if col["id"] in column_ids and col["limit"] is not None:
                held = sum(1 for c in board["cards"] if c["column"] == col["id"] and not c["archived"])
                if held > col["limit"]:
                    raise Conflict(f"That would put {Store._cards(held)} in {col['name']}, over its limit of {col['limit']}, "
                                   "and this board refuses cards once a column is full.")

    @staticmethod
    def _cards(n):
        return f"{n} card{'' if n == 1 else 's'}"

    # ---- labels --------------------------------------------------------------------------------

    @staticmethod
    def _labels(board, labels, keep_ids=False):
        """A whole new list of labels for the board, kept in the order given.

        A label keeps the id it sends when the board has that id (renaming keeps it on its cards); with
        `keep_ids` (imports) any well formed id is kept. A label sent without an id keeps the id of the
        label with the same name. A color left out keeps the old one, or takes the next color in turn.
        """
        if not isinstance(labels, list):
            raise StoreError("Labels must be a list.")
        if len(labels) > MAX_LABELS:
            raise StoreError(f"A board can have at most {MAX_LABELS} labels.")
        entries, names = [], set()
        for raw in labels:
            raw = {"name": raw} if isinstance(raw, str) else raw
            if not isinstance(raw, dict):
                raise StoreError("Each label needs a name.")
            name = text(raw.get("name"), "Label name", MAX_LABEL_NAME, required=True)
            if name.casefold() in names:
                raise StoreError(f"There are two labels named {name}.")
            names.add(name.casefold())
            entries.append((raw, name))
        old_colors = {lab["id"]: lab["color"] for lab in board["labels"]}
        out = []
        for i, ((raw, name), lid) in enumerate(zip(entries, _assign_ids(entries, board["labels"], keep_ids, (), str.casefold))):
            color = raw.get("color") or old_colors.get(lid) or COLORS[i % len(COLORS)]
            if not isinstance(color, str) or color not in COLORS:
                raise StoreError(f"The color for the label {name} must be one of: {', '.join(COLORS)}.")
            out.append({"id": lid, "name": name, "color": color})
        return out

    @staticmethod
    def _fit_card_labels(board):
        """After the labels change: cards keep only labels that still exist, in the board's order."""
        order = {lab["id"]: i for i, lab in enumerate(board["labels"])}
        cards, changed = [], False
        for card in board["cards"]:
            fitted = sorted((lid for lid in card["labels"] if lid in order), key=order.__getitem__)
            if fitted != card["labels"]:
                card, changed = {**card, "labels": fitted}, True
            cards.append(card)
        if changed:
            board["cards"] = cards

    @staticmethod
    def _card_labels(board, value):
        """A card's labels from ids or names (ignoring case), in the board's order, each once."""
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise StoreError("Labels must be a list of label names or ids.")
        by_id = {lab["id"] for lab in board["labels"]}
        by_name = {lab["name"].casefold(): lab["id"] for lab in board["labels"]}
        picked = set()
        for given in value:
            found = given if given in by_id else by_name.get(given.strip().casefold())
            if found is None:
                raise StoreError(f'There\'s no label called "{_shown(given.strip())}". {_listed("Labels", board["labels"])}')
            picked.add(found)
        if len(picked) > MAX_CARD_LABELS:
            raise StoreError(f"A card can have at most {MAX_CARD_LABELS} labels.")
        return [lab["id"] for lab in board["labels"] if lab["id"] in picked]

    # ---- properties ----------------------------------------------------------------------------

    def _apply_properties(self, board, raw, keep_ids=False):
        """Set the board's property definitions, clear card values that no longer fit, and keep the card face
        in step: a property sent with a changed onCard joins or leaves it, and a removed property leaves it."""
        before = {p["id"]: p["onCard"] for p in board["properties"]}
        board["properties"] = clean_definitions(board, raw, keep_ids=keep_ids)
        prune_values(board)
        ids = {p["id"] for p in board["properties"]}
        face = [part for part in board["settings"]["cardFace"] if part in CARD_FACE_PARTS or part in ids]
        for prop in board["properties"]:
            if prop["onCard"] != before.get(prop["id"], False):
                face = [part for part in face if part != prop["id"]] + ([prop["id"]] if prop["onCard"] else [])
        self._set_card_face(board, face)

    @staticmethod
    def _card_values(board, value, current=None):
        """A card's property values from API JSON keyed by property id, merged into `current`. None clears a value."""
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise StoreError("Properties must map property ids to values.")
        by_id = {p["id"]: p for p in board["properties"]}
        out = dict(current or {})
        for pid, given in value.items():
            prop = by_id.get(pid)
            if prop is None:
                raise StoreError(f'There\'s no property with the id "{_shown(pid)}". {_listed("Properties", board["properties"])}')
            cleaned = TYPES[prop["type"]].clean_value(given, prop)
            if cleaned is None:
                out.pop(pid, None)
            else:
                out[pid] = cleaned
        return {p["id"]: out[p["id"]] for p in board["properties"] if p["id"] in out}

    # ---- saved views and settings --------------------------------------------------------------

    @staticmethod
    def _views(board, views, keep_ids=False):
        """A whole new list of saved views. Ids are kept as labels' are; a query or sort left out keeps the old one.

        The query and sort are stored as they are given; `_check_views` makes sure they can be read.
        """
        if not isinstance(views, list):
            raise StoreError("Saved views must be a list.")
        if len(views) > MAX_VIEWS:
            raise StoreError(f"A board can have at most {MAX_VIEWS} saved views.")
        entries, names = [], set()
        for raw in views:
            if not isinstance(raw, dict):
                raise StoreError("Each saved view needs a name.")
            name = text(raw.get("name"), "View name", MAX_VIEW_NAME, required=True)
            if name.casefold() in names:
                raise StoreError(f"There are two saved views named {name}.")
            names.add(name.casefold())
            entries.append((raw, name))
        old = {v["id"]: v for v in board["views"]}
        out = []
        for (raw, name), vid in zip(entries, _assign_ids(entries, board["views"], keep_ids, (), str.casefold)):
            before = old.get(vid, {})
            out.append({"id": vid, "name": name,
                        "query": text(raw.get("query", before.get("query", "")), f"The search for {name}", MAX_QUERY),
                        "sort": text(raw.get("sort", before.get("sort", "")), f"The sort for {name}", MAX_SORT)})
        return out

    def _apply_views(self, board, views, keep_ids=False, check=True):
        before = {v["id"]: v for v in board["views"]}
        board["views"] = self._views(board, views, keep_ids)
        if check:
            self._check_views(board, before)
        default = board["settings"]["defaultView"]
        if default is not None and default not in {v["id"] for v in board["views"]}:
            board["settings"] = {**board["settings"], "defaultView": None}

    @staticmethod
    def _check_views(board, before=None):
        """Every saved view that is new, or whose search or sort changed, must work on the board as it now is.

        A view left as it was isn't read again: one that stopped working when a property it names was
        removed stays on the board, so no change elsewhere loses it, and using it says what's wrong.
        Raises QueryError, with the position in the view's search or sort.
        """
        for view in board["views"]:
            old = (before or {}).get(view["id"])
            if old is not None and old["query"] == view["query"] and old["sort"] == view["sort"]:
                continue
            try:
                prepare(board, view["query"], view["sort"])
            except QueryError as e:
                raise QueryError(f"The saved view \"{_shown(view['name'])}\" can't be saved: {e}", e.position, e.part) from None

    def _apply_settings(self, board, raw):
        """Change some of the board's settings; those left out stay as they are."""
        if not isinstance(raw, dict):
            raise StoreError("Settings must be an object.")
        unknown = set(raw) - set(SETTINGS_FIELDS)
        if unknown:
            raise StoreError(f"Boards have no {', '.join(sorted(unknown))} setting.")
        settings = dict(board["settings"])
        if "limitPolicy" in raw:
            if not isinstance(raw["limitPolicy"], str) or raw["limitPolicy"] not in LIMIT_POLICIES:
                raise StoreError("The limit policy must be warn (a column over its limit is flagged) or refuse "
                                 "(a full column takes no more cards).")
            settings["limitPolicy"] = raw["limitPolicy"]
        if "defaultView" in raw:
            settings["defaultView"] = self._view_id(board, raw["defaultView"])
        board["settings"] = settings
        if "cardFace" in raw:
            self._set_card_face(board, self._card_face(board, raw["cardFace"]))

    @staticmethod
    def _card_face(board, value):
        """What cards show on the board, in order: parts of a card by name, and properties by name or id."""
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise StoreError("The card face must be a list of what each card shows.")
        if len(value) > len(CARD_FACE_PARTS) + len(board["properties"]):
            raise StoreError("The card face lists more than there is to show.")
        out = []
        for given in value:
            word = given.strip()
            if word.lower() in CARD_FACE_PARTS:
                part = word.lower()
            else:
                prop = find_property(board, word)
                part = prop["id"] if prop is not None else None
            if part is None:
                props = ", ".join(p["name"] for p in board["properties"])
                raise StoreError(f'A card can\'t show "{_shown(word)}". It can show {", ".join(CARD_FACE_PARTS)}'
                                 + (f", and the properties {props}." if props else "."))
            if part in out:
                raise StoreError(f'The card face lists "{_shown(word)}" twice.')
            out.append(part)
        return out

    @staticmethod
    def _set_card_face(board, face):
        """settings.cardFace is what counts; each property's onCard follows it."""
        if face != board["settings"]["cardFace"]:
            board["settings"] = {**board["settings"], "cardFace": face}
        shown = set(face)
        if any(p["onCard"] != (p["id"] in shown) for p in board["properties"]):
            board["properties"] = [p if p["onCard"] == (p["id"] in shown) else {**p, "onCard": p["id"] in shown}
                                   for p in board["properties"]]

    @staticmethod
    def _view_id(board, value):
        """A saved view by id, or by name ignoring case. None or "" means no default view."""
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise StoreError("The default view must be the name of a saved view, or nothing.")
        for view in board["views"]:
            if view["id"] == value:
                return view["id"]
        for view in board["views"]:
            if view["name"].casefold() == value.strip().casefold():
                return view["id"]
        raise StoreError(f'There\'s no saved view called "{_shown(value.strip())}". {_listed("Saved views", board["views"])}')

    # ---- tasks ---------------------------------------------------------------------------------

    @staticmethod
    def _task(raw, partial=False):
        """A task's text and done, cleaned. With `partial`, only the ones sent."""
        out = {}
        if not partial or "text" in raw:
            out["text"] = text(raw.get("text"), "A task", MAX_TASK_TEXT, required=True)
        if not partial or "done" in raw:
            done = raw.get("done", False)
            if not isinstance(done, bool):
                raise StoreError("Whether a task is done must be true or false.")
            out["done"] = done
        return out

    @classmethod
    def _tasks(cls, value):
        """A card's whole task list. Tasks may be {id, text, done} or just text. Ids sent are kept if they're
        well formed and not used twice; the rest get new ones."""
        if value is None:
            return []
        if not isinstance(value, list):
            raise StoreError("Tasks must be a list.")
        if len(value) > MAX_TASKS:
            raise StoreError(f"A card can have at most {MAX_TASKS} tasks.")
        rows_in = [{"text": raw} if isinstance(raw, str) else raw for raw in value]
        if not all(isinstance(raw, dict) for raw in rows_in):
            raise StoreError("Each task needs text.")
        cleaned = [cls._task(raw) for raw in rows_in]
        kept, taken = [], set()
        for raw in rows_in:
            given = raw.get("id")
            usable = isinstance(given, str) and ID_RE.fullmatch(given) and given not in taken
            kept.append(given if usable else None)
            if usable:
                taken.add(given)
        return [{"id": tid or cls._new_task_id(taken), **task} for tid, task in zip(kept, cleaned)]

    @staticmethod
    def _new_task_id(taken):
        """A short id no other task on the card has. `taken` gets it added."""
        while True:
            made = "t" + secrets.token_hex(3)
            if made not in taken:
                taken.add(made)
                return made

    @staticmethod
    def _task_index(card, task_id):
        for i, task in enumerate(card["tasks"]):
            if task["id"] == task_id:
                return i
        raise NotFound(f"{card['key']} has no task {task_id}. It may have been removed.")

    # ---- moving a card to another board --------------------------------------------------------

    @staticmethod
    def _carried(source, target, card):
        """The card as it will be on the target board (no key, column or rank yet), and what couldn't come."""
        dropped = []
        group = None
        if card["group"] is not None:
            name = next(g["name"] for g in source["groups"] if g["id"] == card["group"])
            group = next((g["id"] for g in target["groups"] if g["name"].casefold() == name.casefold()), None)
            if group is None:
                dropped.append(f"{source['groupLabel']}: {name}")
        names = {lab["id"]: lab["name"] for lab in source["labels"]}
        there = {lab["name"].casefold(): lab["id"] for lab in target["labels"]}
        labels = set()
        for lid in card["labels"]:
            match = there.get(names[lid].casefold())
            if match is None:
                dropped.append(f"Label: {names[lid]}")
            else:
                labels.add(match)
        values = {}
        for prop in source["properties"]:
            if prop["id"] in card["properties"]:
                value = card["properties"][prop["id"]]
                shown = TYPES[prop["type"]].format_text(value, prop)
                other = next((p for p in target["properties"] if p["name"].casefold() == prop["name"].casefold()), None)
                fitted = None if other is None else Store._fit_value(other, value, shown)
                if fitted is None:
                    dropped.append(f"{prop['name']}: {shown}")
                else:
                    values[other["id"]] = fitted
        lines = {item["id"]: (cl["name"], item["text"]) for cl in source["checklists"] for item in cl["items"]}
        there = {(cl["name"].casefold(), item["text"].casefold()): item["id"]
                 for cl in target["checklists"] for item in cl["items"] if not item["heading"]}
        checks = []
        for cid in card["checks"]:
            if cid in lines:
                match = there.get((lines[cid][0].casefold(), lines[cid][1].casefold()))
                if match is None:
                    dropped.append(f"Tick: {lines[cid][1]}")
                elif match not in checks:
                    checks.append(match)
        moved = {**card, "group": group, "labels": [lab["id"] for lab in target["labels"] if lab["id"] in labels],
                 "properties": {p["id"]: values[p["id"]] for p in target["properties"] if p["id"] in values},
                 "checks": checks, "updated": now()}
        return moved, dropped

    @staticmethod
    def _fit_value(prop, value, shown):
        """A value from another board's property, made to fit `prop`: by its text first (choices by name), then
        as it was stored (a number into a property with another unit). None if neither fits."""
        kind = TYPES[prop["type"]]
        for attempt in (lambda: kind.parse_text(shown, prop), lambda: kind.clean_value(value, prop)):
            try:
                fitted = attempt()
            except StoreError:
                continue
            if fitted is not None:
                return fitted
        return None

    @staticmethod
    def _target_column(target, column, source, card):
        """The column named (by id or name), or else the one with the card's own column's name, or the first."""
        if column not in (None, ""):
            if not isinstance(column, str):
                raise StoreError("The column to move it to must be a column's name or id.")
            for col in target["columns"]:
                if col["id"] == column:
                    return col["id"]
            for col in target["columns"]:
                if col["name"].casefold() == column.strip().casefold():
                    return col["id"]
            raise StoreError(f'{target["name"]} has no column called "{_shown(column.strip())}". '
                             f'{_listed("Columns", target["columns"])}')
        name = next(c["name"] for c in source["columns"] if c["id"] == card["column"]).casefold()
        return next((c["id"] for c in target["columns"] if c["name"].casefold() == name), target["columns"][0]["id"])


def _listed(what, items):
    """The names a person could have meant, for the end of a message."""
    if not items:
        return f"This board has no {what.lower()} yet."
    return f"{what}: {', '.join(item['name'] for item in items)}."
