"""Command line: run the board, look at boards and cards, move data in and out, and recover from backups.

Exit codes: 0 worked, 1 it couldn't be done (the message says why), 2 the command line itself was wrong.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.request
import webbrowser
from datetime import datetime

from . import __version__, formats, samples, storage
from .properties import TYPE_NAMES, TYPES, find_property, property_text
from .query import QueryError, broken_views, describe_error, run, search_boards
from .schema import (CARD_FACE_PARTS, COLORS, LIMIT_POLICIES, MAX_IMPORT_BYTES, NotFound, SCHEMA_VERSION, StaleRevision,
                     StoreError, decode_text, load_bytes)
from .server import BoardServer, encode_json
from .stats import number_text, property_summary, summarize
from .store import Store, default_data_path, outline, restore_backup, resolve_path
from .templates import describe, get_template

DEFAULT_PORT = 8642
PROG = "laneboard"
EXPORT_FORMATS = [name for name, f in formats.FORMATS.items() if f.export is not None]
IMPORT_FORMATS = [name for name, f in formats.FORMATS.items() if f.import_ is not None]

EXAMPLES = """\
examples:
  laneboard --open                         run the board and open it in the browser
  laneboard sample garden                  add an example board
  laneboard add "My board" "Buy stamps"    add a card without opening the page
  laneboard export "My board" -o board.json
  laneboard backups                        list the copies you can go back to

run a command with --help to see its options. If laneboard isn't on your PATH, use python -m laneboard.
"""


def running_version(port):
    """The version of a Laneboard already answering on this port, or None if there isn't one."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never ask a proxy about localhost
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            info = json.load(r)
    except (OSError, ValueError):
        return None
    return info.get("version") if isinstance(info, dict) and info.get("app") == "laneboard" else None


def report(message):
    """Say what went wrong. Started without a console (pythonw, a desktop shortcut) a dialog is all there is."""
    if sys.stderr:
        print(f"{PROG}: {message}", file=sys.stderr)
    elif os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Laneboard", 0x10)


def serve(args):
    try:
        # 127.0.0.1, not "localhost": localhost can resolve to ::1, where another program may hold the same port.
        server = BoardServer(("127.0.0.1", args.port))
    except OSError as e:
        return already_serving(args, e)
    try:
        server.store = Store(args.data)
    except StoreError as e:
        server.server_close()
        report(str(e))
        return 1
    server.verbose = args.verbose
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Laneboard {__version__} at {url}  (data: {server.store.path})  Ctrl+C stops it.", flush=True)
    if args.open:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        server.server_close()
    return 0


def already_serving(args, error):
    """The port is taken. If it's a Laneboard, that's fine: use it."""
    running = running_version(args.port)
    if not running:
        report(f"Can't use port {args.port}: {storage.reason(error)}. Another program may have it. Try --port with another number.")
        return 1
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Laneboard is already running at {url}")
    if running != __version__:
        print(f"That one is version {running} and this is {__version__}. Stop it and start again to update.")
    if args.open:
        webbrowser.open(url)
    return 0


def find_board(store, ref):
    boards = store.list_boards()
    matches = [b for b in boards if b["id"] == ref] or [b for b in boards if b["name"].lower() == ref.lower()]
    if not matches:
        names = ", ".join(b["name"] for b in boards) or "none yet"
        raise NotFound(f"There's no board called {ref!r}. Boards: {names}.")
    if len(matches) > 1:
        ids = ", ".join(b["id"] for b in matches)
        raise StoreError(f"Several boards are called {ref!r}. Use one of these ids instead: {ids}.")
    return matches[0]["id"]


def find_named(items, ref, what, board):
    match = next((i for i in items if i["id"] == ref), None) or next((i for i in items if i["name"].lower() == ref.lower()), None)
    if match is None:
        names = ", ".join(i["name"] for i in items) or "none"
        raise NotFound(f"{board['name']} has no {what} called {ref!r}. It has: {names}.")
    return match["id"]


def read_file(name):
    """The bytes of a file to import, or of standard input for `-`. Reading stops one byte past the most an
    import can take, so a huge file is refused without being read to the end."""
    if name == "-":
        return sys.stdin.buffer.read(MAX_IMPORT_BYTES + 1)
    try:
        with open(name, "rb") as f:
            return f.read(MAX_IMPORT_BYTES + 1)
    except OSError as e:
        raise StoreError(f"Can't read {name}: {storage.reason(e)}.") from e


def write_json(payload, output, force):
    if output in (None, "-"):
        # Plain ASCII escapes survive any console code page and PowerShell's redirection.
        utf8 = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") == "utf8"
        json.dump(payload, sys.stdout, ensure_ascii=not utf8, indent=1)
        print()
        return
    write_file(encode_json(payload, 1) + b"\n", output, force)


def write_file(raw, output, force):
    """Write an export to a file, or to the screen when `output` is None or -."""
    if output in (None, "-"):
        out = getattr(sys.stdout, "buffer", None)
        if out is None:  # not a real console or pipe, as in a test
            sys.stdout.write(raw.decode("utf-8"))
            return
        sys.stdout.flush()
        out.write(raw)
        out.flush()
        return
    path = resolve_path(output)
    if os.path.exists(path) and not force:
        raise StoreError(f"{output} already exists. Use --force to replace it.")
    try:
        storage.write_atomic(path, raw)
    except OSError as e:
        raise StoreError(f"Can't write {output}: {storage.reason(e)}.") from e
    print(f"Wrote {output}.")


def count(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def cmd_boards(args):
    store = Store(args.data, starter=False)  # looking doesn't create a file
    boards = store.list_boards()
    if args.json:
        write_json(boards, None, False)
        return
    for b in boards:
        extra = f", {b['archived']} archived" if b["archived"] else ""
        print(f"{b['id']}  {b['name']}  ({count(b['cards'], 'card')}{extra})")
    if not boards:
        print("No boards yet.")


def card_line(card, columns, labels=None):
    names = [labels[lid] for lid in card.get("labels", ()) if lid in labels] if labels else []
    shown = f"  [{', '.join(names)}]" if names else ""
    blocked = "  [blocked]" if card["blocked"] else ""
    return f"{card['key']:<12} {columns.get(card['column'], card['column']):<14} {card['title']}{shown}{blocked}"


def searched(board, query, sort, archived=False, view=None):
    """Search one board: (keys, cards considered). A search that can't be read is a plain error, with the
    text it is about and a pointer. `view` is the saved view the search (and maybe the sort) came from."""
    try:
        return run(board, query, sort, archived)
    except QueryError as e:
        if view is not None and (e.part == "query" or sort == view["sort"]):
            trouble = view_trouble(view, e)
            e = QueryError(trouble[:1].upper() + trouble[1:], e.position, e.part)
        raise pointed(e, query, sort) from None


def print_cards(board, cards, indent=""):
    columns = {c["id"]: c["name"] for c in board["columns"]}
    labels = {lab["id"]: lab["name"] for lab in board["labels"]}
    for card in cards:
        print(indent + card_line(card, columns, labels))


def cmd_cards(args):
    store = Store(args.data)
    board = store.get_board(find_board(store, args.board))
    column = find_named(board["columns"], args.column, "column", board) if args.column else None
    query, sort, view = args.query or "", args.sort or "", None
    if args.view is not None:
        vid = find_named(board["views"], args.view, "saved view", board)
        view = next(v for v in board["views"] if v["id"] == vid)
        query, sort = view["query"], view["sort"] if args.sort is None else args.sort
    keys, _ = searched(board, query, sort, archived=args.archived, view=view)
    by_key = {c["key"]: c for c in board["cards"]}
    # --archived lists archived cards instead; without it a search can still ask for them, with is:archived
    cards = [by_key[k] for k in keys if (not args.archived or by_key[k]["archived"]) and (column is None or by_key[k]["column"] == column)]
    if args.json:
        write_json(cards, None, False)
        return
    print_cards(board, cards)
    if not cards:
        print("No cards to show.")


def cmd_search(args):
    store = Store(args.data, starter=False)  # looking doesn't create a file
    sort = args.sort or ""
    if args.board:
        board = store.get_board(find_board(store, args.board))
        keys, considered = searched(board, args.query, sort)
        results = [(board, keys, considered)] if keys else []
    else:
        try:
            results = search_boards(store.all_data()["boards"], args.query, sort)
        except QueryError as e:
            raise pointed(e, args.query, sort) from None
    found = [(board, [next(c for c in board["cards"] if c["key"] == k) for k in keys], considered)
             for board, keys, considered in results]
    if args.json:
        write_json([{"board": board["id"], "name": board["name"], "total": considered, "cards": cards}
                    for board, cards, considered in found], None, False)
        return
    for board, cards, considered in found:
        print(f"{board['name']} ({len(cards)} of {count(considered, 'card')})")
        print_cards(board, cards, "  ")
    if not found:
        print("No cards match.")


def cmd_add(args):
    store = Store(args.data)
    board = store.get_board(find_board(store, args.board))
    values = {"title": args.title}
    if args.column:
        values["column"] = find_named(board["columns"], args.column, "column", board)
    if args.group:
        values["group"] = find_named(board["groups"], args.group, board["groupLabel"].lower(), board)
    if args.summary:
        values["summary"] = args.summary
    _, key = store.create_card(board["id"], values)
    print(key)


def cmd_move(args):
    store = Store(args.data)
    board = store.get_board(find_board(store, args.board))
    column = find_named(board["columns"], args.column, "column", board)
    top = max((c["rank"] for c in board["cards"]), default=0)
    store.update_card(board["id"], args.key, {"column": column, "rank": top + 1})
    print(f"Moved {args.key} to {next(c['name'] for c in board['columns'] if c['id'] == column)}.")


def cmd_archive(args):
    store = Store(args.data)
    board_id = find_board(store, args.board)
    store.update_card(board_id, args.key, {"archived": not args.restore})
    print(f"{'Restored' if args.restore else 'Archived'} {args.key}.")


# ---- one card ------------------------------------------------------------------------------------


def card_of(board, key):
    card = next((c for c in board["cards"] if c["key"] == key), None)
    if card is None:
        raise NotFound(f"There's no card {key} on {board['name']}.")
    return card


def property_named(board, name):
    prop = find_property(board, name)
    if prop is None:
        names = ", ".join(p["name"] for p in board["properties"]) or "none"
        raise NotFound(f"{board['name']} has no property called {name!r}. It has: {names}.")
    return prop


def changing(store, board_id, build, tries=5):
    """Save a change worked out from the board as it is now, sent with that board's rev.

    `build(board)` reads the board, makes its change with rev=board["rev"] and returns what it likes.
    If something else (the page, another command) saves in between, the change is worked out again
    from the newer board, so what the other one did isn't undone.
    """
    for attempt in range(tries):
        try:
            return build(store.get_board(board_id))
        except StaleRevision:
            if attempt == tries - 1:
                raise


def read_text(name):
    """A text file, or standard input for `-`."""
    if name == "-":
        return decode_text(sys.stdin.buffer.read(), "Standard input")
    try:
        with open(name, "rb") as f:
            return decode_text(f.read(), name)
    except OSError as e:
        raise StoreError(f"Can't read {name}: {storage.reason(e)}.") from e


def indented(label, value):
    """A labelled piece of text; a second line and later ones are indented under the label."""
    first, *rest = value.split("\n")
    return [f"{label}: {first}"] + ["  " + line for line in rest]


def task_lines(card):
    if not card["tasks"]:
        return [f"{card['key']} has no tasks yet."]
    return [f"{n}. [{'x' if t['done'] else ' '}] {t['text']}" for n, t in enumerate(card["tasks"], 1)]


def card_lines(board, card):
    """Everything on a card, as plain text."""
    names = lambda items: {i["id"]: i["name"] for i in items}  # noqa: E731
    out = [f"{card['key']}  {card['title']}", f"Column: {names(board['columns']).get(card['column'], card['column'])}"]
    if card["group"]:
        out.append(f"{board['groupLabel']}: {names(board['groups']).get(card['group'], card['group'])}")
    labels = names(board["labels"])
    if card["labels"]:
        out.append("Labels: " + ", ".join(labels.get(lid, lid) for lid in card["labels"]))
    if card["blocked"]:
        out.append(f"Blocked by: {card['blocked']}")
    if card["archived"]:
        out.append(f"Archived: {card['archived']}")
    if card["summary"]:
        out += indented("Summary", card["summary"])
    for prop in board["properties"]:
        shown = property_text(board, card, prop)
        if shown:
            out.append(f"{prop['name']}: {shown}")
    for field in card["fields"]:
        out += indented(field["label"], field["value"])
    if card["tasks"]:
        done = sum(1 for t in card["tasks"] if t["done"])
        out.append(f"Tasks ({done} of {len(card['tasks'])} done):")
        out += ["  " + line for line in task_lines(card)]
    ticked = set(card["checks"])
    for cl in board["checklists"]:
        lines = [i for i in cl["items"] if not i["heading"]]
        if lines:
            out.append(f"{cl['name']} ({sum(1 for i in lines if i['id'] in ticked)} of {len(lines)}):")
            out += [f"  {i['text']}" if i["heading"] else f"  [{'x' if i['id'] in ticked else ' '}] {i['text']}"
                    for i in cl["items"]]
    for section in card["sections"]:
        out.append(f"{section['label']}:")
        out += ["  " + line for line in section["body"].split("\n")]
    if card["notes"]:
        out.append("Notes:")
        out += ["  " + line for line in card["notes"].split("\n")]
    out.append(f"Created {card['created']}, updated {card['updated']}")
    return out


def cmd_show(args):
    store = Store(args.data, starter=False)
    board = store.get_board(find_board(store, args.board))
    card = card_of(board, args.key)
    if args.json:
        write_json(card, None, False)
        return
    for line in card_lines(board, card):
        print(line)


def cmd_edit(args):
    notes = read_text(args.notes_file) if args.notes_file is not None else args.notes
    asked = (args.title, args.summary, args.group, args.column, args.blocked, notes)
    if all(v is None for v in asked) and not (args.label or args.unlabel or args.set or args.clear):
        args.parser.error("say what to change, with an option such as --title or --set")
    pairs = [split_pair(pair, "--set", "PROPERTY=VALUE", args.parser) for pair in args.set]
    store = Store(args.data, starter=False)

    def build(board):
        card = card_of(board, args.key)
        changes = {key: value for key, value in (("title", args.title), ("summary", args.summary),
                                                  ("blocked", args.blocked), ("notes", notes)) if value is not None}
        if args.group is not None:
            changes["group"] = find_named(board["groups"], args.group, board["groupLabel"].lower(), board) if args.group else None
        if args.column is not None:
            column = find_named(board["columns"], args.column, "column", board)
            if column != card["column"]:  # to the bottom, as move does
                changes.update(column=column, rank=max((c["rank"] for c in board["cards"]), default=0) + 1)
        if args.label or args.unlabel:
            labels = card["labels"] + [find_named(board["labels"], name, "label", board) for name in args.label]
            gone = {find_named(board["labels"], name, "label", board) for name in args.unlabel}
            changes["labels"] = [lid for lid in labels if lid not in gone]
        if pairs or args.clear:
            values = {}
            for name, value in pairs:
                prop = property_named(board, name)
                values[prop["id"]] = TYPES[prop["type"]].parse_text(value, prop)
            for name in args.clear:
                values[property_named(board, name)["id"]] = None
            changes["properties"] = values
        store.update_card(board["id"], args.key, changes, rev=board["rev"])

    changing(store, find_board(store, args.board), build)
    print(f"Changed {args.key}.")


def cmd_delete(args):
    if not args.yes:
        args.parser.error(f"deleting {args.key} can't be undone except by restoring a backup; add --yes to delete it")
    store = Store(args.data, starter=False)
    board = store.get_board(find_board(store, args.board))
    title = card_of(board, args.key)["title"]
    store.delete_card(board["id"], args.key)
    print(f"Deleted {args.key} ({title}).")


def cmd_task(args):
    store = Store(args.data, starter=False)
    board_id = find_board(store, args.board)
    if not (args.add or args.done or args.reopen or args.remove):
        for line in task_lines(card_of(store.get_board(board_id), args.key)):
            print(line)
        return

    def build(board):
        tasks = [dict(t) for t in card_of(board, args.key)["tasks"]]
        for n in args.done + args.reopen + args.remove:  # positions as the list stood before this command
            if n > len(tasks):
                raise StoreError(f"{args.key} has {count(len(tasks), 'task')}, so there's no task {n}.")
        for n in args.done:
            tasks[n - 1]["done"] = True
        for n in args.reopen:
            tasks[n - 1]["done"] = False
        tasks = [t for n, t in enumerate(tasks, 1) if n not in args.remove] + [{"text": t} for t in args.add]
        return store.update_card(board["id"], args.key, {"tasks": tasks}, rev=board["rev"])

    for line in task_lines(card_of(changing(store, board_id, build), args.key)):
        print(line)


def cmd_transfer(args):
    store = Store(args.data, starter=False)
    source, target = find_board(store, args.board), find_board(store, args.target)
    done = store.transfer_card(source, args.key, target, args.column)
    card = card_of(done["target"], done["key"])
    column = next(c["name"] for c in done["target"]["columns"] if c["id"] == card["column"])
    print(f"Moved {args.key} to {done['target']['name']} as {done['key']}, in {column}.")
    if done["dropped"]:
        print("Not carried over: " + ", ".join(done["dropped"]) + ".")


# ---- a board's structure -------------------------------------------------------------------------


def split_pair(given, option, shape, parser):
    """OLD=NEW or PROPERTY=VALUE, split at the first =."""
    left, sep, right = given.partition("=")
    if not sep or not left.strip():
        parser.error(f"{option} takes {shape}, like {option} {shape.split('=')[0].capitalize()}=...")
    return left.strip(), right


def cmd_new(args):
    store = Store(args.data, starter=False)  # the new board is the first thing in a new file
    board = store.create_board(args.name, args.description, template=args.template)
    if args.template is None:
        print(f"Added {board['name']} as {board['id']}.")
        return
    template = get_template(store.saved_templates(), args.template)
    cards = count(len(board["cards"]), "card") if board["cards"] else "no cards"
    print(f"Added {board['name']} as {board['id']}, from the template {template['name']}, with {cards}.")


def template_line(t):
    cards = count(t["cards"], "starter card") if t["cards"] else "no starter cards"
    about = f" {t['description']}" if t["description"] else ""
    return f"{t['id']:<20} {t['name']} ({count(t['columns'], 'column')}, {cards}{'' if t['builtIn'] else ', saved'}).{about}"


def cmd_templates(args):
    if args.json and args.action:
        args.parser.error("--json goes with listing the templates")
    store = Store(args.data, starter=False)
    if args.action == "save":
        template = store.save_template(find_board(store, args.board), args.name, args.description, args.cards)
        shown = describe(template)
        cards = count(shown["cards"], "starter card") if shown["cards"] else "no starter cards"
        print(f"Saved {shown['name']} as the template {shown['id']} ({count(shown['columns'], 'column')}, {cards}).")
    elif args.action == "delete":
        if not args.yes:
            args.parser.error("deleting a template can't be undone except by restoring a backup; add --yes to delete it")
        template = get_template(store.saved_templates(), args.name)
        store.delete_template(template["id"])
        print(f"Deleted the template {template['name']}.")
    elif args.json:
        write_json(store.list_templates(), None, False)
    else:
        for t in store.list_templates():
            print(template_line(t))


def cmd_labels(args):
    if args.color and args.add is None:
        args.parser.error("--color goes with --add")
    rename = split_pair(args.rename, "--rename", "OLD=NEW", args.parser) if args.rename is not None else None
    store = Store(args.data, starter=False)
    board_id = find_board(store, args.board)
    said = []

    def build(board):
        said.clear()
        labels = [dict(lab) for lab in board["labels"]]
        if rename:
            lid = find_named(board["labels"], rename[0], "label", board)
            for lab in labels:
                if lab["id"] == lid:
                    said.append(f"Renamed {lab['name']} to {rename[1].strip()}.")
                    lab["name"] = rename[1]
        if args.remove is not None:
            lid = find_named(board["labels"], args.remove, "label", board)
            used = sum(1 for c in board["cards"] if lid in c["labels"])
            said.append(f"Removed {next(lab['name'] for lab in labels if lab['id'] == lid)}"
                        + (f" from the board and from {count(used, 'card')}." if used else "."))
            labels = [lab for lab in labels if lab["id"] != lid]
        if args.add is not None:
            labels.append({"name": args.add, "color": args.color})
            said.append(f"Added {args.add.strip()}.")
        return store.update_board(board["id"], {"labels": labels}, rev=board["rev"])

    if rename or args.remove is not None or args.add is not None:
        board = changing(store, board_id, build)
        print("\n".join(said))
        return
    board = store.get_board(board_id)
    for lab in board["labels"]:
        used = sum(1 for c in board["cards"] if lab["id"] in c["labels"])
        print(f"{lab['name']:<24} {lab['color']:<7} {count(used, 'card')}")
    if not board["labels"]:
        print(f"{board['name']} has no labels yet. Add one with --add NAME.")


PROPERTY_OPTIONS = ("choices", "min", "max", "decimals", "unit")


def describe_property(prop):
    """A definition as one line: its type, then each option it has."""
    parts = [prop["type"]]
    for key in PROPERTY_OPTIONS:
        if key in prop and prop[key] not in (None, "") and not (key == "decimals" and prop[key] == 0):
            value = prop[key]
            parts.append(f"{key} " + (", ".join(c["name"] for c in value) if key == "choices" else str(value)))
    return ", ".join(parts)


def cmd_properties(args):
    given = {key: getattr(args, key) for key in PROPERTY_OPTIONS if getattr(args, key) is not None}
    if (args.type or given) and args.add is None:
        args.parser.error("--type, --choices, --min, --max, --decimals and --unit go with --add")
    if args.add is not None and not args.type:
        args.parser.error("--add needs --type, one of " + ", ".join(TYPE_NAMES))
    rename = split_pair(args.rename, "--rename", "OLD=NEW", args.parser) if args.rename is not None else None
    if "choices" in given:
        given["choices"] = [c.strip() for c in given["choices"].split(",") if c.strip()]
    if args.add is not None:
        made = TYPES[args.type].clean_definition({"name": args.add, "type": args.type, **given})  # checks the options
        unused = [f"--{key}" for key in given if key not in made]
        if unused:
            args.parser.error(f"a {args.type} property doesn't take {' or '.join(unused)}")
    store = Store(args.data, starter=False)
    board_id = find_board(store, args.board)
    said = []

    def build(board):
        said.clear()
        props = [dict(p) for p in board["properties"]]
        if rename:
            pid = property_named(board, rename[0])["id"]
            for prop in props:
                if prop["id"] == pid:
                    said.append(f"Renamed {prop['name']} to {rename[1].strip()}.")
                    prop["name"] = rename[1]
        if args.remove is not None:
            prop = property_named(board, args.remove)
            used = sum(1 for c in board["cards"] if prop["id"] in c["properties"])
            said.append(f"Removed {prop['name']}" + (f" from the board and cleared it on {count(used, 'card')}." if used else "."))
            props = [p for p in props if p["id"] != prop["id"]]
        if args.add is not None:
            props.append({"name": args.add, "type": args.type, **given})
            said.append(f"Added {args.add.strip()}.")
        return store.update_board(board["id"], {"properties": props}, rev=board["rev"])

    if rename or args.remove is not None or args.add is not None:
        changing(store, board_id, build)
        print("\n".join(said))
        return
    board = store.get_board(board_id)
    for prop in board["properties"]:
        used = sum(1 for c in board["cards"] if prop["id"] in c["properties"])
        print(f"{prop['name']:<24} {describe_property(prop)}  ({count(used, 'card')} with a value)")
    if not board["properties"]:
        print(f"{board['name']} has no properties yet. Add one with --add NAME --type TYPE.")


def pointed(error, query, sort=""):
    """A search error as a plain StoreError, with the text it is about shown under it and a ^ at the spot."""
    return StoreError(describe_error(sort if error.part == "sort" else query, error))


def view_trouble(view, error):
    return f"the saved view \"{view['name']}\" doesn't work any more: {error}"


def default_view(given):
    """--default NAME: a saved view by name or id, or none for no default view."""
    return None if given.strip().lower() == "none" else given


def opens_with(board):
    default = next((v["name"] for v in board["views"] if v["id"] == board["settings"]["defaultView"]), None)
    return f"The board opens with {default}." if default else "The board opens with no view."


def cmd_views(args):
    if (args.query is not None or args.sort is not None) and args.add is None:
        args.parser.error("--query and --sort go with --add")
    if args.add is not None and args.query is None:
        args.parser.error("--add needs --query (it can be \"\" for every card)")
    store = Store(args.data, starter=False)
    board_id = find_board(store, args.board)
    said = []

    def build(board):
        said.clear()
        views = [dict(v) for v in board["views"]]
        changes = {}
        if args.remove is not None:
            vid = find_named(board["views"], args.remove, "saved view", board)
            said.append(f"Removed {next(v['name'] for v in views if v['id'] == vid)}.")
            views = [v for v in views if v["id"] != vid]
        if args.add is not None:
            views.append({"name": args.add, "query": args.query, "sort": args.sort or ""})
            said.append(f"Added {args.add.strip()}.")
        if args.remove is not None or args.add is not None:
            changes["views"] = views
        if args.default is not None:
            changes["settings"] = {"defaultView": default_view(args.default)}
        updated = store.update_board(board["id"], changes, rev=board["rev"])
        if args.default is not None:
            said.append(opens_with(updated))
        return updated

    if args.remove is not None or args.add is not None or args.default is not None:
        try:
            changing(store, board_id, build)
        except QueryError as e:  # the new view's search or sort can't be read
            raise pointed(e, args.query, args.sort) from None
        print("\n".join(said))
        return
    board = store.get_board(board_id)
    default = board["settings"]["defaultView"]
    for view in board["views"]:
        sort = f"  sorted by {view['sort']}" if view["sort"] else ""
        mark = "  (opens by default)" if view["id"] == default else ""
        print(f"{view['name']:<24} {view['query'] or '(every card)'}{sort}{mark}")
    if not board["views"]:
        print(f"{board['name']} has no saved views yet. Add one with --add NAME --query Q.")


LIMIT_POLICY_WORDS = {"warn": "warn (a column over its limit is flagged)", "refuse": "refuse (a full column takes no more cards)"}


def cmd_settings(args):
    store = Store(args.data, starter=False)
    board_id = find_board(store, args.board)
    changes = {key: value for key, value in (("name", args.name), ("description", args.description), ("prefix", args.prefix))
               if value is not None}
    settings = {}
    if args.limit_policy:
        settings["limitPolicy"] = args.limit_policy
    if args.card_face is not None:
        settings["cardFace"] = [part.strip() for part in args.card_face.split(",") if part.strip()]
    if args.default is not None:
        settings["defaultView"] = default_view(args.default)
    if settings:
        changes["settings"] = settings
    board = store.update_board(board_id, changes) if changes else store.get_board(board_id)
    names = {p["id"]: p["name"] for p in board["properties"]}
    views = {v["id"]: v["name"] for v in board["views"]}
    face = ", ".join(names.get(part, part) for part in board["settings"]["cardFace"]) or "(only the title)"
    print(f"Name: {board['name']}")
    if board["description"]:
        print(*indented("Description", board["description"]), sep="\n")
    print(f"Key prefix: {board['prefix']}")
    print(f"Limit policy: {LIMIT_POLICY_WORDS[board['settings']['limitPolicy']]}")
    print(f"Card face: {face}")
    print(f"Default view: {views.get(board['settings']['defaultView'], 'none')}")


def cmd_import(args):
    name, result = formats.import_file(read_file(args.file), args.format, None if args.file == "-" else args.file)
    store = Store(args.data, starter=False)  # the first thing in a new file is what's being added
    boards = store.import_boards(result.boards, preview=args.preview)
    if args.preview:
        print(f"Format: {formats.FORMATS[name].title}")
        for board in boards:
            shown = outline(board)
            archived = f", {shown['archived']} of them archived" if shown["archived"] else ""
            print(f"Would add {shown['name']}: {count(shown['cards'], 'card')}{archived}")
            print("  Columns: " + ", ".join(f"{c['name']} {c['cards']}" for c in shown["columns"]))
            if shown["labels"]:
                print("  Labels: " + ", ".join(shown["labels"]))
            if shown["properties"]:
                print("  Properties: " + ", ".join(f"{p['name']} ({p['type']})" for p in shown["properties"]))
    else:
        for board in boards:
            print(f"Imported {board['name']} with {count(len(board['cards']), 'card')} as {board['id']}.")
    for warning in result.warnings:
        print(f"Warning: {warning}")
    if args.preview:
        print("Nothing was added. Run it again without --preview to add it.")


def cmd_export(args):
    if args.all == bool(args.board):
        raise StoreError("Name one board to export, or use --all.")
    if args.all and args.format != "laneboard":
        args.parser.error("--all writes the whole file as Laneboard JSON; export one board at a time for other formats")
    store = Store(args.data, starter=False)
    if args.all:
        write_json(store.all_data(), args.output, args.force)
        return
    board = store.get_board(find_board(store, args.board))
    if args.format == "laneboard":
        write_json(board, args.output, args.force)  # as it always has been, so scripts that read it keep working
    else:
        write_file(formats.get_format(args.format, "export").export(board), args.output, args.force)


def cmd_stats(args):
    store = Store(args.data, starter=False)
    board = store.get_board(find_board(store, args.board))
    summary = summarize(board)
    if args.json:
        write_json(summary, None, False)
        return
    for line in stats_lines(board, summary):
        print(line)


def stats_lines(board, summary):
    """A board's summary as text: totals, a table of columns, then groups, labels and properties."""
    head = [count(summary["cards"], "card")]
    head += [f"{summary[key]} {key}" for key in ("blocked", "archived") if summary[key]]
    out = [f"{board['name']}: {', '.join(head)}"]
    for name, key in (("Tasks", "tasks"), ("Checklist ticks", "checks")):
        if summary[key]["total"]:
            out.append(f"{name}: {summary[key]['done']} of {summary[key]['total']} done")
    numbers = [p for p in board["properties"] if p["type"] == "number"]
    table = [["Column", "Cards", "Limit"] + [p["name"] for p in numbers]]
    for col in summary["columns"]:
        table.append([col["name"], str(col["cards"]), str(col["limit"] or "")]
                     + [number_text(col["sums"][p["id"]], p) for p in numbers])
    widths = [max(len(row[i]) for row in table) for i in range(len(table[0]))]
    out.append("")
    for row, col in zip(table, [None] + summary["columns"]):
        line = "  ".join([row[0].ljust(widths[0])] + [cell.rjust(w) for cell, w in zip(row[1:], widths[1:])])
        out.append(line.rstrip() + ("  over the limit" if col and col["over"] else ""))
    for title, entries, rest, rest_name in ((board["groupLabel"], summary["groups"], summary["noGroup"], "none"),
                                            ("Labels", summary["labels"], summary["noLabels"], "no labels")):
        if entries:
            shown = [f"{e['name']} {e['cards']}" for e in entries] + ([f"{rest_name} {rest}"] if rest else [])
            out += ["", f"{title}: " + ", ".join(shown)]
    if summary["properties"]:
        props = {p["id"]: p for p in board["properties"]}
        width = max(len(e["name"]) for e in summary["properties"])
        out += ["", "Properties:"]
        out += [f"  {e['name'].ljust(width)}  {property_summary(e, props[e['id']], summary['cards'])}"
                for e in summary["properties"]]
    return out


def cmd_sample(args):
    if not args.name:
        for s in samples.list_samples():
            print(f"{s['name']:<20} {s['title']} ({count(s['cards'], 'card')}). {s['description']}")
        return
    store = Store(args.data, starter=False)
    board = store.import_board(samples.load(args.name))
    print(f"Added {board['name']} with {count(len(board['cards']), 'card')} as {board['id']}.")


def _describe(item):
    """A backup, and whether it can actually be used."""
    size = f"{item['bytes'] / 1000:.0f} KB"
    when = datetime.fromtimestamp(item["modified"]).strftime("%Y-%m-%d %H:%M")
    try:
        with open(item["path"], "rb") as f:
            data, _ = load_bytes(f.read(), item["name"])
        contents = f"{count(len(data['boards']), 'board')}, {count(sum(len(b['cards']) for b in data['boards']), 'card')}"
    except (OSError, StoreError):
        contents = "can't be read"
    return f"{item['name']:<42} {when}  {size:>8}  {contents}"


def cmd_backups(args):
    path = resolve_path(args.data)
    found = storage.list_backups(path)
    if not found:
        print(f"No backups of {path} yet. They appear after the first change.")
        return
    print(f"Backups of {path}, newest first:")
    for item in found:
        print("  " + _describe(item))
    print("Go back to one with: laneboard restore NAME")


def cmd_restore(args):
    done = restore_backup(args.data, args.name)
    print(f"Restored {args.name}: {count(done['boards'], 'board')}, {count(done['cards'], 'card')}.")
    if done["kept"]:
        print(f"The file it replaced is kept as {os.path.basename(done['kept'])}.")


def cmd_check(args):
    path = resolve_path(args.data)
    print(f"File: {path}")
    if not os.path.exists(path):
        print("It doesn't exist yet. Laneboard creates it the first time it needs it.")
        return
    store = Store(path, create=False)
    status = store.status()
    print(f"Format: {status['schema']}" + (f" (the file is at {status['upgradedFrom']} and upgrades on the next save)" if status["upgradedFrom"] else ""))
    print(f"Boards: {status['boards']}, cards: {status['cards']}")
    print(f"Backups: {len(storage.list_backups(path))}")
    broken = [(board, view, error) for board in store.all_data()["boards"] for view, error in broken_views(board)]
    for board, view, error in broken:
        print(f"Warning: on {board['name']}, {view_trouble(view, error)}")
    if broken:
        print("Those views are kept as they are. Change or remove them with laneboard views.")
    troubles = store.template_troubles()
    for template, view, error in troubles:
        if view is None:
            print(f"Warning: the template {template['name']} can't make a board: {error}")
        else:
            print(f"Warning: the template {template['name']} has a saved view \"{view['name']}\" that doesn't work: {error}")
    if troubles:
        print("No board can be made from those templates as they are. Fix them in the file, or delete them with "
              "laneboard templates delete.")
    print("OK")


def build_parser():
    default = default_data_path()
    common = Parser(add_help=False)
    # On every command, so `laneboard boards --data x` works as well as `laneboard --data x boards`.
    common.add_argument("--data", metavar="FILE", type=data_file, default=argparse.SUPPRESS,
                        help="boards file (default: $LANEBOARD_DATA, or " + default.replace("%", "%%") + ")")

    def run_options(p):
        p.add_argument("--port", type=port_number, default=DEFAULT_PORT, help="port on 127.0.0.1 (default: %(default)s; 0 picks a free one)")
        p.add_argument("--open", action="store_true", help="open the board in the browser")
        p.add_argument("--verbose", action="store_true", help="log every request")

    parser = Parser(prog=PROG, parents=[common], formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description=f"A local kanban board kept in one JSON file. File format {SCHEMA_VERSION}.", epilog=EXAMPLES)
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    run_options(parser)
    sub = parser.add_subparsers(dest="command", metavar="command")

    def command(name, func, help_text):
        p = sub.add_parser(name, parents=[common], help=help_text, description=help_text + ".")
        p.set_defaults(func=func, parser=p)
        return p

    run_options(sub.add_parser("serve", parents=[common], help="run the board (the default)", description="Run the board."))
    command("boards", cmd_boards, "list boards").add_argument("--json", action="store_true", help="print JSON")
    p = command("cards", cmd_cards, "list the cards on a board")
    p.add_argument("board", help="board id or name")
    p.add_argument("--column", help="only this column (id or name)")
    p.add_argument("--archived", action="store_true", help="show archived cards instead")
    find = p.add_mutually_exclusive_group()
    find.add_argument("--query", metavar="Q", help="only cards that match this search, as typed in the search box")
    find.add_argument("--view", metavar="NAME", help="only cards that match this saved view, in its order")
    p.add_argument("--sort", metavar="S", help="the order within each column, like title or -points (default: as on the board)")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = command("search", cmd_search, "find cards on every board, or on one")
    p.add_argument("query", help="what to look for, as typed in the search box")
    p.add_argument("--board", help="only this board (id or name)")
    p.add_argument("--sort", metavar="S", help="the order within each column, like title or -points")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = command("add", cmd_add, "add a card and print its key")
    p.add_argument("board", help="board id or name")
    p.add_argument("title", help="the card's title")
    p.add_argument("--column", help="column id or name (default: the first column)")
    p.add_argument("--group", help="group id or name")
    p.add_argument("--summary", help="the card's summary")
    p = command("move", cmd_move, "move a card to the bottom of a column")
    p.add_argument("board", help="board id or name")
    p.add_argument("key", help="card key, like T-12")
    p.add_argument("column", help="column id or name")
    p = command("archive", cmd_archive, "archive a card, or bring one back")
    p.add_argument("board", help="board id or name")
    p.add_argument("key", help="card key, like T-12")
    p.add_argument("--restore", action="store_true", help="bring the card back")
    p = command("show", cmd_show, "show everything on one card")
    p.add_argument("board", help="board id or name")
    p.add_argument("key", help="card key, like T-12")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = command("edit", cmd_edit, "change a card")
    p.add_argument("board", help="board id or name")
    p.add_argument("key", help="card key, like T-12")
    p.add_argument("--title", help="a new title")
    p.add_argument("--summary", help="a new summary (\"\" clears it)")
    p.add_argument("--group", help="group id or name (\"\" takes the card out of its group)")
    p.add_argument("--column", help="column id or name; the card goes to the bottom of it")
    p.add_argument("--label", metavar="NAME", action="append", default=[], help="add a label (repeat for more)")
    p.add_argument("--unlabel", metavar="NAME", action="append", default=[], help="take a label off (repeat for more)")
    p.add_argument("--set", metavar="PROPERTY=VALUE", action="append", default=[],
                   help="set a property, written as you would type it: Points=3, Priority=high, Reviewed=yes")
    p.add_argument("--clear", metavar="PROPERTY", action="append", default=[], help="clear a property")
    p.add_argument("--blocked", metavar="TEXT", help="what the card is waiting on (\"\" means it isn't blocked)")
    notes = p.add_mutually_exclusive_group()
    notes.add_argument("--notes", metavar="TEXT", help="replace the notes")
    notes.add_argument("--notes-file", metavar="FILE", help="replace the notes with a text file, or - for standard input")
    p = command("delete", cmd_delete, "delete a card for good")
    p.add_argument("board", help="board id or name")
    p.add_argument("key", help="card key, like T-12")
    p.add_argument("--yes", action="store_true", help="really delete it (there's no undo but restoring a backup)")
    p = command("task", cmd_task, "list a card's tasks, or add, tick, untick and remove them")
    p.add_argument("board", help="board id or name")
    p.add_argument("key", help="card key, like T-12")
    p.add_argument("--add", metavar="TEXT", action="append", default=[], help="add a task at the end (repeat for more)")
    p.add_argument("--done", metavar="N", type=task_number, action="append", default=[], help="tick task N, counting from 1")
    p.add_argument("--reopen", metavar="N", type=task_number, action="append", default=[], help="untick task N")
    p.add_argument("--remove", metavar="N", type=task_number, action="append", default=[], help="remove task N")
    p = command("transfer", cmd_transfer, "move a card to another board")
    p.add_argument("board", help="board id or name the card is on")
    p.add_argument("key", help="card key, like T-12")
    p.add_argument("target", metavar="target_board", help="board id or name to move it to")
    p.add_argument("--column", help="column on the other board (default: one with the same name, or else the first)")
    p = command("new", cmd_new, "make a new board")
    p.add_argument("name", help="the board's name")
    p.add_argument("--description", help="a line about what the board is for (default: the template's, if there is one)")
    p.add_argument("--template", metavar="T", help="start from a template, by id or name (see laneboard templates)")
    p = command("templates", cmd_templates, "list the templates a new board can start from, or save and delete your own")
    p.add_argument("--json", action="store_true", help="print JSON")
    actions = p.add_subparsers(dest="action", metavar="action")
    a = actions.add_parser("save", parents=[common], help="save a board as a template", description="Save a board as a template.")
    a.add_argument("board", help="board id or name")
    a.add_argument("name", help="the template's name")
    a.add_argument("--cards", action="store_true", help="keep the board's cards (without their notes) as starter cards")
    a.add_argument("--description", help="a line about what the template is for")
    a = actions.add_parser("delete", parents=[common], help="delete a template you saved",
                           description="Delete a template you saved.")
    a.add_argument("name", help="template id or name")
    a.add_argument("--yes", action="store_true", help="really delete it (there's no undo but restoring a backup)")
    p = command("labels", cmd_labels, "list a board's labels, or add, rename and remove them")
    p.add_argument("board", help="board id or name")
    p.add_argument("--add", metavar="NAME", help="add a label")
    p.add_argument("--color", choices=COLORS, help="the new label's color (default: the next in turn)")
    p.add_argument("--rename", metavar="OLD=NEW", help="rename a label; cards keep it")
    p.add_argument("--remove", metavar="NAME", help="remove a label from the board and from every card")
    p = command("properties", cmd_properties, "list a board's properties, or add, rename and remove them")
    p.add_argument("board", help="board id or name")
    p.add_argument("--add", metavar="NAME", help="add a property")
    p.add_argument("--type", choices=TYPE_NAMES, help="what kind of value the new property holds")
    p.add_argument("--choices", metavar="A,B,C", help="for choice and multi: the choices, separated by commas")
    p.add_argument("--min", type=number, metavar="N", help="for number: the smallest value")
    p.add_argument("--max", type=number, metavar="N", help="for number: the largest value; for rating: how high it goes (3 to 10)")
    p.add_argument("--decimals", type=int, metavar="N", help="for number: decimal places kept, 0 to 4")
    p.add_argument("--unit", metavar="U", help="for number: a unit shown after the value, like h or kg")
    p.add_argument("--rename", metavar="OLD=NEW", help="rename a property; cards keep their values")
    p.add_argument("--remove", metavar="NAME", help="remove a property and every card's value for it")
    p = command("views", cmd_views, "list a board's saved views, or add and remove them")
    p.add_argument("board", help="board id or name")
    p.add_argument("--add", metavar="NAME", help="save a view")
    p.add_argument("--query", metavar="Q", help="the new view's search, as you would type it in the search box")
    p.add_argument("--sort", metavar="S", help="the new view's sort, like title; one that starts with - is written --sort=-points")
    p.add_argument("--remove", metavar="NAME", help="remove a saved view")
    p.add_argument("--default", metavar="NAME", help="the view the board opens with, or none")
    p = command("settings", cmd_settings, "show a board's settings, or change them")
    p.add_argument("board", help="board id or name")
    p.add_argument("--name", help="rename the board")
    p.add_argument("--description", help="a new description (\"\" clears it)")
    p.add_argument("--prefix", help="key prefix for new cards with no group, like T or REL")
    p.add_argument("--limit-policy", choices=LIMIT_POLICIES,
                   help="warn: flag a column over its limit; refuse: a full column takes no more cards")
    p.add_argument("--card-face", metavar="A,B,C",
                   help="what cards show, in order: " + ", ".join(CARD_FACE_PARTS) + " and property names (\"\" for only the title)")
    p.add_argument("--default", metavar="NAME", help="the saved view the board opens with (by name or id), or none")
    p = command("import", cmd_import, "add boards from a file: Laneboard JSON, Markdown, a Trello export or a GitHub issue list")
    p.add_argument("file", help="the file, or - for standard input")
    p.add_argument("--format", type=str.lower, default="auto", choices=["auto"] + IMPORT_FORMATS,
                   help="what the file is (default: auto, which tells from the file)")
    p.add_argument("--preview", action="store_true", help="show what would be added, and add nothing")
    p = command("export", cmd_export, "write a board as JSON, Markdown, a web page or plain text")
    p.add_argument("board", nargs="?", help="board id or name")
    p.add_argument("--format", type=str.lower, default="laneboard", choices=EXPORT_FORMATS,
                   help="what to write (default: laneboard, the JSON that import reads back)")
    p.add_argument("--all", action="store_true", help="export the whole file instead of one board (Laneboard JSON only)")
    p.add_argument("-o", "--output", metavar="FILE", help="write to this file instead of the screen")
    p.add_argument("--force", action="store_true", help="replace the output file if it exists")
    p = command("sample", cmd_sample, "list the example boards, or add one")
    p.add_argument("name", nargs="?", help="example to add")
    command("backups", cmd_backups, "list the copies of your boards file you can go back to")
    p = command("restore", cmd_restore, "go back to a backup (the current file is kept as a backup)")
    p.add_argument("name", help="a name from the backups command, or 'previous' for the last save")
    p = command("stats", cmd_stats, "show a board's numbers: cards per column, group and label, and property totals")
    p.add_argument("board", help="board id or name")
    p.add_argument("--json", action="store_true", help="print JSON")
    command("check", cmd_check, "check that the boards file can be read")
    return parser


def data_file(value):
    # Not the default file: a script whose variable for the file name came out empty would change the usual boards.
    if not value.strip():
        raise argparse.ArgumentTypeError("needs the name of a boards file")
    return value


def port_number(value):
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} isn't a port number")
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"{value} isn't a port number (they go from 1 to 65535)")
    return port


def task_number(value):
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} isn't a task number (tasks are numbered from 1, as the task command lists them)")
    if n < 1:
        raise argparse.ArgumentTypeError(f"{value} isn't a task number (tasks are numbered from 1)")
    return n


def number(value):
    try:
        return int(value)
    except ValueError:
        pass
    try:
        made = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} isn't a number")
    if made != made or made in (float("inf"), float("-inf")):
        raise argparse.ArgumentTypeError(f"{value!r} isn't a number")
    return made


class Parser(argparse.ArgumentParser):
    def _print_message(self, message, file=None):
        if (file or sys.stderr) is not None:  # started without a console, there's nowhere to print
            super()._print_message(message, file)


def _prepare_streams():
    """Never let a character the console can't show crash a command."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")


def main(argv=None):
    _prepare_streams()
    args = build_parser().parse_args(argv)
    args.data = getattr(args, "data", None) or default_data_path()
    try:
        if args.command in (None, "serve"):
            return serve(args)
        args.func(args)
        if sys.stdout:
            sys.stdout.flush()
    except StoreError as e:
        report(str(e))
        return 1
    except BrokenPipeError:  # `laneboard export x | head`
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())  # so the flush at exit stays quiet too
        return 0
    except OSError as e:
        report(f"{storage.reason(e)}.")
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
