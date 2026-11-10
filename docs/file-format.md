# The boards file

One JSON file in UTF-8. You can read it, edit it by hand, and keep it in version control.

```json
{
  "version": 3,
  "boards": [ { "id": "b-91b41531", "name": "Software release", "rev": 12, "...": "..." } ],
  "templates": []
}
```

`boards` are in the order the page and `laneboard boards` show them; moving a board on the boards overview
or with `laneboard board BOARD --move-to N` changes this order. `templates` holds the board templates you
saved. See [Saved templates](#saved-templates).

## A board

| Field | Meaning |
|---|---|
| `id` | Stays the same for the life of the board |
| `name`, `description` | Shown at the top of the page |
| `prefix` | Starts the keys of cards with no group, as in `T-12` |
| `groupLabel` | What a group is on this board: Repo, Client, Project |
| `rev` | Goes up by one with every change. Pages use it to notice changes |
| `created`, `updated` | ISO 8601 times |
| `archived` | `null`, or the ISO 8601 time the board was archived. An archived board keeps everything; the page's board picker and a search of every board leave it out. A board you import comes in use. Missing means `null` |
| `columns` | `[{id, name, limit, done}]`, left to right. `limit` is a number or `null`. `done` is `true` for a column whose cards count as done; when no column has it, the last column counts. Missing means `false` |
| `groups` | `[{id, name, prefix, color, link, details}]`. `details` is `[{label, value}]` |
| `labels` | `[{id, name, color}]`. Names differ, ignoring case |
| `properties` | `[{id, name, type, onCard, ...}]`, the values every card can hold. See below |
| `views` | `[{id, name, query, sort}]`, saved searches. `query` and `sort` are kept as typed, and either can be `""`. A view the board can't read is refused when it is saved |
| `settings` | `{cardFace, limitPolicy, defaultView}`. See below |
| `template` | Names of the sections a new card starts with |
| `checklists` | `[{id, name, items: [{id, text, heading}]}]` |
| `counters` | `{prefix: number}`, the last number used for each key prefix, so a deleted card's key never comes back |
| `cards` | The cards, in no particular order |

Colors are `blue`, `teal`, `violet`, `amber`, `rose`, `green` and `slate`. A group `link` must start
with `http://` or `https://`, or the file is refused.

### Settings

| Field | Meaning |
|---|---|
| `cardFace` | What each card shows on the board under its title, in order: `group`, `labels`, `field` (the first field), `progress` (ticks and tasks), `blocked`, and property ids. `[]` shows only the title |
| `limitPolicy` | `warn`: a column over its limit is flagged. `refuse`: a column at its limit takes no more cards |
| `defaultView` | The `id` of the saved view the board opens with, or `null` |

The default is `{"cardFace": ["group", "labels", "field", "progress", "blocked"], "limitPolicy": "warn",
"defaultView": null}`. With `refuse`, adding a card to a full column, moving one into it, bringing an
archived card back to it, or importing more cards than it holds is refused with a message naming the column
and its limit. Moving cards around inside the column, or out of it, is always fine. Archived cards don't
count.

### Properties

Each property has an `id` (made from its name, and kept when it is renamed), a `name`, a `type`, and
`onCard`, which is `true` when its id is in `settings.cardFace`. The type says what a card can hold:

| `type` | Options | A card holds | Typed as |
|---|---|---|---|
| `text` | none | one line of text | as is |
| `number` | `min`, `max` (a number or `null`), `decimals` (0 to 4), `unit` | a number, rounded to `decimals` | `3`, `2.5`, `3 h` |
| `choice` | `choices: [{id, name, color}]` | one choice id | the choice name |
| `multi` | `choices`, as above | a list of choice ids | names separated by commas: `iOS, Web` |
| `checkbox` | none | `true` | `yes` or `no` |
| `url` | none | a link starting with `http://` or `https://` | as is |
| `rating` | `max` (3 to 10, default 5) | a whole number from 1 to `max` | `4` or `4/5` |

Removing a property clears it on every card in the same save, and removing a choice clears that
choice wherever it was picked.

## A card

| Field | Meaning |
|---|---|
| `key` | `T-12` or `WEB-3`. Unique on the board, never reused |
| `title` | Required |
| `column` | The `id` of a column |
| `rank` | Position in the column, lowest first. Only the order matters |
| `group` | The `id` of a group, or `null` |
| `summary`, `blocked`, `notes` | Text. An empty `blocked` means it isn't blocked |
| `labels` | Label ids, in the board's label order |
| `properties` | `{property id: value}`. Only values that are set are kept |
| `tasks` | `[{id, text, done}]`, the card's own to-do list, in order |
| `fields` | `[{label, value}]`. The first shows on the card |
| `sections` | `[{label, body}]` |
| `checks` | The ids of the checklist items ticked on this card |
| `archived` | `null`, or the time it was archived |
| `created`, `updated` | ISO 8601 times |

Removing a label from the board takes it off every card in the same save.

## Saved templates

Each entry in `templates` is a board's setup, saved with `laneboard templates save` or
`POST /api/templates`. The built-in templates aren't in the file.

| Field | Meaning |
|---|---|
| `id` | Lowercase letters, digits and hyphens, made from the name. Not one of the built-in ids: `basic`, `software`, `personal`, `content`, `support`, `research` |
| `name` | Up to 120 characters. No two saved templates share a name, ignoring case |
| `description` | Up to 1,000 characters |
| `board` | The setup, written the way an import writes it: `prefix`, `groupLabel`, `columns`, `groups`, `labels`, `properties`, `views`, `checklists`, `template` and `settings`, by name rather than by id. Checklist lines keep their ids |
| `cards` | Starter cards: `title`, `column`, `group`, `summary`, `blocked`, `fields`, `sections`, `checks`, `labels`, `properties` and `tasks`, by name as in an import, or `[]`. A starter card's labels must be in `board.labels` |

A hand-written one can leave out most of that:

```json
{
  "id": "weekly-chores",
  "name": "Weekly chores",
  "description": "Jobs around the house, week by week.",
  "board": {
    "prefix": "C",
    "columns": [{ "name": "To do" }, { "name": "Doing", "limit": 2 }, { "name": "Done" }],
    "labels": [{ "name": "Outside", "color": "green" }],
    "properties": [{ "name": "Minutes", "type": "number" }],
    "views": [{ "name": "Quick jobs", "query": "minutes:<=15", "sort": "title" }]
  },
  "cards": [
    { "title": "Sweep the path", "column": "To do", "labels": ["Outside"], "properties": { "Minutes": "10" } },
    { "title": "Clean the windows", "column": "To do", "properties": { "Minutes": "45" } }
  ]
}
```

With that in `templates`, `laneboard new "" --template "weekly chores"` makes a board called Weekly
chores with two cards. A template's views are checked when a board is made from it: one the board can't
read stops the board being made, and the message names the view.

## Limits

Board name 120 characters, card title 200, summary 1,000, other text 20,000. 500 boards, 20,000 cards
per board, 20 columns, 50 groups, 30 fields or sections on a card, 10 checklists of 100 lines.

50 labels per board (names 40 characters) and 20 on a card. 30 properties per board (names 40
characters), 50 choices on a property, 20 picked at once, text and link values 500 characters, units
10. 30 saved views per board (names 60 characters, searches 500, sorts 200). 100 tasks on a card, 300
characters each. 100 saved templates.

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

An import can also say everything else by name rather than by id. Columns and labels can be just
their names, a group with no `prefix` uses the board's, and a label a card names that the board
doesn't have is added. Property values can be written the way you would type them. Tasks can be
just their text. In `settings`, the card face names properties and `defaultView` names a view:

```json
{
  "name": "Garden",
  "prefix": "G",
  "columns": ["To do", { "name": "Doing", "limit": 3 }, "Done"],
  "labels": ["Outside", { "name": "Shopping", "color": "amber" }],
  "properties": [
    { "name": "Hours", "type": "number", "decimals": 1, "unit": "h" },
    { "name": "Season", "type": "choice", "choices": ["Spring", "Autumn"] }
  ],
  "views": [{ "name": "Spring jobs", "query": "season:spring", "sort": "title" }],
  "settings": { "cardFace": ["labels", "Hours"], "limitPolicy": "refuse", "defaultView": "Spring jobs" },
  "cards": [
    {
      "title": "Plant the beans",
      "column": "Doing",
      "labels": ["Outside", "Seeds"],
      "properties": { "Hours": "1.5", "Season": "spring" },
      "tasks": ["Soak the seeds", { "text": "Dig the bed", "done": true }]
    }
  ]
}
```

An import is all or nothing. If anything in it can't be kept, nothing is added, and the message names the
board, the card and what was wrong. With `"Hours": "a while"` that is `Board "Garden", card number 1
("Plant the beans"): Hours must be a number.` A saved view the board can't read is refused the same way,
and the message names the view.

## Versions

`version` says which format a file is in. Laneboard reads every older version, upgrades it in memory, and
writes the new format at the first save. Just opening or checking a file doesn't change it. At that first
save it keeps the original, byte for byte, as `boards.json.v2.bak` (with the number of the old format).

| Version | Change |
|---|---|
| none or 1 | The original format |
| 2 | Cards get `archived` |
| 3 | Boards get `labels`, `properties`, `views`, `settings` and `archived`; columns get `done`; cards get `labels`, `properties` and `tasks`; the file gets `templates` |

A file from a newer Laneboard is refused with a message and not touched.

## Next to the file

| Name | Holds |
|---|---|
| `boards.json.bak` | The version before the last save |
| `boards.json.backups/` | Daily copies and copies made before a delete or restore. The newest 30 are kept |
| `boards.json.v2.bak` | The original file from before a format upgrade, named for its old format |
| `boards.json.lock` | An empty file used so two programs don't save at once. Safe to delete when nothing is running |
