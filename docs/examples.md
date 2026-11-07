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
laneboard: Group name is too long (73 characters; the limit is 60).
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
