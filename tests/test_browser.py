"""Drive the real page in headless Chrome against a real server on a temporary file.

Laneboard itself needs nothing beyond Python, and neither does the rest of the test suite. These
tests need Playwright and skip without it:

    python -m pip install playwright
    python -m playwright install chromium
"""

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
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
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
        self.assertEqual(self.column_keys(page, "backlog"), ["T-1"])
        page.click("#filters >> text=Show all")
        page.click("#filters >> text=Blocked")
        self.assertEqual(self.column_keys(page, "backlog"), ["T-2"])

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


if __name__ == "__main__":
    unittest.main()
