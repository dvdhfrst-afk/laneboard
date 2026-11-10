"""The page's modules fit together without a browser: index.html loads one module, every import names a file the
server serves and something that file exports, and every module is reached from the first one."""

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "laneboard" / "static"
SERVED = r"[a-z0-9][a-z0-9_-]*\.[a-z0-9]+"  # the names the server will hand out, as in server.py
IMPORT = re.compile(r'import \{([^}]*)\} from "\./([^"]*)";')
EXPORT = re.compile(r"^export (?:async )?(?:function\*?|class|const|let)\s+([A-Za-z_$][\w$]*)", re.M)


def modules():
    return {path.name: path.read_text(encoding="utf-8") for path in STATIC.glob("*.js")}


def imports(text):
    """[(names, module)] for each import, failing on any other form of import."""
    found = []
    for start in re.finditer(r"^import\b", text, re.M):
        m = IMPORT.match(text, start.start())
        if not m:
            raise AssertionError(f"Import other than `import {{ ... }} from \"./name.js\";`: {text[start.start():][:80]}")
        found.append(([n.strip() for n in m.group(1).split(",") if n.strip()], m.group(2)))
    return found


class Modules(unittest.TestCase):
    def test_the_page_loads_main_as_a_module(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"<script\b[^>]*>", html), ['<script type="module" src="/static/main.js">'])

    def test_every_import_resolves(self):
        mods = modules()
        exported = {name: set(EXPORT.findall(text)) for name, text in mods.items()}
        for name, text in mods.items():
            self.assertRegex(name, rf"\A{SERVED}\Z")
            for names, target in imports(text):
                self.assertRegex(target, rf"\A{SERVED}\Z", f"{name} imports {target}")
                self.assertIn(target, mods, f"{name} imports {target}")
                self.assertNotEqual(target, "main.js", f"{name} imports the page's first module")
                for n in names:
                    self.assertIn(n, exported[target], f"{name} imports {n} from {target}, which doesn't export it")
                self.assertEqual(len(names), len(set(names)), f"{name} imports a name twice from {target}")

    def test_every_module_is_used(self):
        mods = modules()
        seen, todo = set(), ["main.js"]
        while todo:
            name = todo.pop()
            if name not in seen:
                seen.add(name)
                todo += [target for _, target in imports(mods[name])]
        self.assertEqual(sorted(set(mods) - seen), [])


if __name__ == "__main__":
    unittest.main()
