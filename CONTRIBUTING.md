# Contributing to Fidelity-portfolio-lab

Thanks for your interest! This is a small, dependency-free tool with a strict safety contract.

## Ground rules (non-negotiable)
- **Compliance first.** No credentials, no login automation, no third-party services, no Fidelity
  API. Data is only ever obtained by the user running a **read-only** script in their own browser
  session. The browser scripts must remain read-only — enforced by `tests/test_browser_safety.py`.
- **Never commit real holdings.** Only the synthetic `tests/sample_lots.csv` is tracked. Real
  exports, databases, and any Fidelity account identifiers are rejected by
  `tests/test_data_safety.py` / `scripts/check_data_safety.py`.
- **Stdlib only.** The Python analyzer and tests use the standard library (`csv`, `sqlite3`,
  `argparse`, `datetime`, `unittest`). Do not add pip dependencies.

## Dev workflow
1. Branch off `main` (e.g. `feat/...`, `fix/...`).
2. Write a failing test first (TDD), then implement.
3. Run the full check locally:
   ```
   python scripts/check.py
   ```
   which runs the unit tests, byte-compiles the Python, runs the data-safety scan, `node --check`s
   the browser scripts, and dry-runs the release notes. (Or run `python -m unittest discover -s tests`.)
4. Add a `CHANGELOG.md` entry under `## [Unreleased]` for any shipped-path change (`scripts/`,
   `tests/`, `SKILL.md`). Use `[skip changelog]` in the PR's head commit only when truly N/A.
5. Open a pull request. `@BlancosWay` is auto-requested as reviewer (see `.github/CODEOWNERS`);
   required status checks must pass before merge.

## Style
- Keep functions small and independently testable.
- Comment only what needs clarifying.
- Match the existing patterns in `scripts/analyze/portfolio.py`.

By contributing, you agree your contributions are licensed under the project's MIT License.
