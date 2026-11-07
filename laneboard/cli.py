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

from . import __version__, samples, storage
from .schema import NotFound, SCHEMA_VERSION, StoreError, load_bytes, parse_json
from .server import BoardServer, encode_json
from .store import Store, default_data_path, restore_backup, resolve_path

DEFAULT_PORT = 8642
PROG = "laneboard"

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


def read_json(name):
    """Parse a JSON file, or standard input for `-`."""
    if name == "-":
        return parse_json(sys.stdin.buffer.read(), "Standard input")
    try:
        with open(name, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise StoreError(f"Can't read {name}: {storage.reason(e)}.") from e
    return parse_json(raw, name)


def write_json(payload, output, force):
    if output in (None, "-"):
        # Plain ASCII escapes survive any console code page and PowerShell's redirection.
        utf8 = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") == "utf8"
        json.dump(payload, sys.stdout, ensure_ascii=not utf8, indent=1)
        print()
        return
    path = resolve_path(output)
    if os.path.exists(path) and not force:
        raise StoreError(f"{output} already exists. Use --force to replace it.")
    try:
        storage.write_atomic(path, encode_json(payload, 1) + b"\n")
    except OSError as e:
        raise StoreError(f"Can't write {output}: {storage.reason(e)}.") from e
    print(f"Wrote {output}.")


def cmd_boards(args):
    store = Store(args.data)
    boards = store.list_boards()
    if args.json:
        write_json(boards, None, False)
        return
    for b in boards:
        extra = f", {b['archived']} archived" if b["archived"] else ""
        print(f"{b['id']}  {b['name']}  ({b['cards']} cards{extra})")
    if not boards:
        print("No boards yet.")


def card_line(card, columns):
    blocked = "  [blocked]" if card["blocked"] else ""
    return f"{card['key']:<12} {columns.get(card['column'], card['column']):<14} {card['title']}{blocked}"


def cmd_cards(args):
    store = Store(args.data)
    board = store.get_board(find_board(store, args.board))
    column = find_named(board["columns"], args.column, "column", board) if args.column else None
    order = {c["id"]: i for i, c in enumerate(board["columns"])}
    cards = [c for c in board["cards"] if bool(c["archived"]) == args.archived and (column is None or c["column"] == column)]
    cards.sort(key=lambda c: (order.get(c["column"], 0), c["rank"]))
    if args.json:
        write_json(cards, None, False)
        return
    columns = {c["id"]: c["name"] for c in board["columns"]}
    for card in cards:
        print(card_line(card, columns))
    if not cards:
        print("No cards to show.")


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


def _cards(n):
    return f"{n} card{'' if n == 1 else 's'}"


def cmd_import(args):
    store = Store(args.data, starter=False)  # the first thing in a new file is what's being added
    for board in store.import_data(read_json(args.file)):
        print(f"Imported {board['name']} with {_cards(len(board['cards']))} as {board['id']}.")


def cmd_export(args):
    if args.all == bool(args.board):
        raise StoreError("Name one board to export, or use --all.")
    store = Store(args.data)
    write_json(store.all_data() if args.all else store.get_board(find_board(store, args.board)), args.output, args.force)


def cmd_sample(args):
    if not args.name:
        for s in samples.list_samples():
            print(f"{s['name']:<20} {s['title']} ({s['cards']} cards). {s['description']}")
        return
    store = Store(args.data, starter=False)
    board = store.import_board(samples.load(args.name))
    print(f"Added {board['name']} with {_cards(len(board['cards']))} as {board['id']}.")


def _describe(item):
    """A backup, and whether it can actually be used."""
    size = f"{item['bytes'] / 1000:.0f} KB"
    when = datetime.fromtimestamp(item["modified"]).strftime("%Y-%m-%d %H:%M")
    try:
        with open(item["path"], "rb") as f:
            data, _ = load_bytes(f.read(), item["name"])
        contents = f"{len(data['boards'])} boards, {sum(len(b['cards']) for b in data['boards'])} cards"
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
    print(f"Restored {args.name}: {done['boards']} boards, {done['cards']} cards.")
    if done["kept"]:
        print(f"The file it replaced is kept as {os.path.basename(done['kept'])}.")


def cmd_check(args):
    path = resolve_path(args.data)
    print(f"File: {path}")
    if not os.path.exists(path):
        print("It doesn't exist yet. Laneboard creates it the first time it needs it.")
        return
    status = Store(path, create=False).status()
    print(f"Format: {status['schema']}" + (f" (the file is at {status['upgradedFrom']} and upgrades on the next save)" if status["upgradedFrom"] else ""))
    print(f"Boards: {status['boards']}, cards: {status['cards']}")
    print(f"Backups: {len(storage.list_backups(path))}")
    print("OK")


def build_parser():
    default = default_data_path()
    common = Parser(add_help=False)
    # On every command, so `laneboard boards --data x` works as well as `laneboard --data x boards`.
    common.add_argument("--data", metavar="FILE", default=argparse.SUPPRESS,
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
        p.set_defaults(func=func)
        return p

    run_options(sub.add_parser("serve", parents=[common], help="run the board (the default)", description="Run the board."))
    command("boards", cmd_boards, "list boards").add_argument("--json", action="store_true", help="print JSON")
    p = command("cards", cmd_cards, "list the cards on a board")
    p.add_argument("board", help="board id or name")
    p.add_argument("--column", help="only this column (id or name)")
    p.add_argument("--archived", action="store_true", help="show archived cards instead")
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
    p = command("import", cmd_import, "add boards from a JSON file (one exported board, or a whole boards file)")
    p.add_argument("file", help="the file, or - for standard input")
    p = command("export", cmd_export, "write a board as JSON")
    p.add_argument("board", nargs="?", help="board id or name")
    p.add_argument("--all", action="store_true", help="export the whole file instead of one board")
    p.add_argument("-o", "--output", metavar="FILE", help="write to this file instead of the screen")
    p.add_argument("--force", action="store_true", help="replace the output file if it exists")
    p = command("sample", cmd_sample, "list the example boards, or add one")
    p.add_argument("name", nargs="?", help="example to add")
    command("backups", cmd_backups, "list the copies of your boards file you can go back to")
    p = command("restore", cmd_restore, "go back to a backup (the current file is kept as a backup)")
    p.add_argument("name", help="a name from the backups command, or 'previous' for the last save")
    command("check", cmd_check, "check that the boards file can be read")
    return parser


def port_number(value):
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} isn't a port number")
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"{value} isn't a port number (they go from 1 to 65535)")
    return port


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
