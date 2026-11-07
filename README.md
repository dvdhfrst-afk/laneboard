# Laneboard

A kanban board that runs on your own computer. It is one small Python program with no
dependencies, a web page you open in your browser, and one JSON file that holds every board.
Nothing is sent anywhere.

- Several boards, each with its own columns, groups and card keys
- Cards with a summary, fields, sections, working notes and an optional "blocked by" line
- Checklists that every card ticks through on its own, with progress shown on each card
- A set of sections every new card starts with
- Drag cards between columns, or pick the column from the card
- Search and filter by group
- Column limits that flag a column when it holds too many cards
- Changes save as you make them; edits made in two tabs never silently overwrite each other

## Run it

You need Python 3.9 or newer.

```
python -m laneboard --open
```

That starts the board at http://127.0.0.1:8642 and opens it in your browser. Leave the window
running while you use the board; Ctrl+C stops it. Running the command again while the board is
already up just opens the page.

To install it as a command instead:

```
pip install .
laneboard --open
```

## Where your boards are kept

All boards live in one file, outside this folder:

| System  | File |
|---------|------|
| Windows | `%APPDATA%\laneboard\boards.json` |
| macOS and Linux | `$XDG_DATA_HOME/laneboard/boards.json`, or `~/.local/share/laneboard/boards.json` |

Set `LANEBOARD_DATA` or pass `--data FILE` to use a different file. Every save writes the file
in one step and keeps the previous version next to it as `boards.json.bak`. You can edit the
file by hand while the board is running; the page picks up the change when you come back to it.

## Moving boards in and out

```
python -m laneboard boards                      # list boards
python -m laneboard export "Garden project"     # print a board as JSON (by name or id)
python -m laneboard import board.json           # add a board from a JSON file
```

An exported board can be imported into another file or another computer. Card keys are kept.
To try the board with example data:

```
python -m laneboard import sample/sample-board.json
```

## Using the board

- **New card** (or press `n`) adds a card. The **+** at the top of a column adds one there.
- Click a card to open it. **Edit** changes everything except notes; notes save as you type.
- In a section, a line wrapped in `backticks` shows as code, and a section with several lines
  shows as a list. Web addresses become links.
- **Board settings** sets the board name, columns (with optional limits), and groups. A group
  could be a repo, a client or a project; its key prefix starts the keys of its cards (`WEB-1`),
  and its details show in the table under the board.
- Also in **Board settings**: **New card sections** lists the sections a new card starts with
  (any left empty aren't saved), and **Checklists** holds lists every card ticks through on its
  own. One line per item; a line that starts with `#` is a heading. Ticks save as you click.
- Press `/` to search. Click a group chip to show only that group's cards.

## Safety

The server only listens on `127.0.0.1`, only answers requests addressed to this computer (`127.0.0.1` or `localhost`), and
refuses changes that come from other web pages. The page loads nothing from other sites.

## Development

```
python -m unittest
```

The code is in `laneboard/`: `store.py` (the data file and its rules), `server.py` (HTTP and the
JSON API), `__main__.py` (the command line), and `static/` (the page).

### API

All bodies are JSON. Changes can send the board's `rev` in an `If-Match` header; if the board has
changed since, the server answers `412` and changes nothing.

| Method and path | Does |
|---|---|
| `GET /api/boards` | List boards |
| `POST /api/boards` | Create a board: `{name, description}` |
| `GET /api/boards/<id>` | Get a board with its cards |
| `PATCH /api/boards/<id>` | Change `name`, `description`, `prefix`, `groupLabel`, `columns`, `groups` |
| `DELETE /api/boards/<id>` | Delete a board |
| `POST /api/boards/<id>/cards` | Add a card; the answer includes its `key` |
| `PATCH /api/boards/<id>/cards/<key>` | Change a card |
| `DELETE /api/boards/<id>/cards/<key>` | Delete a card |

## License

MIT. See [LICENSE](LICENSE).
