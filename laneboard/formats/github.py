"""GitHub issues: a board from the JSON that `gh issue list --json number,title,body,labels,state,url,milestone,assignees` writes.

The board gets the usual new-board columns. Open issues go in the first column and closed ones in the
last. Each issue keeps its number in its key (issue 12 is GH-12), its labels (GitHub's colors mapped to
the nearest of Laneboard's), its body as notes and its address as a Link field. Milestones become a
choice property called Milestone, and assignees a property called Assignees that can hold several.
"""
from __future__ import annotations

import colorsys
import re

from .. import store as _store
from ..properties import TYPES, clean_definitions
from ..schema import (KEY_RE, LINK_RE, MAX_CARD_LABELS, MAX_CHOICE_NAME, MAX_CHOICES, MAX_FIELD_TEXT, MAX_LABEL_NAME,
                      MAX_LABELS, MAX_MULTI, MAX_NAME, MAX_TEXT, StoreError)
from .common import Fitter, Format, ImportResult, field, objects, plural, read_json

KEY_PREFIX = "GH"
WHERE = "That issue list"
_REPO = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/(?:issues|pull)/[0-9]+")
_HEX = re.compile(r"#?([0-9a-fA-F]{6})")
_HUES = ((15, "rose"), (70, "amber"), (165, "green"), (200, "teal"), (240, "blue"), (330, "violet"), (360, "rose"))


def color(hex_color):
    """The nearest of Laneboard's colors to a label color like "d73a4a". Grays, black and white are slate."""
    m = _HEX.fullmatch(hex_color) if isinstance(hex_color, str) else None
    if not m:
        return "slate"
    r, g, b = (int(m.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, light, saturation = colorsys.rgb_to_hls(r, g, b)
    if saturation < 0.2 or light < 0.12 or light > 0.94:
        return "slate"
    return next(name for top, name in _HUES if hue * 360 < top)


def _issues(data):
    """The issues, checked, as (title, number, body, closed, url, label (name, color) pairs, milestone, assignees)."""
    out = []
    for n, issue in enumerate(objects(data, WHERE, "issues"), 1):
        at = f"Issue {n} in that list"
        labels = []
        for m, lab in enumerate(objects(issue.get("labels"), at, "labels"), 1):
            there = f"{at}, label {m}"
            name = field(lab, "name", str, there)
            if name:
                labels.append((name, field(lab, "color", str, there)))
        milestone = field(issue, "milestone", dict, at)
        logins = []
        for m, person in enumerate(objects(issue.get("assignees"), at, "assignees"), 1):
            login = field(person, "login", str, f"{at}, assignee {m}")
            if login:
                logins.append(login)
        state = field(issue, "state", str, at) or "open"
        out.append({"title": field(issue, "title", str, at, required=True), "number": field(issue, "number", int, at),
                    "body": field(issue, "body", str, at) or "", "closed": state.casefold() in ("closed", "merged"),
                    "url": field(issue, "url", str, at) or "", "labels": labels,
                    "milestone": field(milestone, "title", str, f"{at}, milestone") if milestone else None,
                    "assignees": logins})
    return out


def _choices(fit, names, what):
    """Choice names for a property, fitted, and a map from each original name to its choice."""
    choices, of, taken = [], {}, set()
    for name in names:
        cleaned = fit.clean(name)
        if cleaned and cleaned.casefold() not in of:
            of[cleaned.casefold()] = fit.name(cleaned, MAX_CHOICE_NAME, taken, (f"{what} name", f"{what} names"), commas=False)
            choices.append(of[cleaned.casefold()])
    kept = set(fit.first(choices, MAX_CHOICES, f"{what}s"))
    return [c for c in choices if c in kept], {k: v for k, v in of.items() if v in kept}


def import_(raw):
    data = read_json(raw)
    if not isinstance(data, list):
        raise StoreError("That isn't a GitHub issue list. Make one with: gh issue list --json "
                         "number,title,body,labels,state,url,milestone,assignees")
    if not data:
        raise StoreError("That issue list is empty.")
    issues, fit = _issues(data), Fitter()

    labels, label_of, taken = [], {}, set()
    for name, hex_color in (pair for issue in issues for pair in issue["labels"]):
        cleaned = fit.clean(name)
        if cleaned and cleaned.casefold() not in label_of:
            label_of[cleaned.casefold()] = fit.name(cleaned, MAX_LABEL_NAME, taken, ("label name", "label names"))
            labels.append({"name": label_of[cleaned.casefold()], "color": color(hex_color)})
    labels = fit.first(labels, MAX_LABELS, "labels")
    kept_labels = {lab["name"] for lab in labels}

    milestones, milestone_of = _choices(fit, [i["milestone"] for i in issues if i["milestone"]], "milestone")
    people, person_of = _choices(fit, [login for i in issues for login in i["assignees"]], "assignee")
    raw_props = []
    if milestones:
        raw_props.append({"name": "Milestone", "type": "choice", "choices": milestones})
    if people:
        raw_props.append({"name": "Assignees", "type": "multi", "choices": people})
    props = {p["name"]: p for p in clean_definitions({"properties": [], "cards": []}, raw_props)}

    columns = [{"name": name, "limit": limit} for name, limit in _store.DEFAULT_COLUMNS]
    keys, repeated, cards = set(), 0, []
    for issue in issues:
        title, notes = fit.title(issue["title"], issue["body"])
        card = {"title": title, "column": columns[-1 if issue["closed"] else 0]["name"]}
        key = f"{KEY_PREFIX}-{issue['number']}" if issue["number"] is not None else None
        if key and KEY_RE.fullmatch(key):
            if key in keys:
                repeated += 1
            else:
                keys.add(key)
                card["key"] = key
        notes = fit.text(notes, MAX_TEXT, ("card's notes", "cards' notes"), multiline=True)
        if notes:
            card["notes"] = notes
        if issue["url"] and len(issue["url"]) <= MAX_FIELD_TEXT and LINK_RE.fullmatch(issue["url"]):
            card["fields"] = [{"label": "Link", "value": issue["url"]}]
        names = list(dict.fromkeys(label_of[n] for n in (fit.clean(name).casefold() for name, _ in issue["labels"])
                                   if n in label_of and label_of[n] in kept_labels))
        if names:
            card["labels"] = fit.first(names, MAX_CARD_LABELS, "labels", "card")
        values = {}
        milestone = milestone_of.get(fit.clean(issue["milestone"]).casefold()) if issue["milestone"] else None
        if milestone:
            prop = props["Milestone"]
            values[prop["id"]] = TYPES["choice"].parse_text(milestone, prop)
        held = list(dict.fromkeys(person_of[n] for n in (fit.clean(login).casefold() for login in issue["assignees"])
                                  if n in person_of))
        if held:
            prop = props["Assignees"]
            values[prop["id"]] = TYPES["multi"].parse_text(", ".join(fit.first(held, MAX_MULTI, "assignees", "card")), prop)
        if values:
            card["properties"] = values
        cards.append(card)

    repos = {m.group(1) for m in (_REPO.match(i["url"]) for i in issues) if m}
    name = next(iter(repos)) if len(repos) == 1 else "GitHub issues"
    board = {"name": fit.text(name, MAX_NAME, ("board name", "board names")), "description": "", "columns": columns,
             "labels": labels, "properties": list(props.values()), "cards": cards}
    warnings = fit.warnings()
    if repeated:
        warnings.append(f"{plural(repeated, 'issue')} had the same number as another, so "
                        f"{'it got a new key' if repeated == 1 else 'they got new keys'}.")
    return ImportResult([board], warnings)


def sniff(sample):
    data = sample.json
    if not isinstance(data, list):
        return 0.0
    if not data:
        return 0.5
    if all(isinstance(item, dict) for item in data[:50]):
        first = data[0]
        if "number" in first and "title" in first:
            return 1.0
        if "title" in first:
            return 0.6
    return 0.0


FORMAT = Format("github", "GitHub issue list", ".json", "application/json; charset=utf-8", import_=import_, sniff=sniff)
