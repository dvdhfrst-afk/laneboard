"""Command line: run the board, list boards, or move a board in and out as JSON.

    python -m laneboard                     run the board at http://127.0.0.1:8642
    python -m laneboard --open              ...and open it in the browser
    python -m laneboard boards              list boards
    python -m laneboard import board.json   add a board from a JSON file
    python -m laneboard export <board>      print a board as JSON (by id or name)
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.request
import webbrowser

from . import __version__
from .server import BoardServer
from .store import Store, StoreError, default_data_path

DEFAULT_PORT = 8642


def already_running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return json.load(r).get("app") == "laneboard"
    except (OSError, ValueError):
        return False


def serve(args):
    # 127.0.0.1, not "localhost": localhost can resolve to ::1, where another program may hold the same port.
    url = f"http://127.0.0.1:{args.port}/"
    try:
        store = Store(args.data)
    except StoreError as e:
        print(f"laneboard: {e}", file=sys.stderr)
        return 2
    try:
        server = BoardServer(("127.0.0.1", args.port), store, verbose=args.verbose)
    except OSError:
        if already_running(args.port):
            print(f"Laneboard is already running at {url}")
            if args.open:
                webbrowser.open(url)
            return 0
        print(f"laneboard: port {args.port} is taken by another program. Try --port.", file=sys.stderr)
        return 2
    print(f"Laneboard {__version__} at {url}  (data: {store.path})  Ctrl+C stops it.")
    if args.open:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def find_board(store, ref):
    boards = store.list_boards()
    matches = [b for b in boards if b["id"] == ref] or [b for b in boards if b["name"].lower() == ref.lower()]
    if len(matches) != 1:
        raise StoreError(f"No single board matches {ref!r}. Run `python -m laneboard boards` to list them.")
    return matches[0]["id"]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="laneboard", description="A local kanban board kept in one JSON file.")
    parser.add_argument("--data", default=default_data_path(), help="boards file (default: %(default)s)")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("serve", help="run the board (the default)")
    for p in (parser, run):
        p.add_argument("--port", type=int, default=DEFAULT_PORT)
        p.add_argument("--open", action="store_true", help="open the board in the browser")
        p.add_argument("--verbose", action="store_true", help="log every request")
    sub.add_parser("boards", help="list boards")
    imp = sub.add_parser("import", help="add a board from a JSON file")
    imp.add_argument("file")
    exp = sub.add_parser("export", help="print a board as JSON")
    exp.add_argument("board", help="board id or name")
    args = parser.parse_args(argv)

    if args.command in (None, "serve"):
        return serve(args)
    try:
        store = Store(args.data)
        if args.command == "boards":
            for b in store.list_boards():
                print(f"{b['id']}  {b['name']}  ({b['cards']} cards)")
        elif args.command == "import":
            with open(args.file, encoding="utf-8") as f:
                board = store.import_board(json.load(f))
            print(f"Imported {board['name']} with {len(board['cards'])} cards as {board['id']}.")
        elif args.command == "export":
            board = store.get_board(find_board(store, args.board))
            json.dump(board, sys.stdout, ensure_ascii=False, indent=1)
            print()
    except (StoreError, OSError, ValueError) as e:
        print(f"laneboard: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
