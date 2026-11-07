# Contributing

## Ground rules

- **Python standard library only**, Python 3.9 syntax, and no network assets in the page. This is why
  it installs in a second and keeps working. Tests may use optional extras if they skip without them.
- **Nothing that could lose someone's board.** Changes to the file format need a tested upgrade, and
  old files must keep loading with everything in them.
- **Plain messages.** A person should never see a traceback for something they did or for their
  computer's state. Say what happened and what to do next.
- **Docs tell the truth.** Numbers come from a run you did. Say what you didn't test.

## Getting going

No install is needed:

```bash
python -m laneboard --data /tmp/try.json --port 8700 --open
```

Use `--data` and `--port` while developing, so you don't touch your real boards or a copy you already
have running.

## Tests

```bash
python -m unittest
```

Browser tests drive the real page in headless Chrome or Chromium and skip without Playwright:

```bash
python -m pip install playwright
```

```bash
python -m playwright install chromium
```

```bash
python -m unittest tests.test_browser
```

`tests/measure.py` prints timings for large boards. To run the tests in continuous integration, here
is a GitHub Actions job as a starting point:

```yaml
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
        python: ["3.9", "3.14"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: python -m unittest
```

## Where things are

| File | Holds |
|---|---|
| `laneboard/schema.py` | The file format: limits, errors, validation and upgrades from older versions |
| `laneboard/storage.py` | Saving safely: file lock, atomic writes, backups |
| `laneboard/store.py` | Boards and cards: every rule about what a change may do |
| `laneboard/server.py` | HTTP, the JSON API and the request checks |
| `laneboard/cli.py` | The command line |
| `laneboard/samples/` | Example boards |
| `laneboard/static/` | The page: one script, one stylesheet, no build step |

`Store` never edits a board it has handed out: each change works on a copy and swaps it in once saved.
The page sends each change with the board's revision, and the server refuses it if the board has moved
on, so nothing gets overwritten unseen.

To change the file format, raise `SCHEMA_VERSION` and add a step to `UPGRADES` in `schema.py`, plus a
test with a file in the old format. Raise `__version__` in `laneboard/__init__.py` and `APP_VERSION` in
`laneboard/static/app.js` together (a test checks they match), and add to `CHANGELOG.md`.
