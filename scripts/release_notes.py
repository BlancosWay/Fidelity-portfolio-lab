#!/usr/bin/env python3
"""Build release notes from CHANGELOG.md (stdlib only).

Commands:
  python scripts/release_notes.py current            # print the VERSION file value
  python scripts/release_notes.py section X.Y.Z      # print the CHANGELOG notes for X.Y.Z
  python scripts/release_notes.py check              # verify VERSION has a non-empty CHANGELOG entry

The CHANGELOG follows "Keep a Changelog": each release is a level-2 heading like
`## [X.Y.Z] - YYYY-MM-DD`. `section` prints everything under that heading up to the next
level-2 heading.
"""
import argparse
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANGELOG = os.path.join(REPO_ROOT, "CHANGELOG.md")
VERSION_FILE = os.path.join(REPO_ROOT, "VERSION")


def read_version():
    with open(VERSION_FILE, encoding="utf-8") as fh:
        return fh.read().strip()


def section(version, changelog_path=CHANGELOG):
    """Return the CHANGELOG body for `version` (stripped), or "" if absent."""
    with open(changelog_path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    header = re.compile(r"^##\s+\[" + re.escape(version) + r"\]")
    start = next((i for i, ln in enumerate(lines) if header.match(ln)), None)
    if start is None:
        return ""
    linkdef = re.compile(r"^\[[^\]]+\]:\s")  # Keep-a-Changelog reference-definition line
    body = []
    for ln in lines[start + 1:]:
        if ln.startswith("## ") or linkdef.match(ln):
            break
        body.append(ln)
    return "\n".join(body).strip("\n")


def main(argv=None):
    p = argparse.ArgumentParser(prog="release_notes", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("current")
    sp = sub.add_parser("section")
    sp.add_argument("version")
    sub.add_parser("check")
    args = p.parse_args(argv)

    if args.cmd == "current":
        print(read_version())
        return 0
    if args.cmd == "section":
        text = section(args.version)
        if not text:
            print(f"error: no CHANGELOG section for {args.version}", file=sys.stderr)
            return 1
        print(text)
        return 0
    if args.cmd == "check":
        version = read_version()
        text = section(version)
        if not text:
            print(f"error: VERSION {version} has no non-empty CHANGELOG entry", file=sys.stderr)
            return 1
        print(f"CHANGELOG OK for v{version}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
