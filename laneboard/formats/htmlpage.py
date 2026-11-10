"""One standalone page for reading or printing a board: its summary, every column with its cards, archived cards and groups.

The page has inline CSS and no script, and loads nothing from anywhere. Every piece of board text is
escaped. As on the board itself, `backticks` become code and web addresses become links; only
addresses starting with http:// or https:// ever become links. A content security policy in the page
refuses scripts, frames and outside requests even if something got through.
"""
from __future__ import annotations

import html
import re

from .. import stats
from ..schema import COLORS, LINK_RE, done_columns
from .common import Format, Lookups, archived_cards, columns_with_cards

_LINK = re.compile(r"(https?://[^\s<>\"'`)\]]+)")
_ENDING = ".,;:!?"  # the period ending a sentence isn't part of the address

CSS = """
:root {
  --bg: oklch(0.985 0.004 255); --surface: oklch(0.995 0.003 255); --sunk: oklch(0.95 0.007 255);
  --line: oklch(0.885 0.01 255); --fg: oklch(0.24 0.02 260); --muted: oklch(0.5 0.018 258);
  --accent: oklch(0.52 0.15 258); --warn: oklch(0.55 0.17 40);
  --blocked: oklch(0.45 0.11 70); --blocked-soft: oklch(0.94 0.07 88);
  --g-blue: oklch(0.55 0.15 255); --g-teal: oklch(0.55 0.1 185); --g-violet: oklch(0.52 0.16 300);
  --g-amber: oklch(0.6 0.13 75); --g-rose: oklch(0.56 0.17 15); --g-green: oklch(0.55 0.13 150);
  --g-slate: oklch(0.55 0.02 258);
  color-scheme: light;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }
body {
  margin: 0 auto; max-width: 1280px; padding: 16px; background: var(--bg); color: var(--fg);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", ui-sans-serif, sans-serif;
}
h1 { font-size: 1.6rem; line-height: 1.25; margin: 0 0 4px; }
h2 { font-size: 1.15rem; margin: 0 0 8px; }
h3 { font-size: 1rem; line-height: 1.35; margin: 0; }
h4 { font-size: 0.85rem; margin: 10px 0 2px; color: var(--muted); }
p { margin: 4px 0; }
a { color: var(--accent); overflow-wrap: anywhere; }
code {
  font-family: ui-monospace, "Cascadia Mono", "SF Mono", Consolas, monospace; font-size: 0.9em;
  background: var(--sunk); border-radius: 4px; padding: 0 4px; overflow-wrap: anywhere;
}
section { margin-top: 24px; }
.muted, .count { color: var(--muted); font-weight: 400; }
.totals { display: flex; flex-wrap: wrap; gap: 8px 28px; margin: 0 0 8px; }
.totals dt { color: var(--muted); font-size: 0.85rem; }
.totals dd { margin: 0; font-size: 1.2rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.table { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 0.9rem; }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.over { color: var(--warn); font-weight: 600; }
.columns { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 280px), 1fr)); gap: 16px; align-items: start; }
.columns > section { margin-top: 0; }
.card {
  background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px;
  margin: 0 0 10px; overflow-wrap: anywhere;
}
.key { font-family: ui-monospace, "Cascadia Mono", "SF Mono", Consolas, monospace; color: var(--muted); font-size: 0.85em; margin-right: 6px; }
.tags { display: flex; flex-wrap: wrap; gap: 4px 6px; margin: 6px 0; padding: 0; list-style: none; }
.tag { font-size: 0.8rem; border-radius: 999px; padding: 0 8px; border: 1px solid var(--g, var(--g-slate)); color: var(--g, var(--g-slate)); }
.flag { background: var(--blocked-soft); color: var(--blocked); border-color: transparent; }
.c-blue { --g: var(--g-blue); } .c-teal { --g: var(--g-teal); } .c-violet { --g: var(--g-violet); }
.c-amber { --g: var(--g-amber); } .c-rose { --g: var(--g-rose); } .c-green { --g: var(--g-green); } .c-slate { --g: var(--g-slate); }
.text { white-space: pre-wrap; }
.props { display: grid; grid-template-columns: minmax(0, max-content) minmax(0, 1fr); gap: 2px 12px; margin: 6px 0; font-size: 0.9rem; }
.props dt { color: var(--muted); }
.props dd { margin: 0; white-space: pre-wrap; }
.tasks { list-style: none; padding: 0; margin: 6px 0; }
.tasks li { padding-left: 1.4em; text-indent: -1.4em; }
.tasks .done { color: var(--muted); }
.empty { color: var(--muted); font-style: italic; }
.plain { padding-left: 1.2em; margin: 4px 0; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
@media print {
  body { background: none; max-width: none; padding: 0; font-size: 10.5pt; }
  .columns { display: block; }
  .columns > section { margin-top: 18px; }
  .card { break-inside: avoid; }
  h2, h3, h4 { break-after: avoid; }
  a { color: inherit; }
  .table { overflow: visible; }
}
""".strip()


def _e(text):
    return html.escape(str(text), quote=True)


def _color(color):
    return color if color in COLORS else "slate"


def rich(text):
    """Card text as HTML: `backticks` become code and web addresses become links, as on the board. All else is escaped."""
    parts = str(text).split("`")
    if len(parts) % 2 == 0:  # an odd number of backticks: the last one is just a backtick
        parts[-2:] = ["`".join(parts[-2:])]
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(f"<code>{_e(part)}</code>")
            continue
        for j, bit in enumerate(_LINK.split(part)):
            if not j % 2:
                out.append(_e(bit))
                continue
            url = bit.rstrip(_ENDING)  # not a regular expression: one on a long run of dots took seconds
            tail = bit[len(url):]
            out.append(f'<a href="{_e(url)}" rel="noopener noreferrer">{_e(url)}</a>{_e(tail)}')
    return "".join(out)


def _link(url):
    return f'<a href="{_e(url)}" rel="noopener noreferrer">{_e(url)}</a>' if LINK_RE.fullmatch(url) else _e(url)


def _tag(text, color, extra=""):
    return f'<li class="tag c-{_color(color)}{extra}">{_e(text)}</li>'


def _summary(board, summary):
    numbers = [p for p in board.get("properties") or [] if p.get("type") == "number"]
    out = ['<section class="summary" aria-labelledby="summary">', '<h2 id="summary">Summary</h2>', '<dl class="totals">']
    totals = [("Cards", summary["cards"]), ("Done", summary["done"]), ("Not done", summary["notDone"]),
              ("Blocked", summary["blocked"]), ("Archived", summary["archived"])]
    for name, key in (("Tasks done", "tasks"), ("Checklist ticks", "checks")):
        if summary[key]["total"]:
            totals.append((name, f"{summary[key]['done']} of {summary[key]['total']}"))
    out += [f"<div><dt>{_e(name)}</dt><dd>{_e(value)}</dd></div>" for name, value in totals] + ["</dl>"]

    head = "".join(f'<th class="n" scope="col">{_e(p["name"])}</th>' for p in numbers)
    out += ['<div class="table"><table>', f'<thead><tr><th scope="col">Column</th><th class="n" scope="col">Cards</th>'
            f'<th class="n" scope="col">Limit</th>{head}</tr></thead>', "<tbody>"]
    for col in summary["columns"]:
        flag = ' <span class="over">over</span>' if col["over"] else ""
        done = ' <span class="muted">(done)</span>' if col["done"] else ""
        sums = "".join(f'<td class="n">{_e(stats.number_text(col["sums"][p["id"]], p))}</td>' for p in numbers)
        out.append(f'<tr><th scope="row">{_e(col["name"])}{done}</th><td class="n">{col["cards"]}{flag}</td>'
                   f'<td class="n">{_e(col["limit"] or "")}</td>{sums}</tr>')
    out.append("</tbody></table></div>")

    for title, entries, rest, none_name in ((board.get("groupLabel") or "Group", summary["groups"], summary["noGroup"],
                                             "None"), ("Labels", summary["labels"], summary["noLabels"], "No labels")):
        if entries:
            items = [_tag(f"{entry['name']} {entry['cards']}", entry["color"]) for entry in entries]
            if rest:
                items.append(_tag(f"{none_name} {rest}", "slate"))
            out.append(f'<h3>{_e(title)}</h3><ul class="tags">{"".join(items)}</ul>')

    props = {p["id"]: p for p in board.get("properties") or []}
    if summary["properties"]:
        out += ['<h3>Properties</h3>', '<div class="table"><table><tbody>']
        for entry in summary["properties"]:
            text = stats.property_summary(entry, props[entry["id"]], summary["cards"])
            out.append(f'<tr><th scope="row">{_e(entry["name"])}</th><td>{_e(text)}</td></tr>')
        out.append("</tbody></table></div>")
    out.append("</section>")
    return out


def _card(look, card, checklists):
    out = ['<article class="card">', f'<h3><span class="key">{_e(card["key"])}</span>{_e(card["title"])}</h3>']
    tags = []
    group = look.groups.get(card.get("group"))
    if group:
        tags.append(_tag(group["name"], group.get("color")))
    tags += [_tag(lab["name"], lab.get("color")) for lab in look.card_labels(card)]
    if card.get("blocked"):
        tags.append(f'<li class="tag flag">Blocked: {_e(card["blocked"])}</li>')
    if tags:
        out.append(f'<ul class="tags">{"".join(tags)}</ul>')
    if card.get("summary"):
        out.append(f'<p class="text">{rich(card["summary"])}</p>')
    pairs = [(p["name"], text) for p, text in look.card_properties(card)]
    pairs += [(f["label"], f["value"]) for f in card.get("fields") or []]
    if pairs:
        out.append('<dl class="props">' + "".join(f"<dt>{_e(name)}</dt><dd>{rich(value)}</dd>" for name, value in pairs)
                   + "</dl>")
    if card.get("tasks"):
        items = []
        for task in card["tasks"]:
            if task.get("done"):
                items.append(f'<li class="done"><span aria-hidden="true">☑ </span><span class="sr">Done: </span>'
                             f'{rich(task["text"])}</li>')
            else:
                items.append(f'<li><span aria-hidden="true">☐ </span><span class="sr">To do: </span>{rich(task["text"])}</li>')
        out.append(f'<ul class="tasks">{"".join(items)}</ul>')
    ticked = set(card.get("checks") or [])
    for name, ids in checklists:
        out.append(f'<p class="muted">{_e(name)}: {len(ticked & ids)} of {len(ids)} ticked</p>')
    for s in card.get("sections") or []:
        out.append(f'<h4>{_e(s["label"])}</h4><div class="text">{rich(s["body"])}</div>')
    if card.get("notes"):
        out.append(f'<h4>Notes</h4><div class="text">{rich(card["notes"])}</div>')
    out.append("</article>")
    return out


def export(board):
    look = Lookups(board)
    summary = stats.summarize(board)
    checklists = [(cl["name"], {i["id"] for i in cl["items"] if not i.get("heading")}) for cl in board.get("checklists") or []]
    checklists = [(name, ids) for name, ids in checklists if ids]
    out = ["<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; "
           "base-uri 'none'; form-action 'none'\">",
           '<meta name="referrer" content="no-referrer">',
           f"<title>{_e(board['name'])}</title>", f"<style>\n{CSS}\n</style>", "</head>", "<body>",
           f"<header><h1>{_e(board['name'])}</h1>"]
    if board.get("description"):
        out.append(f'<p class="text">{rich(board["description"])}</p>')
    out.append("</header>")
    out += _summary(board, summary)
    out.append('<main class="columns">')
    done = done_columns(board)
    for col, cards in columns_with_cards(board):
        count = f"{len(cards)} / {col['limit']}" if col.get("limit") else str(len(cards))
        shown = "over" if col.get("limit") and len(cards) > col["limit"] else "count"
        mark = ' <span class="muted">done</span>' if col["id"] in done else ""
        out.append(f'<section aria-label="{_e(col["name"])}"><h2>{_e(col["name"])} <span class="{shown}">'
                   f"{_e(count)}</span>{mark}</h2>")
        for card in cards:
            out += _card(look, card, checklists)
        if not cards:
            out.append('<p class="empty">No cards</p>')
        out.append("</section>")
    out.append("</main>")
    archived = archived_cards(board)
    if archived:
        out += [f'<section aria-labelledby="archived"><h2 id="archived">Archived cards '
                f'<span class="count">{len(archived)}</span></h2>', '<ul class="plain">']
        for card in archived:
            column = look.columns.get(card.get("column"))
            where = f' <span class="muted">({_e(column["name"])})</span>' if column else ""
            out.append(f'<li><span class="key">{_e(card["key"])}</span>{_e(card["title"])}{where}</li>')
        out.append("</ul></section>")
    if board.get("groups"):
        out += [f'<section aria-labelledby="groups"><h2 id="groups">{_e(board.get("groupLabel") or "Group")} details</h2>',
                '<div class="table"><table><thead><tr><th scope="col">Name</th><th scope="col">Key prefix</th>'
                '<th scope="col">Link</th><th scope="col">Details</th></tr></thead><tbody>']
        for g in board["groups"]:
            details = "<br>".join(f"{_e(d['label'])}: {rich(d['value'])}" for d in g.get("details") or [])
            out.append(f'<tr><th scope="row">{_e(g["name"])}</th><td><code>{_e(g.get("prefix", ""))}</code></td>'
                       f'<td>{_link(g["link"]) if g.get("link") else ""}</td><td>{details}</td></tr>')
        out.append("</tbody></table></div></section>")
    out += ['<footer><p class="muted">Exported from Laneboard.</p></footer>', "</body>", "</html>", ""]
    return "\n".join(out).encode("utf-8", "backslashreplace")


FORMAT = Format("html", "Web page", ".html", "text/html; charset=utf-8", export=export)
