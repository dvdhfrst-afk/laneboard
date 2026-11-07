"""Measure a large board. Run it to get the numbers in docs/testing.md on your own machine:

    python tests/measure.py

It works on a temporary file. The page timings need Playwright (see tests/test_browser.py); without it
only the server timings are printed. These are measurements of one run, not promises.
"""

import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from laneboard.store import Store  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

COLUMNS = ["backlog", "ready", "in-progress", "review", "done"]
WORDS = "gate fence roof pump valve motor report invoice login search header footer".split()


def big_board(cards):
    """A board with the kind of text real cards hold: about 1 KB each on average."""
    return {
        "name": f"{cards} cards",
        "groupLabel": "Area",
        "groups": [{"name": f"Area {i}", "prefix": f"A{i}"} for i in range(8)],
        "checklists": [{"name": "Finish", "items": [{"text": "Tested"}, {"text": "Reviewed"}, {"text": "Written up"}]}],
        "cards": [{
            "key": f"T-{i}",
            "title": f"Fix the {WORDS[i % len(WORDS)]} on item {i}",
            "column": COLUMNS[i % len(COLUMNS)],
            "rank": i,
            "group": f"area-{i % 8}" if i % 9 else None,
            "summary": f"What needs doing about the {WORDS[(i * 7) % len(WORDS)]}, and why it matters. " * 2,
            "fields": [{"label": "Size", "value": f"{i % 8} hours"}, {"label": "Owner", "value": f"Person {i % 12}"}],
            "sections": [{"label": "Steps", "body": "\n".join(f"Step {n} for item {i}" for n in range(6))}],
            "notes": f"Working notes on item {i}. " * 12 if i % 2 else "",
            "blocked": "Waiting for parts" if i % 40 == 0 else "",
        } for i in range(1, cards + 1)],
    }


def timed(fn, times=5):
    samples = []
    for _ in range(times):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def request(url, method="GET", body=None, rev=None):
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if rev is not None:
        headers["If-Match"] = str(rev)
    req = urllib.request.Request(url, method=method, headers=headers, data=None if body is None else json.dumps(body).encode())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=120) as r:
        return json.load(r)


def measure_page(browser, url, board_id):
    page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
    start = time.perf_counter()
    page.goto(f"{url}#{board_id}")
    page.wait_for_selector(".card", timeout=120000)
    opened = (time.perf_counter() - start) * 1000
    drawn = page.locator(".card").count()

    def search():
        """From the last key press to the result showing, including the wait for typing to settle."""
        start = time.perf_counter()
        page.fill("#search", "valve item 77")
        while not page.evaluate("document.querySelector('#search-count').textContent"):
            time.sleep(0.005)
        took = (time.perf_counter() - start) * 1000
        page.fill("#search", "")
        while page.evaluate("document.querySelector('#search-count').textContent"):
            time.sleep(0.005)
        return took
    searched = statistics.median(search() for _ in range(5))

    def open_card():
        page.locator(".card").nth(3).click()
        page.wait_for_selector("#card-notes")
        page.keyboard.press("Escape")
        page.evaluate("new Promise(r => setTimeout(r, 0))")
    opened_card = timed(open_card, 3)

    def move():
        first = page.locator('[data-col="backlog"] .card').first
        first.focus()
        page.keyboard.press("Shift+ArrowDown")
        page.evaluate("new Promise(r => setTimeout(r, 0))")
    moved = timed(move, 3)
    page.context.close()
    return {"open": opened, "drawn": drawn, "search": searched, "card": opened_card, "move": moved}


def main():
    sizes = [int(a) for a in sys.argv[1:]] or [100, 1000, 5000, 20000]
    browser = pw = None
    if sync_playwright:
        pw = sync_playwright().start()
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = pw.chromium.launch(headless=True)
    print(f"Python {sys.version.split()[0]}")
    print(f"{'cards':>6} {'file MB':>8} {'load ms':>8} {'list ms':>8} {'board ms':>9} {'save ms':>8}"
          + (f" {'page ms':>8} {'drawn':>6} {'search ms':>10} {'card ms':>8} {'move ms':>8}" if browser else ""))
    for size in sizes:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "boards.json")
            store = Store(path, starter=False)
            board = store.import_board(big_board(size))
            mb = os.path.getsize(path) / 1_000_000
            load = timed(lambda: Store(path), 3)
            port = free_port()
            url = f"http://127.0.0.1:{port}/"
            server = subprocess.Popen([sys.executable, "-m", "laneboard", "--port", str(port), "--data", path],
                                      cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(600):
                    try:
                        request(url + "api/health")
                        break
                    except OSError:
                        time.sleep(0.1)
                listed = timed(lambda: request(url + "api/boards"))
                got = timed(lambda: request(f"{url}api/boards/{board['id']}"))
                n = [0]

                def save():
                    n[0] += 1
                    request(f"{url}api/boards/{board['id']}/cards/T-1", "PATCH", {"notes": f"note {n[0]}"})
                saved = timed(save)
                row = f"{size:>6} {mb:>8.1f} {load:>8.0f} {listed:>8.0f} {got:>9.0f} {saved:>8.0f}"
                if browser:
                    p = measure_page(browser, url, board["id"])
                    row += f" {p['open']:>8.0f} {p['drawn']:>6} {p['search']:>10.0f} {p['card']:>8.0f} {p['move']:>8.0f}"
                print(row, flush=True)
            finally:
                server.kill()
                server.wait()
    if browser:
        browser.close()
        pw.stop()


if __name__ == "__main__":
    main()
