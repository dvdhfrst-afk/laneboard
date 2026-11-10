# What has and hasn't been tested

Everything here comes from runs of this code unless it says 1.0.0. Nobody outside the project has used it yet.

## The test suite

`python -m unittest` needs nothing but Python. It ran clean on these, all 64-bit:

| System | Python |
|---|---|
| Windows 11 | 3.9.25, 3.11.9, 3.12.10, 3.14.4 |
| Ubuntu under WSL (Windows Subsystem for Linux), as an ordinary user | 3.14.4 |

On Python 3.9 on Windows it is 755 tests, of which 99 skip: 96 browser tests when Playwright isn't installed, and 3
that can't run there (file permissions, closing a pipe early, and symlinks, which that account can't make). One more
skips outside a git checkout. On Linux the browser tests skip the same way.

## Real browser

`tests/test_browser.py` drives the real page in headless Chrome, with the server running on Python 3.9 on
Windows. All 96 tests pass. They include:

- First run, example boards, and a file with no boards at all
- Drag and drop, the keyboard, and the move buttons on a card
- Two tabs at once: notes, ticks, edits and deletes in both, including a conflict that arrives by the regular
  refresh rather than as a refused save
- The server stopping and coming back, with typed notes saved after it does
- A read-only file, a file broken while open, and a file broken before the page opens
- Fast moves on a slow connection (a delay added to every save), and a change still queued when you switch boards
- Hostile boards: script in every text field, which stays text and runs nothing, and no violations of the
  content security policy
- Long unbroken text at widths of 320, 390, 800, 1440 and 2560 pixels, with no sideways scrolling
- Malformed addresses, bad imports, and UTF-16 files
- Labels, properties of every kind, tasks and the card face set up in Board settings and shown on cards
- The search language and its messages, sorting, saved views and the default view, and searching every board
- Templates: starting a board from one and saving your own
- The Import sheet's preview for Markdown and Trello files, a wrong format refused, and export in every format
- The summary panel, checked against the server's own numbers
- Moving a card to another board, the boards overview, archived boards, the Backups sheet and the command palette

**A long session.** The test `LongSession` makes random real actions through the page (add, rename, duplicate,
delete, archive, restore, notes, ticks, three ways of moving, search, filter, collapse), keeps a separate record
of what should have happened, then compares that with the file and a reloaded page. One run of 800 actions on a
1,000 card board ended with a matching file and page on 1.0.0: `LANEBOARD_SESSION_STEPS=800 python -m unittest tests.test_browser.LongSession`.
On 1.1.0 it runs at its usual 120 actions as part of the suite.

**Next to an older program.** The 1.1.0 page was opened against a running 1.0.0 program, as happens when the files
are updated before the program is restarted. It shows the board and a bar asking for a restart. Changes that need
1.1.0 are refused by the older program with a message, and the file is left as it was.

Separately, by hand with scripts, on 1.0.0 and not repeated for 1.1.0:

- The server on Linux, the page in Chrome on Windows through WSL's port forwarding: add, notes, drag, reload.
- A phone-sized window with touch emulation: adding, moving with the card's own controls, and wrong input in
  every part of Board settings. Every mistake got a specific message.
- The boards file held open with no sharing, as a scanner or backup tool can, while saving. After about 2 seconds
  of waiting the page says it can't save and keeps the text, and saving works once the file is released.
- Running with no console at all, as `pythonw` from a desktop shortcut does.

## Files and failures

`tests/test_files.py` runs each of these against real files, some by simulating the failure:

- Killing a process at random moments while it saves, over and over: every confirmed save survives, and the file is
  always whole
- A full disk, a failed backup, a held lock, a folder that can't be written, a read-only file
- Two programs saving to one file at once
- A broken file, an empty file, binary junk, very deep nesting, `NaN` and `Infinity`, wrong shapes, and files from a
  newer version: all refused with a message and left unchanged
- Files in each older format: every card, note, section, tick and revision kept through the upgrade, with a
  byte-for-byte copy of the original

The upgrade from format 2 to 3 also ran on a copy of a real file from daily use: every card came through unchanged.

## How big it goes

`tests/measure.py` builds boards with about 1 KB per card and times them. One run on a desktop PC with Windows 11, 32 GB
of memory and an NVMe SSD, Python 3.12, in headless Chrome. Times are in milliseconds, and are the median of three to five tries:

| Cards | File | Open file | Save one change | Open the page | Search | Open a card | Move a card |
|---|---|---|---|---|---|---|---|
| 100 | 0.1 MB | 2 | 45 | 103 | 6 | 103 | 34 |
| 1,000 | 1.0 MB | 9 | 76 | 157 | 136 | 131 | 100 |
| 5,000 | 5.2 MB | 92 | 175 | 193 | 142 | 138 | 106 |
| 20,000 | 20.9 MB | 419 | 931 | 350 | 164 | 139 | 141 |

- **Save one change** writes the whole file, so it grows with the file. At 20,000 cards a save takes about a second, and
  while one is running the next change waits its turn. The screen updates at once regardless.
- **Open the page** counts until cards show. Only 300 cards per column are drawn at first, so the five columns here stop at 1,500.
- **Search** includes the short wait for typing to settle on boards over 500 cards.

20,000 cards is the limit per board, so that is as far as it goes. Your times will differ. Run the script to see yours.

## Install

For 1.1.0, from a clean copy on Python 3.9 on Windows, in a new virtual environment: `pip install .`, then `laneboard --version`,
`sample` and `boards` from another folder. The built wheel holds the page and the example boards. The source package
also holds the docs and tests.

## Not tested

- **macOS.** Nothing has been run on it. The code uses nothing specific to any system except the file lock, which on
  macOS is the same call as Linux, so it should work, but that's a guess until someone tries it.
- **`pip install` on Linux.** The test system's Python has no pip, so the suite and the program ran from a copy of the
  source.
- **Firefox, Safari and Edge.** Only Chrome. The page uses `<dialog>` and other features they all have, but drag and
  drop in particular differs between browsers.
- **Real phones and tablets.** Touch was emulated in Chrome. Dragging doesn't work on touch screens, by design of the
  browser's drag events, and the card's own controls stand in for it.
- **Screen readers.** The page has labels, a status region for moves, and works by keyboard, but no one has used it
  with a screen reader.
- **Network drives and synced folders.** The file lock and atomic replace aren't reliable on all of them. A folder
  synced by something like Dropbox may also replace the file behind Laneboard's back. It should notice, but that
  wasn't tried.
- **Running for days.** The longest single run was minutes.
- **Ctrl+C in a Windows console.** On Linux, a real interrupt stopped the board with exit code 0 and a waiting `import`
  with 130, and left the file readable. No automated test covers this.
- **The `ssh` tunnel** shown in the README.
- **Windows versions other than 11, and Python 3.10 and 3.13.**
- **An outside security review.** See [SECURITY.md](../SECURITY.md) for what it's meant to defend against.
