# The boards file

One JSON file in UTF-8. You can read it, edit it by hand, and keep it in version control.

```json
{
  "version": 2,
  "boards": [ { "id": "b-91b41531", "name": "Software release", "rev": 12, "...": "..." } ]
}
```

## A board

| Field | Meaning |
|---|---|
| `id` | Stays the same for the life of the board |
| `name`, `description` | Shown at the top of the page |
| `prefix` | Starts the keys of cards with no group, as in `T-12` |
| `groupLabel` | What a group is on this board: Repo, Client, Project |
| `rev` | Goes up by one with every change. Pages use it to notice changes |
| `created`, `updated` | ISO 8601 times |
| `columns` | `[{id, name, limit}]`, left to right. `limit` is a number or `null` |
| `groups` | `[{id, name, prefix, color, link, details}]`. `details` is `[{label, value}]` |
| `template` | Names of the sections a new card starts with |
| `checklists` | `[{id, name, items: [{id, text, heading}]}]` |
| `counters` | `{prefix: number}`, the last number used for each key prefix, so a deleted card's key never comes back |
| `cards` | The cards, in no particular order |

Colors are `blue`, `teal`, `violet`, `amber`, `rose`, `green` and `slate`. A group `link` must start
with `http://` or `https://`, or the file is refused.

## A card

| Field | Meaning |
|---|---|
| `key` | `T-12` or `WEB-3`. Unique on the board, never reused |
| `title` | Required |
| `column` | The `id` of a column |
| `rank` | Position in the column, lowest first. Only the order matters |
| `group` | The `id` of a group, or `null` |
| `summary`, `blocked`, `notes` | Text. An empty `blocked` means it isn't blocked |
| `fields` | `[{label, value}]`. The first shows on the card |
| `sections` | `[{label, body}]` |
| `checks` | The ids of the checklist items ticked on this card |
| `archived` | `null`, or the time it was archived |
| `created`, `updated` | ISO 8601 times |

## Limits

Board name 120 characters, card title 200, summary 1,000, other text 20,000. 500 boards, 20,000 cards
per board, 20 columns, 50 groups, 30 fields or sections on a card, 10 checklists of 100 lines.

## Editing by hand

Edit it while the board is running if you like. When the file changes, Laneboard re-reads it on its next
request. If the result isn't valid, it says what's wrong and where, and refuses reads and saves until it
is fixed, so your file is never overwritten with a guess. Fields it doesn't know are kept. `laneboard check`
tells you whether a file is good without changing anything.

A hand-written import can leave out most of this. Only a board `name` is needed, and cards only need a
`title`. Columns can be named by their name, and missing columns get the defaults:

```json
{
  "name": "Moving house",
  "columns": [{ "name": "To do" }, { "name": "Done" }],
  "cards": [{ "title": "Book the van", "column": "To do" }]
}
```

Save that as `moving.json` and run `laneboard import moving.json`.

## Versions

`version` says which format a file is in. Laneboard reads every older version, upgrades it in memory, and
writes the new format at the first save. Just opening or checking a file doesn't change it. At that first
save it keeps the original, byte for byte, as `boards.json.v1.bak` (with the number of the old format).

| Version | Change |
|---|---|
| none or 1 | The original format |
| 2 | Cards get `archived` |

A file from a newer Laneboard is refused with a message and not touched.

## Next to the file

| Name | Holds |
|---|---|
| `boards.json.bak` | The version before the last save |
| `boards.json.backups/` | Daily copies and copies made before a delete or restore. The newest 30 are kept |
| `boards.json.v1.bak` | The original file from before a format upgrade |
| `boards.json.lock` | An empty file used so two programs don't save at once. Safe to delete when nothing is running |
