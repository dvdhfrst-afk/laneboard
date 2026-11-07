# Changelog

## 1.0.0

The first version meant for other people to use.

### Added

- `laneboard` command with `boards`, `cards`, `add`, `move`, `archive`, `import`, `export`, `sample`,
  `backups`, `restore` and `check`, with `--help` on each and documented exit codes.
- Archive and restore for cards. Archived cards stay out of columns, counts and limits.
- Duplicate a card or a board. Import and export from the page, including every board at once.
- Daily and before-delete backups, listed by `backups` and brought back by `restore`.
- Example boards, offered on an empty board or added with `sample`.
- Keyboard use: arrows between cards, Shift+arrows to move them, a shortcuts page on `?`.
- Move-up and move-down buttons on a card, for touch screens and keyboards.
- Removing a column that holds cards offers to move them to another column.
- Collapsed columns, a blocked-cards filter, and a limit of 300 cards drawn per column with a button
  for more.
- The page follows changes from other tabs, commands and hand edits every few seconds. It shows a
  bar when the program can't be reached, when the file is broken, or when the page and program
  versions differ, and clears it by itself.

### Changed

- File format 2 adds `archived` to cards. Older files load as they are, and the first save upgrades
  them and keeps the old file as `boards.json.v1.bak`.
- A new file started by `import` or `sample` holds only what was added, with no empty board first.
  Looking with `boards` or `export` no longer creates a file.
- Example boards moved into the program, so `sample` works from any folder after installing.

### Fixed

- A board exported after renaming or adding columns or groups failed to import.
- Notes, ticks and retried edits could silently overwrite a change from another tab. Notes and edits
  now stop and ask, and ticks from two tabs both stay.
- A queued change could reach the wrong board if you switched boards before it ran.
- Hand edits to the file didn't reach open pages, though the docs said they did.
- Numbers like `NaN`, `Infinity` or very large ranks in a file could stop the page from drawing.
- Moving a card back and forth many times between two others could run out of room between their
  positions. Ranks are renumbered when that gets close.
- On Windows, a second copy could bind the same port, and a registry setting could make the page's
  script be served with the wrong type and refused.
- Failing to save could drop the connection and print a traceback. It now gives a plain message and
  keeps your text on the page.
- Saving replaced a symlinked file with a plain one and lost the file's permissions.
- Text with unpaired surrogates in a hand-edited file blocked every later save.
- Files saved as UTF-16 (which PowerShell's `>` does) couldn't be imported.
- A malformed address in the browser stopped the page from starting.
- Export crashed on consoles that can't show all the characters in a board.
- Moving cards quickly when saves were slow could put them back where they had been.
- On Windows, a brief lock on the file by another program during the backup copy failed the save. It now waits about
  2 seconds first.
- Line breaks in a board name could end up in a response header on export.
- Error responses could reset the connection on Windows when the request body hadn't been read.
- `--port` out of range printed a traceback, `--port 0` printed `:0`, and `--data` only worked before
  the command name.
- Long unbroken text could push the page sideways. Focus was lost after closing a card or when the
  board redrew. Screen readers were read the whole board on every change.

## 0.1.0

The first working version, used by its author and not published.
