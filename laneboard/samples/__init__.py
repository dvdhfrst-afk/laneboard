"""Example boards that ship with the program."""
from __future__ import annotations

import os

from ..schema import NotFound, parse_json

FOLDER = os.path.dirname(os.path.abspath(__file__))


def names():
    return sorted(f[:-5] for f in os.listdir(FOLDER) if f.endswith(".json"))


def load(name):
    if name not in names():
        raise NotFound(f"There's no example called {name}. The examples are: {', '.join(names())}.")
    with open(os.path.join(FOLDER, name + ".json"), "rb") as f:
        return parse_json(f.read(), f"The example {name}")


def list_samples():
    out = []
    for name in names():
        board = load(name)
        out.append({"name": name, "title": board["name"], "description": board.get("description", ""),
                    "cards": len(board.get("cards", []))})
    return out
