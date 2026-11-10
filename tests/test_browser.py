"""Drive the real page in headless Chrome against a real server on a temporary file.

Laneboard itself needs nothing beyond Python, and neither does the rest of the test suite. These
tests need Playwright and skip without it:

    python -m pip install playwright
    python -m playwright install chromium
"""

import json
import os
import random
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parent.parent
HOSTILE = '<img src=x onerror="window.pwned=1"><script>window.pwned=1</script>'
SHORT = '<img src=x onerror="window.pwned=1">'


def free_port():
    """A free port, from LANEBOARD_TEST_PORTS (like 18700-18799) when that is set, so runs can keep to a range."""
    span = os.environ.get("LANEBOARD_TEST_PORTS", "")
    low, _, high = span.partition("-")
    candidates = random.sample(range(int(low), int(high or low) + 1), int(high or low) - int(low) + 1) if low else [0]
    for port in candidates:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return s.getsockname()[1]
    raise RuntimeError(f"No free port in {span}.")


def sample_board(name="Work", cards=6):
    return {
        "name": name,
        "groupLabel": "Area",
        "groups": [{"name": "Home", "prefix": "HM", "color": "teal"}, {"name": "Shop", "prefix": "SH", "color": "rose"}],
        "checklists": [{"name": "Ship", "items": [{"text": "Tests pass"}, {"text": "Reviewed"}]}],
        "cards": [{"key": f"T-{i}", "title": f"Card {i}", "column": "backlog", "rank": i,
                   "summary": f"About card {i}", "fields": [{"label": "Size", "value": f"{i} hours"}]}
                  for i in range(1, cards + 1)],
    }


class Harness:
    """One server on one temporary file."""

    def __init__(self, folder):
        self.data = os.path.join(folder, "boards.json")
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        self.proc = None

    def start(self):
        python = os.environ.get("LANEBOARD_SERVER_PYTHON", sys.executable)
        self.proc = subprocess.Popen(
            [python, "-m", "laneboard", "--port", str(self.port), "--data", self.data],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(100):
            try:
                opener.open(self.url + "api/health", timeout=1).read()
                return
            except OSError:
                if self.proc.poll() is not None:
                    break
                time.sleep(0.1)
        raise RuntimeError("The server did not start.")

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None

    def load(self):
        for _ in range(50):
            try:
                with open(self.data, encoding="utf-8") as f:
                    return json.load(f)
            except PermissionError:  # on Windows, in the instant the server replaces the file
                time.sleep(0.02)
        raise RuntimeError("Could not read the boards file.")

    def board(self, i=0):
        return self.load()["boards"][i]

    def card(self, key, i=0):
        return next(c for c in self.board(i)["cards"] if c["key"] == key)

    def api(self, method, path, body=None):
        """Call the JSON API the way a script on this computer would."""
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.url + path.lstrip("/"), data=data, method=method,
                                         headers={"Content-Type": "application/json"} if data else {})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10) as answer:
            return json.load(answer)

    def seed(self, *boards):
        """Create the file through the import command, as a person would."""
        self.stop()
        for b in boards:
            path = self.data + ".import.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(b, f)
            done = subprocess.run([sys.executable, "-m", "laneboard", "--data", self.data, "import", path],
                                  cwd=ROOT, capture_output=True, text=True)
            if done.returncode:
                raise RuntimeError(done.stderr)
            os.remove(path)


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class BrowserCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            try:
                cls.browser = cls.pw.chromium.launch(headless=True)
            except Exception as e:
                cls.pw.stop()
                raise unittest.SkipTest(f"No browser to test with: {e}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Harness(self.tmp.name)
        self.addCleanup(self.app.stop)
        self.problems = []
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        self.context.set_default_timeout(8000)
        self.addCleanup(self.context.close)

    def tearDown(self):
        self.assertEqual(self.problems, [], "the page reported errors")

    def open_page(self, path=""):
        page = self.context.new_page()
        page.on("pageerror", lambda e: self.problems.append(f"page error: {e}"))
        # A failed request is logged by the browser; that alone isn't a fault in the page.
        page.on("console", lambda m: self.problems.append(f"console: {m.text}")
                if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.goto(self.app.url + path)
        self.until(page, "document.querySelector('#board').children.length > 0")
        return page

    def until(self, page, expression, timeout=8):
        """Wait for a condition in the page. Playwright's own wait evaluates a string, which this page's policy forbids."""
        end = time.time() + timeout
        while time.time() < end:
            if page.evaluate(expression):
                return
            time.sleep(0.03)
        self.fail(f"Timed out waiting for {expression}")

    def start(self, *boards):
        if boards:
            self.app.seed(*boards)
        self.app.start()
        return self.open_page()

    def poll(self, page):
        """What the page does by itself every few seconds, without the wait."""
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        page.wait_for_timeout(250)

    def wait_until(self, check, what, timeout=6):
        end = time.time() + timeout
        while time.time() < end:
            if check():
                return
            time.sleep(0.05)
        self.fail(f"Timed out waiting for {what}")

    def column_keys(self, page, column):
        return page.eval_on_selector_all(f'[data-col="{column}"] .card', "els => els.map(e => e.dataset.key)")

    def toast(self, page):
        return page.locator("#toast").inner_text()


class FirstRun(BrowserCase):
    def test_first_card_by_keyboard_then_reload(self):
        page = self.start()
        self.assertIn("My board", page.title())
        self.assertTrue(page.locator("#welcome").is_visible())
        page.keyboard.press("n")
        page.wait_for_selector("dialog[open] input")
        page.keyboard.type("Fix the gate")
        page.keyboard.press("Enter")
        page.wait_for_selector("#card-notes")
        page.fill("#card-notes", "Notes é 日本語 🌱")
        self.until(page, "document.querySelector('.savestate').textContent === 'Saved'")
        page.keyboard.press("Escape")
        self.until(page, "!document.querySelector('#sheet').open")
        page.reload()
        page.wait_for_selector(".card")
        self.assertFalse(page.locator("#welcome").is_visible())
        self.assertEqual(page.locator(".card .meta").count(), 0, "a plain card shows nothing under its title")
        self.assertEqual(self.app.card("T-1")["notes"], "Notes é 日本語 🌱")

    def test_example_board_from_the_empty_board(self):
        page = self.start()
        page.click("#welcome >> text=Garden project")
        page.wait_for_selector(".card")
        self.assertEqual(page.locator("#board-name").inner_text(), "Garden project")
        self.assertGreater(page.locator(".card").count(), 3)

    def test_no_boards_at_all(self):
        page = self.start()
        page.click("#board-settings")
        page.click("text=Delete board")
        page.click(".confirm >> text=Delete board")
        page.wait_for_selector(".start")
        self.assertIn("no boards", page.locator(".start").inner_text())
        page.click(".start >> text=Create a board")
        page.fill("dialog[open] input", "Second")
        page.keyboard.press("Enter")
        self.until(page, "document.querySelector('#board-name').textContent === 'Second'")
        self.assertEqual([b["name"] for b in self.app.load()["boards"]], ["Second"])


class Moving(BrowserCase):
    def test_drag_between_and_within_columns(self):
        page = self.start(sample_board())
        page.drag_and_drop('.card[data-key="T-1"]', '[data-col="done"]')
        self.wait_until(lambda: self.app.card("T-1")["column"] == "done", "the move to save")
        self.assertEqual(self.column_keys(page, "done"), ["T-1"])
        page.drag_and_drop('.card[data-key="T-6"]', '.card[data-key="T-2"]', target_position={"x": 20, "y": 2})
        self.wait_until(lambda: self.column_keys(page, "backlog")[0] == "T-6", "the reorder to show")
        page.reload()
        page.wait_for_selector(".card")
        self.assertEqual(self.column_keys(page, "backlog"), ["T-6", "T-2", "T-3", "T-4", "T-5"])

    def test_keyboard_moves_keep_focus(self):
        page = self.start(sample_board())
        page.focus('.card[data-key="T-1"]')
        page.keyboard.press("ArrowDown")
        self.assertEqual(page.evaluate("document.activeElement.dataset.key"), "T-2")
        page.keyboard.press("Shift+ArrowUp")
        self.wait_until(lambda: self.column_keys(page, "backlog")[0] == "T-2", "the card to move up")
        self.assertEqual(page.evaluate("document.activeElement.dataset.key"), "T-2")
        page.keyboard.press("Shift+ArrowRight")
        self.wait_until(lambda: self.app.card("T-2")["column"] == "ready", "the card to change column")
        self.until(page, "document.activeElement.dataset.key === 'T-2'")
        page.keyboard.press("ArrowLeft")
        self.assertEqual(page.evaluate("document.activeElement.closest('.col').dataset.col"), "backlog")
        page.keyboard.press("Enter")
        page.wait_for_selector("#card-notes")
        opened = page.evaluate("location.hash")
        page.keyboard.press("Escape")
        self.until(page, "!document.querySelector('#sheet').open")
        self.until(page, "document.activeElement.classList.contains('card')")
        self.assertTrue(opened.endswith(page.evaluate("document.activeElement.dataset.key")))

    def test_fast_moves_on_a_slow_disk_all_count(self):
        self.app.seed(sample_board())
        self.app.start()
        self.context.add_init_script("""
          const real = window.fetch;
          window.fetch = async (url, opts) => {
            if (opts && opts.method === "PATCH") await new Promise((r) => setTimeout(r, 250));
            return real(url, opts);
          };""")
        page = self.open_page()
        page.focus('.card[data-key="T-1"]')
        for _ in range(3):
            page.keyboard.press("Shift+ArrowDown")
        order = ["T-2", "T-3", "T-4", "T-1", "T-5", "T-6"]
        self.assertEqual(self.column_keys(page, "backlog"), order, "the screen shows the moves at once")
        self.until(page, "document.querySelector('#board .card') && true")
        # While the answers arrive one by one, the screen never goes back.
        for _ in range(20):
            self.assertEqual(self.column_keys(page, "backlog"), order)
            page.wait_for_timeout(50)
        ranks = sorted((c["rank"], c["key"]) for c in self.app.board()["cards"])
        self.assertEqual([k for _, k in ranks], order)
        self.assertEqual(page.evaluate("document.activeElement.dataset.key"), "T-1")

    def test_a_change_queued_before_switching_boards_reaches_its_own_board(self):
        self.app.seed(sample_board("First"), sample_board("Second"))
        self.app.start()
        self.context.add_init_script("""
          const real = window.fetch;
          window.fetch = async (url, opts) => {
            if (opts && opts.method === "PATCH") await new Promise((r) => setTimeout(r, 300));
            return real(url, opts);
          };""")
        page = self.open_page()
        first, second = self.app.load()["boards"]
        self.assertEqual(page.locator("#board-name").inner_text(), "First")
        page.focus('.card[data-key="T-1"]')
        page.keyboard.press("Shift+ArrowRight")
        page.keyboard.press("Shift+ArrowRight")
        page.select_option("#board-pick", second["id"])
        self.until(page, "document.querySelector('#board-name').textContent === 'Second'")
        self.wait_until(lambda: self.app.card("T-1", 0)["column"] == "in-progress", "the moves to reach the first board")
        self.assertEqual(self.app.card("T-1", 1)["column"], "backlog")
        self.assertEqual(self.column_keys(page, "backlog")[0], "T-1", "the second board's own card stays put")

    def test_one_tab_stop_for_the_cards(self):
        page = self.start(sample_board())
        self.assertEqual(page.locator('#board .card[tabindex="0"]').count(), 1)

    def test_move_buttons_in_the_card_view(self):
        page = self.start(sample_board())
        page.click('.card[data-key="T-3"]')
        page.click('button[aria-label="Move up"]')
        self.wait_until(lambda: self.column_keys(page, "backlog").index("T-3") == 1, "the card to move up")
        page.select_option("#card-column", "review")
        self.wait_until(lambda: self.app.card("T-3")["column"] == "review", "the card to change column")

    def test_shortcut_keys(self):
        page = self.start(sample_board())
        page.keyboard.press("?")
        page.wait_for_selector("#sheet >> text=Keyboard shortcuts and help")
        self.assertIn("boards.json", page.locator("#sheet").inner_text())
        page.keyboard.press("Escape")
        self.until(page, "!document.querySelector('#sheet').open")
        page.keyboard.press("/")
        self.assertEqual(page.evaluate("document.activeElement.id"), "search")
        page.keyboard.type("card 3")
        self.until(page, "document.querySelectorAll('.card').length === 1")
        self.assertIn("1 of 6", page.locator("#search-count").inner_text())
        page.keyboard.press("Escape")
        self.until(page, "document.querySelectorAll('.card').length === 6")


class TwoTabs(BrowserCase):
    def two(self):
        first = self.start(sample_board())
        return first, self.open_page()

    def test_a_card_added_in_one_tab_shows_in_the_other(self):
        a, b = self.two()
        b.click("text=New card")
        b.fill("dialog[open] input", "Made in tab B")
        b.keyboard.press("Enter")
        b.wait_for_selector("#card-notes")
        self.poll(a)
        a.wait_for_selector("text=Made in tab B")

    def test_notes_never_silently_overwrite(self):
        a, b = self.two()
        a.click('.card[data-key="T-1"]')
        b.click('.card[data-key="T-1"]')
        a.fill("#card-notes", "from A")
        self.until(a, "document.querySelector('.savestate').textContent === 'Saved'")
        b.fill("#card-notes", "from B")
        b.wait_for_selector("text=also changed somewhere else", timeout=4000)
        self.assertEqual(self.app.card("T-1")["notes"], "from A")
        self.assertEqual(b.input_value("#card-notes"), "from B", "their text is still in the box")
        b.click("text=Keep my text")
        self.until(b, "document.querySelector('.savestate').textContent === 'Saved'")
        self.assertEqual(self.app.card("T-1")["notes"], "from B")

    def test_a_conflict_seen_by_the_regular_refresh_is_still_a_conflict(self):
        a, b = self.two()
        a.click('.card[data-key="T-1"]')
        b.click('.card[data-key="T-1"]')
        a.fill("#card-notes", "from A")
        self.until(a, "document.querySelector('.savestate').textContent === 'Saved'")
        b.locator("#card-notes").focus()
        b.keyboard.type("from B")
        self.poll(b)  # the refresh brings A's notes in before B's save
        b.wait_for_selector("text=also changed somewhere else", timeout=4000)
        self.assertEqual(self.app.card("T-1")["notes"], "from A")
        b.click("text=Use the other text")
        self.assertEqual(b.input_value("#card-notes"), "from A")

    def test_quiet_notes_follow_the_other_tab(self):
        a, b = self.two()
        a.click('.card[data-key="T-1"]')
        b.click('.card[data-key="T-1"]')
        a.fill("#card-notes", "from A")
        self.until(a, "document.querySelector('.savestate').textContent === 'Saved'")
        self.poll(b)
        self.assertEqual(b.input_value("#card-notes"), "from A")

    def test_closing_during_a_conflict_keeps_the_text(self):
        a, b = self.two()
        a.click('.card[data-key="T-1"]')
        b.click('.card[data-key="T-1"]')
        a.fill("#card-notes", "from A")
        self.until(a, "document.querySelector('.savestate').textContent === 'Saved'")
        b.locator("#card-notes").focus()
        b.keyboard.type("from B")
        self.poll(b)
        b.keyboard.press("Escape")
        self.until(b, "!document.querySelector('#sheet').open")
        self.assertIn("weren't saved", self.toast(b))
        self.assertEqual(self.app.card("T-1")["notes"], "from A")
        b.click("#toast >> text=Open them")
        self.assertEqual(b.input_value("#card-notes"), "from B")

    def test_ticks_from_two_tabs_both_stay(self):
        a, b = self.two()
        a.click('.card[data-key="T-1"]')
        b.click('.card[data-key="T-1"]')
        a.click("text=Tests pass")
        self.wait_until(lambda: len(self.app.card("T-1")["checks"]) == 1, "the first tick to save")
        b.click("text=Reviewed")
        self.wait_until(lambda: len(self.app.card("T-1")["checks"]) == 2, "both ticks to be saved")
        self.poll(a)
        self.assertEqual(a.locator('.checks input:checked').count(), 2)

    def test_edit_form_stops_when_the_same_card_changed(self):
        a, b = self.two()
        a.click('.card[data-key="T-1"]')
        a.click("#sheet >> text=Edit")
        b.click('.card[data-key="T-1"]')
        b.click("#sheet >> text=Edit")
        b.fill("dialog[open] input", "Title from B")
        b.keyboard.press("Enter")
        b.wait_for_selector("#card-notes")
        a.fill("dialog[open] input", "Title from A")
        a.keyboard.press("Enter")
        a.wait_for_selector(".form-error:has-text('changed while you were editing')")
        self.assertEqual(self.app.card("T-1")["title"], "Title from B")
        a.keyboard.press("Enter")
        a.wait_for_selector("#card-notes")
        self.assertEqual(self.app.card("T-1")["title"], "Title from A")

    def test_edit_form_goes_on_when_another_card_changed(self):
        a, b = self.two()
        a.click('.card[data-key="T-1"]')
        a.click("#sheet >> text=Edit")
        b.click('.card[data-key="T-2"]')
        b.click("#sheet >> text=Edit")
        b.fill("dialog[open] input", "Other card")
        b.keyboard.press("Enter")
        b.wait_for_selector("#card-notes")
        a.fill("dialog[open] input", "Mine")
        a.keyboard.press("Enter")
        a.wait_for_selector("#card-notes")
        self.assertEqual(self.app.card("T-1")["title"], "Mine")
        self.assertEqual(self.app.card("T-2")["title"], "Other card")

    def test_card_deleted_in_the_other_tab(self):
        a, b = self.two()
        a.click('.card[data-key="T-1"]')
        b.click('.card[data-key="T-1"]')
        b.click("#sheet >> text=Edit")
        b.click("text=Delete card")
        b.click(".confirm button.danger")
        self.until(b, "!document.querySelector('#sheet').open")
        self.poll(a)
        self.until(a, "!document.querySelector('#sheet').open")
        self.assertIn("deleted", self.toast(a))

    def test_the_form_keeps_what_is_typed_when_the_back_button_is_used(self):
        page = self.start(sample_board())
        page.goto(self.app.url + "#anything")
        page.click("text=New card")
        page.fill("dialog[open] input", "Half typed")
        page.evaluate("location.hash = '#elsewhere'")
        page.wait_for_selector("text=Discard your changes?")
        self.assertEqual(page.input_value("dialog[open] input"), "Half typed")
        page.click("text=Keep editing")
        self.assertEqual(page.input_value("dialog[open] input"), "Half typed")


class Failures(BrowserCase):
    def test_server_gone_and_back(self):
        page = self.start(sample_board())
        page.click('.card[data-key="T-1"]')
        self.app.stop()
        page.fill("#card-notes", "typed while it was down")
        page.wait_for_selector("text=Your text is still here", timeout=5000)
        self.assertTrue(page.locator("#banner").is_visible())
        self.assertIn("Can't reach Laneboard", page.locator("#banner").inner_text())
        self.app.start()
        self.poll(page)
        self.until(page, "document.querySelector('#banner').hidden")
        self.wait_until(lambda: self.app.card("T-1")["notes"] == "typed while it was down", "the notes to save")

    def test_notes_closed_while_the_server_is_gone_are_kept(self):
        page = self.start(sample_board())
        page.click('.card[data-key="T-1"]')
        self.app.stop()
        page.fill("#card-notes", "not lost")
        page.keyboard.press("Escape")
        self.until(page, "!document.querySelector('#sheet').open")
        page.wait_for_selector("#toast:has-text(\"weren't saved\")")
        self.app.start()
        self.poll(page)
        self.wait_until(lambda: self.app.card("T-1")["notes"] == "not lost", "the kept notes to save")

    def test_start_page_when_the_file_is_broken_then_fixed(self):
        self.app.seed(sample_board())
        with open(self.app.data, encoding="utf-8") as f:
            good = f.read()
        self.app.start()
        with open(self.app.data, "w", encoding="utf-8") as f:
            f.write(good[:len(good) // 2])
        page = self.context.new_page()
        page.on("pageerror", lambda e: self.problems.append(str(e)))
        page.goto(self.app.url)
        self.until(page, "!document.querySelector('#banner').hidden")
        self.assertIn("boards.json", page.locator("#banner").inner_text())
        self.assertEqual(page.locator(".card").count(), 0)
        with open(self.app.data, "w", encoding="utf-8") as f:
            f.write(good)
        self.poll(page)
        page.wait_for_selector(".card")
        self.assertTrue(page.locator("#banner").is_hidden())

    def test_file_breaks_while_the_page_is_open(self):
        page = self.start(sample_board())
        with open(self.app.data, encoding="utf-8") as f:
            good = f.read()
        with open(self.app.data, "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.poll(page)
        self.until(page, "!document.querySelector('#banner').hidden")
        self.assertEqual(page.locator(".card").count(), 6, "the board stays on the screen")
        with open(self.app.data, "rb") as f:
            self.assertEqual(f.read(), b"{ not json", "a broken file is left for the person to look at")
        with open(self.app.data, "w", encoding="utf-8") as f:
            f.write(good)
        self.poll(page)
        self.until(page, "document.querySelector('#banner').hidden")

    @unittest.skipIf(os.name != "nt", "A read-only file only stops a save on Windows; elsewhere the folder decides")
    def test_read_only_file(self):
        page = self.start(sample_board())
        os.chmod(self.app.data, stat.S_IREAD)
        self.addCleanup(os.chmod, self.app.data, stat.S_IWRITE | stat.S_IREAD)
        page.click('.card[data-key="T-1"]')
        page.fill("#card-notes", "won't fit")
        page.wait_for_selector("text=Your text is still here", timeout=5000)
        self.assertNotIn("Traceback", page.locator(".savestate").inner_text())
        self.assertEqual(page.input_value("#card-notes"), "won't fit")
        os.chmod(self.app.data, stat.S_IWRITE | stat.S_IREAD)
        page.locator("#card-notes").blur()
        self.wait_until(lambda: self.app.card("T-1")["notes"] == "won't fit", "the save to go through")

    def test_bad_addresses(self):
        page = self.start(sample_board())
        for mark in ("%E0%A4%A", "no-such-board", "no-such-board/T-1"):
            page.goto(self.app.url + "#" + mark)
            page.reload()
            page.wait_for_selector(".card")
        page.goto(self.app.url + f"#{self.app.board()['id']}/T-999")
        page.wait_for_selector("#toast:has-text('T-999')")

    def test_new_page_against_a_server_of_another_version(self):
        self.app.start()
        page = self.context.new_page()
        page.route("**/api/health", lambda r: r.fulfill(json={"app": "laneboard", "version": "0.1.0"}))
        page.goto(self.app.url)
        self.until(page, "!document.querySelector('#banner').hidden")
        self.assertIn("0.1.0", page.locator("#banner").inner_text())


class HostileContent(BrowserCase):
    def board(self):
        b = sample_board(f"Board {HOSTILE}")
        b["description"] = HOSTILE
        b["groupLabel"] = "<b onclick=alert(1)>x</b>"
        b["groups"][0].update(name=SHORT, details=[{"label": SHORT, "value": HOSTILE}])
        b["checklists"][0]["name"] = SHORT
        b["checklists"][0]["items"][0]["text"] = HOSTILE
        c = b["cards"][0]
        c.update(title=HOSTILE, summary=f"{HOSTILE} `<b>code</b>` javascript:window.pwned=1 http://x/\"><script>window.pwned=1</script>",
                 blocked=HOSTILE, notes=HOSTILE, group=None,
                 fields=[{"label": HOSTILE[:30], "value": HOSTILE}], sections=[{"label": HOSTILE[:30], "body": HOSTILE + "\n" + HOSTILE}])
        return b

    def test_text_stays_text(self):
        page = self.start(self.board())
        page.click('.card[data-key="T-1"]')
        page.wait_for_selector("#card-notes")
        page.click("#sheet >> text=Edit")
        page.wait_for_selector("dialog[open] input")
        page.click("text=Cancel")
        page.click("#sheet >> text=Close")
        page.click("#board-settings")
        page.wait_for_selector("text=Board settings")
        page.wait_for_timeout(300)
        self.assertIsNone(page.evaluate("window.pwned || null"))
        self.assertEqual(page.locator("#board img, #sheet img, #board script, #sheet script").count(), 0)
        self.assertEqual(page.locator('a[href^="javascript"]').count(), 0)

    def test_links_on_cards_are_only_web_addresses(self):
        b = sample_board()
        b["cards"][0]["summary"] = "see https://example.test/a?b=1. javascript:alert(1) data:text/html,x file:///etc/passwd"
        page = self.start(b)
        page.click('.card[data-key="T-1"]')
        hrefs = page.eval_on_selector_all("#sheet a", "els => els.map(e => e.getAttribute('href'))")
        self.assertEqual(hrefs, ["https://example.test/a?b=1"], "the full stop isn't part of the address")
        self.assertEqual(page.get_attribute("#sheet a", "rel"), "noopener noreferrer")

    def test_the_page_says_no_to_scripts_it_did_not_bring(self):
        page = self.start(sample_board())
        page.evaluate("""() => {
          const s = document.createElement('script');
          s.textContent = 'window.pwned = 1';
          document.head.append(s);
        }""")
        self.assertIsNone(page.evaluate("window.pwned || null"))
        self.assertTrue(any("Content Security Policy" in p for p in self.problems))
        self.problems.clear()


class Features(BrowserCase):
    def test_archive_and_restore(self):
        page = self.start(sample_board())
        page.click('.card[data-key="T-1"]')
        page.click("#sheet >> text=Archive")
        self.until(page, "!document.querySelector('#sheet').open")
        self.assertEqual(page.locator('.card[data-key="T-1"]').count(), 0)
        self.assertEqual(self.column_keys(page, "backlog"), ["T-2", "T-3", "T-4", "T-5", "T-6"])
        self.assertTrue(self.app.card("T-1")["archived"])
        page.click("#menu summary")
        page.click("#menu >> text=Archived cards (1)")
        page.wait_for_selector(".archive-row")
        page.click(".archive-row >> text=Restore")
        page.wait_for_selector("text=Nothing is archived")
        self.assertEqual(self.column_keys(page, "backlog"), ["T-2", "T-3", "T-4", "T-5", "T-6", "T-1"])

    def test_archive_toast_can_restore(self):
        page = self.start(sample_board())
        page.click('.card[data-key="T-1"]')
        page.click("#sheet >> text=Archive")
        page.click("#toast >> text=Restore")
        self.wait_until(lambda: not self.app.card("T-1")["archived"], "the restore to save")

    def test_duplicate_card_and_board(self):
        page = self.start(sample_board())
        page.click('.card[data-key="T-2"]')
        page.fill("#card-notes", "private to the first card")
        page.click("#sheet >> text=Duplicate")
        page.wait_for_selector("#sheet .key:has-text('T-7')")
        self.assertEqual(page.input_value("#card-notes"), "")
        self.assertEqual(self.column_keys(page, "backlog")[:3], ["T-1", "T-2", "T-7"])
        page.click("#sheet >> text=Close")
        page.click("#menu summary")
        page.click("#menu >> text=Duplicate this board")
        self.until(page, "document.querySelector('#board-name').textContent === 'Copy of Work'")
        self.assertEqual(len(self.app.load()["boards"]), 2)

    def test_export_then_import_through_the_page(self):
        page = self.start(sample_board())
        page.click("#menu summary")
        with page.expect_download() as d:
            page.click("#menu >> text=Export this board")
        saved = os.path.join(self.tmp.name, d.value.suggested_filename)
        d.value.save_as(saved)
        with open(saved, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["cards"]), 6)
        page.click("#menu summary")
        page.wait_for_selector("#menu >> text=Import a file")
        page.set_input_files("#import-file", saved)
        page.wait_for_selector("#toast:has-text('Imported')")
        self.assertEqual(len(self.app.load()["boards"]), 2)
        self.assertEqual(page.locator(".card").count(), 6)

    def test_import_of_a_bad_file_changes_nothing(self):
        page = self.start(sample_board())
        before = self.app.load()
        for name, content in [("notes.txt", b"just some text"), ("half.json", b'{"name": "x", "cards": ['),
                              ("wrong.json", b'{"name": "x", "cards": [{"title": 5}]}'), ("empty.json", b"")]:
            bad = os.path.join(self.tmp.name, name)
            with open(bad, "wb") as f:
                f.write(content)
            page.set_input_files("#import-file", bad)
            page.wait_for_selector(f"#toast:has-text('{name}')")
        self.assertEqual(self.app.load()["boards"], before["boards"])

    def test_import_of_a_utf16_file(self):
        page = self.start()
        board = sample_board("Çalışma")
        path = os.path.join(self.tmp.name, "utf16.json")
        with open(path, "w", encoding="utf-16") as f:
            json.dump(board, f, ensure_ascii=False)
        page.set_input_files("#import-file", path)
        self.until(page, "document.querySelector('#board-name').textContent === 'Çalışma'")

    def test_remove_a_column_moving_its_cards(self):
        page = self.start(sample_board())
        page.click("#board-settings")
        row = page.locator('.col-row[data-id="backlog"]')
        row.locator("text=Remove").click()
        row.locator(".c-move").select_option("review")
        page.click("dialog[open] button[type=submit]")
        self.until(page, "!document.querySelector('#sheet').open")
        self.assertEqual(self.column_keys(page, "review"), ["T-1", "T-2", "T-3", "T-4", "T-5", "T-6"])
        self.assertEqual([c["id"] for c in self.app.board()["columns"]], ["ready", "in-progress", "review", "done"])

    def test_settings_add_a_group_with_a_non_ascii_name(self):
        page = self.start(sample_board())
        page.click("#board-settings")
        page.click("text=Add group")
        page.keyboard.type("日本語")
        self.assertEqual(page.locator(".g-prefix").last.input_value(), "G")
        page.locator(".g-prefix").last.fill("JP")
        page.click("dialog[open] button[type=submit]")
        self.until(page, "!document.querySelector('#sheet').open")
        self.assertEqual(page.locator("#filters >> text=日本語").count(), 1)
        page.click("text=New card")
        page.fill("dialog[open] input", "Group card")
        page.locator("dialog[open] select").first.select_option(label="日本語")
        page.click("dialog[open] button[type=submit]")
        page.wait_for_selector("#sheet .key:has-text('JP-1')")

    def test_ticking_rewording_keeps_other_ticks(self):
        page = self.start(sample_board())
        page.click('.card[data-key="T-1"]')
        page.click("text=Tests pass")
        self.wait_until(lambda: len(self.app.card("T-1")["checks"]) == 1, "the tick to save")
        page.click("#sheet >> text=Close")
        page.click("#board-settings")
        items = page.locator(".k-items")
        items.fill(items.input_value().replace("Reviewed", "Reviewed twice"))
        page.click("dialog[open] button[type=submit]")
        self.until(page, "!document.querySelector('#sheet').open")
        self.assertEqual(page.locator('.card[data-key="T-1"] .ticks').inner_text(), "1/2")

    def test_collapsed_columns_stay_collapsed(self):
        page = self.start(sample_board())
        page.click('[data-col="backlog"] .collapse')
        self.assertEqual(page.locator(".card").count(), 0)
        page.reload()
        page.wait_for_selector(".col.collapsed")
        self.assertEqual(page.locator(".card").count(), 0)
        page.click('[data-col="backlog"] .collapse')
        self.assertEqual(page.locator(".card").count(), 6)

    def test_filters(self):
        b = sample_board()
        b["cards"][0]["group"] = "home"
        b["cards"][1]["blocked"] = "Waiting"
        page = self.start(b)
        page.click("#filters >> text=Home")
        self.wait_until(lambda: self.column_keys(page, "backlog") == ["T-1"], "the group filter")
        self.assertEqual(page.input_value("#search"), "group:Home", "the chip writes its part of the search")
        page.click("#filters >> text=Show all")
        self.wait_until(lambda: len(self.column_keys(page, "backlog")) == 6, "every card again")
        page.click("#filters >> text=Blocked")
        self.wait_until(lambda: self.column_keys(page, "backlog") == ["T-2"], "the blocked filter")

    def test_form_says_what_is_wrong(self):
        page = self.start(sample_board())
        page.click("text=New card")
        page.click("dialog[open] button[type=submit]")
        page.wait_for_selector(".form-error:has-text('needs a title')")
        page.fill("dialog[open] input", "T")
        page.locator("dialog[open] textarea").nth(1).fill("no label here")
        page.click("dialog[open] button[type=submit]")
        page.wait_for_selector(".form-error:has-text('Label: value')")


class Sizes(BrowserCase):
    def overflow(self, page):
        return page.evaluate("document.documentElement.scrollWidth - window.innerWidth")

    def test_no_sideways_scrolling_at_any_width(self):
        long = "Unbroken" * 30
        b = sample_board("Name" + long[:100])
        b["description"] = long
        b["groups"][0]["name"] = long[:60]
        b["cards"][0].update(title=long[:200], summary=long, blocked=long,
                             fields=[{"label": long[:30], "value": long}], sections=[{"label": long[:30], "body": long}])
        b["cards"][1].update(group=long[:60].lower())
        page = self.start(b, sample_board("Other"))
        for width, height in [(320, 568), (390, 844), (800, 600), (1440, 900), (2560, 1440)]:
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(50)
            self.assertLessEqual(self.overflow(page), 0, f"board at {width} px")
            page.click('.card[data-key="T-1"]')
            page.wait_for_selector("#card-notes")
            over = page.evaluate("document.querySelector('#sheet').scrollWidth - document.querySelector('#sheet').clientWidth")
            self.assertLessEqual(over, 0, f"card at {width} px")
            page.click("#sheet >> text=Edit")
            page.wait_for_selector("dialog[open] form")
            over = page.evaluate("document.querySelector('#sheet').scrollWidth - document.querySelector('#sheet').clientWidth")
            self.assertLessEqual(over, 0, f"form at {width} px")
            page.click("text=Cancel")
            page.click("#sheet >> text=Close")
            page.click("#board-settings")
            page.wait_for_selector("dialog[open] form")
            over = page.evaluate("document.querySelector('#sheet').scrollWidth - document.querySelector('#sheet').clientWidth")
            self.assertLessEqual(over, 0, f"settings at {width} px")
            page.click("#sheet >> text=Close")
            self.until(page, "!document.querySelector('#sheet').open")

    def test_large_board_shows_more_on_request(self):
        b = sample_board(cards=1200)
        for c in b["cards"]:
            c["fields"] = []
            c["summary"] = ""
        page = self.start(b)
        self.assertEqual(page.locator(".card").count(), 300)
        self.assertIn("Show 300 more of 900", page.locator(".more").inner_text())
        self.assertEqual(page.locator('[data-col="backlog"] .count').inner_text(), "1200")
        page.click(".more")
        self.assertEqual(page.locator(".card").count(), 600)
        page.fill("#search", "Card 1199")
        self.until(page, "document.querySelectorAll('.card').length === 1")
        self.assertIn("1 of 1200", page.locator("#search-count").inner_text())
        page.fill("#search", "")
        self.until(page, "document.querySelectorAll('.card').length === 600")
        before = self.column_keys(page, "backlog")
        page.drag_and_drop('.card[data-key="T-3"]', '[data-col="done"]')
        self.wait_until(lambda: self.app.card("T-3")["column"] == "done", "the move to save")
        self.assertEqual(self.column_keys(page, "backlog")[:3], [before[0], before[1], before[3]])
        self.assertEqual(page.locator('[data-col="backlog"] .count').inner_text(), "1199")


class LongSession(BrowserCase):
    """Random real use, checked at the end against a separate record of what should have happened."""

    STEPS = int(os.environ.get("LANEBOARD_SESSION_STEPS", "120"))
    CARDS = int(os.environ.get("LANEBOARD_SESSION_CARDS", "1000"))

    def close_sheet(self, page):
        page.click("#sheet >> text=Close")
        self.until(page, "!document.querySelector('#sheet').open")

    def settle(self, page):
        """Wait for everything sent to have been answered, as someone reading the screen would."""
        self.until(page, "import('/static/api.js').then((m) => m.pending === 0)")  # the page's own change queue

    def test_session(self):
        rng = random.Random(20261107)
        board = sample_board("Long session", cards=self.CARDS)
        columns = ["backlog", "ready", "in-progress", "review", "done"]
        board["cards"] = [{"key": f"T-{i}", "title": f"Card {i}", "column": columns[i % 5], "rank": i, "group": "home" if i % 3 == 0 else None}
                          for i in range(1, self.CARDS + 1)]
        page = self.start(board)
        expected = {c["key"]: {"title": c["title"], "column": c["column"], "archived": False, "notes": "", "checks": 0} for c in board["cards"]}
        counts = {}

        def live():
            return [k for k, c in expected.items() if not c["archived"]]

        def open_card(key):
            page.click(f'.card[data-key="{key}"]')
            page.wait_for_selector("#card-notes")

        def notes():
            key = rng.choice(live())
            open_card(key)
            text = f"Note {rng.randrange(10 ** 6)} é 日本語"
            page.fill("#card-notes", text)
            expected[key]["notes"] = text
            self.close_sheet(page)  # closing saves straight away

        def move_by_keyboard():
            key = rng.choice(live())
            page.focus(f'.card[data-key="{key}"]')
            at = columns.index(expected[key]["column"])
            step = rng.choice([-1, 1])
            if 0 <= at + step < 5:
                page.keyboard.press("Shift+ArrowRight" if step > 0 else "Shift+ArrowLeft")
                expected[key]["column"] = columns[at + step]

        def move_by_drag():
            key = rng.choice(live())
            target = rng.choice(columns)
            page.drag_and_drop(f'.card[data-key="{key}"]', f'[data-col="{target}"] .col-head')
            expected[key]["column"] = target

        def move_in_card():
            key = rng.choice(live())
            target = rng.choice(columns)
            open_card(key)
            page.select_option("#card-column", target)
            expected[key]["column"] = target
            self.close_sheet(page)

        def rename():
            key = rng.choice(live())
            open_card(key)
            page.click("#sheet >> text=Edit")
            title = f"Renamed {rng.randrange(10 ** 6)} <b>not bold</b>"
            page.fill("dialog[open] input", title)
            page.click("dialog[open] button[type=submit]")
            page.wait_for_selector("#card-notes")
            expected[key]["title"] = title
            self.close_sheet(page)

        def add():
            page.click("#new-card")
            title = f"Added {rng.randrange(10 ** 6)}"
            page.fill("dialog[open] input", title)
            page.click("dialog[open] button[type=submit]")
            page.wait_for_selector("#card-notes")
            key = page.locator("#sheet .key").inner_text()
            expected[key] = {"title": title, "column": "backlog", "archived": False, "notes": "", "checks": 0}
            self.close_sheet(page)

        def duplicate():
            key = rng.choice(live())
            original = expected[key]
            open_card(key)
            page.click("#sheet >> text=Duplicate")
            self.until(page, f"document.querySelector('#sheet .key').textContent !== '{key}'")
            new = page.locator("#sheet .key").inner_text()
            expected[new] = {"title": f"Copy of {original['title']}"[:200], "column": original["column"], "archived": False, "notes": "", "checks": 0}
            self.close_sheet(page)

        def archive():
            key = rng.choice(live())
            open_card(key)
            page.click("#sheet >> text=Archive")
            self.until(page, "!document.querySelector('#sheet').open")
            expected[key]["archived"] = True

        def restore():
            gone = [k for k, c in expected.items() if c["archived"]]
            if not gone:
                return
            key = rng.choice(gone)
            page.click("#menu summary")
            page.click("#menu >> text=Archived cards")
            search = page.locator("dialog[open] input[type=search]")
            search.fill(key)
            row = page.locator(".archive-row", has=page.locator(f'a[href$="/{key}"]'))
            row.locator("text=Restore").click()
            self.until(page, f"!document.querySelector('.archive-row a[href$=\"/{key}\"]')")
            expected[key]["archived"] = False
            self.close_sheet(page)

        def delete():
            key = rng.choice(live())
            open_card(key)
            page.click("#sheet >> text=Edit")
            page.click("text=Delete card")
            page.click(".confirm button.danger")
            self.until(page, "!document.querySelector('#sheet').open")
            del expected[key]

        def tick():
            key = rng.choice(live())
            open_card(key)
            box = page.locator(".checks input").nth(rng.randrange(2))
            before = box.is_checked()
            box.click()
            expected[key]["checks"] += -1 if before else 1
            self.close_sheet(page)

        def search():
            page.fill("#search", rng.choice(["card 7", "renamed", "日本語", "nothing matches this"]))
            page.wait_for_timeout(180)
            page.fill("#search", "")
            page.wait_for_timeout(180)

        def filters():
            page.click("#filters >> text=Home")
            page.click("#filters >> text=Show all")

        def collapse():
            target = rng.choice(columns)
            page.click(f'[data-col="{target}"] .collapse')
            page.click(f'[data-col="{target}"] .collapse')

        actions = [notes, notes, move_by_keyboard, move_by_keyboard, move_by_drag, move_in_card, rename, add, duplicate, archive, restore,
                   delete, tick, tick, search, filters, collapse]
        for _ in range(self.STEPS):
            action = rng.choice(actions)
            counts[action.__name__] = counts.get(action.__name__, 0) + 1
            self.settle(page)
            action()

        page.wait_for_timeout(600)  # the last saves land
        saved = {c["key"]: c for c in self.app.board()["cards"]}
        self.assertEqual(sorted(saved), sorted(expected), "the same cards exist")
        for key, want in expected.items():
            got = saved[key]
            self.assertEqual((got["title"], got["column"], bool(got["archived"]), got["notes"], len(got["checks"])),
                             (want["title"], want["column"], want["archived"], want["notes"], want["checks"]), key)
        page.reload()
        page.wait_for_selector(".card")
        for column in columns:
            want = sum(1 for c in expected.values() if c["column"] == column and not c["archived"])
            self.assertEqual(page.locator(f'[data-col="{column}"] .count').inner_text().split(" ")[0], str(want), column)
        report = os.environ.get("LANEBOARD_SESSION_REPORT")
        if report:
            with open(report, "w", encoding="utf-8") as f:
                json.dump({"steps": self.STEPS, "cards": len(expected), "actions": counts}, f, indent=1, sort_keys=True)



def parts_board(name="Parts", cards=3):
    """A board with labels, a property of every type, a limited column and a checklist."""
    return {
        "name": name,
        "prefix": "T",
        "groupLabel": "Area",
        "columns": ["Backlog", {"name": "Doing", "limit": 1}, "Done"],
        "labels": [{"name": "Bug", "color": "rose"}, {"name": "Docs", "color": "blue"}, {"name": "Quick", "color": "green"}],
        "properties": [
            {"name": "Points", "type": "number", "min": 0, "max": 100, "decimals": 1, "unit": "pt"},
            {"name": "Priority", "type": "choice", "choices": ["Low", "Medium", {"name": "High", "color": "rose"}]},
            {"name": "Platform", "type": "multi", "choices": ["iOS", "Web", "Desktop"]},
            {"name": "Reviewed", "type": "checkbox"},
            {"name": "Link", "type": "url"},
            {"name": "Confidence", "type": "rating", "max": 5},
            {"name": "Owner", "type": "text"},
        ],
        "checklists": [{"name": "Ship", "items": [{"text": "Tests pass"}, {"text": "Signed off"}]}],
        "cards": [{"key": f"T-{i}", "title": f"Card {i}", "column": "Backlog", "rank": i} for i in range(1, cards + 1)],
    }


class PartsCase(BrowserCase):
    def open_card(self, page, key):
        page.click(f'.card[data-key="{key}"]')
        page.wait_for_selector("#card-notes")

    def close_sheet(self, page):
        page.click("#sheet >> text=Close")
        self.until(page, "!document.querySelector('#sheet').open")

    def settings(self, page, tab=None):
        page.click("#board-settings")
        page.wait_for_selector("dialog[open] form")
        if tab:
            page.click(f"#tab-{tab}")

    def save_settings(self, page):
        page.click("dialog[open] button[type=submit]")
        self.until(page, "!document.querySelector('#sheet').open")

    def tab_to(self, page, check, back=False, limit=60):
        """Press Tab (or Shift+Tab) until `check` holds for the focused element, as someone without a mouse would."""
        for _ in range(limit):
            if page.evaluate(f"(() => {{ const el = document.activeElement; return !!({check}); }})()"):
                return
            page.keyboard.press("Shift+Tab" if back else "Tab")
        self.fail(f"Tab never reached {check}")

    def value(self, key, prop):
        return self.app.card(key)["properties"].get(prop)


class Labels(PartsCase):
    def test_labels_show_as_chips_with_their_names(self):
        b = parts_board()
        b["cards"][0]["labels"] = ["Docs", "Bug"]
        page = self.start(b)
        self.assertEqual(page.locator('.card[data-key="T-1"] .chip.label').all_inner_texts(), ["Bug", "Docs"], "in the board's order")
        self.open_card(page, "T-1")
        self.assertEqual(page.locator("#sheet .labels-sec .chip").all_inner_texts(), ["Bug", "Docs"])
        self.assertIn("c-rose", page.get_attribute("#sheet .labels-sec .chip >> nth=0", "class"))

    def test_add_and_take_off_labels_in_the_card_view(self):
        page = self.start(parts_board())
        self.open_card(page, "T-1")
        page.click("#card-labels")
        page.click(".picker >> text=Docs")
        page.click(".picker >> text=Bug")
        self.wait_until(lambda: self.app.card("T-1")["labels"] == ["bug", "docs"], "both labels to save")
        page.click(".picker >> text=Bug")
        self.wait_until(lambda: self.app.card("T-1")["labels"] == ["docs"], "the label to come off")
        page.fill(".picker-search", "qu")
        self.assertEqual(page.locator(".picker-list .chip").all_inner_texts(), ["Quick"], "the search narrows the list")
        page.click(".picker >> text=Done")
        self.close_sheet(page)
        self.assertEqual(page.locator('.card[data-key="T-1"] .chip.label').all_inner_texts(), ["Docs"])

    def test_make_a_label_from_the_picker(self):
        page = self.start(parts_board())
        self.open_card(page, "T-2")
        page.click("#card-labels")
        page.fill(".picker-search", "Needs design")
        page.click("text=Make the label \"Needs design\"")
        self.wait_until(lambda: self.app.card("T-2")["labels"] == ["needs-design"], "the new label to go on the card")
        self.assertEqual([lab["name"] for lab in self.app.board()["labels"]], ["Bug", "Docs", "Quick", "Needs design"])
        self.assertEqual(page.locator("#sheet .labels-sec .label-line .chip").all_inner_texts(), ["Needs design"])

    def test_label_picker_by_keyboard_only(self):
        page = self.start(parts_board())
        page.keyboard.press("Tab")
        self.tab_to(page, "el.dataset.key === 'T-1'")
        page.keyboard.press("Enter")
        page.wait_for_selector("#card-notes")
        self.tab_to(page, "el.id === 'card-labels'")
        page.keyboard.press("Enter")
        self.until(page, "document.activeElement.classList.contains('picker-search')")
        page.keyboard.type("do")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Space")
        self.wait_until(lambda: self.app.card("T-1")["labels"] == ["docs"], "Docs to be ticked")
        page.keyboard.press("ArrowUp")
        page.keyboard.press("Escape")  # clears the search
        self.assertEqual(page.input_value(".picker-search"), "")
        page.keyboard.type("quick")
        page.keyboard.press("Enter")  # ticks the first match
        self.wait_until(lambda: self.app.card("T-1")["labels"] == ["docs", "quick"], "Quick to be ticked")
        page.keyboard.type("Later")
        page.keyboard.press("Enter")  # no match, so it makes the label
        self.wait_until(lambda: self.app.card("T-1")["labels"] == ["docs", "quick", "later"], "the new label")
        page.keyboard.press("Escape")  # closes the picker, not the card
        self.until(page, "!document.querySelector('.picker') && document.activeElement.id === 'card-labels'")
        self.assertTrue(page.evaluate("document.querySelector('#sheet').open"))
        page.keyboard.press("Escape")
        self.until(page, "!document.querySelector('#sheet').open")

    def test_labels_in_the_card_form(self):
        page = self.start(parts_board())
        page.click("#new-card")
        page.fill("dialog[open] input", "With a label")
        page.click("#form-labels")
        page.click(".picker >> text=Bug")
        page.keyboard.press("Enter")  # in the picker, Enter ticks and unticks, and doesn't send the form
        self.assertFalse(page.is_checked(".picker input[data-id=bug]"))
        page.keyboard.press("Enter")
        self.assertTrue(page.is_checked(".picker input[data-id=bug]"))
        self.assertTrue(page.evaluate("!!document.querySelector('dialog[open] form')"))
        page.click(".picker >> text=Done")
        page.click("dialog[open] button[type=submit]")
        page.wait_for_selector("#card-notes")
        self.assertEqual(self.app.card("T-4")["labels"], ["bug"])
        page.click("#sheet >> text=Edit")
        page.click("#form-labels")
        page.click(".picker >> text=Bug")
        page.click(".picker >> text=Quick")
        page.click("dialog[open] button[type=submit]")
        page.wait_for_selector("#card-notes")
        self.assertEqual(self.app.card("T-4")["labels"], ["quick"])

    def test_label_filter(self):
        b = parts_board()
        b["cards"][0]["labels"] = ["Bug"]
        b["cards"][1]["labels"] = ["Docs"]
        page = self.start(b)
        self.assertEqual(page.locator("#filters .label-filter").count(), 2, "only labels in use")
        page.click("#filters .label-filter:has-text('Bug')")
        self.wait_until(lambda: self.column_keys(page, "backlog") == ["T-1"], "the label filter")
        self.assertIn("1 of 3", page.locator("#search-count").inner_text())
        page.click("#filters .label-filter:has-text('Docs')")
        self.wait_until(lambda: self.column_keys(page, "backlog") == ["T-1", "T-2"], "either label")
        page.click("#filters >> text=Show all")
        self.wait_until(lambda: self.column_keys(page, "backlog") == ["T-1", "T-2", "T-3"], "every card again")


class Properties(PartsCase):
    def test_each_type_saves_in_place_and_shows_on_the_card(self):
        b = parts_board()
        b["settings"] = {"cardFace": ["labels", "Points", "Priority", "Platform", "Reviewed", "Link", "Confidence", "Owner"]}
        page = self.start(b)
        self.open_card(page, "T-1")
        page.fill("#prop-points", "2.25")
        page.keyboard.press("Enter")
        self.wait_until(lambda: self.value("T-1", "points") == 2.3, "the number, rounded")
        self.until(page, "document.querySelector('#prop-points').value === '2.3'")
        page.click("#prop-priority >> text=High")
        page.click("#prop-platform >> text=iOS")
        page.click("#prop-platform >> text=Web")
        page.check("#prop-reviewed")
        page.fill("#prop-link", "https://example.test/spec")
        page.keyboard.press("Enter")
        page.click("#prop-confidence label.star >> nth=3")
        page.fill("#prop-owner", "Sam")
        page.keyboard.press("Enter")
        self.wait_until(lambda: self.app.card("T-1")["properties"] == {
            "points": 2.3, "priority": "high", "platform": ["ios", "web"], "reviewed": True,
            "link": "https://example.test/spec", "confidence": 4, "owner": "Sam"}, "every value to save")
        self.assertEqual(page.get_attribute("#sheet .open-link", "href"), "https://example.test/spec")
        self.assertEqual(page.get_attribute("#sheet .open-link", "rel"), "noopener noreferrer")
        self.assertIn("4 of 5", page.locator("#prop-confidence").inner_text())
        self.close_sheet(page)
        face = " ".join(page.locator('.card[data-key="T-1"] .meta').inner_text().split())
        for part in ("Points 2.3 pt", "Priority High", "Platform iOS Web", "✓ Reviewed", "Link example.test", "4 out of 5", "Owner Sam"):
            self.assertIn(part, face)
        self.assertEqual(page.locator('.card[data-key="T-1"] a').count(), 0, "no link inside the card's own link")
        self.open_card(page, "T-1")
        page.fill("#prop-points", "")
        page.keyboard.press("Enter")
        page.click("#prop-priority >> text=None")
        page.click("#prop-confidence >> text=Clear")
        self.wait_until(lambda: not {"points", "priority", "confidence"} & set(self.app.card("T-1")["properties"]), "the values to clear")

    def test_the_server_says_what_is_wrong_next_to_the_value(self):
        page = self.start(parts_board())
        self.open_card(page, "T-1")
        page.fill("#prop-points", "500")
        page.keyboard.press("Enter")
        page.wait_for_selector(".prop-msg:has-text('Points must be a number from 0 to 100')")
        self.assertEqual(page.input_value("#prop-points"), "500", "what was typed stays")
        page.fill("#prop-link", "example.test")
        page.keyboard.press("Enter")
        page.wait_for_selector(".prop-msg:has-text('Link must be a link starting with http')")
        self.assertEqual(self.app.card("T-1")["properties"], {})
        page.fill("#prop-points", "5")
        page.keyboard.press("Enter")
        self.wait_until(lambda: self.value("T-1", "points") == 5, "a good value to save")

    def test_two_tabs_conflict_on_a_property_value(self):
        a = self.start(parts_board())
        b = self.open_page()
        self.open_card(a, "T-1")
        self.open_card(b, "T-1")
        a.fill("#prop-points", "3")
        a.keyboard.press("Enter")
        self.wait_until(lambda: self.value("T-1", "points") == 3, "the first tab's value")
        b.fill("#prop-points", "5")
        b.keyboard.press("Enter")
        b.wait_for_selector(".prop-msg:has-text('also changed somewhere else, to 3 pt')")
        self.assertEqual(self.value("T-1", "points"), 3, "nothing is overwritten without asking")
        self.assertEqual(b.input_value("#prop-points"), "5")
        b.click("text=Keep mine")
        self.wait_until(lambda: self.value("T-1", "points") == 5, "the kept value")
        self.poll(a)
        self.until(a, "document.querySelector('#prop-points').value === '5'")  # the quiet tab follows
        a.fill("#prop-points", "7")
        a.keyboard.press("Enter")
        self.wait_until(lambda: self.value("T-1", "points") == 7, "the first tab again")
        b.fill("#prop-points", "9")
        b.keyboard.press("Enter")
        b.wait_for_selector(".prop-msg:has-text('also changed somewhere else')")
        b.click("text=Use the other")
        self.assertEqual(b.input_value("#prop-points"), "7")
        self.assertEqual(self.value("T-1", "points"), 7)

    def test_properties_in_the_card_form(self):
        page = self.start(parts_board())
        page.click("#new-card")
        page.fill("dialog[open] input", "With values")
        page.fill("#form-prop-points", "lots")
        page.click("#form-prop-priority >> text=Low")
        page.click("dialog[open] button[type=submit]")
        page.wait_for_selector(".form-error:has-text('Points must be a number')")
        self.assertEqual(page.input_value("#form-prop-points"), "lots")
        page.fill("#form-prop-points", "4")
        page.click("dialog[open] button[type=submit]")
        page.wait_for_selector("#card-notes")
        self.assertEqual(self.app.card("T-4")["properties"], {"points": 4, "priority": "low"})
        page.click("#sheet >> text=Edit")
        self.assertEqual(page.input_value("#form-prop-points"), "4")
        page.click("#form-prop-priority >> text=None")
        page.click("dialog[open] button[type=submit]")
        page.wait_for_selector("#card-notes")
        self.assertEqual(self.app.card("T-4")["properties"], {"points": 4})


class Tasks(PartsCase):
    def task_texts(self, key="T-1"):
        return [t["text"] for t in self.app.card(key)["tasks"]]

    def test_add_tick_reword_reorder_and_remove(self):
        page = self.start(parts_board())
        self.open_card(page, "T-1")
        for text in ("First", "Second", "Third"):
            page.fill("#task-new", text)
            page.keyboard.press("Enter")
        self.wait_until(lambda: self.task_texts() == ["First", "Second", "Third"], "three tasks")
        self.until(page, "document.querySelectorAll('.task[data-id]:not([data-id=\"\"])').length === 3")
        page.locator(".task >> nth=0").locator("input[type=checkbox]").check()
        self.wait_until(lambda: self.app.card("T-1")["tasks"][0]["done"], "the tick")
        page.locator(".task-text >> nth=1").fill("Second, reworded")
        page.keyboard.press("Enter")
        self.wait_until(lambda: self.task_texts()[1] == "Second, reworded", "the new wording")
        page.locator(".task >> nth=2").locator("button[aria-label='Move up']").click()
        self.wait_until(lambda: self.task_texts() == ["First", "Third", "Second, reworded"], "the new order")
        page.locator(".task >> nth=0").locator("button[aria-label='Remove task']").click()
        self.wait_until(lambda: self.task_texts() == ["Third", "Second, reworded"], "the task to go")
        self.assertIn("0/2", page.locator(".tasks h3").inner_text())
        self.close_sheet(page)
        self.assertEqual(page.locator('.card[data-key="T-1"] .ticks').inner_text(), "0/4", "two checklist lines and two tasks")

    def test_task_list_by_keyboard_only(self):
        page = self.start(parts_board())
        page.keyboard.press("Tab")
        self.tab_to(page, "el.dataset.key === 'T-1'")
        page.keyboard.press("Enter")
        page.wait_for_selector("#card-notes")
        self.tab_to(page, "el.id === 'task-new'")
        for text in ("One", "Two", "Three"):
            page.keyboard.type(text)
            page.keyboard.press("Enter")
        self.wait_until(lambda: self.task_texts() == ["One", "Two", "Three"], "three tasks")
        self.until(page, "[...document.querySelectorAll('.task-text')].every((t) => !t.readOnly)")
        self.tab_to(page, "el.classList.contains('task-text') && el.value === 'Three'", back=True)
        page.keyboard.press("Alt+ArrowUp")
        page.keyboard.press("Alt+ArrowUp")
        self.wait_until(lambda: self.task_texts() == ["Three", "One", "Two"], "Three to move to the top")
        self.assertEqual(page.evaluate("document.activeElement.value"), "Three", "focus moves with the task")
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Space")
        self.wait_until(lambda: self.app.card("T-1")["tasks"][0]["done"], "the tick")
        self.tab_to(page, "el.getAttribute('aria-label') === 'Remove task'")
        page.keyboard.press("Enter")
        self.wait_until(lambda: self.task_texts() == ["One", "Two"], "the task to go")
        self.assertEqual(page.evaluate("document.activeElement.value"), "One", "focus goes to the next task")
        page.keyboard.press("Control+a")
        page.keyboard.type("Uno")
        page.keyboard.press("Enter")
        self.wait_until(lambda: self.task_texts() == ["Uno", "Two"], "the new wording")
        page.keyboard.type(" and more")
        page.keyboard.press("Escape")  # puts back the saved text and keeps the card open
        self.assertEqual(page.evaluate("document.activeElement.value"), "Uno")
        self.assertTrue(page.evaluate("document.querySelector('#sheet').open"))
        page.keyboard.press("Escape")
        self.until(page, "!document.querySelector('#sheet').open")

    def test_progress_on_the_card_face_counts_ticks_and_tasks(self):
        b = parts_board()
        b["cards"][0]["tasks"] = [{"text": "Done one", "done": True}, "Open one"]
        b["cards"][1]["tasks"] = ["Only a task"]
        b["checklists"] = []
        page = self.start(b)
        self.assertEqual(page.locator('.card[data-key="T-1"] .ticks').inner_text(), "1/2")
        self.assertEqual(page.get_attribute('.card[data-key="T-1"] .ticks', "title"), "Tasks done")
        self.assertEqual(page.locator('.card[data-key="T-3"] .ticks').count(), 0)


class MoveToBoard(PartsCase):
    def boards(self, target_limit=None):
        source = parts_board()
        source["cards"][0].update(labels=["Bug", "Docs"], properties={"Points": 3, "Priority": "High"}, notes="kept")
        target = {"name": "Other", "prefix": "OB", "columns": ["To do", {"name": "Doing", "limit": target_limit}, "Done"],
                  "labels": ["Bug"], "properties": [{"name": "Points", "type": "number"}],
                  "cards": [{"title": "Already here", "column": "Doing"}]}
        if target_limit:
            target["settings"] = {"limitPolicy": "refuse"}
        return source, target

    def test_move_a_card_and_follow_it(self):
        page = self.start(*self.boards())
        self.open_card(page, "T-1")
        page.click("#sheet >> text=Move to board")
        self.until(page, "!document.querySelector('#move-column').disabled")
        page.select_option("#move-column", label="Doing")
        page.click(".move-panel >> text=\"Move\"")
        page.wait_for_selector("#toast:has-text('Moved T-1 to Other as OB-2')")
        message = self.toast(page)
        self.assertIn("Label: Docs", message)
        self.assertIn("Priority: High", message)
        self.assertNotIn("Points", message)
        self.assertFalse(page.evaluate("document.querySelector('#sheet').open"))
        self.assertEqual(page.locator('.card[data-key="T-1"]').count(), 0)
        page.click("#toast >> text=Go to it")
        self.until(page, "document.querySelector('#board-name').textContent === 'Other'")
        page.wait_for_selector("#sheet .key:has-text('OB-2')")
        self.assertEqual(page.locator("#sheet .labels-sec .chip").all_inner_texts(), ["Bug"])
        self.assertEqual(page.input_value("#prop-points"), "3")
        self.assertEqual(page.input_value("#card-notes"), "kept")
        moved = next(c for c in self.app.board(1)["cards"] if c["key"] == "OB-2")
        self.assertEqual(moved["column"], "doing")

    def test_a_refusal_keeps_the_choice_and_the_card(self):
        page = self.start(*self.boards(target_limit=1))
        self.open_card(page, "T-1")
        page.click("#sheet >> text=Move to board")
        self.until(page, "!document.querySelector('#move-column').disabled")
        page.select_option("#move-column", label="Doing (limit 1)")
        page.click(".move-panel >> text=\"Move\"")
        page.wait_for_selector(".move-panel .form-error:has-text('Doing is full')")
        self.assertEqual(page.input_value("#move-column"), "doing")
        self.assertEqual(self.app.card("T-1")["title"], "Card 1")
        page.select_option("#move-column", label="Done")
        page.click(".move-panel >> text=\"Move\"")
        page.wait_for_selector("#toast:has-text('Moved T-1 to Other')")


class BoardSettingsParts(PartsCase):
    def test_labels_tab_add_rename_recolor_remove_with_a_count(self):
        b = parts_board()
        b["cards"][0]["labels"] = ["Bug", "Docs"]
        b["cards"][1]["labels"] = ["Bug"]
        page = self.start(b)
        self.settings(page, "labels")
        rows = page.locator(".label-row")
        rows.nth(1).locator(".l-name").fill("Documentation")
        rows.nth(2).locator(".l-color").select_option("violet")
        page.click("text=Add label")
        page.keyboard.type("Later")
        rows.nth(0).locator("text=Remove").click()
        page.wait_for_selector(".confirm:has-text('Bug is on 2 cards')")
        page.click(".confirm button.danger")
        self.save_settings(page)
        labels = self.app.board()["labels"]
        self.assertEqual([(lab["id"], lab["name"]) for lab in labels], [("docs", "Documentation"), ("quick", "Quick"), ("later", "Later")])
        self.assertEqual([lab["color"] for lab in labels[:2]], ["blue", "violet"])
        self.assertEqual(self.app.card("T-1")["labels"], ["docs"])
        self.assertEqual(self.app.card("T-2")["labels"], [])
        self.assertEqual(page.locator('.card[data-key="T-1"] .chip.label').all_inner_texts(), ["Documentation"])

    def test_properties_tab(self):
        b = parts_board()
        b["cards"][0]["properties"] = {"Points": 3, "Link": "https://example.test/"}
        page = self.start(b)
        self.settings(page, "properties")
        defs = page.locator(".prop-def")
        defs.nth(0).locator(".p-name").fill("Story points")
        defs.nth(1).locator(".choice-row >> nth=2").locator("button[aria-label='Move choice up']").click()
        defs.nth(1).locator(".choice-row >> nth=0").locator(".ch-name").fill("Lowest")
        defs.nth(6).locator("text=Remove").click()  # Owner: no card has a value, so no question
        defs.nth(4).locator("text=Remove").click()  # Link: one card has a value
        page.wait_for_selector(".confirm:has-text('Link has a value on 1 card')")
        page.click(".confirm button.danger")
        page.click("text=Add property")
        new = page.locator(".prop-def").last
        new.locator(".p-name").fill("Size")
        new.locator(".p-type").select_option("choice")
        new.locator(".ch-name").fill("S")
        new.locator("text=Add choice").click()
        page.keyboard.type("M")
        page.click("text=Add property")
        rated = page.locator(".prop-def").last
        rated.locator(".p-name").fill("Effort")
        rated.locator(".p-type").select_option("rating")
        rated.locator(".r-max").select_option("7")
        self.save_settings(page)
        props = {p["name"]: p for p in self.app.board()["properties"]}
        self.assertEqual(list(props), ["Story points", "Priority", "Platform", "Reviewed", "Confidence", "Size", "Effort"])
        self.assertEqual(props["Story points"]["id"], "points")
        self.assertEqual([c["name"] for c in props["Priority"]["choices"]], ["Lowest", "High", "Medium"])
        self.assertEqual([c["name"] for c in props["Size"]["choices"]], ["S", "M"])
        self.assertEqual(props["Effort"]["max"], 7)
        self.assertEqual(self.app.card("T-1")["properties"], {"points": 3})
        # A change the board can't make: the server's reason shows, and the form stays as it was.
        self.settings(page, "properties")
        page.locator(".prop-def >> nth=0").locator(".p-type").select_option("checkbox")
        page.click("dialog[open] button[type=submit]")
        page.wait_for_selector(".form-error:has-text(\"can't change from number to checkbox\")")
        self.assertEqual(page.locator(".prop-def >> nth=0").locator(".p-type").input_value(), "checkbox")
        self.assertEqual(self.app.board()["properties"][0]["type"], "number")

    def test_card_face_tab(self):
        b = parts_board()
        b["cards"][0].update(labels=["Bug"], properties={"Priority": "High"})
        page = self.start(b)
        self.assertEqual(page.locator('.card[data-key="T-1"] .chip.label').count(), 1)
        self.settings(page, "face")
        rows = page.locator(".face-row")
        rows.filter(has_text="Labels").locator("input").uncheck()
        priority = rows.filter(has_text="Priority")
        priority.locator("input").check()
        for _ in range(8):
            priority.locator("button[aria-label='Move it up']").click()
        self.save_settings(page)
        face = self.app.board()["settings"]["cardFace"]
        self.assertEqual(face[0], "priority")
        self.assertNotIn("labels", face)
        self.assertEqual(page.locator('.card[data-key="T-1"] .chip.label').count(), 0)
        self.assertTrue(page.locator('.card[data-key="T-1"] .meta > :first-child').inner_text().startswith("Priority"))
        # A property added in the same save can be put on the card face straight away.
        self.settings(page, "properties")
        page.click("text=Add property")
        page.locator(".prop-def").last.locator(".p-name").fill("Team")
        page.click("#tab-face")
        page.locator(".face-row").filter(has_text="Team").locator("input").check()
        self.save_settings(page)
        self.wait_until(lambda: "team" in self.app.board()["settings"]["cardFace"], "the new property on the card face")

    def test_a_full_column_refuses_where_the_card_was_going(self):
        b = parts_board()
        b["cards"][1]["column"] = "Doing"
        page = self.start(b)
        self.settings(page)
        page.check("input[name='limit-policy'][value='refuse']")
        self.save_settings(page)
        self.assertEqual(self.app.board()["settings"]["limitPolicy"], "refuse")
        page.drag_and_drop('.card[data-key="T-1"]', '[data-col="doing"]')
        page.wait_for_selector('[data-col="doing"] .col-note:has-text("Doing is full")')
        self.assertEqual(self.column_keys(page, "backlog"), ["T-1", "T-3"], "the card goes back")
        self.assertEqual(self.app.card("T-1")["column"], "backlog")
        self.open_card(page, "T-1")
        page.select_option("#card-column", "doing")
        page.wait_for_selector("#sheet .form-error:has-text('Doing is full')")
        self.until(page, "document.querySelector('#card-column').value === 'backlog'")
        self.close_sheet(page)
        page.click('[data-col="doing"] button[aria-label="Add a card to Doing"]')
        page.fill("dialog[open] input", "Keep this title")
        page.click("dialog[open] button[type=submit]")
        page.wait_for_selector(".form-error:has-text('Doing is full')")
        self.assertEqual(page.input_value("dialog[open] input"), "Keep this title")


class PartsHostile(PartsCase):
    def test_names_and_values_stay_text(self):
        b = parts_board()
        b["labels"] = [{"name": SHORT, "color": "rose"}, "Plain"]
        b["properties"] = [
            {"name": SHORT, "type": "choice", "choices": [SHORT, "Other"]},
            {"name": "Note", "type": "text"},
            {"name": "Link", "type": "url"},
            {"name": "Count", "type": "number", "unit": SHORT[:10]},
        ]
        b["settings"] = {"cardFace": ["labels", SHORT, "Note", "Link", "Count"]}
        b["cards"][0].update(labels=[SHORT], tasks=[HOSTILE], properties={
            SHORT: SHORT, "Note": HOSTILE, "Link": 'https://x.test/"><script>window.pwned=1</script>', "Count": 2})
        page = self.start(b, sample_board("Second"))
        self.assertIn(SHORT, page.locator('.card[data-key="T-1"] .meta').inner_text())
        page.click("#filters .label-filter >> nth=0")
        self.open_card(page, "T-1")
        page.click("#card-labels")
        page.click("#sheet >> text=Move to board")
        self.assertEqual(page.input_value("#prop-note"), HOSTILE)
        page.click("#sheet >> text=Edit")
        page.wait_for_selector("dialog[open] form")
        page.click("text=Cancel")
        self.close_sheet(page)
        self.settings(page)
        for tab in ("labels", "properties", "face"):
            page.click(f"#tab-{tab}")
        page.wait_for_timeout(300)
        self.assertIsNone(page.evaluate("window.pwned || null"))
        self.assertEqual(page.locator("#board img, #sheet img, #board script, #sheet script, #filters img").count(), 0)
        self.assertEqual(page.locator('a[href^="javascript"]').count(), 0)


class PartsSizes(PartsCase):
    def sheet_overflow(self, page):
        return page.evaluate("document.querySelector('#sheet').scrollWidth - document.querySelector('#sheet').clientWidth")

    def test_narrow_screens(self):
        long = "Unbroken" * 30
        b = parts_board()
        b["labels"].append({"name": long[:40], "color": "teal"})
        b["properties"].append({"name": long[:40], "type": "multi", "choices": [long[:40], "Short"]})
        b["cards"][0].update(labels=["Bug", long[:40]], tasks=[long[:300], "Short"],
                             properties={"Owner": long[:500], "Link": "https://example.test/" + long[:400], "Points": 12.5, long[:40]: long[:40]})
        b["settings"] = {"cardFace": ["labels", "Owner", "Link", "Points", long[:40], "progress"]}
        page = self.start(b, sample_board("Other"))
        for width, height in [(390, 844), (320, 568)]:
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(50)
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - window.innerWidth"), 0, f"board at {width} px")
            self.open_card(page, "T-1")
            page.click("#card-labels")
            page.click("#sheet >> text=Move to board")
            self.until(page, "!document.querySelector('#move-column').disabled")
            self.assertLessEqual(self.sheet_overflow(page), 0, f"card at {width} px")
            page.click("#sheet >> text=Edit")
            page.click("#form-labels")
            self.assertLessEqual(self.sheet_overflow(page), 0, f"form at {width} px")
            page.click("text=Cancel")
            self.close_sheet(page)
            self.settings(page)
            for tab in ("general", "labels", "properties", "face"):
                page.click(f"#tab-{tab}")
                self.assertLessEqual(self.sheet_overflow(page), 0, f"settings {tab} at {width} px")
            self.close_sheet(page)



def find_board(name="Parts", cards=8):
    """A board with labels, points, a priority, a blocked card and a card in Doing, to search and sort."""
    b = parts_board(name, cards=cards)
    b["cards"][0].update(labels=["Bug"], properties={"Points": 5, "Priority": "High"}, blocked="Waiting on design")
    b["cards"][1].update(labels=["Docs"], properties={"Points": 2}, title="Write the guide")
    b["cards"][2].update(labels=["Bug", "Quick"], properties={"Points": 8, "Priority": "Low"})
    b["cards"][3].update(column="Doing", title="Alpha")
    return b


class FindCase(PartsCase):
    def keys(self, page):
        return page.eval_on_selector_all("#board .card", "els => els.map(e => e.dataset.key)")

    def server_keys(self, board_id, query, sort=""):
        """The keys the server finds, asked directly, in the order it gives them."""
        return self.app.api("GET", f"api/boards/{board_id}/search?{urllib.parse.urlencode({'q': query, 'sort': sort})}")["keys"]

    def shows(self, page, keys, what="the board to show the search"):
        self.wait_until(lambda: self.keys(page) == keys, f"{what}: {keys}")

    def search(self, page, text, keys):
        """Type a search, and wait for the board to show exactly what the server finds for it."""
        page.fill("#search", text)
        self.assertEqual(self.server_keys(self.app.board()["id"], text), keys, f"the server's answer for {text}")
        self.shows(page, keys, text)


class Searching(FindCase):
    def test_the_search_language_from_the_box(self):
        page = self.start(find_board())
        self.search(page, "label:bug", ["T-1", "T-3"])
        self.assertIn("2 of 8", page.locator("#search-count").inner_text())
        self.search(page, "points:<5", ["T-2"])
        self.search(page, "-label:bug column:backlog", ["T-2", "T-5", "T-6", "T-7", "T-8"])
        self.search(page, 'title:"write the"', ["T-2"])
        self.search(page, "(label:docs OR is:blocked) priority:high", ["T-1"])
        self.search(page, "column:doing OR label:quick", ["T-3", "T-4"])
        self.search(page, "card 6", ["T-6"])  # plain words still find what they always did
        self.assertIn("card+6", page.evaluate("location.hash"), "the address keeps the search")

    def test_a_bad_search_says_where_and_keeps_the_last_result(self):
        page = self.start(find_board())
        self.search(page, "label:bug", ["T-1", "T-3"])
        page.fill("#search", "label:bug pionts:3")
        page.wait_for_selector("#search-error:not([hidden])")
        self.assertIn('no property called "pionts"', page.locator("#search-error").inner_text())
        self.assertEqual(page.locator("#search-error mark").inner_text(), "pionts:3", "the bad spot is marked")
        self.assertEqual(page.get_attribute("#search", "aria-invalid"), "true")
        self.assertEqual(self.keys(page), ["T-1", "T-3"], "the board keeps the last search that worked")
        page.click("#search-error .query-text")
        self.assertEqual(page.evaluate("[document.activeElement.id, document.activeElement.selectionStart]"), ["search", 10])
        page.fill("#search", "label:bug points:5")
        self.shows(page, ["T-1"])
        self.assertTrue(page.locator("#search-error").is_hidden())
        self.assertEqual(page.get_attribute("#search", "aria-invalid"), "false")

    def test_a_slow_answer_never_replaces_a_newer_one(self):
        self.app.seed(find_board())
        self.app.start()
        self.context.add_init_script("""
          const real = window.fetch;
          window.fetch = async (url, opts) => {
            if (String(url).includes("/search?") && String(url).includes("bug")) {
              await new Promise((r) => setTimeout(r, 1500));
              const answer = await real(url, { ...opts, signal: undefined });  // arrives even though it was given up on
              window.slowAnswered = true;
              return answer;
            }
            return real(url, opts);
          };""")
        page = self.open_page()
        page.fill("#search", "label:bug")
        page.wait_for_timeout(400)  # its request is on its way
        page.fill("#search", "label:docs")
        self.shows(page, ["T-2"])
        self.until(page, "window.slowAnswered === true", timeout=5)
        page.wait_for_timeout(200)
        self.assertEqual(self.keys(page), ["T-2"], "the older answer came last and was not drawn")
        self.assertIn("1 of 8", page.locator("#search-count").inner_text())

    def test_the_chips_write_their_part_of_the_search(self):
        page = self.start(find_board())
        page.click("#filters .label-filter:has-text('Bug')")
        self.assertEqual(page.input_value("#search"), "label:Bug")
        self.shows(page, ["T-1", "T-3"])
        self.assertEqual(page.get_attribute("#filters .label-filter:has-text('Bug')", "aria-pressed"), "true")
        page.click("#filters .label-filter:has-text('Docs')")
        self.assertEqual(page.input_value("#search"), "(label:Bug OR label:Docs)", "chips of one kind mean either")
        self.shows(page, ["T-1", "T-2", "T-3"])
        page.click("#filters .label-filter:has-text('Bug')")
        page.click("#filters >> text=Blocked")
        self.assertEqual(page.input_value("#search"), "label:Docs is:blocked", "different kinds narrow each other")
        self.shows(page, [])
        page.click("#filters .label-filter:has-text('Docs')")
        self.assertEqual(page.input_value("#search"), "is:blocked")
        self.shows(page, ["T-1"])
        page.fill("#search", "points:>1 label:quick")
        self.shows(page, ["T-3"])
        self.assertEqual(page.get_attribute("#filters .label-filter:has-text('Quick')", "aria-pressed"), "true", "typed terms press their chip")
        page.click("#new-card")
        page.wait_for_selector("dialog[open] form")
        self.assertIn("Quick", page.locator("#sheet").inner_text(), "a new card starts with the one label searched for")
        page.click("text=Cancel")
        self.until(page, "!document.querySelector('#sheet').open")
        page.click("#filters >> text=Show all")
        self.assertEqual(page.input_value("#search"), "")
        self.assertEqual(len(self.keys(page)), 8)

    def test_the_search_follows_changes_to_the_board(self):
        page = self.start(find_board())
        board_id = self.app.board()["id"]
        self.search(page, "column:doing", ["T-4"])
        self.app.api("PATCH", f"api/boards/{board_id}/cards/T-1", {"column": "doing"})  # from a script, or another tab
        self.poll(page)
        self.shows(page, ["T-1", "T-4"], "a card moved in elsewhere")
        self.open_card(page, "T-4")
        page.select_option("#card-column", "done")
        self.close_sheet(page)
        self.shows(page, ["T-1"], "a card moved out here")

    def test_search_every_board_then_go_to_a_card(self):
        page = self.start(find_board(), sample_board("Other"))
        self.assertTrue(page.locator("#search-all").is_hidden())
        page.fill("#search", "Card 3")
        page.click("#search-all")
        self.until(page, "document.querySelectorAll('.found-board').length === 2")
        text = page.locator("#sheet").inner_text()
        self.assertIn("Parts", text)
        self.assertIn("Other", text)
        page.click(".found-board:has-text('Other') .found-cards a")
        self.until(page, "document.querySelector('#board-name').textContent === 'Other'")
        page.wait_for_selector("#card-notes")
        self.assertEqual(page.locator("#sheet .key").inner_text(), "T-3")
        self.assertEqual(page.input_value("#search"), "Card 3", "the board opens with the same search")
        self.shows(page, ["T-3"])
        self.close_sheet(page)
        page.focus("#search")
        page.keyboard.press("Shift+Enter")  # the keyboard way in
        self.until(page, "document.querySelectorAll('.found-board').length === 2")
        page.fill("#everywhere-search", "pionts:3")
        page.wait_for_selector("#sheet .search-error mark")
        self.assertIn("pionts", page.locator("#sheet .search-error").inner_text())


class LargeSearch(FindCase):
    CARDS = 2000

    def test_searching_a_big_board_stays_quick(self):
        columns = ["Backlog", "Doing", "Done"]
        b = parts_board("Big", cards=0)
        b["columns"] = columns
        b["cards"] = [{"key": f"T-{i}", "title": f"Card {i}", "column": columns[i % 3], "rank": i,
                       "labels": ["Bug"] if i % 7 == 0 else [], "properties": {"Points": i % 10}} for i in range(1, self.CARDS + 1)]
        page = self.start(b)
        board_id = self.app.board()["id"]
        timings = {}
        for text in ("label:bug points:>=5", "Card 1999", "(label:bug OR points:9) -column:done"):
            want = self.server_keys(board_id, text)
            start = time.perf_counter()
            page.fill("#search", text)
            # A column draws 300 cards until asked for more, so compare the count and that nothing else is drawn.
            self.wait_until(lambda: page.locator("#search-count").inner_text().startswith(f"{len(want)} of")
                            and set(self.keys(page)) <= set(want), f"the answer to {text}", timeout=10)
            timings[text] = round(time.perf_counter() - start, 3)
        report = os.environ.get("LANEBOARD_SEARCH_REPORT")
        if report:
            with open(report, "w", encoding="utf-8") as f:
                json.dump({"cards": self.CARDS, "seconds": timings}, f, indent=1)
        for text, seconds in timings.items():
            self.assertLess(seconds, 3, f"{text} took {seconds} s on {self.CARDS} cards")


class Sorting(FindCase):
    def test_sort_by_a_property_and_a_part_both_ways(self):
        page = self.start(find_board())
        page.select_option("#sort-by", "points")
        self.shows(page, ["T-2", "T-1", "T-3", "T-5", "T-6", "T-7", "T-8", "T-4"])
        self.assertEqual(page.locator("#sort-dir").inner_text(), "Lowest first")
        page.click("#sort-dir")
        self.shows(page, ["T-3", "T-1", "T-2", "T-5", "T-6", "T-7", "T-8", "T-4"], "highest first, cards with no value still last")
        self.assertIn("sort=-points", page.evaluate("location.hash"))
        page.select_option("#sort-by", "title")
        self.shows(page, ["T-1", "T-3", "T-5", "T-6", "T-7", "T-8", "T-2", "T-4"])
        page.reload()
        self.shows(page, ["T-1", "T-3", "T-5", "T-6", "T-7", "T-8", "T-2", "T-4"], "the address keeps the sort")
        self.assertEqual(page.input_value("#sort-by"), "title")
        page.fill("#search", "label:bug")
        page.select_option("#sort-by", "points")
        page.click("#sort-dir")
        self.shows(page, ["T-3", "T-1"], "a search and a sort together")
        page.select_option("#sort-by", "")
        self.shows(page, ["T-1", "T-3"])
        self.assertNotIn("sort=", page.evaluate("location.hash"))
        self.assertTrue(page.locator("#sort-dir").is_hidden())

    def test_a_sorted_board_moves_cards_between_columns_only(self):
        page = self.start(find_board())
        page.select_option("#sort-by", "title")
        self.shows(page, ["T-1", "T-3", "T-5", "T-6", "T-7", "T-8", "T-2", "T-4"])
        self.assertTrue(page.locator("#sort-hint").is_visible())
        ranks = {c["key"]: c["rank"] for c in self.app.board()["cards"]}
        page.drag_and_drop('.card[data-key="T-2"]', '.card[data-key="T-1"]', target_position={"x": 20, "y": 2})
        page.wait_for_timeout(400)
        self.assertEqual({c["key"]: c["rank"] for c in self.app.board()["cards"]}, ranks, "no reordering by drag")
        page.focus('.card[data-key="T-3"]')
        page.keyboard.press("Shift+ArrowUp")
        self.assertIn("can't be moved up or down", self.toast(page))
        self.until(page, "document.querySelector('#live').textContent.includes(\"can't be moved up or down\")")
        page.wait_for_timeout(300)
        self.assertEqual({c["key"]: c["rank"] for c in self.app.board()["cards"]}, ranks, "nor by keyboard")
        page.keyboard.press("Shift+ArrowRight")
        self.wait_until(lambda: self.app.card("T-3")["column"] == "doing", "a move to the next column")
        page.drag_and_drop('.card[data-key="T-5"]', '[data-col="done"]')
        self.wait_until(lambda: self.app.card("T-5")["column"] == "done", "a drag to another column")
        self.open_card(page, "T-6")
        self.assertTrue(page.locator('#sheet button[aria-label="Move up"]').is_disabled())
        self.close_sheet(page)
        page.click("#sort-hint >> text=Use manual order")
        self.assertTrue(page.locator("#sort-hint").is_hidden())
        page.focus('.card[data-key="T-7"]')
        page.keyboard.press("Shift+ArrowUp")
        self.wait_until(lambda: self.column_keys(page, "backlog").index("T-7") < self.column_keys(page, "backlog").index("T-6"), "a move by hand again")


class SavedViewsPage(FindCase):
    def test_a_view_round_trip_through_reload(self):
        page = self.start(find_board())
        board_id = self.app.board()["id"]
        page.fill("#search", "label:bug")
        page.select_option("#sort-by", "points")
        page.click("#sort-dir")
        self.shows(page, ["T-3", "T-1"])
        page.click("#views-summary")
        page.click("#views >> text=Save view")
        page.fill("dialog[open] input[maxlength='60']", "Big bugs")
        page.check("dialog[open] input[type=checkbox]")
        page.click("dialog[open] button[type=submit]")
        self.until(page, "!document.querySelector('#sheet').open")
        saved = self.app.board()
        self.assertEqual([(v["name"], v["query"], v["sort"]) for v in saved["views"]], [("Big bugs", "label:bug", "-points")])
        self.assertEqual(saved["settings"]["defaultView"], saved["views"][0]["id"])
        self.assertIn("Big bugs", page.locator("#views-summary").inner_text())
        page.reload()
        self.shows(page, ["T-3", "T-1"], "the address reopens the view")
        self.assertEqual(page.input_value("#search"), "label:bug")
        self.assertEqual(page.locator("#sort-dir").inner_text(), "Highest first")
        page.locator("#search").press("Escape")
        self.assertIn("Big bugs (changed)", page.locator("#views-summary").inner_text())
        fresh = self.open_page(f"#{board_id}")  # no search in the address: the board's default view
        self.shows(fresh, ["T-3", "T-1"], "the default view")
        self.assertEqual(fresh.input_value("#search"), "label:bug")
        fresh.close()
        page.fill("#search", "label:bug -is:blocked")
        page.click("#views-summary")
        page.click("#views >> text=Update Big bugs to this search")
        self.wait_until(lambda: self.app.board()["views"][0]["query"] == "label:bug -is:blocked", "the view to update")
        self.assertNotIn("changed", page.locator("#views-summary").inner_text())
        page.click("#views-summary")
        page.click("#views >> text=Manage views")
        page.fill("dialog[open] .v-name", "Bugs by points")
        page.check("dialog[open] input[name=default-view][value='']")
        page.click("dialog[open] button[type=submit]")
        self.until(page, "!document.querySelector('#sheet').open")
        saved = self.app.board()
        self.assertEqual(saved["views"][0]["name"], "Bugs by points")
        self.assertIsNone(saved["settings"]["defaultView"])
        page.fill("#search", "")
        page.select_option("#sort-by", "")
        self.shows(page, ["T-1", "T-2", "T-3", "T-5", "T-6", "T-7", "T-8", "T-4"])
        page.focus('.card[data-key="T-1"]')
        page.keyboard.press("v")
        self.assertTrue(page.evaluate("document.activeElement.classList.contains('view-item')"), "v opens the views")
        page.keyboard.press("Enter")
        self.shows(page, ["T-3"], "picking a view")
        page.click("#views-summary")
        page.click("#views >> text=Manage views")
        page.click("dialog[open] .view-row >> text=Remove")
        page.click("dialog[open] button[type=submit]")
        self.until(page, "!document.querySelector('#sheet').open")
        self.assertEqual(self.app.board()["views"], [])
        self.assertIn("Views", page.locator("#views-summary").inner_text())

    def test_a_view_that_stopped_working_says_why_and_can_be_fixed(self):
        b = find_board()
        b["views"] = [{"name": "Pointed", "query": "points:>=3", "sort": ""}]
        self.app.seed(b)
        self.app.start()
        board = self.app.board()
        self.app.api("PATCH", f"api/boards/{board['id']}", {"properties": [p for p in board["properties"] if p["name"] != "Points"]})
        page = self.open_page()
        page.click("#views-summary")
        page.click("#views .view-item")
        page.wait_for_selector("#search-error:not([hidden])")
        self.assertIn('"points"', page.locator("#search-error").inner_text())
        page.click("#search-error >> text=Edit the view Pointed")
        page.wait_for_selector("dialog[open] .view-row .form-error mark")
        self.assertIn("doesn't work any more", page.locator("dialog[open] .view-row .form-error").inner_text())
        self.assertEqual(page.evaluate("document.activeElement.className"), "v-query")
        page.fill("dialog[open] .v-query", "label:bug")
        self.until(page, "!document.querySelector('dialog[open] .view-row mark')")
        page.click("dialog[open] button[type=submit]")
        self.until(page, "!document.querySelector('#sheet').open")
        self.assertEqual(self.app.board()["views"][0]["query"], "label:bug")
        page.click("#views-summary")
        page.click("#views .view-item")
        self.shows(page, ["T-1", "T-3"])


class FindSizes(FindCase):
    def sheet_overflow(self, page):
        return page.evaluate("document.querySelector('#sheet').scrollWidth - document.querySelector('#sheet').clientWidth")

    def test_narrow_screens(self):
        long = "Unbroken" * 30
        b = find_board()
        b["views"] = [{"name": long[:60], "query": f'title:"{long[:200]}"', "sort": "-points"}]
        page = self.start(b, sample_board("Other"))
        for width, height in [(390, 844), (320, 568)]:
            page.set_viewport_size({"width": width, "height": height})
            page.fill("#search", f"label:bug {long[:100]}:3")
            page.wait_for_selector("#search-error:not([hidden])")
            page.select_option("#sort-by", "title")
            page.click("#views-summary")
            page.wait_for_timeout(50)
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - window.innerWidth"), 0, f"board at {width} px")
            menu = page.evaluate("(() => { const r = document.querySelector('#view-items').getBoundingClientRect(); return [r.left, r.right]; })()")
            self.assertGreaterEqual(menu[0], 0, f"views menu at {width} px")
            self.assertLessEqual(menu[1], width, f"views menu at {width} px")
            page.click("#views >> text=Save view")
            self.assertLessEqual(self.sheet_overflow(page), 0, f"save view at {width} px")
            self.close_sheet(page)
            page.click("#views-summary")
            page.click("#views >> text=Manage views")
            page.wait_for_selector("dialog[open] .view-row")
            self.assertLessEqual(self.sheet_overflow(page), 0, f"manage views at {width} px")
            self.close_sheet(page)
            page.fill("#search", "card")
            page.click("#search-all")
            self.until(page, "document.querySelectorAll('.found-board').length === 2")
            self.assertLessEqual(self.sheet_overflow(page), 0, f"every board at {width} px")
            self.close_sheet(page)
            page.keyboard.press("?")
            page.wait_for_selector("#sheet >> text=Searching")
            self.assertLessEqual(self.sheet_overflow(page), 0, f"help at {width} px")
            self.close_sheet(page)
            page.select_option("#sort-by", "")


if __name__ == "__main__":
    unittest.main()
