# Changelog

## 1.1.0

Boards can now hold much more than a column of titles, and you can find, sort and move what they hold.

### Added

- Labels on a board, with a colour each, and any number of them on a card.
- Card properties defined per board: text, number, choice, several choices, checkbox, web address
  and rating, each with its own checks. Pick which ones show on the card face.
- Tasks on a card: a short list of steps you tick, separate from the board's checklists.
- A search language: words and quoted phrases, `OR`, `-word`, brackets, and qualifiers such as
  `column:`, `group:`, `label:`, `is:blocked`, `is:done`, `has:tasks`, `progress:>50` and any
  property by name (`points:>=3`, `priority:high`). Sorting by title, key, created, updated or any
  property. Mistakes get a message that points at the part that is wrong.
- Saved views: a named search and sort, kept with the board, with one chosen to open by default.
- Search every board at once, from the page, the API and `laneboard search`.
- Templates: six built in, plus your own saved from any board. A new board can start from one.
- Import from Markdown, a Trello board export and a GitHub issue list, with a preview of what would
  be added and a warning for anything left out. Export as Markdown, a web page and plain text as
  well as Laneboard JSON.
- A summary of each board: cards per column, group, label and choice, number totals and averages,
  blocked and archived cards, tasks and checklist ticks. Pick a count to search for those cards.
  `laneboard stats` prints the same numbers.
- Move a card to another board, keeping its labels and properties where the other board has them,
  and saying what it can't keep.
- Done columns: mark the columns whose cards count as done. Search, the summary and every export
  follow them.
- All boards: an overview of every board with its counts, where you open, rename, duplicate,
  export, archive, restore, delete and reorder boards. Archived boards stay out of the way until
  restored.
- A Backups sheet in the page that lists and restores the copies Laneboard keeps.
- A command palette on Ctrl+K (Cmd+K on a Mac) for actions, boards and cards.
- Board settings for card face, column limits (warn or refuse) and the view a board opens with.
- Command line: `board`, `search`, `show`, `edit`, `delete`, `task`, `transfer`, `new`,
  `templates`, `labels`, `properties`, `views`, `settings` and `stats`, and `--format` on `import`
  and `export`.

### Changed

- File format 3. Older files load as they are, and the first save upgrades them and keeps the old
  file as `boards.json.v2.bak`. A file from 1.1.0 is refused by 1.0.0 with a message rather than
  changed.
- The page is split into small modules. It still loads nothing from other sites.
- A name typed on the command line wins over another item whose id happens to be the same text.
- Trello cards from archived lists land in the last column, with a warning that says so.

### Fixed

- A save made by another program while the file was being read could be lost.
- `--data ""` fell back to the usual boards file instead of refusing.

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
