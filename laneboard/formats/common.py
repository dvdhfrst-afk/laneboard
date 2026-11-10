"""What every format shares: the Format record, reading untrusted bytes, fitting outside data into
Laneboard's limits, and walking a board in the order the page shows it.

Nothing here knows about any one format. Imports never trust what they are given: sizes are checked
before anything is decoded, text must be UTF-8 (with or without a byte order mark) or UTF-16 with a
byte order mark, and wrong types are refused with a message that says what and where.
"""
from __future__ import annotations

import json
import os
import sys
from typing import List, NamedTuple

from .. import schema
from ..properties import property_text
from ..schema import BadFile, StoreError, key_sort

MAX_IMPORT_BYTES = 64_000_000  # the same limit the server puts on an import request

# Limits of the board data model that imports fit other programs' data into.
MAX_COLUMN_NAME = 40
MAX_FIELD_TEXT = 500
MAX_LABELS = 50
MAX_LABEL_NAME = 40
MAX_CARD_LABELS = 20
MAX_TASKS = 100
MAX_TASK_TEXT = 300


class ImportResult(NamedTuple):
    """What an import read: boards in the loose import shape the store accepts, and plain warnings."""
    boards: List[dict]
    warnings: List[str]


class Format:
    """One file format. `export(board) -> bytes` and `import_(raw) -> ImportResult` are None when the
    format only goes one way. `detect(raw, filename)` says how sure it is that a file is in this format,
    from 0 (it isn't) to 1 (it certainly is)."""

    def __init__(self, name, title, extension, mime, export=None, import_=None, sniff=None, hints=()):
        self.name = name
        self.title = title
        self.extension = extension  # with the dot, as used for a download's file name
        self.mime = mime  # the Content-Type to send an export with
        self.export = export
        self.import_ = import_
        self._sniff = sniff
        self._hints = (extension,) + tuple(hints)  # file name endings that make this format a little more likely

    def detect(self, raw, filename=None):
        return self.sniff(Sample(raw, filename))

    def sniff(self, sample):
        """`detect` for a Sample already in hand, so detection decodes and parses a file only once."""
        if self.import_ is None or self._sniff is None:
            return 0.0
        score = self._sniff(sample)
        if score and sample.extension in self._hints:
            score = min(1.0, score + 0.05)
        return score

    def info(self):
        """What the formats list in the API shows."""
        return {"name": self.name, "title": self.title, "extension": self.extension, "mime": self.mime,
                "export": self.export is not None, "import": self.import_ is not None}

    def __repr__(self):
        return f"<Format {self.name}>"


# ---- reading untrusted bytes ---------------------------------------------------------------------


def as_bytes(raw):
    if isinstance(raw, (bytearray, memoryview)):
        raw = bytes(raw)
    if not isinstance(raw, bytes):
        raise TypeError("imports read bytes")
    if len(raw) > MAX_IMPORT_BYTES:
        raise StoreError(f"That file is too large to import ({len(raw) / 1_000_000:.0f} MB; the limit is "
                         f"{MAX_IMPORT_BYTES // 1_000_000} MB).")
    return raw


def decode(raw, where="That file"):
    """Text from a file: UTF-8 with or without a byte order mark, or UTF-16 with one. Raises StoreError."""
    raw = as_bytes(raw)
    try:
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            content = raw.decode("utf-16")
        else:
            content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise StoreError(f"{where} isn't UTF-8 text ({e.reason} at byte {e.start}). Save it as UTF-8 and try again.") from e
    if "\x00" in content:
        raise StoreError(f"{where} isn't a text file, so it can't be imported.")
    if not content.strip():
        raise StoreError(f"{where} is empty.")
    return content


def read_json(raw, where="That file"):
    """Parsed JSON from a file, the same way the boards file itself is read. Raises StoreError."""
    try:
        return schema.parse_json(as_bytes(raw), where)
    except BadFile as e:  # a broken import is the caller's mistake (400), not an unreadable boards file (503)
        raise StoreError(str(e)) from e


_UNSET = object()


class Sample:
    """A file being looked at for detection. It is decoded and parsed at most once, and a failure
    just means "not this", so detection never raises for content."""

    def __init__(self, raw, filename=None):
        self.raw = raw
        self.filename = filename or ""
        self.extension = os.path.splitext(self.filename)[1].lower()
        self._text = self._json = _UNSET
        self.problem = None  # why the file couldn't be read as text, when it couldn't

    @property
    def text(self):
        if self._text is _UNSET:
            try:
                self._text = decode(self.raw)
            except StoreError as e:
                self._text, self.problem = None, str(e)
        return self._text

    @property
    def json(self):
        """The parsed value, or None when the file isn't JSON."""
        if self._json is _UNSET:
            self._json = None
            text = self.text
            if text is not None and text.lstrip()[:1] in ("{", "["):
                try:
                    self._json = json.loads(text, parse_constant=_refuse)
                except (ValueError, RecursionError):
                    pass
        return self._json

    @property
    def is_json(self):
        return self.json is not None


def _refuse(name):
    raise ValueError(name)


def describe(value):
    """What kind of JSON value something is, for messages."""
    if value is None:
        return "empty"
    if isinstance(value, bool):
        return "true or false"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "a list"
    return "an object"


_WANTED = {str: "text", list: "a list", dict: "an object", bool: "true or false", int: "a whole number"}


def field(obj, key, kind, where, required=False):
    """`obj[key]` when it is of `kind` (str, list, dict, bool, int or "number"). Missing or null gives None,
    or an error when `required`. Anything else is refused with a message naming `where` and the key."""
    value = obj.get(key)
    if value is None:
        if required:
            raise StoreError(f'{where} has no "{key}".')
        return None
    if kind == "number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif kind is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, kind)
    if not ok:
        wanted = "a number" if kind == "number" else _WANTED[kind]
        raise StoreError(f'{where} has a "{key}" that is {describe(value)}, not {wanted}.')
    return value


def objects(value, where, what):
    """A list of JSON objects, or a plain error saying which entry isn't one."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise StoreError(f"{where}: {what} must be a list, not {describe(value)}.")
    for n, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise StoreError(f"{where}: entry {n} of {what} is {describe(item)}, not an object.")
    return value


def plural(n, word, words=None):
    return f"{n} {word if n == 1 else (words or word + 's')}"


def join_words(parts):
    """["a", "b", "c"] as "a, b and c"."""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


# ---- fitting outside data into Laneboard's limits ------------------------------------------------


class Fitter:
    """Makes text from another program fit Laneboard's limits, and counts every change so the import
    can say what it changed. Names are cut to length and made different from each other; long titles
    are cut, with the whole title kept at the top of the card's notes; other long text is cut."""

    def __init__(self):
        self._counts = {}  # message parts -> how many times, in the order first seen

    def count(self, what, n=1):
        if n:
            self._counts[what] = self._counts.get(what, 0) + n

    @staticmethod
    def clean(value, multiline=False):
        """Laneboard's own text cleaning, with no length limit. Anything that isn't text becomes ""."""
        if not isinstance(value, str):
            return ""
        return schema.text(value, "Text", sys.maxsize, multiline=multiline)

    def text(self, value, limit, noun, multiline=False):
        """Cleaned text, cut to `limit` characters. `noun` is (singular, plural), as in ("card's notes", "cards' notes")."""
        cleaned = self.clean(value, multiline)
        if len(cleaned) > limit:
            cleaned = cleaned[:limit].rstrip()
            self.count(("cut", noun, limit))
        return cleaned

    def first(self, items, limit, what, holder=None):
        """The first `limit` of `items`, counting a cut when there were more. `what` is plural ("tasks");
        `holder` is what held them ("card"), or None for the whole board."""
        if len(items) <= limit:
            return items
        self.count(("first", what, holder, limit))
        return items[:limit]

    def title(self, value, notes, fallback="Untitled card"):
        """(title, notes) for a card. A title over the limit is cut, and the whole of it starts the notes."""
        title = self.clean(value) or fallback
        if len(title) > schema.MAX_TITLE:
            notes = title + ("\n\n" + notes if notes else "")
            title = title[:schema.MAX_TITLE - 3].rstrip() + "..."
            self.count(("title",))
        return title, notes

    def name(self, value, limit, taken, noun, key=str.casefold, fallback="Untitled", commas=True):
        """A name no longer than `limit` whose key isn't in `taken` (it is added). With `commas=False`,
        commas become spaces, since choice names can't hold them. A missing name becomes `fallback`."""
        cleaned = self.clean(value)
        if not commas and "," in cleaned:
            cleaned = " ".join(cleaned.replace(",", " ").split())
            self.count(("commas", noun))
        name = cleaned[:limit].rstrip() or fallback
        base, n = name, 2
        while key(name) in taken:
            suffix = f" {n}"
            name = base[:limit - len(suffix)].rstrip() + suffix
            n += 1
        taken.add(key(name))
        if cleaned and name != cleaned:
            self.count(("renamed", noun, limit))
        return name

    def warnings(self):
        out = []
        for what, n in self._counts.items():
            if what[0] == "title":
                out.append(f"Shortened {plural(n, 'card title')} to {schema.MAX_TITLE} characters. "
                           f"{'The full title is' if n == 1 else 'Each full title is'} at the top of the card's notes.")
            elif what[0] == "cut":
                (one, many), limit = what[1], what[2]
                out.append(f"Shortened {n} {one if n == 1 else many} to {limit:,} characters.")
            elif what[0] == "commas":
                one, many = what[1]
                out.append(f"Took the commas out of {n} {one if n == 1 else many}, since a choice can't have one in its name.")
            elif what[0] == "first":
                _, items, holder, limit = what
                where = f" on {plural(n, holder)}" if holder else ""
                out.append(f"Kept the first {limit} {items}{where} and left out the rest.")
            else:
                (one, many), limit = what[1], what[2]
                out.append(f"Renamed {n} {one if n == 1 else many} so that each name is different and at most "
                           f"{limit} characters long.")
        return out


# ---- walking a board in display order ------------------------------------------------------------


def card_order(card):
    return (card.get("rank", 0), key_sort(card["key"]))


def columns_with_cards(board):
    """[(column, cards on the board in it, top to bottom)], left to right. Archived cards are left out."""
    held = {col["id"]: [] for col in board["columns"]}
    for card in board.get("cards") or []:
        if not card.get("archived") and card.get("column") in held:
            held[card["column"]].append(card)
    return [(col, sorted(held[col["id"]], key=card_order)) for col in board["columns"]]


def archived_cards(board):
    """Archived cards, most recently archived first, as the page lists them."""
    found = sorted((c for c in board.get("cards") or [] if c.get("archived")), key=lambda c: key_sort(c["key"]))
    return sorted(found, key=lambda c: str(c["archived"]), reverse=True)


class Lookups:
    """A board's columns, groups, labels and properties by id, built once per export."""

    def __init__(self, board):
        self.board = board
        self.columns = {c["id"]: c for c in board.get("columns") or []}
        self.groups = {g["id"]: g for g in board.get("groups") or []}
        self.labels = {lab["id"]: lab for lab in board.get("labels") or []}
        self.properties = list(board.get("properties") or [])

    def card_labels(self, card):
        return [self.labels[lid] for lid in card.get("labels") or [] if lid in self.labels]

    def card_properties(self, card):
        """[(property, value as text)] for the values the card has, in the board's property order."""
        values = card.get("properties") or {}
        return [(p, property_text(self.board, card, p)) for p in self.properties if values.get(p["id"]) is not None]
