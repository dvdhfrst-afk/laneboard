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
| `POST /api/boards` | Create a board: `{name, description}` | `201 {board}` |
| `GET /api/boards/<id>` | A board with its cards | `{board}` |
| `PATCH /api/boards/<id>` | Change `name`, `description`, `prefix`, `groupLabel`, `template`, `columns`, `groups`, `checklists`, `labels`, `properties`, `views`, `settings` | `{board}` |
| `DELETE /api/boards/<id>` | Delete a board, keeping a backup first | `{deleted}` |
| `GET /api/boards/<id>/export` | Download the board | The board |
| `POST /api/boards/<id>/cards` | Add a card | `201 {board, key}` |
| `PATCH /api/boards/<id>/cards/<key>` | Change a card. Only the fields you send change | `{board}` |
| `DELETE /api/boards/<id>/cards/<key>` | Delete a card | `{board}` |
| `POST /api/boards/<id>/cards/<key>/tasks` | Add a task to the end of the card's list: `{text, done}` | `201 {board, task}`, where `task` is the new task's id |
| `PATCH /api/boards/<id>/cards/<key>/tasks/<task id>` | Change a task's `text` or `done` | `{board}` |
| `DELETE /api/boards/<id>/cards/<key>/tasks/<task id>` | Remove a task | `{board}` |
| `POST /api/boards/<id>/cards/<key>/transfer` | Move a card to another board: `{board, column, targetRev}` | `{board, target, key, dropped}` |
| `GET /api/export` | Download the whole file | The file |
| `POST /api/import` | Add one board, or every board in a whole file. All or nothing | `201 {boards: [{id, name, cards}]}` |
| `GET /api/samples` | List the example boards | `{samples: [{name, title, description, cards}]}` |
| `POST /api/samples/<name>` | Add an example board | `201 {board}` |

`PATCH` on a board may also send `moveCards`, `{removedColumnId: targetColumnId}`, along with `columns`, to
move cards out of columns being removed. Without it, removing a column that holds cards is refused.

`labels`, `properties` and `views` are sent as whole lists, like `columns`: an entry keeps its `id`, one
sent without an `id` keeps the id of the one with the same name, and a new one gets an id made from its
name. A label or view left without a `color`, `query` or `sort` keeps the one it had. Removing a label or
property takes it off every card in the same save, and removing the board's default view clears
`settings.defaultView`. `settings` changes only the keys sent: `cardFace` (parts and properties by name or
id), `limitPolicy` (`warn` or `refuse`), and `defaultView` (a view's name or id, or `null`).

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

| Status | Means |
|---|---|
| `400` | The request or a value isn't valid |
| `403` | Refused: it came from another site, or wasn't addressed to this computer |
| `404` | That board, card or task doesn't exist |
| `409` | It conflicts with the rules, like removing a column that holds cards, going over a limit, or adding a card to a full column on a board that refuses them |
| `412` | The board changed since the `rev` you sent |
| `413` | The body is too large: 4 MB, or 64 MB for an import |
| `415` | The body wasn't sent as JSON |
| `503` | The boards file can't be read or saved right now. The message says why |

## Example

```bash
curl -s http://127.0.0.1:8642/api/boards
```
