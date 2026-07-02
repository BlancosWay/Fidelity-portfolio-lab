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
    """String/template/regex-aware removal of JS line (//) and block (/* */) comments.

    Uses a stack-based tokenizer so it is correct under arbitrary nesting of template literals and
    `${...}` interpolations: comment delimiters inside any string/template/regex literal are
    preserved, while `${...}` interpolation code (at any nesting depth) is still scanned. Only genuine
    comments are removed. It "fails closed": a mis-classification can only *preserve* literal text for
    scanning, never drop real code.
    """
    out = []
    i, n = 0, len(src)
    prev = ""       # last significant output char (regex-vs-division heuristic)
    prev_word = ""  # last identifier/keyword emitted (so `return /re/` is seen as a regex)
    stack = [{"kind": "code", "depth": 0, "interp": False}]

    # Keywords after which a `/` begins a regex literal, not division.
    regex_keywords = {"return", "typeof", "instanceof", "in", "of", "new", "delete",
                      "void", "throw", "do", "else", "yield", "await", "case"}

    def regex_allowed(p, pw):
        return p == "" or p in "(,=:[!&|?{;}+-*/%<>~^" or pw in regex_keywords

    def read_quote(q):
        nonlocal i
        out.append(src[i]); i += 1
        while i < n:
            ch = src[i]; out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(src[i + 1]); i += 2; continue
            i += 1
            if ch == q:
                break

    while i < n:
        frame = stack[-1]
        c = src[i]
        d = src[i + 1] if i + 1 < n else ""

        if frame["kind"] == "tmpl":                    # inside a template literal
            if c == "\\" and i + 1 < n:
                out.append(c); out.append(d); i += 2; continue
            if c == "`":
                out.append(c); i += 1; stack.pop(); prev = "`"; prev_word = ""; continue
            if c == "$" and d == "{":
                out.append("${"); i += 2
                stack.append({"kind": "code", "depth": 0, "interp": True})
                prev = ""; prev_word = ""
                continue
            out.append(c); i += 1
            continue

        # code frame
        if c == "/" and d == "/":                      # line comment
            i += 2
            while i < n and src[i] != "\n":
                i += 1
            out.append(" "); continue
        if c == "/" and d == "*":                      # block comment
            i += 2
            while i < n and not (src[i] == "*" and src[i + 1:i + 2] == "/"):
                i += 1
            i += 2; out.append(" "); continue
        if c in "\"'":                                  # string literal
            read_quote(c); prev = c; prev_word = ""; continue
        if c == "`":                                    # open a template literal
            out.append(c); i += 1
            stack.append({"kind": "tmpl", "depth": 0, "interp": False})
            prev_word = ""
            continue
        if c == "/" and regex_allowed(prev, prev_word):  # regex literal
            out.append(c); i += 1
            in_class = False
            while i < n:
                ch = src[i]; out.append(ch)
                if ch == "\\" and i + 1 < n:
                    out.append(src[i + 1]); i += 2; continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    i += 1; break
                i += 1
            while i < n and src[i].isalpha():
                out.append(src[i]); i += 1
            prev = "/"; prev_word = ""; continue
        if c.isalpha() or c in "_$":                    # identifier / keyword
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            word = src[i:j]
            out.append(word); prev_word = word; prev = word[-1]; i = j; continue
        if c == "{":
            frame["depth"] += 1; out.append(c); prev = "{"; prev_word = ""; i += 1; continue
        if c == "}":
            if frame["interp"] and frame["depth"] == 0:
                out.append("}"); i += 1; stack.pop(); prev = "}"; prev_word = ""; continue
            if frame["depth"] > 0:
                frame["depth"] -= 1
            out.append(c); prev = "}"; prev_word = ""; i += 1; continue
        out.append(c)
        if not c.isspace():
            prev = c; prev_word = ""
        i += 1
    return "".join(out)


def code_of(path):
    return strip_comments(read(path))


def safeclick_body(src):
    """Return the brace-matched body of the `safeClick = el => { ... }` helper, or None.
    Operates on comment-stripped source; the helper body contains no `{`/`}` inside strings/regex."""
    m = re.search(r"safeClick\s*=\s*el\s*=>\s*\{", src)
    if not m:
        return None
    start = m.end() - 1  # index of the opening brace
    depth = 0
    for j in range(start, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    return None


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
# The `.click(` site itself (whitespace-tolerant, matching CLICK_CALL) for offset/containment checks.
CLICK_SITE = re.compile(r"\.\s*click\s*\(")

# Selecting an anchor/link is banned. The only anchor in these scripts is the createElement('a')
# download link; there is never a reason to *query* for an <a>/link. Banning link selection is
# defence in depth on top of the runtime safeClick() guard, closing the "click a link to navigate"
# vector regardless of which identifier holds the element or which quote style selects it.
_Q = "['\"`]"        # a JS string quote: ' or " or `
_NQ = "[^'\"`]"      # any char that is not a quote
ANCHOR_SELECTION = [
    r"getElementsByTagName\(\s*" + _Q + "a" + _Q,                  # getElementsByTagName('a')
    r"\.(?:links|anchors)\b",                                       # document.links / .anchors
    # querySelector/closest/matches with a selector that targets an <a> tag (any quote style):
    r"(?:querySelector(?:All)?|closest|matches)\(\s*" + _Q + _NQ + r"*(?<![\w.#-])a(?![\w-])" + _NQ + r"*" + _Q,
    # ...or an [href] attribute selector (selects links):
    r"(?:querySelector(?:All)?|closest|matches)\(\s*" + _Q + _NQ + r"*\[\s*href",
]


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
        for pat in ANCHOR_SELECTION:
            self.assertIsNone(re.search(pat, src),
                              f"{name}: selects an anchor/link (/{pat}/) — clicks could navigate")
        idents = set(CLICK_CALL.findall(src))
        self.assertTrue(idents, f"{name}: expected at least one <ident>.click()")
        self.assertTrue(
            idents.issubset(allowed_click_idents),
            f"{name}: click callers {sorted(idents)} not subset of {sorted(allowed_click_idents)}",
        )
        # All clicking must route through a single audited helper `safeClick(el)` that verifies the
        # element at RUNTIME before clicking, so a link (or any non-approved element) can never be
        # clicked regardless of how it was obtained (defence in depth, closes the ident loophole).
        self.assertRegex(src, r"safeClick\s*=\s*el\s*=>", f"{name}: no safeClick(el) helper")
        self.assertEqual(idents, {"el"},
                         f"{name}: the only click site must be el.click() inside safeClick, got {sorted(idents)}")
        # Prove EVERY .click() lives INSIDE safeClick (not merely that the receiver is named `el`):
        # locate the safeClick body and require all .click( offsets to fall within it.
        clicks = [m.start() for m in CLICK_SITE.finditer(src)]
        self.assertTrue(clicks, f"{name}: expected at least one .click() inside safeClick")
        body = safeclick_body(src)
        self.assertIsNotNone(body, f"{name}: could not locate safeClick body")
        body_start = src.index(body)
        body_end = body_start + len(body)
        for c in clicks:
            self.assertTrue(body_start <= c < body_end,
                            f"{name}: a .click() call at offset {c} is outside safeClick")
        # safeClick must actually gate on the element: a blob-URL download anchor is the only anchor
        # it will click.
        self.assertRegex(src, r"el\.tagName\s*===?\s*['\"]A['\"]", f"{name}: safeClick must check the anchor tag")
        self.assertIn("blob:", src, f"{name}: safeClick must require the download anchor to be a blob URL")
        # Every href assignment must be a local blob/object URL (no external navigation/exfil),
        # and there must be no element `.src` load. Then bind the download anchor `a` to a
        # createElement('a') + blob-URL + download (so it can only save a local file).
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
        self._scan(EXPORT, {"el"})
        src = code_of(EXPORT)
        # safeClick's approved expander targets: Fidelity's own position lot-expander buttons and
        # the account-group toggle / "Expand groups" control -- never a link/navigation target.
        self.assertIn('button.posweb-cell-symbol-name[aria-expanded="false"]', src)
        self.assertIn('button.posweb-cell-symbol-name[aria-expanded="true"]', src)
        self.assertIn("posweb-cell-symbol-name", src)  # okExpander runtime check
        self.assertIn("group-contracted", src)         # okGroup runtime check
        self.assertIn("ag-row-group-contracted", src)  # collapsed-group scoping

    def test_inspector_is_safe(self):
        self._scan(INSPECTOR, {"el"})

    def test_click_outside_safeclick_is_detected(self):
        # The "every .click() inside safeClick" invariant must catch a click added elsewhere,
        # including a whitespace-obfuscated `el . click()` (which CLICK_CALL still treats as a click).
        fake = ("const safeClick = el => { if (el.tagName === 'A') { el.click(); } return false; };\n"
                "const el = document.querySelector('button.trade'); el . click();\n")
        clicks = [m.start() for m in CLICK_SITE.finditer(fake)]
        self.assertEqual(len(clicks), 2)  # both the inside and the spaced-outside click are seen
        body = safeclick_body(fake)
        self.assertIsNotNone(body)
        start = fake.index(body)
        end = start + len(body)
        outside = [c for c in clicks if not (start <= c < end)]
        self.assertTrue(outside, "the out-of-helper click must be detected as outside safeClick")

    def test_node_check_if_available(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not on PATH")
        for path in (EXPORT, INSPECTOR):
            res = subprocess.run([node, "--check", path], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"node --check failed for {path}: {res.stderr}")

    def test_anchor_selection_guard_catches_link_clicks(self):
        # The guard must flag any attempt to SELECT a link (the loophole raised in review:
        # `g = document.querySelector('a'); g.click()` uses only allowed idents but clicks a link).
        unsafe = [
            "var g = document.querySelector('a'); g.click();",
            "document.querySelectorAll('div a').forEach(x => x.click());",
            "el.closest('a').click();",
            "var g = document.querySelector('a[href]'); g.click();",
            "document.getElementsByTagName('a')[0].click();",
            "document.links[0].click();",
            "document.querySelector('td [href^=\"http\"]').click();",
            "var g = document.querySelector(`a`); g.click();",
        ]
        for snippet in unsafe:
            self.assertTrue(any(re.search(p, snippet) for p in ANCHOR_SELECTION),
                            f"guard failed to flag: {snippet!r}")

    def test_anchor_selection_guard_allows_real_selectors(self):
        # The guard must NOT false-positive on the actual (anchor-free) selectors the scripts use.
        safe = [
            "document.querySelectorAll('.ag-pinned-left-cols-container [role=\"row\"].ag-row-group-contracted')",
            "document.querySelectorAll('button.posweb-cell-symbol-name[aria-expanded=\"false\"]')",
            "cell.querySelector('.posweb-cell-account_primary')",
            "row.querySelector('.ag-group-contracted, [class*=\"group-contracted\"]')",
            "document.querySelector('table.posweb-purchase-history')",
            "document.querySelectorAll('button')",
            "document.querySelector('[col-id=\"sym\"]')",
        ]
        for snippet in safe:
            self.assertFalse(any(re.search(p, snippet) for p in ANCHOR_SELECTION),
                             f"guard false-positived on: {snippet!r}")


class StripCommentsTests(unittest.TestCase):
    def test_removes_real_comments(self):
        self.assertNotIn("secret", strip_comments("var x = 1; // secret\n"))
        self.assertNotIn("secret", strip_comments("/* secret */ var x = 1;"))

    def test_keeps_delimiters_inside_strings(self):
        # A `//` inside a string literal must NOT swallow the rest of the line (the round-5 bypass):
        # code after a URL-bearing string must survive for scanning.
        stripped = strip_comments("const marker = \"http://\"; el.click();\n")
        self.assertIn("http://", stripped)
        self.assertIn("el.click()", stripped)
        # a hidden banned call after a //-bearing string must also survive to be scanned
        self.assertIn("fetch(", strip_comments("const u = 'a//b'; fetch(u);"))

    def test_keeps_block_delimiters_inside_strings(self):
        self.assertIn("el.click()", strip_comments("const s = \"/*\"; el.click(); const t = \"*/\";"))

    def test_template_interpolation_scanned_as_code(self):
        self.assertIn("fetch(", strip_comments("const s = `x ${ fetch(u) } y`;"))
        # a // inside the template's string part is preserved (not treated as a comment)
        self.assertIn("http://", strip_comments("const s = `see http:// ${v}`;"))

    def test_nested_template_does_not_drop_code(self):
        # Round-6 case: a nested template literal inside ${...} must not let a `//` start a comment
        # that eats following executable code.
        self.assertIn("fetch(", strip_comments("const s = `${ `http://` }`; fetch(u);"))
        self.assertIn("fetch(", strip_comments("const s = `${ `a ${ `b//c` } d` }`; fetch(u);"))

    def test_regex_literal_not_treated_as_comment(self):
        self.assertIn("keep", strip_comments("const r = /a\\/\\//; var keep = 1;"))

    def test_regex_after_keyword_not_treated_as_comment(self):
        # Round-7 case: a regex after `return`/`throw` containing an escaped slash must not let the
        # embedded `//` start a comment that drops following code.
        self.assertIn("fetch(", strip_comments("function f(x){ return /https?:\\/\\//.test(x) && fetch(u); }"))
        self.assertIn("fetch(", strip_comments("throw /a\\/\\// ; fetch(u);"))

    def test_real_scripts_doc_comments_are_stripped(self):
        # The safety doc-comments name banned APIs; those must not survive to the code scan.
        for path in (EXPORT, INSPECTOR):
            self.assertNotIn("no network calls", code_of(path).lower())


if __name__ == "__main__":
    unittest.main()
