"""Markdown: a board as a document people can read, which imports back exactly; and task lists from other tools.

An export looks like this, and an import reads all of it back:

    # Board name

    The board's description.

    ## Column name

    ### KEY Card title

    - Group: Web app
    - Labels: Bug, Front end
    - Blocked by: Waiting on the design review

    > The summary, quoted line by line.

    #### Properties

    - Points: 3
    - Platforms: iOS, Web

    #### Fields

    - Size: 1 day
      (more lines of a value are indented by two spaces)

    #### Tasks

    - [x] A task that is done
    - [ ] A task that isn't

    #### Checklist: Before merge

    - [x] Tests pass
    - [ ] Changelog updated

    #### Section label

    Section text, as it is.

    #### Notes

    Notes, as they are, blank lines and code blocks included.

    ## Archived

    (archived cards, each with "- Column:" and "- Archived:" lines)

    ## Board settings

    (the board's columns, labels, properties, views, settings and checklists, as Laneboard JSON)

So that card text can't be mistaken for the layout: in free text (the description, sections and notes),
a line that would start a heading, or a code block that is never closed, gets a backslash in front, and a
line that already starts with backslashes before one of those gets one more. Closed code blocks are left
as they are. In "Label: value" lines, backslashes and colons in the label get a backslash, and so do
backslashes and commas in label names. A column called Archived or Board settings is written as
"## Column: Archived", and a section named like one of the card parts as "#### Section: Notes".

A file without those Laneboard parts is read as a plain task list: headings become columns, list items
become cards, ticked items go to the last column, and items indented under a card become its tasks.
"""
from __future__ import annotations

import json
import re

from ..properties import TYPES, clean_definitions, find_property
from ..schema import (KEY_RE, MAX_CARDS, MAX_COLUMN_NAME, MAX_COLUMNS, MAX_NAME, MAX_SUMMARY, MAX_TASK_TEXT, MAX_TASKS,
                      MAX_TEXT, StoreError)
from .common import (Fitter, Format, ImportResult, Lookups, archived_cards, columns_with_cards, decode, plural,
                     read_json)

# What the Board settings part holds: the board's structure, as stored, apart from its name,
# description and cards (which the rest of the file holds).
STRUCTURE = ("prefix", "groupLabel", "columns", "groups", "labels", "properties", "views", "settings",
             "checklists", "template", "counters")
DEFAULT_NAME = "Imported tasks"

_ARCHIVED = "archived"
_SETTINGS = "board settings"
_IN_ARCHIVE = object()  # where cards under "## Archived" are, as opposed to a column of that name
_LIST_PARTS = ("properties", "fields", "tasks")
_PART_NAMES = _LIST_PARTS + ("notes",)

_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_CLOSER = re.compile(r" {0,3}(`{3,}|~{3,})[ \t]*")
_MARKER = re.compile(r"( {0,3})(\\*)(?:#{1,6}(?:[ \t]|$)|`{3,}|~{3,})")
_ESCAPED = re.compile(r"( {0,3})\\\\*(?:#{1,6}(?:[ \t]|$)|`{3,}|~{3,})")
_HEADING = re.compile(r"(#{1,4})(?:[ \t]+(.*))?")  # matched against the line without its trailing spaces
_META = re.compile(r"- (group|labels|blocked by|column|archived):(?: (.*))?", re.IGNORECASE)
_TASK = re.compile(r"- \[([ xX])\](?: (.*))?")

_SETTINGS_LINE = re.compile(r"^## Board settings[ \t]*$", re.MULTILINE | re.IGNORECASE)
_CARD_LINE = re.compile(r"^### [A-Z][A-Z0-9]{0,7}-[1-9][0-9]{0,5}[ \t]", re.MULTILINE)
_TASK_LINE = re.compile(r"^[ \t]*[-*+][ \t]+\[[ xX]\]", re.MULTILINE)
_SYNTAX_LINE = re.compile(r"^(?: {0,3}#{1,6}[ \t]+\S|[ \t]*(?:[-*+]|[0-9]{1,9}[.)])[ \t]+\S)", re.MULTILINE)


def _starts(text, prefix):
    return text[:len(prefix)].casefold() == prefix


def _too_many_cards():
    return StoreError(f"That Markdown file has more than {MAX_CARDS} cards, and a board can have at most {MAX_CARDS}.")


def _lines(text):
    """The lines of `text` one at a time, so a huge file isn't split into a list of every line at once."""
    start = 0
    while True:
        end = text.find("\n", start)
        if end < 0:
            yield text[start:]
            return
        yield text[start:end]
        start = end + 1


# ---- free text that can't break the layout -------------------------------------------------------


def _opens(line):
    """(fence character, length) when the line opens a code block."""
    m = _FENCE.fullmatch(line)
    if not m or (m.group(1)[0] == "`" and "`" in m.group(2)):
        return None
    return m.group(1)[0], len(m.group(1))


def _closes(line, fence):
    m = _CLOSER.fullmatch(line)
    return bool(m) and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]


def escape_text(text):
    """Free text as Markdown lines that an import reads back exactly. See the module notes."""
    lines = text.split("\n")
    # The longest closing fence of each kind at or below each line, so a fence that never closes is
    # found without searching to the end every time.
    longest = {"`": [0] * (len(lines) + 1), "~": [0] * (len(lines) + 1)}
    for i in range(len(lines) - 1, -1, -1):
        for after in longest.values():
            after[i] = after[i + 1]
        m = _CLOSER.fullmatch(lines[i])
        if m:
            after = longest[m.group(1)[0]]
            after[i] = max(after[i], len(m.group(1)))
    out, i = [], 0
    while i < len(lines):
        line, fence = lines[i], _opens(lines[i])
        if fence and longest[fence[0]][i + 1] >= fence[1]:
            end = i + 1
            while not _closes(lines[end], fence):
                end += 1
            out.extend(lines[i:end + 1])
            i = end + 1
            continue
        m = _MARKER.match(line)
        out.append(line[:len(m.group(1))] + "\\" + line[len(m.group(1)):] if m else line)
        i += 1
    return out


def unescape_text(lines):
    """`escape_text` undone."""
    out, fence = [], None
    for line in lines:
        if fence is not None:
            if _closes(line, fence):
                fence = None
        else:
            fence = _opens(line)
            if fence is None and _ESCAPED.match(line):
                pad = len(line) - len(line.lstrip(" "))
                line = line[:pad] + line[pad + 1:]
        out.append(line)
    return out


def _escape(text, special):
    return text.replace("\\", "\\\\").replace(special, "\\" + special)


def _split_label(text):
    """("Label", "value") from `Label: value` with the label's escapes undone, or None without a colon."""
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
        elif ch == ":":
            value = text[i + 1:]
            return "".join(out), value[1:] if value.startswith(" ") else value
        else:
            out.append(ch)
            i += 1
    return None


def _split_names(text):
    names, current, i = [], [], 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            current.append(text[i + 1])
            i += 2
            continue
        if ch == ",":
            names.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    names.append("".join(current).strip())
    return [n for n in names if n]


# ---- export --------------------------------------------------------------------------------------


def _column_heading(name):
    folded = name.casefold()
    return f"Column: {name}" if folded in (_ARCHIVED, _SETTINGS) or _starts(name, "column:") else name


def _section_heading(label):
    special = label.casefold() in _PART_NAMES or _starts(label, "checklist:") or _starts(label, "section:")
    return f"Section: {label}" if special else label


def _part(title, lines):
    return [f"#### {title}", ""] + lines + [""]


def _card_lines(look, card, archived_in=None):
    out = [f"### {card['key']} {card['title']}" if card.get("key") else f"### {card['title']}", ""]
    meta = []
    group = look.groups.get(card.get("group"))
    if group:
        meta.append(f"- Group: {group['name']}")
    labels = look.card_labels(card)
    if labels:
        meta.append("- Labels: " + ", ".join(_escape(lab["name"], ",") for lab in labels))
    if card.get("blocked"):
        meta.append(f"- Blocked by: {card['blocked']}")
    if card.get("archived"):
        if archived_in is not None:
            meta.append(f"- Column: {archived_in['name']}")
        meta.append(f"- Archived: {card['archived']}")
    if meta:
        out += meta + [""]
    if card.get("summary"):
        out += [f"> {line}" if line else ">" for line in card["summary"].split("\n")] + [""]
    values = look.card_properties(card)
    if values:
        out += _part("Properties", [f"- {_escape(p['name'], ':')}: {text}" for p, text in values])
    if card.get("fields"):
        lines = []
        for f in card["fields"]:
            first, *more = f["value"].split("\n")
            lines.append(f"- {_escape(f['label'], ':')}:" + (f" {first}" if first else ""))
            lines += [f"  {line}" if line else "" for line in more]
        out += _part("Fields", lines)
    if card.get("tasks"):
        out += _part("Tasks", [f"- [{'x' if t.get('done') else ' '}] {t['text']}" for t in card["tasks"]])
    ticked = set(card.get("checks") or [])
    for cl in look.board.get("checklists") or []:
        items = [i for i in cl["items"] if not i.get("heading")]
        if any(i["id"] in ticked for i in items):
            out += _part(f"Checklist: {cl['name']}", [f"- [{'x' if i['id'] in ticked else ' '}] {i['text']}" for i in items])
    for s in card.get("sections") or []:
        out += _part(_section_heading(s["label"]), escape_text(s["body"]) if s["body"] else [])
    if card.get("notes"):
        out += _part("Notes", escape_text(card["notes"]))
    return out


def settings_json(structure):
    """JSON with each setting on its own line and each entry of a list on its own line, for a shorter block."""
    def dump(value):
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    lines = []
    for key, value in structure.items():
        if isinstance(value, list) and value:
            shown = "[\n" + ",\n".join(f"  {dump(item)}" for item in value) + "\n ]"
        else:
            shown = dump(value)
        lines.append(f" {dump(key)}: {shown}")
    return ["{"] + ",\n".join(lines).split("\n") + ["}"]


def export(board):
    look = Lookups(board)
    out = [f"# {board['name']}", ""]
    if board.get("description"):
        out += escape_text(board["description"]) + [""]
    for col, cards in columns_with_cards(board):
        out += [f"## {_column_heading(col['name'])}", ""]
        for card in cards:
            out += _card_lines(look, card)
    archived = archived_cards(board)
    if archived:
        out += ["## Archived", ""]
        for card in archived:
            out += _card_lines(look, card, look.columns.get(card.get("column")))
    structure = {key: board[key] for key in STRUCTURE if key in board}
    out += ["## Board settings", "",
            "Laneboard reads this part to import the file again with the same columns, labels, properties and "
            "checklists.", "", "```json"]
    out += settings_json(structure) + ["```", ""]
    return "\n".join(out).encode("utf-8", "backslashreplace")


# ---- import: a file Laneboard wrote --------------------------------------------------------------


def _is_body(level, text):
    """Whether the lines under a heading are free text (code blocks in it are followed) or a list."""
    if level == 1:
        return True
    if level == 2:
        return text.casefold() == _SETTINGS
    if level == 3:
        return False
    return not (text.casefold() in _LIST_PARTS or _starts(text, "checklist:"))


def _blocks(lines):
    """(heading level, heading text, lines under it) for each heading in turn, each one as soon as it ends.
    Level 0 holds anything before the first heading."""
    block = (0, "", [])
    fence, body = None, True
    for line in lines:
        if fence is not None:
            block[2].append(line)
            if _closes(line, fence):
                fence = None
            continue
        m = _HEADING.fullmatch(line.rstrip(" \t"))
        if m:
            yield block
            level, text = len(m.group(1)), m.group(2) or ""
            block = (level, text, [])
            body = _is_body(level, text)
            continue
        if body:
            fence = _opens(line)
        block[2].append(line)
    yield block


def _body_text(lines):
    lines = lines[1:] if lines and lines[0] == "" else list(lines)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(unescape_text(lines))


def _settings(lines):
    """The parsed Board settings block."""
    where = "The Board settings part of that file"
    found, fence, inside = None, None, []
    for line in lines:
        if fence is None:
            opened = _opens(line)
            if opened and _FENCE.fullmatch(line).group(2).strip().casefold() == "json":
                fence, inside = opened, []
        elif _closes(line, fence):
            found = inside
            break
        else:
            inside.append(line)
    if found is None:
        raise StoreError(f"{where} has no JSON block in it.")
    data = read_json("\n".join(found).encode("utf-8"), where)
    if not isinstance(data, dict):
        raise StoreError(f"{where} must hold a JSON object.")
    return data


class _Reader:
    """Reads the parts of a file Laneboard wrote, keeping count of lines it couldn't place."""

    def __init__(self):
        self.stray = 0

    def count_stray(self, lines):
        self.stray += sum(1 for line in lines if line.strip())

    def head(self, card, lines):
        summary = []
        for line in lines:
            if not line.strip():
                continue
            if line.startswith(">"):
                summary.append(line[2:] if line.startswith("> ") else line[1:])
                continue
            m = _META.fullmatch(line)
            if m:
                card["meta"][m.group(1).casefold()] = m.group(2) or ""
            else:
                self.stray += 1
        if summary:
            card["summary"] = "\n".join(summary)

    def pairs(self, lines, continued):
        """`- Label: value` lines. With `continued`, lines indented two spaces carry on the value above."""
        out, gap = [], 0
        for line in lines:
            pair = _split_label(line[2:]) if line.startswith("- ") else None
            if pair is not None:
                out.append((pair[0], [pair[1]]))
                gap = 0
            elif continued and out and line.startswith("  "):
                out[-1][1].extend([""] * gap + [line[2:]])
                gap = 0
            elif not line.strip():
                gap += 1
            else:
                self.stray += 1
        return [(label, "\n".join(value)) for label, value in out]

    def ticks(self, lines):
        out = []
        for line in lines:
            m = _TASK.fullmatch(line)
            if m:
                out.append((m.group(2) or "", m.group(1) != " "))
            elif line.strip():
                self.stray += 1
        return out

    def part(self, card, heading, lines):
        folded = heading.casefold()
        if folded == "properties":
            card["props"] += self.pairs(lines, False)
        elif folded == "fields":
            card["fields"] += [{"label": label, "value": value} for label, value in self.pairs(lines, True)]
        elif folded == "tasks":
            card["tasks"] += [{"text": text, "done": done} for text, done in self.ticks(lines)]
        elif _starts(heading, "checklist:"):
            card["ticks"] += [(heading[10:].strip(), text, done) for text, done in self.ticks(lines)]
        elif folded == "notes":
            card["notes"].append(_body_text(lines))
        else:
            label = heading[8:].strip() if _starts(heading, "section:") else heading
            card["sections"].append({"label": label, "body": _body_text(lines)})


def _new_card(heading, column):
    first, _, rest = heading.partition(" ")
    key, title = (first, rest.strip()) if KEY_RE.fullmatch(first) and rest.strip() else (None, heading)
    return {"title": title, "key": key, "column": column, "meta": {}, "summary": "", "props": [], "fields": [],
            "tasks": [], "ticks": [], "sections": [], "notes": []}


def _find_property(board, name):
    """A property by its exact name first (ignoring case), so a name never finds another property's id."""
    folded = name.casefold()
    return next((p for p in board["properties"] if p["name"].casefold() == folded), None) or find_property(board, name)


def _read_laneboard(text):
    reader, warnings = _Reader(), []
    name, intro, headings, raw_cards, settings = None, [], [], [], None
    column, card = None, None
    for level, heading, lines in _blocks(_lines(text)):
        if level == 0 or (level == 1 and name is None):
            name = heading if level == 1 else name
            intro += lines
        elif level <= 2:
            card = None
            folded = heading.casefold()
            if level == 2 and folded == _ARCHIVED:
                column = _IN_ARCHIVE
            elif level == 2 and folded == _SETTINGS:
                settings, column = _settings(lines), None
                continue
            else:
                column = heading[7:].strip() if _starts(heading, "column:") else heading
                headings.append(column)
            reader.count_stray(lines)
        elif level == 3:
            if len(raw_cards) == MAX_CARDS:
                raise _too_many_cards()
            card = _new_card(heading, column)
            raw_cards.append(card)
            reader.head(card, lines)
        elif card is None:
            reader.count_stray(lines)
        else:
            reader.part(card, heading, lines)

    board = {"name": name or DEFAULT_NAME,
             "description": " ".join(line.strip() for line in unescape_text(intro) if line.strip())}
    if name is None:
        warnings.append(f"The file has no # heading with a board name, so the board is called {DEFAULT_NAME}.")
    if settings is not None:
        board.update({key: settings[key] for key in STRUCTURE if key in settings})
    columns = board.get("columns") if isinstance(board.get("columns"), list) else []
    known = {c["name"].casefold() for c in columns if isinstance(c, dict) and isinstance(c.get("name"), str)}
    for heading in headings:
        if heading.casefold() not in known:
            known.add(heading.casefold())
            columns = columns + [{"name": heading, "limit": None}]
    if not columns:
        columns = [{"name": "To do", "limit": None}]
    board["columns"] = columns
    first_column = next((c["name"] for c in columns if isinstance(c, dict) and isinstance(c.get("name"), str)), None)

    if settings is None:  # no definitions to go by, so every property becomes a text property
        names = list(dict.fromkeys(label for c in raw_cards for label, _ in c["props"]))
        if names:
            warnings.append("The file has no Board settings part, so its properties were imported as text.")
        raw_props = [{"name": n, "type": "text"} for n in names]
    else:
        raw_props = board.get("properties") or []
    try:
        board["properties"] = clean_definitions({"properties": [], "cards": []}, raw_props, keep_ids=True)
    except StoreError as e:
        raise StoreError(f"The properties in that file can't be used: {e}") from e

    checklists = board.get("checklists") if isinstance(board.get("checklists"), list) else []
    unmatched = 0
    board["cards"] = []
    for raw in raw_cards:
        out, unmatched_here = _finish_card(board, raw, checklists, first_column)
        unmatched += unmatched_here
        board["cards"].append(out)
    if reader.stray:
        warnings.append(f"Left out {plural(reader.stray, 'line')} that {'was' if reader.stray == 1 else 'were'} not in "
                        "a part of the file Laneboard reads.")
    if unmatched:
        warnings.append(f"Left out {plural(unmatched, 'checklist line')} that didn't match a checklist on the board.")
    return ImportResult([board], warnings)


def _finish_card(board, raw, checklists, first_column):
    meta = raw["meta"]
    where = f"Card {raw['key'] or repr(raw['title'])}"
    card = {"title": raw["title"]}
    if raw["key"]:
        card["key"] = raw["key"]
    column = raw["column"]
    if column is _IN_ARCHIVE or "archived" in meta:
        card["archived"] = meta.get("archived") or True
        column = meta.get("column") or (None if column is _IN_ARCHIVE else column)
    card["column"] = column or first_column
    if meta.get("group"):
        card["group"] = meta["group"]
    if meta.get("labels"):
        card["labels"] = _split_names(meta["labels"])
    if meta.get("blocked by"):
        card["blocked"] = meta["blocked by"]
    for part in ("summary", "fields", "tasks", "sections"):
        if raw[part]:
            card[part] = raw[part]
    if raw["notes"]:
        card["notes"] = "\n\n".join(raw["notes"])
    values = {}
    for label, text in raw["props"]:
        prop = _find_property(board, label)
        if prop is None:
            raise StoreError(f'{where}: there\'s no property called "{label}" on this board.')
        try:
            value = TYPES[prop["type"]].parse_text(text, prop)
        except StoreError as e:
            raise StoreError(f"{where}: {e}") from e
        if value is not None:
            values[prop["id"]] = value
    if values:
        card["properties"] = values
    checks, used, unmatched = [], set(), 0
    for list_name, text, done in raw["ticks"]:
        match = None
        for cl in checklists:
            if isinstance(cl, dict) and isinstance(cl.get("name"), str) and cl["name"].casefold() == list_name.casefold():
                match = next((i for i in cl.get("items") or [] if isinstance(i, dict) and not i.get("heading")
                              and i.get("text") == text and isinstance(i.get("id"), str) and i["id"] not in used), None)
                break
        if match is None:
            unmatched += 1
            continue
        used.add(match["id"])
        if done:
            checks.append(match["id"])
    if checks:
        card["checks"] = checks
    return card, unmatched


# ---- import: a plain task list -------------------------------------------------------------------

_ANY_HEADING = re.compile(r" {0,3}(#{1,6})(?:[ \t]+(.*))?")
_ITEM = re.compile(r"( *)([-*+]|[0-9]{1,9}[.)])(?:[ \t]+(.*))?")
_CHECKBOX = re.compile(r"\[([ xX])\](?:[ \t]+(.*))?")
_BREAK = re.compile(r" {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*")
_ANY_FENCE = re.compile(r" *(`{3,}|~{3,})(.*)")


def _any_heading(line):
    """(level, text) when the line is a heading, as in `## Text` or `## Text ##`; None when it isn't."""
    m = _ANY_HEADING.fullmatch(line.rstrip(" \t"))
    if not m:
        return None
    text = m.group(2) or ""
    bare = text.rstrip("#")  # a closing run of #s, after a space or with nothing before it, isn't part of the text
    if len(bare) < len(text) and (not bare or bare[-1] in " \t"):
        text = bare.rstrip(" \t")
    return len(m.group(1)), text


def _read_plain(text):
    fit, warnings = Fitter(), []
    name, intro, headings, items = None, [], [], []
    current, fence, column, stray, empty = None, None, None, 0, 0

    def note(line):
        indent = len(line) - len(line.lstrip(" "))
        current["notes"] += [""] * current["gap"] + [line[min(indent, current["pad"]):]]
        current["gap"], current["open"] = 0, False

    for line in _lines(text):
        line = line.expandtabs(4).rstrip()
        indent = len(line) - len(line.lstrip(" "))
        inside = current is not None and indent >= current["pad"]
        if fence is not None:
            if inside or (current is not None and not line):
                note(line)
            else:
                stray += bool(line)
            m = _ANY_FENCE.fullmatch(line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not m.group(2).strip():
                fence = None
            continue
        if not line:
            if current is not None:
                current["gap"] += bool(current["notes"])
                current["open"] = False
            continue
        m = _ANY_FENCE.fullmatch(line)
        if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            fence = (m.group(1)[0], len(m.group(1)))
            if inside:
                note(line)
            else:
                stray, current = stray + 1, None
            continue
        heading = _any_heading(line)
        if heading:
            if name is None and heading[0] == 1 and not headings and not items:
                name = heading[1]
            else:
                headings.append(heading[1])
                column = len(headings) - 1
            current = None
            continue
        if _BREAK.fullmatch(line):
            current = None
            continue
        m = _ITEM.fullmatch(line)
        if m:
            done, rest = False, (m.group(3) or "").strip()
            box = _CHECKBOX.fullmatch(rest)
            if box:
                done, rest = box.group(1) != " ", (box.group(2) or "").strip()
            if inside:
                if rest:
                    current["tasks"].append((rest, done))
                else:
                    empty += 1
                current["open"] = False
            elif rest:
                if len(items) == MAX_CARDS:
                    raise _too_many_cards()
                current = {"title": rest, "done": done, "column": column, "tasks": [], "notes": [], "gap": 0,
                           "pad": indent + len(m.group(2)) + 1, "open": True}
                items.append(current)
            else:
                empty, current = empty + 1, None
            continue
        if current is not None and current["open"]:  # the item's text carries on, as a wrapped line does
            if len(current["title"]) <= MAX_TEXT:  # a long title goes into the notes, which can't hold more
                current["title"] += " " + line.strip()
        elif inside:
            note(line)
        elif name is not None and not headings and not items:
            intro.append(line.strip())
        else:
            stray, current = stray + 1, None

    if not items and not headings:
        raise StoreError("That Markdown file has no headings or list items to make a board from.")
    names, keys, index = [], {}, []
    taken = set()
    for heading in headings:
        cleaned = fit.clean(heading) or "Untitled"
        if cleaned.casefold() not in keys:
            keys[cleaned.casefold()] = len(names)
            names.append(fit.name(cleaned, MAX_COLUMN_NAME, taken, ("column name", "column names")))
        index.append(keys[cleaned.casefold()])
    if not names:
        names.append(fit.name("To do", MAX_COLUMN_NAME, taken, ("column name", "column names")))
    if any(item["done"] for item in items) and len(names) == 1 and names[0].casefold() != "done":
        names.append(fit.name("Done", MAX_COLUMN_NAME, taken, ("column name", "column names")))
    if len(names) > MAX_COLUMNS:
        raise StoreError(f"That Markdown file has {len(names)} headings, and a board can have at most {MAX_COLUMNS} "
                         "columns. Join some of the headings together and try again.")

    cards = []
    for item in items:
        title, notes = fit.title(item["title"], "\n".join(item["notes"]))
        if item["done"]:
            column = names[-1]
        else:
            column = names[0] if item["column"] is None else names[index[item["column"]]]
        card = {"title": title, "column": column}
        notes = fit.text(notes, MAX_TEXT, ("card's notes", "cards' notes"), multiline=True)
        if notes:
            card["notes"] = notes
        tasks = fit.first(item["tasks"], MAX_TASKS, "tasks", "card")
        if tasks:
            card["tasks"] = [{"text": fit.text(t, MAX_TASK_TEXT, ("task", "tasks")), "done": done} for t, done in tasks]
        cards.append(card)

    board = {"name": fit.text(name, MAX_NAME, ("board name", "board names")) if name else DEFAULT_NAME,
             "description": fit.text(" ".join(intro), MAX_SUMMARY, ("description", "descriptions")),
             "columns": [{"name": n, "limit": None} for n in names], "cards": cards}
    if not name:
        warnings.append(f"The file has no # heading with a board name, so the board is called {DEFAULT_NAME}.")
    warnings += fit.warnings()
    if empty:
        warnings.append(f"Left out {plural(empty, 'list item')} with no text.")
    if stray:
        warnings.append(f"Left out {plural(stray, 'line')} that "
                        f"{'was not a heading or a list item' if stray == 1 else 'were not headings or list items'}.")
    return ImportResult([board], warnings)


def import_(raw):
    text = decode(raw).replace("\r\n", "\n").replace("\r", "\n")
    if _SETTINGS_LINE.search(text) or _CARD_LINE.search(text):
        return _read_laneboard(text)
    return _read_plain(text)


def sniff(sample):
    text = sample.text
    if text is None or sample.is_json:
        return 0.0
    if _SETTINGS_LINE.search(text) or _CARD_LINE.search(text):
        return 1.0
    if _TASK_LINE.search(text):
        return 0.8
    named = sample.extension in (".md", ".markdown")
    if _SYNTAX_LINE.search(text):
        return 0.6 if named else 0.4
    return 0.3 if named else 0.0


FORMAT = Format("markdown", "Markdown", ".md", "text/markdown; charset=utf-8",
                export=export, import_=import_, sniff=sniff, hints=(".markdown",))
