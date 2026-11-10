"""Laneboard's own JSON: a board exactly as it is stored, or a whole boards file.

Export writes the same bytes the export has always written, so a board carries every field it has
(labels, properties, views, settings, tasks and anything newer). Import reads a single board or a whole
boards file, as the import command always has, and hands the boards on unchanged for the store to check.
"""
from __future__ import annotations

from .. import schema
from .. import store as _store
from ..schema import StoreError
from .common import Format, ImportResult, read_json


def export(board):
    return _store.encode_data(board)


def import_(raw):
    data = read_json(raw)
    if not isinstance(data, dict):
        raise StoreError("An imported file must hold a JSON object: one board, or a whole boards file.")
    if "boards" not in data:
        return ImportResult([data], [])
    if not isinstance(data["boards"], list):
        raise StoreError("boards must be a list.")
    version = data.get("version")
    if isinstance(version, int) and not isinstance(version, bool) and version > schema.SCHEMA_VERSION:
        raise StoreError(f"That file was written by a newer Laneboard (file format {version}; this one "
                         f"reads up to {schema.SCHEMA_VERSION}). Update Laneboard to import it.")
    if not data["boards"]:
        raise StoreError("That file has no boards in it.")
    return ImportResult(list(data["boards"]), [])


def sniff(sample):
    data = sample.json
    if not isinstance(data, dict):
        return 0.0
    if isinstance(data.get("boards"), list):
        return 1.0
    if "lists" in data or not isinstance(data.get("name"), str):  # a Trello export has lists; a board has a name
        return 0.0
    return 0.8 if any(key in data for key in ("columns", "cards", "groups", "prefix")) else 0.5


FORMAT = Format("laneboard", "Laneboard JSON", ".json", "application/json; charset=utf-8",
                export=export, import_=import_, sniff=sniff)
