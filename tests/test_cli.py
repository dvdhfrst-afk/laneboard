"""The command line, run the way a person runs it: as a separate program, watching output and exit codes."""
import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest import mock

from laneboard import __version__, cli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMANDS = ["serve", "boards", "cards", "add", "move", "archive", "show", "edit", "delete", "task", "transfer", "new",
            "labels", "properties", "views", "settings", "import", "export", "sample", "backups", "restore", "check"]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)  # a cleanup, so servers started by a test are stopped first
        self.data = os.path.join(self.tmp.name, "boards.json")

    def env(self, **extra):
        env = {**os.environ, "LANEBOARD_DATA": self.data, "PYTHONPATH": ROOT}
        env.pop("PYTHONIOENCODING", None)
        env.update(extra)
        return env

    def run_cli(self, *args, code=0, stdin=None, **env):
        result = subprocess.run([sys.executable, "-m", "laneboard", *args], capture_output=True, input=stdin,
                                env=self.env(**env), cwd=self.tmp.name, timeout=60)
        out, err = result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")
        self.assertEqual(result.returncode, code, f"{args}: {out}{err}")
        self.assertNotIn("Traceback", out + err, args)
        return out, err

    def add_sample(self):
        self.run_cli("sample", "garden")
        return "Garden project"


class HelpTest(CliCase):
    def test_version(self):
        out, _ = self.run_cli("--version")
        self.assertEqual(out.strip(), f"laneboard {__version__}")

    def test_every_command_has_help(self):
        out, _ = self.run_cli("--help")
        for name in COMMANDS:
            self.assertIn(name, out)
        for name in COMMANDS:
            out, _ = self.run_cli(name, "--help")
            self.assertIn("--data", out, name)
            self.assertIn("usage: laneboard " + name, out, name)

    def test_a_percent_in_the_data_path_does_not_break_help(self):
        out, _ = self.run_cli("--help", LANEBOARD_DATA=os.path.join(self.tmp.name, "100%x.json"))
        self.assertIn("usage", out)

    def test_bad_usage_exits_2(self):
        for args in (["nothing"], ["--port", "abc"], ["--port", "70000"], ["--port", "-1"], ["move", "only one"], ["cards"], ["restore"]):
            _, err = self.run_cli(*args, code=2)
            self.assertIn("usage", err)

    def test_data_goes_before_or_after_the_command(self):
        other = os.path.join(self.tmp.name, "other.json")
        self.run_cli("--data", other, "sample", "garden")
        self.assertTrue(os.path.exists(other))
        other2 = os.path.join(self.tmp.name, "other2.json")
        self.run_cli("sample", "garden", "--data", other2)
        self.assertTrue(os.path.exists(other2))
        self.assertFalse(os.path.exists(self.data))

    def test_an_empty_data_file_name_is_refused_rather_than_meaning_the_default(self):
        # A script that says --data "$FILE" with FILE unset must not quietly change the boards in the usual place.
        for args in (["--data", "", "sample", "garden"], ["sample", "garden", "--data", ""], ["--data=", "boards"]):
            _, err = self.run_cli(*args, code=2)
            self.assertIn("--data", err)
        self.assertFalse(os.path.exists(self.data))


class WorkflowTest(CliCase):
    def test_a_whole_session(self):
        out, _ = self.run_cli("boards")
        self.assertIn("No boards yet", out)
        self.assertFalse(os.path.exists(self.data), "looking doesn't create a file")
        out, _ = self.run_cli("add", "My board", "Buy stamps", "--summary", "A book of ten")
        self.assertEqual(out.strip(), "T-1")
        self.run_cli("add", "my BOARD", "Second", "--column", "Ready")
        out, _ = self.run_cli("cards", "My board")
        self.assertIn("T-1", out)
        self.assertIn("Ready", out)
        out, _ = self.run_cli("cards", "My board", "--column", "ready")
        self.assertNotIn("T-1", out)
        self.assertIn("T-2", out)
        out, _ = self.run_cli("move", "My board", "T-1", "Done")
        self.assertIn("Moved T-1 to Done", out)
        self.run_cli("archive", "My board", "T-1")
        out, _ = self.run_cli("boards")
        self.assertIn("1 card, 1 archived", out)
        out, _ = self.run_cli("cards", "My board", "--archived", "--json")
        self.assertEqual(json.loads(out)[0]["key"], "T-1")
        self.run_cli("archive", "My board", "T-1", "--restore")
        out, _ = self.run_cli("cards", "My board")
        self.assertIn("T-1", out)
        out, _ = self.run_cli("boards", "--json")
        self.assertEqual(json.loads(out)[0]["cards"], 2)

    def test_export_then_import(self):
        name = self.add_sample()
        target = os.path.join(self.tmp.name, "garden-export.json")
        out, _ = self.run_cli("export", name, "-o", target)
        self.assertIn("Wrote", out)
        self.run_cli("export", name, "-o", target, code=1)
        self.run_cli("export", name, "-o", target, "--force")
        out, _ = self.run_cli("import", target)
        self.assertIn(f"Imported {name} with 5 cards", out)
        out, _ = self.run_cli("export", "--all")
        self.assertEqual(len(json.loads(out)["boards"]), 2)

    def test_import_from_standard_input_and_whole_files(self):
        board = json.dumps({"name": "Piped", "cards": [{"title": "One"}]}).encode()
        out, _ = self.run_cli("import", "-", stdin=board)
        self.assertIn("Imported Piped with 1 card as", out)
        whole = os.path.join(self.tmp.name, "whole.json")
        with open(whole, "w", encoding="utf-8") as f:
            f.write(self.run_cli("export", "--all")[0])
        out, _ = self.run_cli("import", whole)
        self.assertEqual(out.count("Imported"), 1)

    def test_samples(self):
        out, _ = self.run_cli("sample")
        self.assertIn("garden", out)
        self.assertIn("software-release", out)
        out, _ = self.run_cli("sample", "software-release")
        self.assertIn("Added Software release", out)
        out, err = self.run_cli("sample", "nope", code=1)
        self.assertIn("examples are", err)

    def test_backups_and_restore(self):
        out, _ = self.run_cli("backups")
        self.assertIn("No backups", out)
        self.run_cli("add", "My board", "One")
        self.run_cli("add", "My board", "Two")
        out, _ = self.run_cli("backups")
        self.assertIn("previous", out)
        self.assertIn("1 board, 1 card", out)
        out, _ = self.run_cli("restore", "previous")
        self.assertIn("1 board, 1 card", out)
        out, _ = self.run_cli("cards", "My board")
        self.assertNotIn("T-2", out)
        _, err = self.run_cli("restore", "nothing", code=1)
        self.assertIn("no backup called", err)

    def test_check(self):
        out, _ = self.run_cli("check")
        self.assertIn("doesn't exist yet", out)
        self.assertFalse(os.path.exists(self.data))
        self.run_cli("sample", "garden")
        out, _ = self.run_cli("check")
        self.assertIn("OK", out)
        with open(self.data, "w", encoding="utf-8") as f:
            f.write('{"version": 2, "boards": [{"id": "x"}]}')
        _, err = self.run_cli("check", code=1)
        self.assertIn("needs a text name", err)


class FailureTest(CliCase):
    def test_user_errors_are_plain_messages_with_exit_1(self):
        self.run_cli("boards")
        cases = [
            (["export", "No such board"], "no board called"),
            (["export"], "Name one board"),
            (["export", "My board", "--all"], "Name one board"),
            (["cards", "No such"], "no board called"),
            (["add", "My board", ""], "Title can't be empty"),
            (["add", "My board", "x", "--column", "Nope"], "no column called"),
            (["add", "My board", "x", "--group", "Nope"], "no group called"),
            (["move", "My board", "T-99", "Done"], "no card T-99"),
            (["archive", "My board", "T-99"], "no card T-99"),
            (["import", "missing.json"], "Can't read missing.json"),
        ]
        for args, phrase in cases:
            _, err = self.run_cli(*args, code=1)
            self.assertIn(phrase, err, args)
            self.assertTrue(err.startswith("laneboard: "), err)

    def test_bad_import_files(self):
        self.run_cli("boards")
        bad = os.path.join(self.tmp.name, "bad.json")
        for content, phrase, given in ((b"{nope", "isn't valid JSON", ()), (b"", "is empty", ()), (b"\x00\xff\x89", "UTF-8", ()),
                                       (b"[1]", "can't tell what that JSON file is", ()),
                                       (b'{"cards": []}', "can't tell what that JSON file is", ()),
                                       (b'{"cards": []}', "Board name", ("--format", "laneboard"))):
            with open(bad, "wb") as f:
                f.write(content)
            _, err = self.run_cli("import", bad, *given, code=1)
            self.assertIn(phrase, err)

    def test_a_broken_data_file_is_explained_and_left_alone(self):
        with open(self.data, "w", encoding="utf-8") as f:
            f.write("{ broken")
        for args in (["boards"], ["cards", "x"], ["add", "x", "y"], ["serve", "--port", "0"]):
            _, err = self.run_cli(*args, code=1)
            self.assertIn("isn't valid JSON", err)
            self.assertIn("backups", err)
        with open(self.data, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{ broken")

    def test_a_folder_as_the_data_file(self):
        for args in (["boards"], ["serve", "--port", "0"]):
            _, err = self.run_cli(*args, code=1, LANEBOARD_DATA=self.tmp.name)
            self.assertIn("is a folder", err)

    def test_two_boards_with_the_same_name_ask_for_an_id(self):
        self.run_cli("import", "-", stdin=b'{"name": "My board"}')
        self.run_cli("import", "-", stdin=b'{"name": "My board"}')
        _, err = self.run_cli("cards", "My board", code=1)
        self.assertIn("Use one of these ids", err)

    def test_non_ascii_in_any_console_encoding(self):
        title = "café 日本語 \U0001f600"
        self.run_cli("import", "-", stdin=json.dumps({"name": title, "cards": [{"title": title}]}).encode())
        for encoding in ("cp1252", "ascii", "utf-8"):
            out, _ = self.run_cli("boards", PYTHONIOENCODING=encoding)
            self.assertIn("caf", out)
            out, _ = self.run_cli("cards", title, PYTHONIOENCODING="utf-8")
            self.assertIn(title, out)
            out, _ = self.run_cli("export", title, PYTHONIOENCODING=encoding)
            self.assertEqual(json.loads(out)["name"], title, encoding)

    def test_powershell_redirection_can_be_imported(self):
        board = {"name": "Redirected é", "cards": [{"title": "One"}]}
        path = os.path.join(self.tmp.name, "utf16.json")
        with open(path, "wb") as f:
            f.write(b"\xff\xfe" + json.dumps(board, ensure_ascii=False).encode("utf-16-le"))
        out, _ = self.run_cli("import", path)
        self.assertIn("Redirected", out)

    def test_a_closed_pipe_is_quiet(self):
        if os.name == "nt":
            self.skipTest("closing a pipe early needs POSIX semantics")
        self.run_cli("import", "-", stdin=json.dumps({"name": "Big", "cards": [{"title": "x" * 150} for _ in range(2000)]}).encode())
        proc = subprocess.Popen([sys.executable, "-m", "laneboard", "export", "Big"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=self.env(), cwd=self.tmp.name)
        proc.stdout.read(100)
        proc.stdout.close()
        err = proc.stderr.read().decode()
        proc.wait(timeout=30)
        proc.stderr.close()
        self.assertEqual(err, "")


class ServeTest(CliCase):
    def start(self, *args):
        proc = subprocess.Popen([sys.executable, "-u", "-m", "laneboard", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=self.env(), cwd=self.tmp.name)
        self.addCleanup(self.stop, proc)
        return proc

    @staticmethod
    def stop(proc):
        if proc.poll() is None:
            proc.kill()
        proc.communicate()

    def wait_up(self, port):
        for _ in range(100):
            if cli.running_version(port):
                return
            time.sleep(0.05)
        self.fail("the server never came up")

    def test_the_startup_line_names_the_real_port_and_file(self):
        proc = self.start("--port", "0")
        line = proc.stdout.readline().decode()
        self.assertIn(f"Laneboard {__version__} at http://127.0.0.1:", line)
        self.assertIn(self.data, line)
        self.assertNotIn(":0/", line)
        port = int(line.split("127.0.0.1:")[1].split("/")[0])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            self.assertIn(b"<title>Laneboard</title>", r.read())

    def test_a_second_run_finds_the_first(self):
        port = free_port()
        self.start("--port", str(port))
        self.wait_up(port)
        out, _ = self.run_cli("--port", str(port))
        self.assertIn(f"already running at http://127.0.0.1:{port}/", out)

    def test_a_port_taken_by_something_else_is_explained(self):
        with socket.socket() as other:
            other.bind(("127.0.0.1", 0))
            other.listen(1)
            port = other.getsockname()[1]
            _, err = self.run_cli("--port", str(port), code=1)
        self.assertIn(f"Can't use port {port}", err)
        self.assertIn("--port", err)

    def test_the_command_line_can_change_data_while_the_server_runs(self):
        proc = self.start("--port", "0")
        line = proc.stdout.readline().decode()
        port = int(line.split("127.0.0.1:")[1].split("/")[0])
        out, _ = self.run_cli("add", "My board", "From the command line")
        self.assertEqual(out.strip(), "T-1")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/boards") as r:
            listed = json.load(r)["boards"]
        self.assertEqual(listed[0]["cards"], 1)
        self.assertGreater(listed[0]["rev"], 1)


class NoConsoleTest(CliCase):
    """pythonw and desktop shortcuts have no console: sys.stdout and sys.stderr are None."""

    def run_without_console(self, *args):
        dialogs = []
        with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None), \
                mock.patch.dict(os.environ, {"LANEBOARD_DATA": self.data}):
            if os.name == "nt":
                with mock.patch("ctypes.windll.user32.MessageBoxW", lambda _w, text, _t, _f: dialogs.append(text)):
                    code = cli.main(list(args))
            else:
                code = cli.main(list(args))
        return code, dialogs

    def test_a_broken_file_shows_a_dialog_and_exits(self):
        with open(self.data, "w", encoding="utf-8") as f:
            f.write("{ broken")
        code, dialogs = self.run_without_console("serve", "--port", "0")
        self.assertEqual(code, 1)
        if os.name == "nt":
            self.assertIn("isn't valid JSON", dialogs[0])

    def test_bad_usage_does_not_crash(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_without_console("nothing")
        self.assertEqual(caught.exception.code, 2)

    def test_quiet_commands_work(self):
        code, _ = self.run_without_console("boards")
        self.assertEqual(code, 0)


class InProcessTest(CliCase):
    def test_main_returns_the_exit_code(self):
        with mock.patch.dict(os.environ, {"LANEBOARD_DATA": self.data}):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(cli.main(["boards", "--json"]), 0)
            self.assertEqual(json.loads(buffer.getvalue()), [])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["cards", "Nope"]), 1)


NO_CONSOLE = """
import json, os, sys, threading, time, urllib.error, urllib.request
port, result, data = sys.argv[1:4]
def probe():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = "http://127.0.0.1:" + port
    seen = []
    time.sleep(1)
    seen.append(opener.open(base + "/api/health", timeout=5).status)
    seen.append(opener.open(base + "/", timeout=5).status)
    request = urllib.request.Request(base + "/api/boards", method="POST", data=b'{"name": "x"}',
                                     headers={"Content-Type": "application/json"})
    seen.append(opener.open(request, timeout=5).status)
    try:
        opener.open(base + "/nope", timeout=5)
    except urllib.error.HTTPError as e:
        seen.append(e.code)
    with open(result, "w") as f:
        json.dump(seen, f)
    os._exit(0)
threading.Thread(target=probe, daemon=True).start()
sys.stdout = sys.stderr = None  # what pythonw gives a program started from a desktop shortcut
from laneboard.cli import main
main(["--port", port, "--data", data, "--verbose"])
"""


class NoConsole(CliCase):
    def test_serving_and_logging_with_nowhere_to_print(self):
        result = os.path.join(self.tmp.name, "result.json")
        subprocess.run([sys.executable, "-c", NO_CONSOLE, str(free_port()), result, self.data],
                       env=self.env(), cwd=self.tmp.name, timeout=30, capture_output=True)
        with open(result, encoding="utf-8") as f:
            self.assertEqual(json.load(f), [200, 200, 201, 404])


class NewFile(CliCase):
    def test_adding_first_leaves_no_empty_starter_board(self):
        self.run_cli("sample", "garden")
        with open(self.data, encoding="utf-8") as f:
            self.assertEqual([b["name"] for b in json.load(f)["boards"]], ["Garden project"])

    def test_a_failed_import_into_nothing_writes_nothing(self):
        bad = os.path.join(self.tmp.name, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{ nope")
        self.run_cli("import", bad, code=1)
        self.assertFalse(os.path.exists(self.data))


if __name__ == "__main__":
    unittest.main()
