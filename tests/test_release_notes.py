"""Tests for scripts/release_notes.py (stdlib unittest)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import release_notes  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ReleaseNotesTests(unittest.TestCase):
    def test_current_matches_version_file(self):
        with open(os.path.join(REPO_ROOT, "VERSION"), encoding="utf-8") as fh:
            self.assertEqual(release_notes.read_version(), fh.read().strip())

    def test_section_present_for_current_version(self):
        body = release_notes.section(release_notes.read_version())
        self.assertTrue(body)
        self.assertIn("Added", body)

    def test_section_absent_returns_empty(self):
        self.assertEqual(release_notes.section("99.99.99"), "")

    def test_section_excludes_link_definitions(self):
        # The real CHANGELOG ends with Keep-a-Changelog reference definitions after 0.1.0;
        # generated notes must not include them.
        body = release_notes.section(release_notes.read_version())
        self.assertNotIn("]: https://", body)
        self.assertNotIn("[Unreleased]", body)

    def test_section_stops_at_next_heading(self):
        text = "\n".join([
            "# Changelog", "",
            "## [Unreleased]", "",
            "## [1.2.3] - 2026-01-01", "### Added", "- a thing", "",
            "## [1.2.2] - 2025-12-01", "### Added", "- older thing",
        ])
        fd, path = tempfile.mkstemp(suffix=".md")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            body = release_notes.section("1.2.3", path)
            self.assertIn("- a thing", body)
            self.assertNotIn("older thing", body)
        finally:
            os.unlink(path)

    def test_check_command_ok(self):
        self.assertEqual(release_notes.main(["check"]), 0)

    def test_section_command_unknown_version_errors(self):
        self.assertEqual(release_notes.main(["section", "99.99.99"]), 1)


if __name__ == "__main__":
    unittest.main()
