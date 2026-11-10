"""Board templates: the structure of a board (columns, labels, properties, views, settings) with optional starter cards.

A template is `{id, name, description, board, cards}`. `board` and `cards` are in the import shape that
`Store.import_data` accepts: names instead of ids, no card keys. Built-in templates are JSON files in this
folder; saved templates live in the boards file's top-level `templates` list. This module only reads and
builds dicts, it never touches the boards file.
"""
from __future__ import annotations

import copy
import os

from .. import properties as props
from ..schema import (COLORS, ID_RE, MAX_CARDS, MAX_CHECK_ITEMS, MAX_CHECKLISTS, MAX_COLUMNS, MAX_GROUPS, MAX_NAME,
                      MAX_ROWS, MAX_SUMMARY, MAX_TITLE, PREFIX_RE, BadFile, NotFound, StoreError, _is_int, _is_str,
                      _require, _text_rows, parse_json, slug, text)

FOLDER = os.path.dirname(os.path.abspath(__file__))
BUILTIN_IDS = ("basic", "software", "personal", "content", "support", "research")

MAX_LABELS = 50
MAX_LABEL_NAME = 40
MAX_CARD_LABELS = 20
MAX_VIEWS = 30
MAX_VIEW_NAME = 60
MAX_QUERY = 500
MAX_TASKS = 100
MAX_TASK_TEXT = 300
MAX_SECTION_NAME = 60
FACE_PARTS = ("group", "labels", "field", "progress", "blocked")
LIMIT_POLICIES = ("warn", "refuse")

_cache = []


def builtin_templates():
    """The templates that ship with the program, in order. Loaded and checked once; callers get their own copies."""
    if not _cache:
        loaded = []
        for template_id in BUILTIN_IDS:
            where = f"The built-in template {template_id}"
            with open(os.path.join(FOLDER, template_id + ".json"), "rb") as f:
                template = parse_json(f.read(), where)
            _check(template, where)
            if template["id"] != template_id:
                raise BadFile(f"{where} has the id {template['id']!r}.")
            loaded.append(template)
        _cache.extend(loaded)
    return copy.deepcopy(_cache)


# ---- the file check ------------------------------------------------------------------------------


def check_template(template, where):
    """File check for one saved template. Fills in what is missing and raises BadFile naming the template and part."""
    _check(template, where)


def check_templates(templates, where):
    """File check for the top-level `templates` list: each template, no two with the same id or name, no built-in ids."""
    _require(isinstance(templates, list), where, "templates must be a list.")
    ids, names = set(), set()
    for template in templates:
        check_template(template, where)
        _require(template["id"] not in BUILTIN_IDS, where,
                 f"the template {template['name']!r} uses the id {template['id']!r}, which belongs to a built-in template.")
        _require(template["id"] not in ids, where, f"two templates share the id {template['id']!r}.")
        _require(template["name"].casefold() not in names, where, f"two templates are named {template['name']!r}.")
        ids.add(template["id"])
        names.add(template["name"].casefold())


def _check(template, where):
    _require(isinstance(template, dict), where, "a template isn't an object.")
    _require(_is_str(template.get("id")) and ID_RE.fullmatch(template["id"]), where,
             "a template needs an id of lowercase letters, digits and hyphens.")
    name = template.get("name")
    _require(_is_str(name) and name.strip() and len(name) <= MAX_NAME, where,
             f"template {template['id']!r} needs a name of 1 to {MAX_NAME} characters.")
    at = f"{where}: template {name!r}"
    template.setdefault("description", "")
    _require(_is_str(template["description"]) and len(template["description"]) <= MAX_SUMMARY, at,
             f"the description must be text of at most {MAX_SUMMARY} characters.")
    board = template.get("board")
    _require(isinstance(board, dict), at, "it needs a board part with its structure.")
    cards = template.setdefault("cards", [])
    _require(isinstance(cards, list), at, "cards must be a list.")
    _require(len(cards) <= MAX_CARDS, at, f"it has more than {MAX_CARDS} starter cards.")
    columns = _check_columns(board, at)
    groups = _check_groups(board, at)
    labels = _check_labels(board, at)
    definitions = _check_properties(board, at)
    views = _check_views(board, at)
    _check_checklists(board, at)
    _check_new_card_sections(board, at)
    _check_settings(board, definitions, views, at)
    for i, card in enumerate(cards):
        _check_card(card, i, at, columns, groups, labels, definitions)


def _unique_names(items, at, what, limit_each, limit_all):
    _require(len(items) <= limit_all, at, f"{what} can have at most {limit_all} entries.")
    seen = set()
    for item in items:
        _require(_is_str(item.get("name")) and item["name"].strip() and len(item["name"]) <= limit_each, at,
                 f"each of the {what} needs a name of 1 to {limit_each} characters.")
        _require(item["name"].casefold() not in seen, at, f"the {what} include two named {item['name']!r}.")
        seen.add(item["name"].casefold())
    return seen


def _check_columns(board, at):
    columns = board.get("columns")
    _require(isinstance(columns, list) and columns and all(isinstance(c, dict) for c in columns), at,
             "columns must be a list of columns, each with a name and a limit.")
    names = _unique_names(columns, at, "columns", MAX_NAME, MAX_COLUMNS)
    for col in columns:
        col.setdefault("limit", None)
        _require(col["limit"] is None or (_is_int(col["limit"]) and col["limit"] >= 1), at,
                 f"the limit of column {col['name']!r} must be a whole number of at least 1, or null.")
    return names


def _check_groups(board, at):
    prefix = board.setdefault("prefix", "T")
    _require(_is_str(prefix) and PREFIX_RE.fullmatch(prefix), at, "the key prefix must be capital letters and digits, like T.")
    label = board.setdefault("groupLabel", "Group")
    _require(_is_str(label) and 0 < len(label) <= 30, at, "the group label must be text of 1 to 30 characters.")
    groups = board.setdefault("groups", [])
    _require(isinstance(groups, list) and all(isinstance(g, dict) for g in groups), at, "groups must be a list of groups.")
    names = _unique_names(groups, at, "groups", MAX_NAME, MAX_GROUPS)
    for g in groups:
        g.setdefault("prefix", prefix)
        _require(_is_str(g["prefix"]) and PREFIX_RE.fullmatch(g["prefix"]), at, f"group {g['name']!r} has a bad key prefix.")
        g.setdefault("color", "slate")
        _require(g["color"] in COLORS, at, f"group {g['name']!r} has an unknown color.")
        g.setdefault("link", "")
        _require(_is_str(g["link"]), at, f"the link of group {g['name']!r} must be text.")
        g.setdefault("details", [])
        _text_rows(g["details"], at, f"the details of group {g['name']!r}", "value")
        _require(len(g["details"]) <= MAX_ROWS, at, f"group {g['name']!r} has too many details.")
    return names


def _check_labels(board, at):
    labels = board.setdefault("labels", [])
    _require(isinstance(labels, list) and all(isinstance(x, dict) for x in labels), at,
             "labels must be a list of labels, each with a name and a color.")
    names = _unique_names(labels, at, "labels", MAX_LABEL_NAME, MAX_LABELS)
    for label in labels:
        label.setdefault("color", "slate")
        _require(label["color"] in COLORS, at,
                 f"label {label['name']!r} has an unknown color (use one of {', '.join(COLORS)}).")
    return names


def _check_properties(board, at):
    """The cleaned definitions: that is how the registry checks a template's property part."""
    raw = board.setdefault("properties", [])
    try:
        return props.clean_definitions({"properties": [], "cards": []}, raw)
    except StoreError as e:
        raise BadFile(f"{at}: {e}") from e


def _check_views(board, at):
    views = board.setdefault("views", [])
    _require(isinstance(views, list) and all(isinstance(v, dict) for v in views), at,
             "views must be a list of views, each with a name, a query and a sort.")
    names = _unique_names(views, at, "views", MAX_VIEW_NAME, MAX_VIEWS)
    for view in views:
        for part in ("query", "sort"):
            view.setdefault(part, "")
            _require(_is_str(view[part]) and len(view[part]) <= MAX_QUERY, at,
                     f"the {part} of view {view['name']!r} must be text of at most {MAX_QUERY} characters.")
    return names


def _check_checklists(board, at):
    lists = board.setdefault("checklists", [])
    _require(isinstance(lists, list) and len(lists) <= MAX_CHECKLISTS, at,
             f"checklists must be a list of at most {MAX_CHECKLISTS}.")
    for cl in lists:
        _require(isinstance(cl, dict) and _is_str(cl.get("name")) and cl["name"].strip(), at, "each checklist needs a name.")
        items = cl.setdefault("items", [])
        _require(isinstance(items, list) and len(items) <= MAX_CHECK_ITEMS, at,
                 f"the lines of checklist {cl['name']!r} must be a list of at most {MAX_CHECK_ITEMS}.")
        for item in items:
            _require(isinstance(item, dict) and _is_str(item.get("text")) and item["text"].strip(), at,
                     f"each line of checklist {cl['name']!r} needs text.")
            item["heading"] = bool(item.get("heading"))


def _check_new_card_sections(board, at):
    names = board.setdefault("template", [])
    _require(isinstance(names, list) and len(names) <= MAX_ROWS and
             all(_is_str(n) and 0 < len(n.strip()) <= MAX_SECTION_NAME for n in names), at,
             f"new card sections must be a list of at most {MAX_ROWS} names of up to {MAX_SECTION_NAME} characters.")
    _require(len({n.casefold() for n in names}) == len(names), at, "a new card section is listed twice.")


def _check_settings(board, definitions, views, at):
    settings = board.setdefault("settings", {})
    _require(isinstance(settings, dict), at, "settings must be an object.")
    pseudo = {"properties": definitions}
    face = settings.get("cardFace")
    if face is not None:
        _require(isinstance(face, list) and all(_is_str(p) for p in face), at, "settings.cardFace must be a list of names.")
        for part in face:
            _require(part in FACE_PARTS or props.find_property(pseudo, part) is not None, at,
                     f"settings.cardFace names {part!r}, which is neither a card part nor a property of this template.")
    policy = settings.get("limitPolicy")
    _require(policy is None or policy in LIMIT_POLICIES, at, "settings.limitPolicy must be warn or refuse.")
    default = settings.get("defaultView")
    _require(default is None or (_is_str(default) and default.casefold() in views), at,
             "settings.defaultView must be the name of one of the template's views, or null.")


def _check_card(card, index, at, columns, groups, labels, definitions):
    _require(isinstance(card, dict), at, f"starter card number {index + 1} isn't an object.")
    title = card.get("title")
    _require(_is_str(title) and title.strip() and len(title) <= MAX_TITLE, at,
             f"starter card number {index + 1} needs a title of 1 to {MAX_TITLE} characters.")
    where = f"{at}, card {title!r}"
    for part in ("summary", "blocked", "notes"):
        _require(card.get(part) is None or _is_str(card[part]), where, f"{part} must be text.")
    _require(card.get("column") is None or (_is_str(card["column"]) and card["column"].casefold() in columns), where,
             "it is in a column the template doesn't have.")
    _require(card.get("group") in (None, "") or (_is_str(card["group"]) and card["group"].casefold() in groups), where,
             "it belongs to a group the template doesn't have.")
    given = card.get("labels", [])
    _require(isinstance(given, list) and len(given) <= MAX_CARD_LABELS and all(_is_str(n) for n in given), where,
             f"labels must be a list of at most {MAX_CARD_LABELS} names.")
    for name in given:
        _require(name.casefold() in labels, where, f"it uses the label {name!r}, which the template doesn't have.")
    for part, value_key in (("fields", "value"), ("sections", "body")):
        _require(isinstance(card.get(part, []), list), where, f"{part} must be a list.")
        _text_rows(card.get(part, []), where, part, value_key)
        _require(len(card.get(part, [])) <= MAX_ROWS, where, f"{part} has too many entries.")
    checks = card.get("checks", [])
    _require(isinstance(checks, list) and all(_is_str(c) for c in checks), where, "checks must be a list of ids.")
    tasks = card.get("tasks", [])
    _require(isinstance(tasks, list) and len(tasks) <= MAX_TASKS, where, f"tasks must be a list of at most {MAX_TASKS}.")
    for task in tasks:
        _require(isinstance(task, dict) and _is_str(task.get("text")) and task["text"].strip() and
                 len(task["text"]) <= MAX_TASK_TEXT and isinstance(task.get("done", False), bool), where,
                 f"each task needs text of up to {MAX_TASK_TEXT} characters and done set to true or false.")
    values = card.get("properties", {})
    _require(isinstance(values, dict), where, "properties must map property names to values.")
    pseudo = {"properties": definitions}
    for key, value in values.items():
        prop = props.find_property(pseudo, key)
        _require(prop is not None, where, f"it has a value for {key!r}, a property the template doesn't have.")
        kind = props.TYPES[prop["type"]]
        try:
            if isinstance(value, str):
                kind.parse_text(value, prop)
            else:
                kind.clean_value(value, prop)
        except StoreError as e:
            raise BadFile(f"{where}: {e}") from e


# ---- lookups -------------------------------------------------------------------------------------


def find_template(saved, name_or_id):
    """A template by id (built-ins first, then saved ones), else by name ignoring case. None when there isn't one."""
    if not _is_str(name_or_id):
        return None
    pools = (builtin_templates(), [t for t in saved or [] if isinstance(t, dict)])
    for pool in pools:
        for template in pool:
            if template.get("id") == name_or_id:
                return template
    wanted = name_or_id.strip().casefold()
    for pool in pools:
        for template in pool:
            if _is_str(template.get("name")) and template["name"].strip().casefold() == wanted:
                return template
    return None


def get_template(saved, name_or_id):
    """Like `find_template`, but a missing template is a plain error."""
    template = find_template(saved, name_or_id)
    if template is None:
        everything = builtin_templates() + [t for t in saved or [] if isinstance(t, dict) and _is_str(t.get("name"))]
        raise NotFound(f"There's no template called {name_or_id}. The templates are: "
                       f"{', '.join(t['name'] for t in everything)}.")
    return template


def describe(template):
    """What a template listing shows: `{id, name, description, builtIn, columns, cards}` (the last two are counts)."""
    return {"id": template["id"], "name": template["name"], "description": template.get("description", ""),
            "builtIn": template["id"] in BUILTIN_IDS, "columns": len(template["board"]["columns"]),
            "cards": len(template.get("cards") or [])}


# ---- board to template and back ------------------------------------------------------------------


def template_from_board(board, name, description="", include_cards=False, taken_ids=()):
    """A template made from a board's structure. Cards are left out unless asked for.

    Starter cards keep their column, group, labels, properties, tasks, fields, sections and checks, and lose their
    notes, their keys and their archived state (archived cards aren't starter cards, so they are left out).
    """
    name = text(name, "The template name", MAX_NAME, required=True)
    description = text(description, "The template description", MAX_SUMMARY)
    template = {"id": slug(name, set(taken_ids) | set(BUILTIN_IDS)), "name": name, "description": description,
                "board": _structure(board), "cards": _starter_cards(board) if include_cards else []}
    try:
        _check(template, "That board can't be saved as a template")
    except BadFile as e:
        raise StoreError(str(e)) from e
    return template


def board_from_template(template, name=None, description=None):
    """A new board in the import shape, ready for `Store.import_data`: the template's structure and starter cards."""
    out = copy.deepcopy(template["board"])
    out["name"] = name if name else template["name"]
    out["description"] = template.get("description", "") if description is None else description
    out["cards"] = copy.deepcopy(template.get("cards") or [])
    return out


def _structure(board):
    properties = board.get("properties") or []
    views = board.get("views") or []
    prefix = board.get("prefix", "T")
    out = {
        "prefix": prefix,
        "groupLabel": board.get("groupLabel", "Group"),
        "columns": [{"name": c["name"], "limit": c.get("limit")} for c in board["columns"]],
        "groups": [{"name": g["name"], "prefix": g.get("prefix", prefix), "color": g.get("color", "slate"),
                    "link": g.get("link", ""), "details": copy.deepcopy(g.get("details") or [])}
                   for g in board.get("groups") or []],
        "labels": [{"name": x["name"], "color": x.get("color", "slate")} for x in board.get("labels") or []],
        "properties": [_property_shape(p) for p in properties],
        "views": [{"name": v["name"], "query": v.get("query", ""), "sort": v.get("sort", "")} for v in views],
        "checklists": [{"id": cl["id"], "name": cl["name"],
                        "items": [{"id": i["id"], "text": i["text"], "heading": bool(i.get("heading"))}
                                  for i in cl["items"]]}
                       for cl in board.get("checklists") or []],
        "template": list(board.get("template") or []),
    }
    if isinstance(board.get("settings"), dict):
        out["settings"] = _settings_shape(board["settings"], properties, views)
    return out


def _property_shape(prop):
    out = {"name": prop["name"], "type": prop["type"]}
    for key, value in prop.items():
        if key not in ("id", "name", "type", "onCard"):
            out[key] = [{"name": c["name"], "color": c.get("color", "slate")} for c in value] if key == "choices" else value
    return out


def _settings_shape(settings, properties, views):
    by_id = {p["id"]: p for p in properties}
    out = {}
    if "cardFace" in settings:
        # A property is written by name, except one named like a card part (say "Group"), which is written by id.
        face = []
        for part in settings["cardFace"]:
            if part in FACE_PARTS:
                face.append(part)
            elif part in by_id:
                face.append(by_id[part]["name"] if by_id[part]["name"].casefold() not in FACE_PARTS else part)
        out["cardFace"] = face
    if "limitPolicy" in settings:
        out["limitPolicy"] = settings["limitPolicy"]
    if "defaultView" in settings:
        out["defaultView"] = {v["id"]: v["name"] for v in views}.get(settings["defaultView"])
    return out


def _starter_cards(board):
    columns = {c["id"]: c["name"] for c in board["columns"]}
    groups = {g["id"]: g["name"] for g in board.get("groups") or []}
    labels = {x["id"]: x["name"] for x in board.get("labels") or []}
    by_id = {p["id"]: p for p in board.get("properties") or []}
    out = []
    for card in sorted((c for c in board.get("cards") or [] if not c.get("archived")), key=lambda c: c.get("rank", 0)):
        made = {"title": card["title"], "column": columns[card["column"]]}
        if card.get("group") in groups:
            made["group"] = groups[card["group"]]
        for part in ("summary", "blocked", "fields", "sections"):
            if card.get(part):
                made[part] = copy.deepcopy(card[part])
        if card.get("checks"):
            made["checks"] = list(card["checks"])
        names = [labels[i] for i in card.get("labels") or [] if i in labels]
        if names:
            made["labels"] = names
        values = {by_id[pid]["name"]: props.TYPES[by_id[pid]["type"]].format_text(value, by_id[pid])
                  for pid, value in (card.get("properties") or {}).items() if pid in by_id}
        if values:
            made["properties"] = values
        if card.get("tasks"):
            made["tasks"] = [{"text": t["text"], "done": bool(t.get("done"))} for t in card["tasks"]]
        out.append(made)
    return out
