"""A board's numbers: cards per column, group and label, property totals, blocked cards, task and tick progress.

`summarize(board)` works on any board dict the store hands out, including boards from before labels,
properties and tasks existed (missing lists count as empty). It never changes the board, and what it
returns is plain JSON. Archived cards count only in `archived`; every other number is about the cards
still on the board. The API, the `stats` command and the HTML export all show this one summary.
"""
from __future__ import annotations

import math

from .properties import TYPES


def tickable_ids(board):
    """Ids of the checklist lines a card can tick (headings can't be ticked)."""
    return {item["id"] for cl in board.get("checklists") or [] for item in cl.get("items") or [] if not item.get("heading")}


def _whole(number):
    return int(number) if isinstance(number, float) and number.is_integer() else number


def _total(values, decimals):
    """The sum, exact for whole numbers and rounded to the property's decimal places otherwise."""
    if all(isinstance(v, int) for v in values):
        return sum(values)
    return _whole(round(math.fsum(values), decimals))


def _average(values, places):
    return _whole(round(math.fsum(values) / len(values), places)) if values else None


# How each type of property is summed up. Every type in properties.TYPE_NAMES has an entry here; the
# registry knows what a value is, this table only knows what is worth counting about it.

def _count_only(prop, values):
    return {}


def _number(prop, values):
    return {"total": _total(values, prop.get("decimals", 0)),
            "average": _average(values, min(prop.get("decimals", 0) + 2, 6)),
            "min": min(values) if values else None, "max": max(values) if values else None,
            "unit": prop.get("unit", ""), "decimals": prop.get("decimals", 0)}


def _rating(prop, values):
    return {"average": _average(values, 2), "outOf": prop.get("max", 5)}


def _options(prop, values):
    counts = {c["id"]: 0 for c in prop.get("choices") or []}
    for value in values:
        for cid in (value if isinstance(value, list) else [value]):
            if cid in counts:
                counts[cid] += 1
    return {"options": [{"id": c["id"], "name": c["name"], "color": c.get("color", "slate"), "cards": counts[c["id"]]}
                        for c in prop.get("choices") or []]}


_SUMMARIES = {"text": _count_only, "url": _count_only, "checkbox": _count_only, "number": _number,
              "rating": _rating, "choice": _options, "multi": _options}


def number_text(value, prop):
    """A number property's total, average or sum as it shows on a card, with its unit."""
    return TYPES["number"].format_text(value, prop)


def _cards(n):
    return f"{n} card{'' if n == 1 else 's'}"


def property_summary(entry, prop, active):
    """One property's line of the summary in words, like "Total 12 h, average 3 h, from 1 h to 5 h, on 4 cards".
    `entry` is the property's entry in `summarize`, `prop` its definition, `active` the board's card count."""
    if "total" in entry:
        if not entry["cards"]:
            return "No values yet"
        return (f"Total {number_text(entry['total'], prop)}, average {number_text(entry['average'], prop)}, from "
                f"{number_text(entry['min'], prop)} to {number_text(entry['max'], prop)}, on {_cards(entry['cards'])}")
    if "outOf" in entry:
        if not entry["cards"]:
            return "No ratings yet"
        return f"Average {entry['average']:g} out of {entry['outOf']}, on {_cards(entry['cards'])}"
    if "options" in entry:
        return ", ".join(f"{option['name']} {option['cards']}" for option in entry["options"])
    verb = "Ticked" if entry["type"] == "checkbox" else "Set"
    return f"{verb} on {entry['cards']} of {_cards(active)}"


def summarize(board):
    """The board's numbers as a JSON-safe dict.

    `cards`, `archived`, `blocked`: counts. `columns`: `{id, name, cards, limit, over, sums}` in board
    order, where `over` means more cards than the limit and `sums` totals each number property.
    `groups` and `labels`: `{id, name, color, cards}`, with `noGroup` and `noLabels` for the rest.
    `properties`: one entry per property, `{id, name, type, cards}` where `cards` is how many cards
    have a value (a ticked box, for checkboxes), plus `total`, `average`, `min`, `max`, `unit` and
    `decimals` for numbers, `average` and `outOf` for ratings, and `options` (`{id, name, color,
    cards}`) for choices. `tasks` and `checks`: `{done, total}` for card tasks and checklist ticks.
    """
    cards = board.get("cards") or []
    active = [c for c in cards if not c.get("archived")]
    props = [p for p in board.get("properties") or [] if p.get("type") in _SUMMARIES]
    numbers = [p for p in props if p["type"] == "number"]
    tickable = tickable_ids(board)

    columns = {}
    for col in board.get("columns") or []:
        columns[col["id"]] = {"id": col["id"], "name": col["name"], "cards": 0, "limit": col.get("limit"), "over": False,
                              "_values": {p["id"]: [] for p in numbers}}
    groups = {g["id"]: 0 for g in board.get("groups") or []}
    labels = {lab["id"]: 0 for lab in board.get("labels") or []}
    values = {p["id"]: [] for p in props}
    no_group = no_labels = blocked = tasks_done = tasks_total = ticks_done = 0

    for card in active:
        column = columns.get(card.get("column"))
        if column is not None:
            column["cards"] += 1
        group = card.get("group")
        if group in groups:
            groups[group] += 1
        else:
            no_group += 1
        held = [lid for lid in card.get("labels") or [] if lid in labels]
        for lid in held:
            labels[lid] += 1
        no_labels += not held
        blocked += bool(card.get("blocked"))
        tasks = card.get("tasks") or []
        tasks_total += len(tasks)
        tasks_done += sum(1 for t in tasks if t.get("done"))
        ticks_done += len(set(card.get("checks") or []) & tickable)
        for pid, value in (card.get("properties") or {}).items():
            if pid in values and value is not None:
                values[pid].append(value)
                if column is not None and pid in column["_values"]:
                    column["_values"][pid].append(value)

    by_id = {p["id"]: p for p in numbers}
    for column in columns.values():
        column["over"] = bool(column["limit"]) and column["cards"] > column["limit"]
        column["sums"] = {pid: _total(found, by_id[pid].get("decimals", 0)) for pid, found in column.pop("_values").items()}

    return {
        "cards": len(active),
        "archived": len(cards) - len(active),
        "blocked": blocked,
        "columns": list(columns.values()),
        "groups": [{"id": g["id"], "name": g["name"], "color": g.get("color", "slate"), "cards": groups[g["id"]]}
                   for g in board.get("groups") or []],
        "noGroup": no_group,
        "labels": [{"id": lab["id"], "name": lab["name"], "color": lab.get("color", "slate"), "cards": labels[lab["id"]]}
                   for lab in board.get("labels") or []],
        "noLabels": no_labels,
        "properties": [{"id": p["id"], "name": p["name"], "type": p["type"], "cards": len(values[p["id"]]),
                        **_SUMMARIES[p["type"]](p, values[p["id"]])} for p in props],
        "tasks": {"done": tasks_done, "total": tasks_total},
        "checks": {"done": ticks_done, "total": len(tickable) * len(active)},
    }
