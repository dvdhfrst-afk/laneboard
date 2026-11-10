"""Trello: a board from the JSON file Trello's "Export as JSON" writes.

Open lists become columns, in Trello's order. Cards keep their name, description (as notes), labels
(Trello's colors mapped to Laneboard's) and checklists (as the card's tasks). Archived cards stay
archived, and the cards of an archived list come in as archived cards in the board's done column (its
last). Custom fields become properties
where Laneboard has a matching kind: text, number, checkbox, and list (as a choice). What Laneboard
doesn't keep (members, attachments, votes, stickers, comments, due dates) is left out, and the import
says how much.
"""
from __future__ import annotations

import math
import re

from ..properties import _name_key, TYPES, clean_definitions
from ..schema import (MAX_CARD_LABELS, MAX_CHOICE_NAME, MAX_CHOICES, MAX_COLUMN_NAME, MAX_COLUMNS, MAX_DECIMALS,
                      MAX_LABEL_NAME, MAX_LABELS, MAX_NAME, MAX_PROPERTIES, MAX_PROPERTY_NAME, MAX_PROPERTY_TEXT,
                      MAX_SUMMARY, MAX_TASK_TEXT, MAX_TASKS, MAX_TEXT, StoreError)
from .common import Fitter, Format, ImportResult, field, join_words, objects, plural, read_json

COLORS = {"green": "green", "yellow": "amber", "orange": "amber", "red": "rose", "purple": "violet", "blue": "blue",
          "sky": "teal", "lime": "green", "pink": "rose", "black": "slate"}
# Trello's custom field kinds that Laneboard has a property type for.
FIELD_TYPES = {"text": "text", "number": "number", "checkbox": "checkbox", "list": "choice"}
WHERE = "That Trello export"
_NUMBER = re.compile(r"\s*[+-]?(?=\.?[0-9])[0-9]*(?:\.([0-9]*))?\s*")


def _color(value):
    """Laneboard's nearest color to a Trello one ("green", "green_dark", "sky_light" and so on)."""
    return COLORS.get(value.split("_")[0], "slate") if isinstance(value, str) else "slate"


def _id(value):
    """An id from the file, or None. Ids are only ever compared, so anything that isn't text is no id."""
    return value if isinstance(value, str) else None


def _position(n, item):
    """A sort key for Trello's own order (`pos`), keeping file order for ties and for no position."""
    pos = item.get("pos")
    usable = isinstance(pos, (int, float)) and not isinstance(pos, bool) and math.isfinite(pos)
    return (pos if usable else math.inf, n)


def _ordered(items):
    return [item for n, item in sorted(enumerate(items), key=lambda pair: _position(*pair))]


def _decimals(text):
    """How many decimal places a number written as text has, or None when it isn't a plain number."""
    m = _NUMBER.fullmatch(text) if isinstance(text, str) else None
    return len(m.group(1) or "") if m else None


class _Fields:
    """Trello custom fields as Laneboard properties, and each card's values read through the property registry."""

    def __init__(self, data, cards, fit):
        self.fit, self.skipped, self.bad, self.rounded = fit, [], 0, 0
        places = {}  # field id -> the most decimal places any card's value has
        for n, card in enumerate(cards, 1):
            for item in objects(card.get("customFieldItems"), f"Card {n} in that Trello export", "customFieldItems"):
                if isinstance(item.get("value"), dict) and _id(item.get("idCustomField")):
                    found = _decimals(item["value"].get("number"))
                    if found is not None:
                        places[item["idCustomField"]] = max(places.get(item["idCustomField"], 0), found)
        wanted = []  # (field id, name in the file, definition, {option id: choice name})
        for n, cf in enumerate(objects(data.get("customFields"), WHERE, "customFields"), 1):
            at = f"Custom field {n} in that Trello export"
            kind = FIELD_TYPES.get(field(cf, "type", str, at))
            fid, name = field(cf, "id", str, at), field(cf, "name", str, at)
            entry, options = {"type": kind}, {}
            if kind == "choice":
                choices, taken = [], set()
                for m, opt in enumerate(objects(cf.get("options"), at, "options"), 1):
                    there = f"{at}, option {m}"
                    value = field(opt, "value", dict, there) or {}
                    choice = fit.name(field(value, "text", str, there), MAX_CHOICE_NAME, taken,
                                      ("choice name", "choice names"), fallback=f"Option {m}", commas=False)
                    choices.append((field(opt, "id", str, there), choice, _color(field(opt, "color", str, there))))
                choices = fit.first(choices, MAX_CHOICES, "choices", "custom field")
                entry["choices"] = [{"name": choice, "color": color} for _, choice, color in choices]
                options = {oid: choice for oid, choice, _ in choices if oid is not None}
            elif kind == "number":
                entry["decimals"] = min(places.get(fid, 0), MAX_DECIMALS)
            if kind is None or (kind == "choice" and not entry["choices"]):
                self.skipped.append(fit.clean(name) or f"number {n}")
                continue
            wanted.append((fid, name, entry, options))
        taken = set()
        wanted = fit.first(wanted, MAX_PROPERTIES, "custom fields")
        for _, name, entry, _ in wanted:
            entry["name"] = fit.name(name, MAX_PROPERTY_NAME, taken, ("property name", "property names"), key=_name_key,
                                     fallback="Custom field")
        self.properties = clean_definitions({"properties": [], "cards": []}, [entry for _, _, entry, _ in wanted])
        self.by_id = {fid: (prop, options) for (fid, _, _, options), prop in zip(wanted, self.properties) if fid is not None}

    def values(self, card, at):
        """{property id: stored value} for one card. Values that can't be read are counted, not kept."""
        out = {}
        for item in objects(card.get("customFieldItems"), at, "customFieldItems"):
            found = self.by_id.get(_id(item.get("idCustomField")))
            if found is None:
                continue
            prop, options = found
            value = item.get("value") if isinstance(item.get("value"), dict) else {}
            if prop["type"] == "choice":
                text = options.get(_id(item.get("idValue")))
            elif prop["type"] == "checkbox":
                text = value.get("checked")
            elif prop["type"] == "number":
                text = value.get("number")
                self.rounded += (_decimals(text) or 0) > MAX_DECIMALS
            else:
                text = value.get("text")
                if isinstance(text, str):
                    text = self.fit.text(text, MAX_PROPERTY_TEXT, ("text value", "text values"))
            if not isinstance(text, str):
                self.bad += 1
                continue
            try:
                stored = TYPES[prop["type"]].parse_text(text, prop)
            except StoreError:
                self.bad += 1
                continue
            if stored is not None:  # an unticked box, or an empty value
                out[prop["id"]] = stored
        return out


def _columns(data, fit):
    """(columns, {list id: column name}, {archived list id: its name}, {list id: position})."""
    columns, column_of, closed, order, taken = [], {}, {}, {}, set()
    for n, lst in enumerate(_ordered(objects(data["lists"], WHERE, "lists")), 1):
        at = f"List {n} in that Trello export"
        lid, name = field(lst, "id", str, at), field(lst, "name", str, at)
        if field(lst, "closed", bool, at):
            if lid is not None:
                closed[lid] = fit.clean(name) or "Untitled list"
            continue
        column = fit.name(name, MAX_COLUMN_NAME, taken, ("column name", "column names"), fallback="Untitled list")
        columns.append({"name": column, "limit": None})
        if lid is not None:
            column_of[lid] = column
            order.setdefault(lid, n)
    if len(columns) > MAX_COLUMNS:
        raise StoreError(f"That Trello board has {len(columns)} open lists, and a Laneboard board can have at most "
                         f"{MAX_COLUMNS} columns. Archive or join some lists in Trello, then export it again.")
    if not columns:
        columns.append({"name": "To do", "limit": None})
    return columns, column_of, closed, order


def _labels(data, fit):
    labels, label_of, taken = [], {}, set()
    for n, lab in enumerate(fit.first(objects(data.get("labels"), WHERE, "labels"), MAX_LABELS, "labels"), 1):
        at = f"Label {n} in that Trello export"
        color = field(lab, "color", str, at)
        name = fit.name(field(lab, "name", str, at), MAX_LABEL_NAME, taken, ("label name", "label names"),
                        fallback=(color or "No color").replace("_", " ").capitalize())
        labels.append({"name": name, "color": _color(color)})
        lid = field(lab, "id", str, at)
        if lid is not None:
            label_of[lid] = name
    return labels, label_of


def _tasks(checklists, at, fit):
    """A card's checklists as one task list, and whether any checklist item had a due date."""
    tasks, due = [], False
    checklists = _ordered(checklists)
    for n, cl in enumerate(checklists, 1):
        there = f"{at}, checklist {n}"
        name = fit.clean(field(cl, "name", str, there))
        for item in _ordered(objects(cl.get("checkItems"), there, "checkItems")):
            text = fit.clean(field(item, "name", str, there))
            due = due or bool(field(item, "due", str, there))
            if text:
                tasks.append({"text": fit.text(f"{name}: {text}" if len(checklists) > 1 and name else text, MAX_TASK_TEXT,
                                               ("checklist item", "checklist items")),
                              "done": item.get("state") == "complete"})
    return tasks, due


def _badge(badges, key):
    value = badges.get(key)
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def import_(raw):
    data = read_json(raw)
    if not isinstance(data, dict) or not isinstance(data.get("lists"), list) or not isinstance(data.get("cards"), list):
        raise StoreError("That isn't a Trello board export: it needs the lists and cards that Trello's Export as JSON writes.")
    fit, warnings = Fitter(), []
    columns, column_of, closed, order = _columns(data, fit)
    first, last = columns[0]["name"], columns[-1]["name"]  # the last column counts as done
    labels, label_of = _labels(data, fit)
    raw_cards = objects(data["cards"], WHERE, "cards")
    fields = _Fields(data, raw_cards, fit)
    checklists = {}
    for n, cl in enumerate(objects(data.get("checklists"), WHERE, "checklists"), 1):
        card_id = field(cl, "idCard", str, f"Checklist {n} in that Trello export")
        if card_id is not None:
            checklists.setdefault(card_id, []).append(cl)

    cards, moved, lost, due = [], 0, 0, 0
    left = {"members": 0, "attachments": 0, "votes": 0, "stickers": 0}
    for n, raw in sorted(enumerate(raw_cards, 1), key=lambda pair: (order.get(_id(pair[1].get("idList")), math.inf),
                                                                    _position(*pair))):
        at = f"Card {n} in that Trello export"
        title, notes = fit.title(field(raw, "name", str, at), field(raw, "desc", str, at) or "")
        list_id = field(raw, "idList", str, at)
        card = {"title": title, "column": last if list_id in closed else column_of.get(list_id, first)}
        notes = fit.text(notes, MAX_TEXT, ("card's notes", "cards' notes"), multiline=True)
        if notes:
            card["notes"] = notes
        if field(raw, "closed", bool, at) or list_id in closed:
            card["archived"] = True
        moved += list_id in closed
        lost += list_id not in column_of and list_id not in closed
        names = list(dict.fromkeys(label_of[i] for i in field(raw, "idLabels", list, at) or [] if _id(i) in label_of))
        if names:
            card["labels"] = fit.first(names, MAX_CARD_LABELS, "labels", "card")
        card_id = field(raw, "id", str, at)
        tasks, task_due = _tasks(checklists.get(card_id, []) if card_id is not None else [], at, fit)
        if tasks:
            card["tasks"] = fit.first(tasks, MAX_TASKS, "tasks", "card")
        values = fields.values(raw, at)
        if values:
            card["properties"] = values
        due += bool(field(raw, "due", str, at)) or task_due
        badges = raw.get("badges") if isinstance(raw.get("badges"), dict) else {}
        left["members"] += bool(field(raw, "idMembers", list, at))
        left["attachments"] += bool(field(raw, "attachments", list, at)) or _badge(badges, "attachments")
        left["votes"] += bool(field(raw, "idMembersVoted", list, at)) or _badge(badges, "votes")
        left["stickers"] += bool(field(raw, "stickers", list, at))
        cards.append(card)

    board = {"name": fit.text(field(data, "name", str, WHERE), MAX_NAME, ("board name", "board names")) or "Trello board",
             "description": fit.text(field(data, "desc", str, WHERE), MAX_SUMMARY, ("description", "descriptions")),
             "columns": columns, "labels": labels, "properties": fields.properties, "cards": cards}
    if closed:
        names = join_words(sorted(closed.values(), key=str.casefold))
        cards_in = (f" The {plural(moved, 'card')} in {'it' if len(closed) == 1 else 'them'} came in as "
                    f"{'an archived card' if moved == 1 else 'archived cards'} in {last}." if moved else "")
        warnings.append(f"Skipped {plural(len(closed), 'archived list')} ({names}).{cards_in}")
    if lost:
        warnings.append(f"{plural(lost, 'card')} named a list that isn't in the export, so "
                        f"{'it is' if lost == 1 else 'they are'} in {first}.")
    if fields.skipped:
        kinds = "those kinds" if len(fields.skipped) > 1 else "that kind"
        warnings.append(f"Left out {plural(len(fields.skipped), 'custom field')} ({join_words(fields.skipped)}) because "
                        f"Laneboard has no property of {kinds}.")
    warnings += fit.warnings()
    if fields.rounded:
        warnings.append(f"Rounded {plural(fields.rounded, 'number value')} to {MAX_DECIMALS} decimal places.")
    if fields.bad:
        warnings.append(f"Left out {plural(fields.bad, 'custom field value')} that couldn't be read.")
    if due:
        warnings.append(f"Left out the due dates on {plural(due, 'card')}.")
    actions = data.get("actions") if isinstance(data.get("actions"), list) else []
    comments = sum(1 for a in actions if isinstance(a, dict) and a.get("type") == "commentCard")
    dropped = [f"{what} on {plural(n, 'card')}" for what, n in left.items() if n]
    if comments:
        dropped.append(plural(comments, "comment"))
    if dropped:
        warnings.append(f"Left out {join_words(dropped)}, which Laneboard doesn't keep.")

    return ImportResult([board], warnings)


def sniff(sample):
    data = sample.json
    if not isinstance(data, dict) or not isinstance(data.get("lists"), list) or not isinstance(data.get("cards"), list):
        return 0.0
    return 1.0 if any(key in data for key in ("idOrganization", "labelNames", "prefs", "shortLink")) else 0.9


FORMAT = Format("trello", "Trello board export", ".json", "application/json; charset=utf-8", import_=import_, sniff=sniff)
