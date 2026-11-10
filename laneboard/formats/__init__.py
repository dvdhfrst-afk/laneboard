"""Import and export formats: one registry that the command line and the API both use.

Each format is a `Format` with a name, a title, a file extension, a MIME type and an `export(board) ->
bytes` and/or an `import_(raw bytes) -> ImportResult(boards, warnings)`. Imported boards are in the
loose shape `Store.import_data` accepts (names rather than ids, property values as text or as stored
values), so every format ends in the store's all-or-nothing import. `detect_format` picks the format of
a file from its content, with the file name's extension as a hint.
"""
from __future__ import annotations

from ..schema import StoreError
from . import github, htmlpage, markdown, native, plaintext, trello
from .common import MAX_IMPORT_BYTES, Format, ImportResult, Sample

__all__ = ["FORMATS", "Format", "ImportResult", "MAX_IMPORT_BYTES", "detect_format", "get_format", "import_file"]

FORMATS = {f.name: f for f in (native.FORMAT, markdown.FORMAT, htmlpage.FORMAT, plaintext.FORMAT, trello.FORMAT,
                               github.FORMAT)}

_SURE_ENOUGH = 0.2  # below this, detection says it can't tell rather than guess


def _names(use):
    return ", ".join(f.name for f in FORMATS.values() if getattr(f, use) is not None)


def get_format(name, use=None):
    """The format called `name` (any case). With `use` "export" or "import", one that can do that. Raises StoreError."""
    fmt = FORMATS.get(name.strip().lower()) if isinstance(name, str) else None
    if fmt is None:
        shown = name if isinstance(name, str) and len(name) <= 40 else "that"
        raise StoreError(f'There\'s no format called "{shown}". The formats are {", ".join(FORMATS)}.')
    if use == "export" and fmt.export is None:
        raise StoreError(f"{fmt.title} files can be imported, not exported. Formats to export to: {_names('export')}.")
    if use == "import" and fmt.import_ is None:
        raise StoreError(f"{fmt.title} files can be exported, not imported. Formats to import from: {_names('import_')}.")
    return fmt


def detect_format(raw, filename=None):
    """The name of the format a file is in, judged from its content. Raises StoreError when it can't tell."""
    sample = Sample(raw, filename)
    scores = [(f.sniff(sample), n, f.name) for n, f in enumerate(FORMATS.values())]
    best, _, name = max(scores, key=lambda s: (s[0], -s[1]))
    if best >= _SURE_ENOUGH:
        return name
    if sample.text is None:
        raise StoreError(sample.problem)
    if sample.text.lstrip()[:1] in ("{", "[") and not sample.is_json:
        native.FORMAT.import_(raw)  # says exactly what is wrong with the JSON
    if sample.is_json:
        raise StoreError("Laneboard can't tell what that JSON file is. It reads Laneboard boards, Trello board "
                         "exports and GitHub issue lists. Choose the format to see what doesn't fit.")
    raise StoreError("Laneboard can't tell what kind of file that is. It reads Laneboard JSON, Markdown, Trello board "
                     "exports and GitHub issue lists. Choose the format to see what doesn't fit.")


def import_file(raw, name="auto", filename=None):
    """Read a file in the named format, or in the one detected when `name` is "auto". Returns (format name, ImportResult)."""
    fmt = get_format(detect_format(raw, filename) if name in (None, "", "auto") else name, "import")
    return fmt.name, fmt.import_(raw)
