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
| `PATCH /api/boards/<id>` | Change `name`, `description`, `prefix`, `groupLabel`, `template`, `columns`, `groups`, `checklists` | `{board}` |
| `DELETE /api/boards/<id>` | Delete a board, keeping a backup first | `{deleted}` |
| `GET /api/boards/<id>/export` | Download the board | The board |
| `POST /api/boards/<id>/cards` | Add a card | `201 {board, key}` |
| `PATCH /api/boards/<id>/cards/<key>` | Change a card. Only the fields you send change | `{board}` |
| `DELETE /api/boards/<id>/cards/<key>` | Delete a card | `{board}` |
| `GET /api/export` | Download the whole file | The file |
| `POST /api/import` | Add one board, or every board in a whole file. All or nothing | `201 {boards: [{id, name, cards}]}` |
| `GET /api/samples` | List the example boards | `{samples: [{name, title, description, cards}]}` |
| `POST /api/samples/<name>` | Add an example board | `201 {board}` |

`PATCH` on a board may also send `moveCards`, `{removedColumnId: targetColumnId}`, along with `columns`, to
move cards out of columns being removed. Without it, removing a column that holds cards is refused.

A card takes `title`, `column`, `rank`, `group`, `summary`, `blocked`, `notes`, `fields`, `sections`,
`checks` and `archived` (`true` or `false`). See [file-format.md](file-format.md) for what they hold.

## Errors

Errors are `{"error": "A sentence saying what's wrong."}`.

| Status | Means |
|---|---|
| `400` | The request or a value isn't valid |
| `403` | Refused: it came from another site, or wasn't addressed to this computer |
| `404` | That board or card doesn't exist |
| `409` | It conflicts with the rules, like removing a column that holds cards, or going over a limit |
| `412` | The board changed since the `rev` you sent |
| `413` | The body is too large: 4 MB, or 64 MB for an import |
| `415` | The body wasn't sent as JSON |
| `503` | The boards file can't be read or saved right now. The message says why |

## Example

```bash
curl -s http://127.0.0.1:8642/api/boards
```
