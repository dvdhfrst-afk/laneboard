# The JSON API

This is what the page uses, and scripts can use it too. For most scripts the command line is simpler.

Requests must come from the same computer and be addressed to `127.0.0.1` or `localhost`. Bodies are JSON
with `Content-Type: application/json`. Cross-site requests are refused (see [SECURITY.md](../SECURITY.md)).

## Revisions

Each board has a `rev` that goes up with every change. A change can send the `rev` it started from in an
`If-Match` header. If the board has moved on, the server answers `412` and changes nothing. Without the
header the change is applied regardless, so a script that wants to be careful should send it.

## Routes

| Method and path | Does | Answers |
|---|---|---|
| `GET /api/health` | Version and where the data is. Works when the file is broken | `{app, version, schema, data, backups}` |
| `GET /api/boards` | List boards | `{boards: [{id, name, rev, cards, archived}]}` |
| `POST /api/boards` | Create a board: `{name, description}`, or `{name, description, template}` to start from a template | `201 {board}` |
| `GET /api/boards/<id>` | A board with its cards | `{board}` |
| `PATCH /api/boards/<id>` | Change `name`, `description`, `prefix`, `groupLabel`, `template`, `columns`, `groups`, `checklists`, `labels`, `properties`, `views`, `settings` | `{board}` |
| `DELETE /api/boards/<id>` | Delete a board, keeping a backup first | `{deleted}` |
| `GET /api/boards/<id>/export` | Download the board | The board |
| `GET /api/boards/<id>/search?q=&sort=&archived=0` | Search one board | `{keys, total, matched}` |
| `POST /api/boards/<id>/cards` | Add a card | `201 {board, key}` |
| `PATCH /api/boards/<id>/cards/<key>` | Change a card. Only the fields you send change | `{board}` |
| `DELETE /api/boards/<id>/cards/<key>` | Delete a card | `{board}` |
| `POST /api/boards/<id>/cards/<key>/tasks` | Add a task to the end of the card's list: `{text, done}` | `201 {board, task}`, where `task` is the new task's id |
| `PATCH /api/boards/<id>/cards/<key>/tasks/<task id>` | Change a task's `text` or `done` | `{board}` |
| `DELETE /api/boards/<id>/cards/<key>/tasks/<task id>` | Remove a task | `{board}` |
| `POST /api/boards/<id>/cards/<key>/transfer` | Move a card to another board: `{board, column, targetRev}` | `{board, target, key, dropped}` |
| `GET /api/search?q=&sort=&archived=0` | Search every board | `{results: [{board, name, keys, total}]}` |
| `GET /api/export` | Download the whole file | The file |
| `POST /api/import` | Add one board, or every board in a whole file. All or nothing | `201 {boards: [{id, name, cards}]}` |
| `GET /api/samples` | List the example boards | `{samples: [{name, title, description, cards}]}` |
| `POST /api/samples/<name>` | Add an example board | `201 {board}` |
| `GET /api/templates` | List the templates, built-in ones first | `{templates: [{id, name, description, builtIn, columns, cards}]}` |
| `POST /api/templates` | Save a board's setup as a template: `{board, name, description, cards}` | `201 {template}` |
| `PATCH /api/templates/<id>` | Change a saved template's `name` or `description` | `{template}` |
| `DELETE /api/templates/<id>` | Delete a saved template, keeping a backup first | `{deleted}` |

`PATCH` on a board may also send `moveCards`, `{removedColumnId: targetColumnId}`, along with `columns`, to
move cards out of columns being removed. Without it, removing a column that holds cards is refused.

`labels`, `properties` and `views` are sent as whole lists, like `columns`: an entry keeps its `id`, one
sent without an `id` keeps the id of the one with the same name, and a new one gets an id made from its
name. A label or view left without a `color`, `query` or `sort` keeps the one it had. Removing a label or
property takes it off every card in the same save, and removing the board's default view clears
`settings.defaultView`. A view's `query` and `sort` are checked when the view is added or either one
changes, and one the board can't read is refused with `400` and the place it stopped (see
[Errors](#errors)). A view that stops working later, say because a property it names was removed, is kept
as it is, and searching with its query answers `400` like any search the board can't read. `settings`
changes only the keys sent: `cardFace` (parts and properties by name or id), `limitPolicy` (`warn` or
`refuse`), and `defaultView` (a view's name or id, or `null`).

A card takes `title`, `column`, `rank`, `group`, `summary`, `blocked`, `notes`, `fields`, `sections`,
`checks`, `archived` (`true` or `false`), `labels`, `properties` and `tasks`. See
[file-format.md](file-format.md) for what they hold.

- `labels` is the whole list, by id or name. A name the board doesn't have is refused, and the message
  lists the board's labels.
- `properties` maps property ids to values, and only the ones sent change. A `null` or `""` value clears
  one, and `"properties": null` clears them all.
- `tasks` is the whole list. A task can be just its text; one sent with its `id` keeps it.

When the board's `limitPolicy` is `refuse`, adding a card to a full column or moving one into it answers
`409` and names the column and its limit.

## Search

`GET /api/boards/<id>/search` runs a search on one board and answers with the keys of the cards it found,
in the order the board shows them: column by column, and in each column by the sort. `q` is the search and
`sort` the sort, both as typed in the search box (see the Search section of the [README](../README.md#search)).
Archived cards are left out unless `archived=1` is sent or the search has `is:archived`. `total` is how many
cards were looked at, so a page can show `matched` of `total`, like "3 of 12". Both are empty by default,
which finds every card in board order.

`GET /api/search` runs the same search on every board. `results` has one entry per board with a match, in
the order of the boards; boards with no match are left out, and `total` is again the cards looked at on that
board. A board that lacks something the search names, like a property or a label, has no match. A sort by a
property a board doesn't have leaves that board's cards in board order. It is an error only when no board can
read the search, or none of the boards that read it can read the sort; the error is from the board that
read furthest.

Searches change nothing and need no `rev`.

## Templates

A template is a board's setup: columns, groups, labels, properties, saved views, checklists, new card
sections and settings, with starter cards or without. The built-in ones can't be changed or deleted; the
ones you save are kept in the boards file (see [file-format.md](file-format.md)).

`POST /api/boards` with `template`, a template's id or name, makes a board from it. Without a `name`, or
with an empty one, the board takes the template's name; without a `description` it takes the template's.

`POST /api/templates` saves the board whose id is `board` under `name`. `cards: true` keeps the board's
cards as starter cards, without their notes; archived cards are left out. The new template's id is made
from its name. Names must differ from every other template's, built-in ones included, and a file holds at
most 100 saved templates. A board with a saved view that no longer works can't be saved until the view is
changed or removed.

Saving, changing or deleting a template never changes a board or its `rev`. Changing or deleting a built-in
template answers `409`.

## Moving a card to another board

`POST /api/boards/<id>/cards/<key>/transfer` saves both boards in one step. `board` is the id of the board
to move it to. `column` is a column on that board, by name or id; without it the card goes to the column
with the same name as the one it is in, or else the first column. It goes to the bottom, and gets a new
key from the other board. Send the card's board `rev` in `If-Match`, and the other board's as `targetRev`;
if either has moved on the answer is `412`. Both revs go up.

The card's group, labels, property values and checklist ticks come with it where the other board has one
with the same name. `dropped` lists, as short text, each one that didn't, like `"Label: Docs"` or
`"Points: 3"`. Everything else on the card comes with it as it is.

## Errors

Errors are `{"error": "A sentence saying what's wrong."}`.

A search or saved view that can't be read answers `400` with two more keys: `position`, where in the
text it stopped (counting from 0), and `part`, which text that is: `query` or `sort`. A page can use them
to point at the spot:

```json
{"error": "There's no property called \"pionts\". Properties: Points.", "position": 4, "part": "query"}
```

| Status | Means |
|---|---|
| `400` | The request or a value isn't valid |
| `403` | Refused: it came from another site, or wasn't addressed to this computer |
| `404` | That board, card, task or template doesn't exist |
| `409` | It conflicts with the rules, like removing a column that holds cards, going over a limit, adding a card to a full column on a board that refuses them, or changing a built-in template |
| `412` | The board changed since the `rev` you sent |
| `413` | The body is too large: 4 MB, or 64 MB for an import |
| `415` | The body wasn't sent as JSON |
| `503` | The boards file can't be read or saved right now. The message says why |

## Example

```bash
curl -s http://127.0.0.1:8642/api/boards
```
