"""Static safety scan of the browser scripts (compliance guarantee).

Enforces the "read-only, zero security risk" contract without running the scripts:
- no network/exfiltration or credential/storage APIs,
- no navigation side effects,
- no obfuscated/indirect click forms (only literal `<ident>.click(`),
- click callers restricted (export -> {a, b}, inspector -> {a}),
- `a` is a local Blob/ObjectURL download anchor; the export's `b` targets Fidelity's own
  posweb expander buttons.
"""
import os
import re
import shutil
import subprocess
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BROWSER = os.path.join(REPO, "scripts", "browser")
EXPORT = os.path.join(BROWSER, "fidelity_lot_export.js")
INSPECTOR = os.path.join(BROWSER, "fidelity_dom_inspector.js")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def strip_comments(src):
    """Remove JS block and line comments so the scan checks executable code, not the
    safety documentation (which intentionally names the APIs the scripts avoid)."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"//[^\n]*", " ", src)
    return src


def code_of(path):
    return strip_comments(read(path))


# Network / exfiltration / credential + storage APIs — banned in both scripts.
BANNED = [
    r"\bfetch\s*\(",
    r"\bXMLHttpRequest\b",
    r"\bWebSocket\b",
    r"\bEventSource\b",
    r"\bsendBeacon\b",
    r"\bnew\s+Image\b",
    r"\bimport\s*\(",            # dynamic import()
    r"\bdocument\s*\.\s*cookie\b",
    r"\blocalStorage\b",
    r"\bsessionStorage\b",
]

# Navigation side effects (assignment/calls only — reading location.href for a report is allowed).
NAV = [
    r"\bwindow\s*\.\s*open\s*\(",
    r"\blocation\s*\.\s*assign\s*\(",
    r"\blocation\s*\.\s*replace\s*\(",
    r"\blocation\s*\.\s*href\s*=",
    r"(?<![.\w=!<>])location\s*=(?!=)",
    r"\.submit\s*\(",
]

# Obfuscated / indirect click forms — must not appear at all.
OBFUSCATED_CLICK = [
    r"\?\.\s*click\s*\(",
    r"\[\s*['\"]click['\"]\s*\]",
    r"\bdispatchEvent\s*\(",
    r"\.click\s*\.\s*call\b",
    r"\.click\s*\.\s*apply\b",
    r"\bprototype\s*\.\s*click\b",
]

# The only permitted click invocation form: <ident>.click(
CLICK_CALL = re.compile(r"(?<![\w$])([A-Za-z_$][\w$]*)\s*\.\s*click\s*\(")


class BrowserSafety(unittest.TestCase):
    def _scan(self, path, allowed_click_idents):
        src = code_of(path)
        name = os.path.basename(path)
        for pat in BANNED:
            self.assertIsNone(re.search(pat, src), f"{name}: banned API /{pat}/ present")
        for pat in NAV:
            self.assertIsNone(re.search(pat, src), f"{name}: navigation side-effect /{pat}/ present")
        for pat in OBFUSCATED_CLICK:
            self.assertIsNone(re.search(pat, src), f"{name}: obfuscated click form /{pat}/ present")
        idents = set(CLICK_CALL.findall(src))
        self.assertTrue(idents, f"{name}: expected at least one <ident>.click()")
        self.assertTrue(
            idents.issubset(allowed_click_idents),
            f"{name}: click callers {sorted(idents)} not subset of {sorted(allowed_click_idents)}",
        )
        # Every href assignment must be a local blob/object URL (no external navigation/exfil),
        # and there must be no element `.src` load. Then bind the clicked anchor `a` to a
        # createElement('a') + blob-URL + download (so `a.click()` can only save a local file).
        for m in re.finditer(r"(\w+)\.href\s*=\s*([^\n;]+)", src):
            self.assertIn("URL.createObjectURL", m.group(2),
                          f"{name}: non-blob href assignment: {m.group(0).strip()!r}")
        self.assertIsNone(re.search(r"\.src\s*=", src), f"{name}: element .src assignment present")
        self.assertRegex(src, r"\ba\s*=\s*document\.createElement\(\s*['\"]a['\"]\s*\)",
                         f"{name}: download anchor 'a' not created via document.createElement('a')")
        self.assertRegex(src, r"\ba\.href\s*=\s*URL\.createObjectURL\(",
                         f"{name}: a.href not assigned from URL.createObjectURL")
        self.assertRegex(src, r"\ba\.download\s*=", f"{name}: a.download not set")

    def test_files_exist(self):
        self.assertTrue(os.path.isfile(EXPORT), EXPORT)
        self.assertTrue(os.path.isfile(INSPECTOR), INSPECTOR)

    def test_export_is_safe(self):
        self._scan(EXPORT, {"a", "b"})
        src = code_of(EXPORT)
        self.assertIn('button.posweb-cell-symbol-name[aria-expanded="false"]', src)
        self.assertIn('button.posweb-cell-symbol-name[aria-expanded="true"]', src)

    def test_inspector_is_safe(self):
        self._scan(INSPECTOR, {"a"})

    def test_node_check_if_available(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not on PATH")
        for path in (EXPORT, INSPECTOR):
            res = subprocess.run([node, "--check", path], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"node --check failed for {path}: {res.stderr}")


if __name__ == "__main__":
    unittest.main()
