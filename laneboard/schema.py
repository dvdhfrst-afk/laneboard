"""What a boards file looks like: limits, text rules, upgrades from older versions, the load check.

The load check never changes a value that is already there. It fills in optional fields that are
missing, and it refuses a file it can't trust with a message that says where the problem is.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

SCHEMA_VERSION = 3

COLORS = ("blue", "teal", "violet", "amber", "rose", "green", "slate")
PREFIX_RE = re.compile(r"[A-Z][A-Z0-9]{0,7}")
KEY_RE = re.compile(r"([A-Z][A-Z0-9]{0,7})-([1-9][0-9]{0,5})")
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
BOARD_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
ANY_KEY_RE = re.compile(r"([A-Z][A-Z0-9]*)-([0-9]+)")
LINK_RE = re.compile(r"https?://\S*", re.IGNORECASE)

MAX_NAME = 120
MAX_TITLE = 200
MAX_SUMMARY = 1000
MAX_TEXT = 20000
MAX_BOARDS = 500
MAX_IMPORT_BYTES = 64_000_000  # a file to import, read by the command line or sent to the server
MAX_TEMPLATES = 100  # saved ones, besides the built-in ones
MAX_CARDS = 20000
MAX_COLUMNS = 20
MAX_COLUMN_NAME = 40
MAX_COLUMN_LIMIT = 999
MAX_GROUPS = 50
MAX_GROUP_NAME = 60
MAX_GROUP_LABEL = 30
MAX_ROWS = 30  # fields, sections, details
MAX_FIELD_TEXT = 500  # a field's value, the blocked-by line, and a group's link and details
MAX_SECTION_NAME = 60  # the sections a new card starts with
MAX_CHECKLISTS = 10
MAX_CHECKLIST_NAME = 60
MAX_CHECK_ITEMS = 100
MAX_CHECK_TEXT = 500
MAX_RANK = 1e15
MAX_LABELS = 50
MAX_LABEL_NAME = 40
MAX_CARD_LABELS = 20
MAX_VIEWS = 30
MAX_VIEW_NAME = 60
MAX_QUERY = 500  # a search, as typed or saved in a view
MAX_SORT = 200  # a saved view's sort
MAX_TASKS = 100
MAX_TASK_TEXT = 300

# What a card can show on the board, besides property values. settings.cardFace lists these and property ids.
CARD_FACE_PARTS = ("group", "labels", "field", "progress", "blocked")
LIMIT_POLICIES = ("warn", "refuse")

# Card properties (laneboard/properties.py checks and cleans them)
MAX_PROPERTIES = 30
MAX_PROPERTY_NAME = 40
MAX_PROPERTY_TEXT = 500  # text and link values
MAX_CHOICES = 50
MAX_CHOICE_NAME = 40
MAX_MULTI = 20  # choices picked at once in a multi property
MAX_UNIT = 10
MAX_DECIMALS = 4
MAX_NUMBER = 10 ** 15
RATING_MAX_RANGE = (3, 10)  # how high a rating property can go: at least 3, at most 10
DEFAULT_RATING_MAX = 5

_SURROGATE = re.compile("[\ud800-\udfff]")
_CONTROL_LINE = re.compile("[\x00-\x1f\x7f-\x9f  ]+")
_CONTROL_TEXT = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


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


class Unavailable(StoreError):
    """The boards file can't be read or written right now. Nothing was changed (HTTP 503)."""
    status = 503


class BadFile(Unavailable):
    """The boards file (or a backup) isn't something Laneboard can safely use."""


def default_settings():
    """A board's settings when nothing has been chosen: today's card face, and full columns only flagged."""
    return {"cardFace": list(CARD_FACE_PARTS), "limitPolicy": "warn", "defaultView": None}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def text(value, label, limit, required=False, multiline=False):
    """Clean text coming from a person or an imported file, or refuse it."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise StoreError(f"{label} must be text.")
    value = _SURROGATE.sub("�", value)  # half of an emoji can't be saved as UTF-8
    if multiline:
        value = _CONTROL_TEXT.sub("", value.replace("\r\n", "\n").replace("\r", "\n")).rstrip()
    else:
        value = _CONTROL_LINE.sub(" ", value).strip()
    if required and not value:
        raise StoreError(f"{label} can't be empty.")
    if len(value) > limit:
        raise StoreError(f"{label} is too long ({len(value)} characters; the limit is {limit}).")
    return value


def rows(value, label, value_key, value_limit):
    """A list of {label, <value_key>} rows, as used by card fields, card sections and group details."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise StoreError(f"{label} must be a list.")
    if len(value) > MAX_ROWS:
        raise StoreError(f"{label} can have at most {MAX_ROWS} entries.")
    out = []
    for row in value:
        if not isinstance(row, dict):
            raise StoreError(f"Each entry in {label} needs a label and a {value_key}.")
        out.append({"label": text(row.get("label"), f"A label in {label}", 60, required=True),
                    value_key: text(row.get(value_key), f"{label} text", value_limit, multiline=True)})
    return out


def rank(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StoreError("Rank must be a number.")
    if not math.isfinite(value) or abs(value) > MAX_RANK:
        raise StoreError(f"Rank must be a number between -{MAX_RANK:.0f} and {MAX_RANK:.0f}.")
    return value


def slug(name, taken):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "item"
    made, n = base[:40].rstrip("-"), 2
    while made in taken:
        made = f"{base[:36].rstrip('-')}-{n}"
        n += 1
    return made


def done_columns(board):
    """The ids of the columns whose cards count as done: the columns marked done, or the last column when none is.

    This one rule decides for search (is:done), the summary, the exports, the command line and the page.
    """
    columns = board.get("columns") or []
    marked = {c.get("id") for c in columns if c.get("done")}
    return marked if marked or not columns else {columns[-1].get("id")}


def tickable_ids(board):
    """Ids of the checklist lines a card can tick (headings can't be ticked)."""
    return {item.get("id") for cl in board.get("checklists") or [] for item in cl.get("items") or [] if not item.get("heading")}


def key_sort(key):
    """Sort T-2 before T-10."""
    m = ANY_KEY_RE.fullmatch(key)
    return (m.group(1), int(m.group(2))) if m else (key, 0)


def decode_text(raw: bytes, where: str) -> str:
    """Text from a file's bytes: UTF-8, or the UTF-16 that PowerShell redirection writes, without a BOM."""
    try:
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return raw.decode("utf-16")
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise BadFile(f"{where} isn't UTF-8 text ({e.reason} at byte {e.start}).") from e


# A boards file nests about 8 deep and the exports Laneboard reads not much more. How deep Python's own JSON reader
# can go before it gives up depends on the system (much deeper on Linux than on Windows), so the limit is set here.
MAX_NESTING = 64


def nested_too_deeply(value) -> bool:
    """Whether parsed JSON goes more than MAX_NESTING lists and objects deep. Walks without recursing."""
    if not isinstance(value, (dict, list)):
        return False
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_NESTING:
            return True
        for child in (item.values() if isinstance(item, dict) else item):
            if isinstance(child, (dict, list)):
                stack.append((child, depth + 1))
    return False


def parse_json(raw: bytes, where: str):
    """Parse JSON bytes from a file. Handles the BOMs that Notepad and PowerShell add."""
    content = decode_text(raw, where)
    if not content.strip():
        raise BadFile(f"{where} is empty.")
    try:
        value = json.loads(content, parse_constant=_refuse_constant)
        if nested_too_deeply(value):
            raise RecursionError
        return value
    except RecursionError as e:
        raise BadFile(f"{where} is nested too deeply to be a board file.") from e
    except json.JSONDecodeError as e:
        raise BadFile(f"{where} isn't valid JSON: {e.msg} at line {e.lineno}, column {e.colno}.") from e
    except ValueError as e:
        raise BadFile(f"{where} isn't valid JSON: {e}.") from e


def _refuse_constant(name):
    raise ValueError(f"{name} isn't allowed in a board file")


# ---- upgrades ------------------------------------------------------------------------------------


def _upgrade_1_to_2(data):
    """Version 2 adds `archived` to cards. Version 1 files may also predate checklists and templates."""
    for board in data["boards"]:
        if isinstance(board, dict):
            for card in board.get("cards") if isinstance(board.get("cards"), list) else []:
                if isinstance(card, dict):
                    card.setdefault("archived", None)


def _upgrade_2_to_3(data):
    """Version 3 adds labels, properties, saved views and settings to boards; labels, properties and tasks to
    cards; and a list of saved templates to the file. Everything starts empty, so nothing on a board changes."""
    data.setdefault("templates", [])
    for board in data["boards"]:
        if isinstance(board, dict):
            for name in ("labels", "properties", "views"):
                board.setdefault(name, [])
            board.setdefault("settings", default_settings())
            for card in board.get("cards") if isinstance(board.get("cards"), list) else []:
                if isinstance(card, dict):
                    card.setdefault("labels", [])
                    card.setdefault("properties", {})
                    card.setdefault("tasks", [])


UPGRADES = {1: _upgrade_1_to_2, 2: _upgrade_2_to_3}


def upgrade(data, where):
    """Bring parsed file contents up to SCHEMA_VERSION. Returns the version the file was at."""
    if not isinstance(data, dict) or not isinstance(data.get("boards"), list):
        raise BadFile(f"{where} doesn't look like a Laneboard file: it has no list of boards.")
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise BadFile(f"{where} doesn't look like a Laneboard file: its version number is missing or wrong.")
    if version > SCHEMA_VERSION:
        raise BadFile(f"{where} was written by a newer Laneboard (file format {version}; this one reads up to "
                      f"{SCHEMA_VERSION}). Nothing was changed. Update Laneboard to open it.")
    start = version
    while version < SCHEMA_VERSION:
        UPGRADES[version](data)
        version += 1
    data["version"] = SCHEMA_VERSION
    return start


# ---- the load check ------------------------------------------------------------------------------


def _is_str(value):
    return isinstance(value, str)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _require(ok, where, message):
    if not ok:
        raise BadFile(f"{where}: {message}")


def _text_rows(value, where, name, value_key):
    _require(isinstance(value, list), where, f"{name} must be a list.")
    for row in value:
        _require(isinstance(row, dict) and _is_str(row.get("label")), where, f"each entry in {name} needs a text label.")
        row.setdefault(value_key, "")
        _require(_is_str(row[value_key]), where, f"each entry in {name} needs text in {value_key}.")


def _check_columns(board, where):
    columns = board.get("columns")
    _require(isinstance(columns, list) and columns, where, "it needs a list with at least one column.")
    seen = set()
    for col in columns:
        _require(isinstance(col, dict) and _is_str(col.get("id")) and col["id"] and _is_str(col.get("name")),
                 where, "each column needs an id and a name.")
        _require(col["id"] not in seen, where, f"two columns share the id {col['id']!r}.")
        seen.add(col["id"])
        col.setdefault("limit", None)
        _require(col["limit"] is None or _is_int(col["limit"]), where, f"the limit of column {col['name']!r} must be a whole number or null.")
        col.setdefault("done", False)
        _require(isinstance(col["done"], bool), where, f"done must be true or false on column {col['name']!r}.")
    return seen


def _check_groups(board, where):
    groups = board.setdefault("groups", [])
    _require(isinstance(groups, list), where, "groups must be a list.")
    seen = set()
    for g in groups:
        _require(isinstance(g, dict) and _is_str(g.get("id")) and g["id"] and _is_str(g.get("name")),
                 where, "each group needs an id and a name.")
        _require(g["id"] not in seen, where, f"two groups share the id {g['id']!r}.")
        seen.add(g["id"])
        g.setdefault("prefix", board["prefix"])
        _require(_is_str(g["prefix"]) and PREFIX_RE.fullmatch(g["prefix"]), where, f"group {g['name']!r} has a bad key prefix.")
        g.setdefault("color", "slate")
        _require(g["color"] in COLORS, where, f"group {g['name']!r} has an unknown color.")
        g.setdefault("link", "")
        _require(_is_str(g["link"]) and (not g["link"] or LINK_RE.fullmatch(g["link"])),
                 where, f"the link of group {g['name']!r} must start with http:// or https://.")
        g.setdefault("details", [])
        _text_rows(g["details"], where, f"the details of group {g['name']!r}", "value")
    return seen


def _check_checklists(board, where):
    lists = board.setdefault("checklists", [])
    _require(isinstance(lists, list), where, "checklists must be a list.")
    for cl in lists:
        _require(isinstance(cl, dict) and _is_str(cl.get("id")) and _is_str(cl.get("name")), where, "each checklist needs an id and a name.")
        items = cl.setdefault("items", [])
        _require(isinstance(items, list), where, f"the lines of checklist {cl['name']!r} must be a list.")
        for item in items:
            _require(isinstance(item, dict) and _is_str(item.get("id")) and _is_str(item.get("text")),
                     where, f"each line of checklist {cl['name']!r} needs an id and text.")
            item["heading"] = bool(item.get("heading"))


def _check_labels(board, where):
    labels = board.setdefault("labels", [])
    _require(isinstance(labels, list), where, "labels must be a list.")
    seen = set()
    for label in labels:
        _require(isinstance(label, dict) and _is_str(label.get("id")) and label["id"] and _is_str(label.get("name")),
                 where, "each label needs an id and a name.")
        _require(label["id"] not in seen, where, f"two labels share the id {label['id']!r}.")
        seen.add(label["id"])
        label.setdefault("color", "slate")
        _require(label["color"] in COLORS, where, f"label {label['name']!r} has an unknown color.")
    return seen


def _check_views(board, where):
    views = board.setdefault("views", [])
    _require(isinstance(views, list), where, "views must be a list.")
    seen = set()
    for view in views:
        _require(isinstance(view, dict) and _is_str(view.get("id")) and view["id"] and _is_str(view.get("name")),
                 where, "each saved view needs an id and a name.")
        _require(view["id"] not in seen, where, f"two saved views share the id {view['id']!r}.")
        seen.add(view["id"])
        for name in ("query", "sort"):
            view.setdefault(name, "")
            _require(_is_str(view[name]), where, f"the {name} of saved view {view['name']!r} must be text.")
    return seen


def _check_settings(board, where, view_ids):
    """After the properties are checked: the card face can name them."""
    settings = board.setdefault("settings", default_settings())
    _require(isinstance(settings, dict), where, "settings must be an object.")
    face = settings.setdefault("cardFace", list(CARD_FACE_PARTS))
    _require(isinstance(face, list) and all(_is_str(part) for part in face), where, "settings.cardFace must be a list of names.")
    known = set(CARD_FACE_PARTS) | {p["id"] for p in board["properties"]}
    for part in face:
        _require(part in known, where, f"settings.cardFace lists {part!r}, which isn't a part of a card or one of its properties.")
    _require(len(set(face)) == len(face), where, "settings.cardFace lists something twice.")
    settings.setdefault("limitPolicy", "warn")
    _require(settings["limitPolicy"] in LIMIT_POLICIES, where, f"settings.limitPolicy must be {' or '.join(LIMIT_POLICIES)}.")
    settings.setdefault("defaultView", None)
    view = settings["defaultView"]
    _require(view is None or (_is_str(view) and view in view_ids), where, "settings.defaultView must be null or the id of one of its saved views.")


def _check_tasks(card, where):
    tasks = card.setdefault("tasks", [])
    _require(isinstance(tasks, list), where, "tasks must be a list.")
    seen = set()
    for task in tasks:
        _require(isinstance(task, dict) and _is_str(task.get("id")) and task["id"] and _is_str(task.get("text")),
                 where, "each task needs an id and text.")
        _require(task["id"] not in seen, where, f"two tasks share the id {task['id']!r}.")
        seen.add(task["id"])
        task.setdefault("done", False)
        _require(isinstance(task["done"], bool), where, f"done must be true or false on task {task['id']!r}.")


def _check_card(card, index, board, where, column_ids, group_ids, label_ids):
    _require(isinstance(card, dict), where, f"card number {index + 1} isn't an object.")
    key = card.get("key")
    _require(_is_str(key) and ANY_KEY_RE.fullmatch(key), where, f"card number {index + 1} has no key like ABC-12.")
    at = f"{where}, card {key}"
    _require(_is_str(card.get("title")), at, "it needs a text title.")
    _require(card.get("column") in column_ids if _is_str(card.get("column")) else False, at, "it is in a column that doesn't exist.")
    card.setdefault("group", None)
    _require(card["group"] is None or (_is_str(card["group"]) and card["group"] in group_ids), at, "it belongs to a group that doesn't exist.")
    card.setdefault("rank", index + 1)
    number = card["rank"]
    _require(not isinstance(number, bool) and isinstance(number, (int, float)) and math.isfinite(number), at, "its rank must be a number.")
    for name in ("summary", "blocked", "notes"):
        card.setdefault(name, "")
        _require(_is_str(card[name]), at, f"{name} must be text.")
    card.setdefault("fields", [])
    _text_rows(card["fields"], at, "fields", "value")
    card.setdefault("sections", [])
    _text_rows(card["sections"], at, "sections", "body")
    card.setdefault("checks", [])
    _require(isinstance(card["checks"], list) and all(_is_str(c) for c in card["checks"]), at, "checks must be a list of ids.")
    card.setdefault("labels", [])
    labels = card["labels"]
    _require(isinstance(labels, list) and all(_is_str(label) for label in labels), at, "labels must be a list of label ids.")
    _require(all(label in label_ids for label in labels), at, "it has a label that doesn't exist on the board.")
    _require(len(set(labels)) == len(labels), at, "it lists a label twice.")
    _check_tasks(card, at)
    card.setdefault("archived", None)
    _require(card["archived"] is None or _is_str(card["archived"]), at, "archived must be a date or null.")
    for name in ("created", "updated"):
        card.setdefault(name, board["created"])
        _require(_is_str(card[name]), at, f"{name} must be a date.")
    return key


def check_board(board, index, known_ids, where):
    _require(isinstance(board, dict), where, f"board number {index + 1} isn't an object.")
    board_id = board.get("id")
    _require(_is_str(board_id) and BOARD_ID_RE.fullmatch(board_id), where, f"board number {index + 1} has no usable id.")
    _require(board_id not in known_ids, where, f"two boards share the id {board_id}.")
    known_ids.add(board_id)
    _require(_is_str(board.get("name")), where, f"board {board_id} needs a text name.")
    at = f"{where}: board {board['name']!r}"
    board.setdefault("description", "")
    _require(_is_str(board["description"]), at, "description must be text.")
    board.setdefault("prefix", "T")
    _require(_is_str(board["prefix"]) and PREFIX_RE.fullmatch(board["prefix"]), at, "it has a bad key prefix.")
    board.setdefault("groupLabel", "Group")
    _require(_is_str(board["groupLabel"]), at, "groupLabel must be text.")
    board.setdefault("rev", 1)
    _require(_is_int(board["rev"]) and board["rev"] >= 0, at, "rev must be a whole number.")
    board.setdefault("created", now())
    board.setdefault("updated", board["created"])
    _require(_is_str(board["created"]) and _is_str(board["updated"]), at, "created and updated must be dates.")
    board.setdefault("archived", None)
    _require(board["archived"] is None or _is_str(board["archived"]), at, "archived must be a date or null.")
    column_ids = _check_columns(board, at)
    group_ids = _check_groups(board, at)
    _check_checklists(board, at)
    label_ids = _check_labels(board, at)
    view_ids = _check_views(board, at)
    template = board.setdefault("template", [])
    _require(isinstance(template, list) and all(_is_str(t) for t in template), at, "template must be a list of names.")
    cards = board.get("cards", None)
    if cards is None:
        cards = board["cards"] = []
    _require(isinstance(cards, list), at, "cards must be a list.")
    keys = set()
    for i, card in enumerate(cards):
        key = _check_card(card, i, board, at, column_ids, group_ids, label_ids)
        _require(key not in keys, at, f"two cards share the key {key}.")
        keys.add(key)
    counters = board.get("counters")
    if counters is None:
        counters = board["counters"] = {}
        for key in keys:
            prefix, number = ANY_KEY_RE.fullmatch(key).groups()
            counters[prefix] = max(counters.get(prefix, 0), int(number))
    _require(isinstance(counters, dict) and all(_is_int(v) for v in counters.values()), at, "counters must map prefixes to whole numbers.")
    from .properties import check_definitions  # here, not at the top: properties.py builds on this module
    check_definitions(board, at)
    _check_settings(board, at, view_ids)


def check_data(data, where):
    """Check parsed file contents (already upgraded). Raises BadFile naming the first problem.

    Saved templates get the same check as when they are made (laneboard/templates checks them).
    """
    known = set()
    for i, board in enumerate(data["boards"]):
        check_board(board, i, known, where)
    from .templates import check_templates  # here, not at the top: the templates module builds on this one
    check_templates(data.setdefault("templates", []), where)
    return data


def load_bytes(raw: bytes, where: str):
    """Parse, upgrade and check a boards file. Returns (data, version the file was at)."""
    data = parse_json(raw, where)
    start = upgrade(data, where)
    return check_data(data, where), start
