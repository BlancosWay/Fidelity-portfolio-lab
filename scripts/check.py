#!/usr/bin/env python3
"""Local one-shot gate mirroring CI (stdlib only).

Runs, in order: unit tests, byte-compile, data-safety scan, `node --check` on the browser scripts
(if node is available), and a release-notes dry run. Exits non-zero on the first failure.

Run: python scripts/check.py
"""
import os
import shutil
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(title, cmd):
    print(f"\n=== {title} ===\n$ {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=REPO_ROOT)
    if res.returncode != 0:
        print(f"FAILED: {title}", file=sys.stderr)
        sys.exit(res.returncode)


def main():
    py = sys.executable
    run("Unit tests", [py, "-m", "unittest", "discover", "-s", "tests"])
    run("Byte-compile", [py, "-m", "compileall", "-q", "scripts", "tests"])
    run("Data safety", [py, "scripts/check_data_safety.py"])

    node = shutil.which("node")
    if node:
        browser = os.path.join(REPO_ROOT, "scripts", "browser")
        for name in sorted(os.listdir(browser)):
            if name.endswith(".js"):
                run(f"node --check {name}", [node, "--check", os.path.join("scripts", "browser", name)])
    else:
        print("\n=== node --check: SKIPPED (node not on PATH) ===")

    run("Release notes dry run", [py, "scripts/release_notes.py", "check"])
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
