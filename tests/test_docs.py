"""Keep the docs honest: versions agree, documented commands run, and the text follows the house rules."""

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from laneboard import __version__
from laneboard.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
LONG_DASHES = {chr(0x2013), chr(0x2014)}
DOCS = ["README.md", "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md", "docs/api.md", "docs/examples.md",
        "docs/file-format.md", "docs/testing.md"]


def blocks(path, kinds=("bash",)):
    text = (ROOT / path).read_text(encoding="utf-8")
    return [m.group(2).strip() for m in re.finditer(r"^```(\w*)\n(.*?)^```", text, re.M | re.S) if m.group(1) in kinds]


def repo_text_files():
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "build", "dist") and not d.endswith((".egg-info", ".venv"))]
        for name in files:
            if name.rsplit(".", 1)[-1] in ("py", "md", "js", "css", "html", "json", "toml", "in", "svg", "txt") or name in ("LICENSE", ".gitignore"):
                yield Path(folder) / name


class Versions(unittest.TestCase):
    def test_the_page_and_program_agree(self):
        found = [version for path in sorted((ROOT / "laneboard" / "static").glob("*.js"))
                 for version in re.findall(r'^(?:export )?const APP_VERSION = "([^"]*)";$', path.read_text(encoding="utf-8"), re.M)]
        self.assertEqual(found, [__version__], "the page defines APP_VERSION once, matching the program")

    def test_the_changelog_covers_this_version(self):
        self.assertIn(f"## {__version__}\n", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))

    def test_the_readme_start_line_shows_this_version(self):
        self.assertIn(f"Laneboard {__version__} at http://127.0.0.1:8642/", (ROOT / "README.md").read_text(encoding="utf-8"))


class HouseRules(unittest.TestCase):
    def test_no_long_dashes_anywhere(self):
        for path in repo_text_files():
            text = path.read_text(encoding="utf-8")
            self.assertFalse(LONG_DASHES & set(text), path)

    def test_documented_files_exist(self):
        for doc in DOCS:
            self.assertTrue((ROOT / doc).exists(), doc)
            text = (ROOT / doc).read_text(encoding="utf-8")
            for link in re.findall(r"\]\(([^)#\s]+)\)", text):
                if not link.startswith("http"):
                    self.assertTrue(((ROOT / doc).parent / link).exists(), f"{doc} links to {link}")

    def test_every_command_is_in_the_readme(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        parser = build_parser()
        commands = next(a for a in parser._actions if a.dest == "command").choices
        for name in commands:
            self.assertRegex(readme, rf"`laneboard {name}\b|or `laneboard {name}`", name)


class ExamplesRun(unittest.TestCase):
    """Run the worked examples in order, against a temporary file, as a reader would."""

    def run_line(self, line, cwd, env):
        words = shlex.split(line)
        self.assertEqual(words[0], "laneboard", line)
        done = subprocess.run([sys.executable, "-m", "laneboard", *words[1:]], cwd=cwd, env=env, capture_output=True, text=True,
                              encoding="utf-8", timeout=60)
        self.assertEqual(done.returncode, 0, f"{line}\n{done.stdout}{done.stderr}")
        return done.stdout

    def test_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "LANEBOARD_DATA": os.path.join(tmp, "try.json"), "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
            with open(os.path.join(tmp, "moving.json"), "w", encoding="utf-8") as f:
                f.write(blocks("docs/examples.md", ("json",))[0])
            outputs = [(line, self.run_line(line, tmp, env)) for line in blocks("docs/examples.md")]
            found = dict(outputs)
            self.assertEqual(found['laneboard add "Software release" "Write the upgrade notes" --column "To do" '
                                   '--summary "What changes for people upgrading."'].strip(), "REL-3")
            shown = blocks("docs/examples.md", ("text",))
            self.assertEqual(found["laneboard sample"].strip(), shown[1])
            self.assertEqual(found['laneboard cards "Software release"'].strip(), shown[2])
            self.assertEqual(found['laneboard properties "Software release"'].strip(), shown[4])
            self.assertEqual(found['laneboard task "Software release" REL-3 --done 1'].strip(), shown[5])
            card = found['laneboard show "Software release" REL-3'].strip().splitlines()
            self.assertEqual(card[:len(shown[6].splitlines())], shown[6].splitlines())
            self.assertEqual(found['laneboard transfer "Software release" REL-3 "Next release" --column Ready'].strip(), shown[7])

    def test_readme_commands_that_do_not_start_a_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "LANEBOARD_DATA": os.path.join(tmp, "try.json"), "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
            lines = [b for b in blocks("README.md") if b.startswith("laneboard") and "--open" not in b]
            self.assertGreaterEqual(len(lines), 3)
            self.run_line("laneboard sample software-release", tmp, env)  # so there is a previous version to go back to
            for line in lines:
                self.run_line(line, tmp, env)

    def test_the_minimal_board_in_the_format_doc_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "LANEBOARD_DATA": os.path.join(tmp, "try.json"), "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
            with open(os.path.join(tmp, "moving.json"), "w", encoding="utf-8") as f:
                f.write(blocks("docs/file-format.md", ("json",))[1])
            self.assertIn("Moving house", self.run_line("laneboard import moving.json", tmp, env))

    def test_the_full_import_in_the_format_doc_imports_as_described(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "LANEBOARD_DATA": os.path.join(tmp, "try.json"), "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
            with open(os.path.join(tmp, "garden.json"), "w", encoding="utf-8") as f:
                f.write(blocks("docs/file-format.md", ("json",))[2])
            self.assertIn("Garden", self.run_line("laneboard import garden.json", tmp, env))
            shown = self.run_line("laneboard show Garden G-1", tmp, env)
            for line in ("Labels: Outside, Seeds", "Hours: 1.5 h", "Season: Spring", "  1. [ ] Soak the seeds", "  2. [x] Dig the bed"):
                self.assertIn(line, shown.splitlines())


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class Clean(unittest.TestCase):
    def test_no_data_files_are_tracked(self):
        if not (ROOT / ".git").exists():
            self.skipTest("Not a git checkout")
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True).stdout.split("\n")
        self.assertFalse([t for t in tracked if t.endswith(("boards.json", ".bak", ".lock"))])


if __name__ == "__main__":
    unittest.main()
