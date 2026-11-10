# Laneboard

A kanban board that runs on your own computer. It is one small Python program with no
dependencies, a web page you open in your browser, and one JSON file that holds every board.
Nothing is sent anywhere, and there is no account to make.

It is built for one person on one computer. It has no sign-in and no sync, and it isn't made to be
shared over a network. If several people need the same board, use something else.

- Several boards, each with its own columns, groups and card keys
- Cards with a summary, fields, sections, working notes and a "blocked by" line
- Checklists that every card ticks through on its own, with progress shown on each card
- Drag cards between columns, or move them with the keyboard or the buttons on a card
- Search, filter by group or blocked cards, collapse columns, flag a column that holds too many cards
- Archive cards to get them out of the way without losing them; duplicate cards and boards
- Import and export from the page or the command line, and a command line for scripts
- Changes save as you make them. Edits made in two tabs don't silently overwrite each other
- A dated backup every day you make changes, and a `restore` command to go back to one

## Install and run

You need Python 3.9 or newer. Nothing else gets installed. On macOS and Linux the command may be
`python3` rather than `python`. From a copy of this repository:

```bash
python -m laneboard --open
```

That starts the board and opens it in your browser. It prints one line and keeps running:

```text
Laneboard 1.0.0 at http://127.0.0.1:8642/  (data: /your/home/.local/share/laneboard/boards.json)  Ctrl+C stops it.
```

Leave it running while you use the board. Ctrl+C stops it, and nothing is lost when it stops,
since every change has already been saved. Running the command again while the board is up just
opens the page.

To have a `laneboard` command that works from any folder:

```bash
python -m pip install .
```

```bash
laneboard --open
```

If port 8642 is taken by something else, Laneboard says so. Pick another with `--port`:

```bash
laneboard --port 8700 --open
```

## Your first five minutes

The first run gives you an empty board called **My board**, with a choice of two example boards to
look around. You can also add one from the command line:

```bash
laneboard sample garden
```

To start a board of your own already set up, use a template. It brings columns, labels, properties,
saved views and a few example cards to change or delete. List the templates, then make a board from
one:

```bash
laneboard templates
```

```bash
laneboard new "Chores" --template personal
```

Once a board is set up the way you like, `laneboard templates save "Chores" "My chores"` keeps its
setup as a template of your own (add `--cards` to keep its cards too).

Press `n` to add a card. Click a card to open it, and type in **Notes**; they save as you type. Drag
the card to another column. **Board settings** changes the columns and adds groups. Press `?` at any
time to see the keyboard shortcuts and where your data is.

[docs/examples.md](docs/examples.md) has worked examples, including using the board from a script.

## Using the board

- **New card** (or `n`) adds a card. The **+** at the top of a column adds one there.
- Click a card to open it. **Edit** changes everything except notes and ticks, which save as you go.
- **Archive** takes a card off the board. Find it again under **More**, **Archived cards**.
- In any card text, wrap code in `backticks` to get a Copy button. Web addresses become links. A
  section with several lines shows as a list.
- A **group** can be a repo, a client or a project. Its key prefix starts the keys of its cards
  (`WEB-1`), and its details show in a table under the board.
- **New card sections** in Board settings lists the sections a new card starts with. **Checklists**
  are lists every card ticks through on its own: one line per item, and a line starting with `#` is
  a heading. Lines you don't change keep their ticks when you edit a checklist.
- Removing a column that holds cards asks which column they should move to.
- Collapsing a column is remembered in that browser. It isn't saved in your boards file.

Columns show the first 300 cards with a button for more, so a very long column stays quick.

### Keyboard

| Key | Does |
|---|---|
| `n` | New card |
| `/` | Search |
| `?` | Shortcuts and where your data is |
| Arrow keys | Go from card to card (Tab reaches the board, then arrows take over) |
| Enter | Open the card |
| Shift + Up or Down | Move the card within its column |
| Shift + Left or Right | Move the card to the next column |
| Esc | Close what's open, or clear the search |

Dragging doesn't work on touch screens. Open the card and use its column list and arrow buttons.

## Search

Type words to find the cards that have all of them, anywhere on the card: its key, title, summary,
"blocked by" line, notes, fields, sections, tasks, group, labels and property values. Capitals and
accents don't matter. Archived cards are left out unless you ask for them with `is:archived`.

| You type | It finds |
|---|---|
| `login page` | Cards with both words |
| `"release notes"` | The words together, as typed |
| `bug OR crash` | Cards with either word |
| `-wontfix` or `NOT wontfix` | Cards without the word |
| `(bug OR crash) column:review` | Parentheses group words; this finds either word in the Review column |
| `column:review` or `col:review`, `group:web`, `label:bug` | Cards in that column, group or label, by its name or the start of it |
| `key:WEB-3`, `title:login`, `notes:token` | Words in just that part of a card |
| `is:blocked`, `is:archived`, `is:over-limit`, `is:done` | Blocked cards, archived cards, cards in a column over its limit, cards in the last column |
| `has:notes`, `has:summary`, `has:labels`, `has:tasks`, `has:points` | Cards with something there, or with a value for that property |
| `tasks:open`, `tasks:done`, `progress:>=50` | Cards with tasks left to do, with every task done, or at least half ticked off |
| `points:3`, `points:>=3`, `points:2..5` | A number or rating property compared with a value, or between two |
| `priority:high`, `platform:ios`, `reviewed:yes`, `link:example.com` | A choice, one of several choices, a checkbox, or words in a link |

A property is named the way the board names it; capitals, spaces and hyphens don't matter, so
`story-points:3` finds a property called Story points. A search that names something the board
doesn't have says so and lists what it does have.

To change the order of the cards in each column, add a sort: `sort:title`, `sort:-points` (a minus
sorts the other way), or several, like `sort:-priority,title`. You can sort by `title`, `key`,
`created`, `updated`, `group`, `progress`, `rank` (the order on the board, which is the default) or
any property. Cards with no value go last either way.

The command line takes the same searches, and a saved view keeps one under a name:

```bash
laneboard search "upgrade OR session"
```

```bash
laneboard cards "Software release" --query "group:web OR is:blocked" --sort title
```

## Import and export

`laneboard export BOARD --format F` writes a board in one of these formats, and `laneboard import FILE`
reads the ones that go the other way. An import tells the format from the file, or takes `--format`.
It adds everything or nothing, never changes a board you have, and says what it left out.
`--preview` shows what an import would add without adding it.

| Format | `--format` | Export | Import | What it keeps | What it leaves out |
|---|---|---|---|---|---|
| Laneboard JSON | `laneboard` | yes | yes | Everything on the board | Nothing |
| Markdown | `markdown` | yes | yes | A document to read, with every column and card, and a block of board settings at the end. It imports back as the same board: columns, groups, labels, properties, saved views, settings, checklists, and each card's key, column, labels, values, summary, fields, sections, tasks, ticks and notes. A plain Markdown task list imports too: headings become columns, list items cards, and ticked items go to the last column | The times cards were created and last changed; the board copy gets new ones |
| Web page | `html` | yes | no | A page to read or print: the board's numbers, every column and card, archived cards and groups. It runs no script and loads nothing | Saved views and settings. Archived cards show only their key, title and column |
| Plain text | `text` | yes | no | Each column with its count, then one line per card with its key, title and labels, for pasting into a message | Everything else on a card, and archived cards |
| Trello | `trello` | no | yes | From Trello's Export as JSON: open lists as columns, cards with their name, description (as notes), labels (in the nearest color), checklists (as tasks) and archived state, and custom fields of the text, number, checkbox and list kinds | Archived lists (their cards come in archived), due dates, members, attachments, votes, stickers, comments, and other kinds of custom field. Trello due dates are not imported |
| GitHub issues | `github` | no | yes | The JSON from `gh issue list --json number,title,body,labels,state,url,milestone,assignees`: open issues in the first column and closed ones in the last, the issue number as the key (`GH-12`), labels, the body as notes, the address as a Link field, and the milestone and assignees as properties | Anything else the issue has |

```bash
laneboard export "Software release" --format markdown -o release.md
```

```bash
laneboard import release.md --preview
```

`laneboard stats BOARD` prints a board's numbers: cards and limits per column with number totals, cards
per group and label, how each property is filled in, and tasks and checklist ticks done.

## Where your boards are kept

All boards live in one file, outside this folder:

| System | File |
|---|---|
| Windows | `%APPDATA%\laneboard\boards.json` |
| macOS and Linux | `$XDG_DATA_HOME/laneboard/boards.json`, or `~/.local/share/laneboard/boards.json` |

Set `LANEBOARD_DATA` or pass `--data FILE` to use a different file. `--data` works before or after
the command name.

Saving writes a new file and swaps it into place in one step, so a crash or power cut leaves either
the old version or the new one, not half of each. You can edit the file by hand while the board is
running. The page notices within a few seconds, and if your edit leaves the file broken Laneboard
tells you and changes nothing until it's fixed. The format is described in
[docs/file-format.md](docs/file-format.md).

### Backups

Next to your file:

- `boards.json.bak` is the version before the last save.
- `boards.json.backups/` holds one copy per day you made changes, and a copy taken before a board
  is deleted or a backup is restored. The newest 30 are kept.
- `boards.json.v2.bak` is your file as it was before Laneboard upgraded its format, if that happened
  (the number is the old format's).

To go back:

```bash
laneboard backups
```

```bash
laneboard restore previous
```

`restore` first keeps what it replaces, so you can change your mind. It works while
the board is running, and it works when the current file is broken. Copies of your file on another
disk are still your job; these backups sit in the same folder.

## Command line

Every command takes `--help`. A board can be named by its name or id.

| Command | Does |
|---|---|
| `laneboard` or `laneboard serve` | Run the board. `--open`, `--port PORT`, `--verbose` |
| `laneboard boards` | List boards. `--json` |
| `laneboard cards BOARD` | List cards. `--column NAME`, `--archived`, `--query Q` or `--view NAME`, `--sort S`, `--json` |
| `laneboard search QUERY` | Find cards on every board, board by board. `--board B`, `--sort S`, `--json` |
| `laneboard add BOARD TITLE` | Add a card and print its key. `--column`, `--group`, `--summary` |
| `laneboard move BOARD KEY COLUMN` | Move a card to the bottom of a column |
| `laneboard archive BOARD KEY` | Archive a card. `--restore` brings it back |
| `laneboard show BOARD KEY` | Everything on one card. `--json` |
| `laneboard edit BOARD KEY` | Change a card. `--title`, `--summary`, `--group`, `--column`, `--label`, `--unlabel`, `--set PROPERTY=VALUE`, `--clear PROPERTY`, `--blocked`, `--notes` or `--notes-file` |
| `laneboard delete BOARD KEY --yes` | Delete a card for good |
| `laneboard task BOARD KEY` | List a card's tasks. `--add TEXT`, `--done N`, `--reopen N`, `--remove N`, counting from 1 |
| `laneboard transfer BOARD KEY TARGET_BOARD` | Move a card to another board. `--column` |
| `laneboard new NAME` | Make a board. `--description`, `--template T` to start from a template |
| `laneboard templates` | List the templates. `--json` |
| `laneboard templates save BOARD NAME` | Keep a board's setup as a template. `--cards` keeps its cards too (without notes), `--description` |
| `laneboard templates delete NAME --yes` | Delete a template you saved |
| `laneboard labels BOARD` | List labels. `--add NAME` (with `--color`), `--rename OLD=NEW`, `--remove NAME` |
| `laneboard properties BOARD` | List properties. `--add NAME --type TYPE` (with `--choices`, `--min`, `--max`, `--decimals`, `--unit`), `--rename OLD=NEW`, `--remove NAME` |
| `laneboard views BOARD` | List saved views. `--add NAME --query Q` (with `--sort`), `--remove NAME`, `--default NAME` or `none` |
| `laneboard settings BOARD` | Show a board's settings. `--name`, `--description`, `--prefix`, `--limit-policy warn` or `refuse`, `--card-face A,B,C`, `--default VIEW` or `none` |
| `laneboard import FILE` | Add boards from a file, or `-` for standard input. All or nothing. `--format F` (default `auto`), `--preview` |
| `laneboard export BOARD` | Write a board. `--format F` (default `laneboard`), `-o FILE`, `--force`, or `--all` for every board as Laneboard JSON |
| `laneboard stats BOARD` | A board's numbers: columns, groups, labels, properties, tasks and ticks. `--json` |
| `laneboard sample [NAME]` | List the example boards, or add one |
| `laneboard backups` | List the copies you can go back to |
| `laneboard restore NAME` | Go back to one |
| `laneboard check` | Check that the file can be read, without changing it. Saved views that no longer work are listed as warnings |

`--label`, `--unlabel`, `--set`, `--clear` and the `task` options can be given more than once. A value
that starts with `-`, like a sort, goes after an `=`: `--sort=-points`.

Commands can run while the board is running. The page picks up what they do.

Exit codes: `0` it worked, `1` it failed with a message saying why, `2` the command itself was
wrong, `130` you pressed Ctrl+C during a command. Stopping the board itself with Ctrl+C is normal and
exits with `0`.

## When something goes wrong

| What you see | What it means |
|---|---|
| A red bar saying Laneboard can't be reached | The program stopped. Start it again; the page reconnects by itself and saves what you typed. |
| A red bar saying the boards file has a problem | The file was damaged or edited badly. It names the place. Nothing is overwritten. Fix it by hand or use `laneboard restore`. |
| A bar about two version numbers | The page and the running program differ. Stop Laneboard, start it again, then reload the page. |
| "These notes also changed somewhere else" | Another tab or a command changed them. Your text is still in the box; choose which to keep. |
| "Can't save ... Your change was not saved" | The file or folder can't be written (read-only, disk full, held by another program). Your text stays on the page. |
| "That file was written by a newer Laneboard" | Update Laneboard. Your file is left as it was. |

## Safety

Laneboard has no sign-in, so it relies on staying private to this computer:

- It listens only on `127.0.0.1`, and there is no option to change that.
- It answers only requests addressed to this computer, which stops DNS rebinding (a web page tricking
  your browser into treating a site's address as your machine).
- It refuses changes that come from other web pages.
- The page loads nothing from other sites, and a strict content security policy stops it running any
  script it didn't bring. Card text is always shown as text, even from an imported file.

Other programs running on your computer as you can still reach it, the same way they can read your
files. The file isn't encrypted. To reach a board from another computer you control, use an SSH
tunnel rather than exposing the port:

```bash
ssh -L 8642:127.0.0.1:8642 you@that-computer
```

Details and how to report a problem are in [SECURITY.md](SECURITY.md).

## What has been tested

The short version: the test suite and real-browser sessions ran on Windows 11, the suite also ran on
Linux, and **macOS, Firefox and Safari have not been tried**. Nobody but the authors has used this
yet. [docs/testing.md](docs/testing.md) has the numbers and the full list, including how big a board
was measured and how quick it was.

## Development

```bash
python -m unittest
```

No install is needed. [CONTRIBUTING.md](CONTRIBUTING.md) covers the code layout and the optional
browser tests, and [docs/api.md](docs/api.md) the JSON API the page uses. Changes are listed in
[CHANGELOG.md](CHANGELOG.md).

## License

MIT. See [LICENSE](LICENSE).
