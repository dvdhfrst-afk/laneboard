"""Plain text for pasting into a message: each column with its count, then one `KEY  Title [labels]` line per card.

Archived cards are left out. Keys are padded to the same width so titles line up.
"""
from __future__ import annotations

from .common import Format, Lookups, columns_with_cards


def _count(col, cards):
    if not col.get("limit"):
        return f"({len(cards)})"
    over = ", over the limit" if len(cards) > col["limit"] else ""
    return f"({len(cards)}/{col['limit']}{over})"


def export(board):
    look = Lookups(board)
    columns = columns_with_cards(board)
    width = max((len(card["key"]) for _, cards in columns for card in cards), default=0)
    out = [board["name"]]
    if board.get("description"):
        out.append(board["description"])
    for col, cards in columns:
        out += ["", f"{col['name']} {_count(col, cards)}"]
        for card in cards:
            labels = ", ".join(lab["name"] for lab in look.card_labels(card))
            out.append(f"{card['key'].ljust(width)}  {card['title']}" + (f" [{labels}]" if labels else ""))
    out.append("")
    return "\n".join(out).encode("utf-8", "backslashreplace")


FORMAT = Format("text", "Plain text", ".txt", "text/plain; charset=utf-8", export=export)
