"""The search language: one reader for the search box, the command line and saved views.

    login page              cards with both words somewhere in their text
    "release notes"         a phrase; write \\" for a quote inside it
    bug OR crash            either one
    -wontfix, NOT wontfix   leave cards out
    (bug OR crash) col:rev  parentheses group terms
    points:>=3 points:2..5  compare a property
    sort:-points            order the results (taken out of the search, see `split_sort`)

The grammar, loosest first. OR, AND and NOT are keywords only in upper case:

    search := all ("OR" all)*
    all    := one (["AND"] one)*          terms side by side must all match
    one    := ("-" | "NOT")* (word | "phrase" | name:value | name:"phrase" | "(" search ")")

Words and phrases match inside a card's text, ignoring case and accents (`fold`): its key, title,
summary, blocked line, notes, fields, sections, tasks, group name, label names and property values.
A `name:value` term is a qualifier (column: or col:, group:, label:, key:, title:, notes:, is:,
has:, tasks:, progress:) or a property, by name or id. Values naming a column, group, label or
choice match its id or name, or the start of exactly one of them. A query is compiled once for a
board and then tested on each card, so a big board costs one quick pass.
"""
from __future__ import annotations

import operator
import re
import unicodedata
from fractions import Fraction

from .properties import TYPES, _shown, find_property
from .schema import ANY_KEY_RE, MAX_DECIMALS, MAX_QUERY, StoreError, key_sort

MAX_DEPTH = 20   # parentheses inside parentheses

QUALIFIERS = ("column", "group", "label", "key", "title", "notes", "is", "has", "tasks", "progress", "sort")
IS_WORDS = ("blocked", "archived", "active", "over-limit", "done")
HAS_WORDS = ("notes", "summary", "labels", "tasks", "checklist", "group")
SORT_NAMES = ("title", "key", "created", "updated", "rank", "group", "progress")

_ORDER = {"=": operator.eq, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}
_ALL_COMPARISONS = frozenset(_ORDER) | {".."}
_QUALIFIER = re.compile(r"([^\W_][\w-]*):")
_PERCENT = re.compile(r"([0-9]+(?:\.[0-9]+)?|\.[0-9]+)%?")
_GAPS = re.compile(r"[\s_-]+")
_marks = None


class QueryError(StoreError):
    """A search or sort that can't be read (HTTP 400).

    `position` is the character offset of the problem in the text it came from. `part` names that
    text: "query" for the search (including any sort: written in it) or "sort" for a sort string
    given separately, so a caller knows which box to point at.
    """
    status = 400

    def __init__(self, message, position=0, part="query"):
        super().__init__(message)
        self.position = position
        self.part = part


def fold(text):
    """Text as it is compared: ignoring case and accents. Both sides of every match go through this."""
    if text.isascii():
        return text.lower()
    return _mark_pattern().sub("", unicodedata.normalize("NFKD", unicodedata.normalize("NFKD", text).casefold()))


def _mark_pattern():
    """Every combining mark (accents and the like) as one character class, built the first time it's needed."""
    global _marks
    if _marks is None:
        spans, start, end = [], None, None
        for code in range(0x300, 0x20000):
            if unicodedata.combining(chr(code)):
                start = code if start is None else start
                end = code
            elif start is not None:
                spans.append(f"{chr(start)}-{chr(end)}")
                start = None
        _marks = re.compile("[" + "".join(spans) + "]")
    return _marks


def _space(c):
    """Whitespace and control characters separate terms."""
    return c.isspace() or c < " " or "\x7f" <= c <= "\x9f"


def _listed(names, last="or"):
    names = list(names)
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {last} {names[-1]}"


def _checked(text, what, part="query"):
    if text is None:
        return ""
    if not isinstance(text, str):
        raise QueryError(f"A {what} must be text.", 0, part)
    if len(text) > MAX_QUERY:
        raise QueryError(f"A {what} can be at most {MAX_QUERY} characters long (this one has {len(text)}).", MAX_QUERY, part)
    return text


# ---- reading the text ----------------------------------------------------------------------------


class _Token:
    __slots__ = ("kind", "pos", "end", "value", "name", "value_pos", "quoted")

    def __init__(self, kind, pos, end, value="", name="", value_pos=0, quoted=False):
        self.kind, self.pos, self.end, self.value = kind, pos, end, value
        self.name, self.value_pos, self.quoted = name, value_pos, quoted


def _quoted(text, start):
    """The phrase in quotes starting at `start`, with \\" and \\\\ read as a quote and a backslash."""
    out, i, n = [], start + 1, len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n and text[i + 1] in '"\\':
            out.append(text[i + 1])
            i += 2
        elif c == '"':
            return "".join(out), i + 1
        else:
            out.append(c)
            i += 1
    raise QueryError('This quote is never closed. Add a " where the phrase ends.', start)


def _lex(text):
    """Tokens: ( ) OR AND NOT -, words (bare or quoted) and name:value terms."""
    tokens, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if _space(c):
            i += 1
        elif c in "()":
            tokens.append(_Token(c, i, i + 1))
            i += 1
        elif c == '"':
            value, end = _quoted(text, i)
            tokens.append(_Token("word", i, end, value, quoted=True))
            i = end
        elif c == "-" and i + 1 < n and not _space(text[i + 1]) and text[i + 1] != ")":
            tokens.append(_Token("-", i, i + 1))
            i += 1
        else:
            j = i
            while j < n and not _space(text[j]) and text[j] not in '()"':
                j += 1
            run = text[i:j]
            m = _QUALIFIER.match(run)
            if run in ("OR", "AND", "NOT"):
                tokens.append(_Token(run, i, j))
            elif m:
                value, end, quoted, value_pos = run[m.end():], j, False, i + m.end()
                if not value and j < n and text[j] == '"':
                    value, end = _quoted(text, j)
                    quoted, value_pos = True, j + 1
                tokens.append(_Token("qual", i, end, value, m.group(1), value_pos, quoted))
                j = end
            else:
                tokens.append(_Token("word", i, j, run))
            i = j
    return tokens


def _take_sort(tokens):
    """Split the sort: terms out of the tokens. One right after - or NOT stays, for the parser to refuse."""
    kept, taken = [], []
    for tok in tokens:
        if tok.kind == "qual" and fold(tok.name) == "sort" and not (kept and kept[-1].kind in ("-", "NOT")):
            if not tok.value.strip():
                raise QueryError("Type what to sort by after sort:, like sort:title or sort:-points.", tok.pos)
            taken.append(tok)
        else:
            kept.append(tok)
    return kept, taken


def split_sort(text):
    """Pull the sort: terms out of a search. Returns (search text, sort text).

    `"bug sort:-points label:ui"` gives `("bug label:ui", "-points")`. Several sort: terms join with
    commas, in order. Text inside quotes is never a sort term.
    """
    text = _checked(text, "search")
    _, taken = _take_sort(_lex(text))
    pieces, last = [], 0
    for tok in taken:
        end = tok.end
        while end < len(text) and _space(text[end]):
            end += 1
        pieces.append(text[last:tok.pos])
        last = end
    pieces.append(text[last:])
    return "".join(pieces).strip(), ",".join(tok.value.strip() for tok in taken)


# ---- building the test ---------------------------------------------------------------------------
#
# A compiled term is (test, cost). `test` takes a _View of a card and returns a bool. Terms side by
# side run cheapest first, so a search for "col:review login" folds the text of review cards only.


def _always(view):
    return True


_ANY = (_always, 0)


class _View:
    """A card while one query looks at it, with its folded text made once, when a term first needs it."""
    __slots__ = ("card", "_build", "_text")

    def __init__(self, card, build):
        self.card, self._build, self._text = card, build, None

    def text(self):
        if self._text is None:
            self._text = self._build(self.card)
        return self._text


def _both(nodes):
    if len(nodes) == 1:
        return nodes[0]
    nodes = sorted(nodes, key=lambda node: node[1])
    tests, cost = tuple(node[0] for node in nodes), nodes[-1][1]
    if len(tests) == 2:
        a, b = tests
        return (lambda v: a(v) and b(v)), cost

    def every(v):
        for test in tests:
            if not test(v):
                return False
        return True
    return every, cost


def _either(nodes):
    if len(nodes) == 1:
        return nodes[0]
    nodes = sorted(nodes, key=lambda node: node[1])
    tests, cost = tuple(node[0] for node in nodes), nodes[-1][1]
    if len(tests) == 2:
        a, b = tests
        return (lambda v: a(v) or b(v)), cost

    def some(v):
        for test in tests:
            if test(v):
                return True
        return False
    return some, cost


def _negate(node):
    test, cost = node
    return (lambda v: not test(v)), cost


def _contains(raw):
    """A test for whether folded text holds `raw`. Inside a phrase, any run of whitespace matches any other."""
    pieces = fold(raw).split()
    if not pieces:
        return None
    if len(pieces) == 1:
        needle = pieces[0]
        return lambda hay: needle in hay
    search = re.compile(r"\s+".join(map(re.escape, pieces))).search
    return lambda hay: search(hay) is not None


def _ordered(get, op, operand):
    """A test comparing `get(view)` with `operand`; a card with no value (None) never matches."""
    if op == "..":
        low, high = operand

        def between(v):
            key = get(v)
            return key is not None and low <= key <= high
        return between
    compare = _ORDER[op]

    def test(v):
        key = get(v)
        return key is not None and compare(key, operand)
    return test


def _pick(options, given, pos, missing, noun):
    """The id among `options` [(id, name)] that `given` means: an id, then a name, ignoring case and
    accents, then the start of exactly one name or id. Raises QueryError with `missing`, or naming the
    options that `given` could be."""
    want = fold(given.strip())
    for field in (0, 1):
        for option in options:
            if fold(option[field]) == want:
                return option[0]
    found = [option for option in options if fold(option[1]).startswith(want) or fold(option[0]).startswith(want)]
    if len(found) == 1:
        return found[0][0]
    if found:
        raise QueryError(f'"{_shown(given.strip())}" matches more than one {noun}: '
                         f'{", ".join(option[1] for option in found)}. Type more of the name.', pos)
    raise QueryError(missing, pos)


def _tickable(board):
    return {item.get("id") for cl in board.get("checklists") or [] for item in cl.get("items") or []
            if not item.get("heading")}


def _progress(board):
    """A function giving a card's progress as an exact percent: ticked checklist lines plus done tasks,
    out of the board's checklist lines plus the card's tasks. None when there is neither."""
    tickable = _tickable(board)
    lines = len(tickable)

    def progress(card):
        tasks = card.get("tasks") or ()
        total = lines + len(tasks)
        if not total:
            return None
        done = sum(1 for task in tasks if task.get("done"))
        if lines:
            done += len(tickable.intersection(card.get("checks") or ()))
        return Fraction(100 * done, total)
    return progress


class _Compiler:
    """Turns terms into tests for one board, with the board's columns, groups, labels and properties at hand."""

    def __init__(self, board):
        self.board = board
        self.columns = board.get("columns") or []
        self.groups = board.get("groups") or []
        self.labels = board.get("labels") or []
        self.props = [p for p in board.get("properties") or [] if p.get("type") in TYPES]
        self.wants_archived = False
        group_names = {g.get("id"): g.get("name") for g in self.groups}
        label_names = {label.get("id"): label.get("name") for label in self.labels}
        props = [(p["id"], p.get("name") or "", TYPES[p["type"]], p) for p in self.props]

        def card_text(card):
            parts = [card.get("key"), card.get("title"), card.get("summary"), card.get("blocked"), card.get("notes"),
                     group_names.get(card.get("group"))]
            for row in card.get("fields") or ():
                parts.append(f"{row.get('label', '')} {row.get('value', '')}")
            for row in card.get("sections") or ():
                parts.append(f"{row.get('label', '')} {row.get('body', '')}")
            for task in card.get("tasks") or ():
                parts.append(task.get("text"))
            for label_id in card.get("labels") or ():
                parts.append(label_names.get(label_id))
            values = card.get("properties")
            if values:
                for pid, name, kind, prop in props:
                    value = values.get(pid)
                    if value is not None:
                        parts.append(f"{name} {kind.format_text(value, prop)}")
            return fold("\x00".join(part for part in parts if isinstance(part, str) and part))
        self.card_text = card_text

    # ---- one term

    def term(self, tok, positive):
        if tok.kind == "word":
            return self.words(tok.value)
        name = fold(tok.name)
        if name == "col":
            name = "column"
        elif name == "labels":
            name = "label"
        prop = None
        if name not in QUALIFIERS:
            prop = find_property(self.board, tok.name)
            if prop is None or prop.get("type") not in TYPES:
                if not tok.value or tok.value.startswith("//") or tok.name[0].isdigit():
                    return self.words(f"{tok.name}:{tok.value}")  # a time like 10:30, a link, or "Re:"
                raise QueryError(self.unknown(tok.name), tok.pos)
        if not tok.value.strip():
            raise QueryError(f'Type what to look for after "{tok.name}:".', tok.pos)
        if prop is not None:
            return self.property(tok, prop)
        return getattr(self, "q_" + name)(tok, positive)

    def unknown(self, name):
        if self.props:
            return f'There\'s no property called "{_shown(name)}". Properties: {", ".join(p["name"] for p in self.props)}.'
        return f'There\'s no qualifier called "{_shown(name)}". Qualifiers: {", ".join(QUALIFIERS)}.'

    def words(self, raw):
        test = _contains(raw)
        if test is None:
            return _ANY
        return (lambda v: test(v.text())), 3

    @staticmethod
    def part_text(part, raw):
        test = _contains(raw)
        if test is None:
            return _ANY
        return (lambda v: test(fold(v.card.get(part) or ""))), 2

    def q_column(self, tok, positive):
        options = [(c.get("id"), c.get("name")) for c in self.columns]
        cid = _pick(options, tok.value, tok.value_pos, f'There\'s no column called "{_shown(tok.value.strip())}". '
                    f'Columns: {", ".join(o[1] for o in options)}.', "column")
        return (lambda v: v.card.get("column") == cid), 1

    def q_group(self, tok, positive):
        if not self.groups:
            raise QueryError("This board has no groups.", tok.value_pos)
        options = [(g.get("id"), g.get("name")) for g in self.groups]
        gid = _pick(options, tok.value, tok.value_pos, f'There\'s no group called "{_shown(tok.value.strip())}". '
                    f'Groups: {", ".join(o[1] for o in options)}.', "group")
        return (lambda v: v.card.get("group") == gid), 1

    def q_label(self, tok, positive):
        if not self.labels:
            raise QueryError("This board has no labels.", tok.value_pos)
        options = [(label.get("id"), label.get("name")) for label in self.labels]
        lid = _pick(options, tok.value, tok.value_pos, f'There\'s no label called "{_shown(tok.value.strip())}". '
                    f'Labels: {", ".join(o[1] for o in options)}.', "label")
        return (lambda v: lid in (v.card.get("labels") or ())), 1

    def q_key(self, tok, positive):
        whole = tok.value.strip().upper()
        if ANY_KEY_RE.fullmatch(whole):  # a whole key means that card: key:T-1 isn't T-10
            return (lambda v: str(v.card.get("key") or "").upper() == whole), 1
        return self.part_text("key", tok.value)

    def q_title(self, tok, positive):
        return self.part_text("title", tok.value)

    def q_notes(self, tok, positive):
        return self.part_text("notes", tok.value)

    def q_is(self, tok, positive):
        word = _GAPS.sub("", fold(tok.value))
        if word == "blocked":
            return (lambda v: bool(v.card.get("blocked"))), 1
        if word == "archived":
            self.wants_archived = self.wants_archived or positive
            return (lambda v: bool(v.card.get("archived"))), 1
        if word == "active":
            self.wants_archived = self.wants_archived or not positive
            return (lambda v: not v.card.get("archived")), 1
        if word == "overlimit":
            counts = {}
            for card in self.board.get("cards") or []:
                if not card.get("archived"):
                    counts[card.get("column")] = counts.get(card.get("column"), 0) + 1
            over = {c.get("id") for c in self.columns if c.get("limit") and counts.get(c.get("id"), 0) > c["limit"]}
            return (lambda v: v.card.get("column") in over), 1
        if word == "done":
            last = self.columns[-1].get("id") if self.columns else None
            return (lambda v: last is not None and v.card.get("column") == last), 1
        raise QueryError(f'is: can be {_listed(IS_WORDS)}, not "{_shown(tok.value)}".', tok.value_pos)

    def q_has(self, tok, positive):
        word = fold(tok.value.strip())
        if word in ("notes", "summary"):
            return (lambda v: bool(v.card.get(word))), 1
        if word in ("labels", "label"):
            return (lambda v: bool(v.card.get("labels"))), 1
        if word == "tasks":
            return (lambda v: bool(v.card.get("tasks"))), 1
        if word == "checklist":  # the board's checklists are on every card
            present = bool(_tickable(self.board))
            return (lambda v: present), 0
        if word == "group":
            return (lambda v: v.card.get("group") is not None), 1
        prop = find_property(self.board, tok.value.strip())
        if prop is not None and prop.get("type") in TYPES:
            pid = prop["id"]
            return (lambda v: (v.card.get("properties") or {}).get(pid) is not None), 1
        words = (f"{', '.join(HAS_WORDS)} or a property: {', '.join(p['name'] for p in self.props)}" if self.props
                 else _listed(HAS_WORDS))
        raise QueryError(f'There\'s nothing called "{_shown(tok.value.strip())}" to look for with has:. Use {words}.',
                         tok.value_pos)

    def q_tasks(self, tok, positive):
        word = fold(tok.value.strip())
        if word == "done":
            return (lambda v: bool(v.card.get("tasks")) and all(t.get("done") for t in v.card["tasks"])), 1
        if word == "open":
            return (lambda v: any(not t.get("done") for t in v.card.get("tasks") or ())), 1
        raise QueryError(f'tasks: can be done or open, not "{_shown(tok.value)}".', tok.value_pos)

    def q_progress(self, tok, positive):
        op, operand = self.compare(tok, _ALL_COMPARISONS, self.percent)
        if op == ".." and operand[0] > operand[1]:
            raise QueryError(f"The range {tok.value} runs backwards. Put the smaller number first.", tok.value_pos)
        progress = _progress(self.board)
        return _ordered(lambda v: progress(v.card), op, operand), 1

    def q_sort(self, tok, positive):
        raise QueryError("Sorting can't be left out with - or NOT. To sort the other way, put the minus "
                         "inside: sort:-title.", tok.pos)

    @staticmethod
    def percent(text, pos):
        m = _PERCENT.fullmatch(text)
        if not m:
            raise QueryError(f'Progress is a percent, like progress:50 or progress:>=80, not "{_shown(text)}".', pos)
        number = Fraction(m.group(1))
        if number > 100:
            raise QueryError("Progress is a percent from 0 to 100.", pos)
        return number

    # ---- comparisons and properties

    @staticmethod
    def compare(tok, allowed, read):
        """(operator, operand) from a value like >=3, 2..5 or 3, reading operands with `read(text, pos)`.

        Only the operators in `allowed` are recognized; otherwise the whole value is one operand, so a
        choice named ">50" can still be found. Quoted values are always one operand.
        """
        value, pos = tok.value, tok.value_pos
        if not tok.quoted:
            for op in (">=", "<=", ">", "<", "="):
                if op in allowed and value.startswith(op):
                    rest = value[len(op):]
                    if not rest:
                        raise QueryError(f'Type a value after "{tok.name}:{op}".', pos)
                    return op, read(rest, pos + len(op))
            low, dots, high = value.partition("..")
            if dots and ".." in allowed:
                if not low or not high:
                    raise QueryError(f"Write a range with a value at each end, like {tok.name}:2..5.", pos)
                return "..", (read(low, pos), read(high, pos + len(low) + 2))
        return "=", read(value, pos)

    def operand(self, prop, kind, text, pos):
        """A value as the property reads typed text, with no rounding: points:2.5 isn't points:3."""
        choices = prop.get("choices")
        if isinstance(choices, list):
            options = [(c["id"], c["name"]) for c in choices]
            cid = _pick(options, text, pos, f'{prop["name"]} has no choice called "{_shown(text.strip())}". '
                        f'Choices: {", ".join(o[1] for o in options)}.', f"choice of {prop['name']}")
            text = next(name for oid, name in options if oid == cid)
        exact = {**prop, "decimals": MAX_DECIMALS} if "decimals" in prop else prop
        try:
            return kind.parse_text(text, exact)
        except StoreError as e:
            raise QueryError(str(e), pos) from None

    def property(self, tok, prop):
        kind, pid = TYPES[prop["type"]], prop["id"]

        def value_of(v):
            return (v.card.get("properties") or {}).get(pid)

        if not kind.comparisons:  # text and links: words inside the value
            test = _contains(tok.value)
            if test is None:
                return _ANY

            def holds(v):
                value = value_of(v)
                return value is not None and test(fold(kind.format_text(value, prop)))
            return holds, 2

        op, operand = self.compare(tok, kind.comparisons, lambda text, pos: self.operand(prop, kind, text, pos))
        if op == "=":
            if isinstance(operand, list):  # a multi: has these choices
                wanted = set(operand)

                def has_all(v):
                    value = value_of(v)
                    return isinstance(value, list) and wanted.issubset(value)
                return has_all, 1
            return (lambda v: value_of(v) == operand), 1  # None (like reviewed:no) means not set

        def key_of(v):
            value = value_of(v)
            return None if value is None else kind.sort_key(value, prop)

        ends = operand if op == ".." else (operand,)
        if any(end is None for end in ends):
            raise QueryError(f'Type a value after "{tok.name}:{op}".', tok.value_pos)
        if op == "..":
            low, high = (kind.sort_key(end, prop) for end in ends)
            if low > high:
                raise QueryError(f"The range {tok.value} runs backwards. Put the smaller value first.", tok.value_pos)
            return _ordered(key_of, op, (low, high)), 1
        return _ordered(key_of, op, kind.sort_key(operand, prop)), 1


class _Parser:
    def __init__(self, tokens, compiler):
        self.tokens, self.i, self.compiler = tokens, 0, compiler

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def parse(self):
        if not self.tokens:
            return _ANY
        node = self.any_of(0, True)
        tok = self.peek()
        if tok is not None:  # only a ")" stops the top level early
            raise QueryError('This ")" has no "(" before it.', tok.pos)
        return node

    def any_of(self, depth, positive):
        nodes = [self.all_of(depth, positive)]
        while self.peek() is not None and self.peek().kind == "OR":
            tok = self.peek()
            self.i += 1
            after = self.peek()
            if after is None or after.kind in (")", "OR", "AND"):
                raise QueryError("OR needs a search term on each side of it.", tok.pos)
            nodes.append(self.all_of(depth, positive))
        return _either(nodes)

    def all_of(self, depth, positive):
        tok = self.peek()
        if tok.kind in ("OR", "AND"):
            raise QueryError(f"{tok.kind} needs a search term on each side of it.", tok.pos)
        nodes = [self.one(depth, positive)]
        while True:
            tok = self.peek()
            if tok is None or tok.kind in ("OR", ")"):
                return _both(nodes)
            if tok.kind == "AND":
                self.i += 1
                after = self.peek()
                if after is None or after.kind in (")", "OR", "AND"):
                    raise QueryError("AND needs a search term on each side of it.", tok.pos)
            nodes.append(self.one(depth, positive))

    def one(self, depth, positive):
        flips, last = 0, None
        while self.peek() is not None and self.peek().kind in ("-", "NOT"):
            last = self.peek()
            self.i += 1
            flips += 1
        tok = self.peek()
        if last is not None and (tok is None or tok.kind in (")", "OR", "AND")):
            raise QueryError('"-" needs a search term right after it.' if last.kind == "-"
                             else "NOT needs a search term after it.", last.pos)
        if tok.kind == ")":
            raise QueryError('This ")" has no "(" before it.', tok.pos)
        self.i += 1
        flip = flips % 2 == 1
        positive = positive != flip
        if tok.kind == "(":
            if depth >= MAX_DEPTH:
                raise QueryError(f"Parentheses can go at most {MAX_DEPTH} deep.", tok.pos)
            inner = self.peek()
            if inner is not None and inner.kind == ")":
                raise QueryError("There's nothing between these parentheses.", tok.pos)
            if inner is None:
                raise QueryError('This "(" is never closed.', tok.pos)
            node = self.any_of(depth + 1, positive)
            if self.peek() is None:
                raise QueryError('This "(" is never closed.', tok.pos)
            self.i += 1
        else:
            node = self.compiler.term(tok, positive)
        return _negate(node) if flip else node


class Query:
    """A search compiled for one board.

    `matches(card)` tests one card against the search alone. Archived cards are the caller's to leave
    out: `search` does, unless asked for them or `wants_archived` is true (the search asks for
    archived cards, with is:archived or -is:active). `sort` is the text of any sort: terms the
    search had, comma separated.
    """

    def __init__(self, text, test, wants_archived, build, sort_terms):
        self.text = text
        self.wants_archived = wants_archived
        self.sort = ",".join(tok.value.strip() for tok in sort_terms)
        self._test, self._build, self._sort_terms = test, build, sort_terms

    def matches(self, card):
        return bool(self._test(_View(card, self._build)))


def compile_query(text, board):
    """Read a search for `board`. Raises QueryError with the position of the first problem.

    Any sort: terms are taken out (as `split_sort` does) and kept in `Query.sort`; positions in errors
    count from the start of `text` as given.
    """
    text = _checked(text, "search")
    kept, taken = _take_sort(_lex(text))
    compiler = _Compiler(board)
    test, _ = _Parser(kept, compiler).parse()
    return Query(text, test, compiler.wants_archived, compiler.card_text, taken)


# ---- sorting -------------------------------------------------------------------------------------


class _Descending:
    """A sort key that orders the other way."""
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __lt__(self, other):
        return other.value < self.value

    def __eq__(self, other):
        return self.value == other.value


_EMPTY = (1, 0)


def _manual(card):
    """Manual order: rank, then key (T-2 before T-10), as the board shows a column."""
    return card.get("rank") or 0, key_sort(card.get("key") or "")


class Sort:
    """An order for the cards in a column.

    `key(card)` sorts ascending. Each part orders by one value; a card with no value for a part goes
    after the others whichever way that part runs. Manual order breaks the last tie.
    """

    def __init__(self, parts=()):
        self._parts = tuple(parts)

    def then(self, other):
        """This order, with ties broken by `other`."""
        return Sort(self._parts + other._parts)

    def key(self, card):
        out = []
        for get, descending in self._parts:
            value = get(card)
            if value is None:
                out.append(_EMPTY)
            else:
                out.append((0, _Descending(value) if descending else value))
        out.append(_manual(card))
        return tuple(out)


def _sort_part(name, board, pos):
    word = fold(name)
    if word == "title":
        return lambda card: fold(card.get("title") or "") or None
    if word == "key":
        return lambda card: key_sort(card["key"]) if card.get("key") else None
    if word in ("created", "updated"):
        return lambda card: card.get(word) or None
    if word == "rank":
        return _manual
    if word == "group":
        order = {g.get("id"): i for i, g in enumerate(board.get("groups") or [])}
        return lambda card: order.get(card.get("group"))
    if word == "progress":
        return _progress(board)
    prop = find_property(board, name)
    if prop is not None and prop.get("type") in TYPES:
        kind, pid = TYPES[prop["type"]], prop["id"]

        def value_key(card):
            value = (card.get("properties") or {}).get(pid)
            return None if value is None else kind.sort_key(value, prop)
        return value_key
    names = [p["name"] for p in board.get("properties") or [] if p.get("type") in TYPES]
    if names:
        raise QueryError(f'There\'s no property called "{_shown(name)}" to sort by. Sort by {", ".join(SORT_NAMES)} '
                         f'or a property: {", ".join(names)}.', pos, "sort")
    raise QueryError(f'There\'s nothing called "{_shown(name)}" to sort by. Sort by {_listed(SORT_NAMES)}.', pos, "sort")


def compile_sort(text, board):
    """Read a sort string like `-points,title`: names separated by commas, each one a later tie-break.

    A name is title, key, created, updated, rank (manual order, also what an empty string means),
    group, progress, or a property by name or id. A minus sign in front reverses that name.
    Raises QueryError (part "sort") with the position of the bad name.
    """
    text = _checked(text, "sort", part="sort")
    parts, start = [], 0
    for item in text.split(","):
        at = start + len(item) - len(item.lstrip())
        start += len(item) + 1
        word = item.strip()
        if not word:
            continue
        name = word[1:].lstrip() if word[0] in "+-" else word
        if not name:
            raise QueryError(f'Type what to sort by after "{word[0]}", like {word[0]}title.', at, "sort")
        parts.append((_sort_part(name, board, at + len(word) - len(name)), word[0] == "-"))
    return Sort(parts)


# ---- the whole search ----------------------------------------------------------------------------


def search(board, text="", sort="", archived=False):
    """The keys of the cards on `board` that match `text`, in display order.

    Columns come in board order; inside each, cards follow the sort: terms in `text`, then `sort`,
    then manual order. Archived cards are left out unless `archived` is true or the search asks for
    them (is:archived); then they are listed in their columns with the rest.
    """
    query = compile_query(text, board)
    order = Sort()
    for tok in query._sort_terms:
        try:
            order = order.then(compile_sort(tok.value, board))
        except QueryError as e:
            raise QueryError(str(e), tok.value_pos + e.position) from None
    if sort:
        order = order.then(compile_sort(sort, board))
    include = bool(archived) or query.wants_archived
    test, build = query._test, query._build
    found = {col.get("id"): [] for col in board.get("columns") or []}
    elsewhere = []  # a card whose column isn't on the board (only in a hand-made dict) goes at the end
    for card in board.get("cards") or []:
        if card.get("archived") and not include:
            continue
        if test(_View(card, build)):
            found.get(card.get("column"), elsewhere).append(card)
    keys = []
    for cards in (*found.values(), elsewhere):
        cards.sort(key=order.key)
        keys.extend(card["key"] for card in cards)
    return keys


def describe_error(text, error):
    """The error's message with the text under it and a ^ under the bad spot, for the command line.

        There's no property called "pionts". Properties: Points, Priority.
          bug pionts:3
              ^
    Long text is cut to a window around the spot. Pass the text the error's `part` names.
    """
    message = str(error)
    position = getattr(error, "position", None)
    if not isinstance(text, str) or not text.strip() or not isinstance(position, int):
        return message
    position = max(0, min(position, len(text)))
    shown = "".join(" " if _space(c) else c for c in text)
    start = max(0, min(position - 30, len(shown) - 60))
    end = min(len(shown), start + 60)
    before, after = ("..." if start else ""), ("..." if end < len(shown) else "")
    return f"{message}\n  {before}{shown[start:end]}{after}\n  {' ' * (len(before) + position - start)}^"
