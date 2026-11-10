# Worked examples

Every command here is run by the test suite (`tests/test_docs.py`), on a temporary file. To try them
without touching your own boards, point Laneboard at a scratch file first:

```text
export LANEBOARD_DATA=/tmp/try.json            (macOS and Linux)
$env:LANEBOARD_DATA = "$env:TEMP\try.json"     (Windows PowerShell)
```

If `laneboard` isn't on your PATH, write `python -m laneboard` instead.

## Look around the examples

```bash
laneboard sample
```

```text
garden               Garden project (5 cards). A made-up board that shows what a card can hold: fields, sections, code, links and a blocked card.
software-release     Software release (6 cards). A made-up release board: groups per component, a checklist every card ticks through, and a set of sections each new card starts with.
```

Add one and list it:

```bash
laneboard sample software-release
```

```bash
laneboard cards "Software release"
```

```text
API-2        To do          Add an index for the audit query  [blocked]
REL-1        To do          Write the upgrade guide
WEB-1        Doing          Show a clear message when the session ends
API-1        In review      Rate limit the search endpoint
WEB-2        Shipped        Fix the date picker on narrow screens
```

One more card, REL-2, is archived, so it isn't listed. See it with `--archived`.

## Keep a list from the command line

Add a card, move it along, and archive it when you're done:

```bash
laneboard add "Software release" "Write the upgrade notes" --column "To do" --summary "What changes for people upgrading."
```

```text
REL-3
```

```bash
laneboard move "Software release" REL-3 Doing
```

```bash
laneboard archive "Software release" REL-3
```

```bash
laneboard archive "Software release" REL-3 --restore
```

An open page shows each of these within a few seconds.

## Labels, properties and tasks

A board can have labels, and properties that each card fills in, like points or a priority:

```bash
laneboard labels "Software release" --add Docs --color amber
```

```bash
laneboard properties "Software release" --add Points --type number --min 0
```

```bash
laneboard properties "Software release" --add Priority --type choice --choices "Low, Medium, High"
```

```bash
laneboard properties "Software release"
```

```text
Points                   number, min 0  (0 cards with a value)
Priority                 choice, choices Low, Medium, High  (0 cards with a value)
```

Give a card a label and its values, written the way you would say them:

```bash
laneboard edit "Software release" REL-3 --label Docs --set Points=3 --set Priority=high
```

A card can also keep its own short to-do list. Tasks are numbered from 1:

```bash
laneboard task "Software release" REL-3 --add "List what changed" --add "Ask for a review"
```

```bash
laneboard task "Software release" REL-3 --done 1
```

```text
1. [x] List what changed
2. [ ] Ask for a review
```

`show` prints everything on the card, then its checklist, sections and notes:

```bash
laneboard show "Software release" REL-3
```

```text
REL-3  Write the upgrade notes
Column: Doing
Labels: Docs
Summary: What changes for people upgrading.
Points: 3
Priority: High
Tasks (1 of 2 done):
  1. [x] List what changed
  2. [ ] Ask for a review
```

Save a search as a view, and choose what the cards show on the board:

```bash
laneboard views "Software release" --add "Docs work" --query "label:docs" --sort=-points
```

```bash
laneboard settings "Software release" --card-face "labels, Points, progress"
```

## Move a card to another board

```bash
laneboard new "Next release"
```

```bash
laneboard labels "Next release" --add docs
```

```bash
laneboard transfer "Software release" REL-3 "Next release" --column Ready
```

```text
Moved REL-3 to Next release as T-1, in Ready.
Not carried over: Points: 3, Priority: High.
```

The card gets a key from the board it moves to. Its label comes with it because the other board has
one with the same name; the other board has no Points or Priority, so those stay behind, and the
message says so. Its notes, sections and tasks always come with it.

## Find cards

`search` looks through every board and lists what it finds, board by board. Words match anywhere on a
card, `OR` takes either word, and the README's [Search](../README.md#search) section has the rest:

```bash
laneboard search "upgrade OR session"
```

```text
Software release (2 of 5 cards)
  REL-1        To do          Write the upgrade guide
  WEB-1        Doing          Show a clear message when the session ends
Next release (1 of 1 card)
  T-1          Ready          Write the upgrade notes  [docs]
```

`cards` takes the same searches for one board, with a sort. This lists the web cards and the blocked
ones, by title within each column:

```bash
laneboard cards "Software release" --query "group:web OR is:blocked" --sort title
```

```text
API-2        To do          Add an index for the audit query  [blocked]
WEB-1        Doing          Show a clear message when the session ends
WEB-2        Shipped        Fix the date picker on narrow screens
```

A search that names something the boards don't have says so, lists what they do have, and points at
the spot. `laneboard search "label:dcos"` prints this and exits with `1`:

```text
laneboard: There's no label called "dcos". Labels: Docs.
  label:dcos
        ^
```

`cards --view "Docs work"` runs the view saved earlier. A view is checked when it is saved, so a typo
like that one is refused then, not found later.

## Start a board from a template

A template starts a board with its columns, labels, properties and saved views already set up:

```bash
laneboard templates
```

```text
basic                Basic (5 columns, 4 starter cards). Five plain columns from Backlog to Done, with a limit of one card in progress.
software             Software team (5 columns, 5 starter cards). A product board with bugs, features and chores, story points, a priority and a component.
personal             Personal (3 columns, 4 starter cards). A light board for home, errands and health: To do, Doing, Done.
content              Content pipeline (5 columns, 5 starter cards). Posts, newsletters and videos from first idea to published, with channels and word counts.
support              Customer support (4 columns, 5 starter cards). Requests from new to resolved, with a severity and a customer tier. Triaged holds five at a time and refuses a sixth.
research             Research notes (4 columns, 4 starter cards). Questions, reading and notes through to a written summary, with a source link and a confidence rating.
```

```bash
laneboard new "Our team" --template software
```

The starter cards show what the board is for. Change them or delete them.

A board you have set up can become a template of your own. This keeps the release board's columns,
groups, checklist, labels, properties, saved view and settings, without its cards (add `--cards` to keep
them too, without their notes):

```bash
laneboard templates save "Software release" "Release board" --description "Components, a release checklist and new card sections."
```

```text
Saved Release board as the template release-board (4 columns, no starter cards).
```

```bash
laneboard new "Next quarter" --template "Release board"
```

```bash
laneboard views "Next quarter"
```

```text
Docs work                label:docs  sorted by -points
```

Saving or deleting a template never changes a board. `laneboard templates delete "Release board" --yes`
deletes it, keeping a backup of the file first.

## Use it from a script

`--json` gives you the data, and the exit code says whether it worked:

```bash
laneboard cards "Software release" --column Doing --json
```

So, in a shell, a nightly job can add a card when a check fails, using the key it prints:

```text
key=$(laneboard add "Software release" "Nightly check failed" --column "To do") && echo "Added $key"
```

## Move a board to another computer

```bash
laneboard export "Software release" -o release.json
```

```bash
laneboard import release.json
```

Card keys, notes, ticks and archived cards all come across. Importing always adds a new board and never
changes one you have. If anything in the file is wrong, nothing is added, and the message says what:

```text
laneboard: Board "Software release": Group name is too long (73 characters; the limit is 60).
```

## Write a board by hand

Only a board's `name` and each card's `title` are needed. This goes in a file called `moving.json`:

```json
{
  "name": "Moving house",
  "columns": [{ "name": "To do" }, { "name": "Done" }],
  "cards": [
    { "title": "Book the van", "column": "To do" },
    { "title": "Tell the bank", "summary": "They need the new address by the 1st." }
  ]
}
```

```bash
laneboard import moving.json
```

[file-format.md](file-format.md) has everything a card and board can hold.

## Go back to an earlier copy

```bash
laneboard backups
```

```text
Backups of /tmp/try.json, newest first:
  previous                                   2026-11-07 10:53      5 KB  1 board, 6 cards
  boards-2026-11-07.json                     2026-11-07 10:53      5 KB  1 board, 6 cards
Go back to one with: laneboard restore NAME
```

```bash
laneboard restore previous
```

The copy it replaces is kept as another backup, so you can change your mind.

```bash
laneboard check
```

## Use a different file for each thing

```bash
laneboard boards --data work.json
```

A file that doesn't exist yet is created the first time something is saved to it, along with its folder.
