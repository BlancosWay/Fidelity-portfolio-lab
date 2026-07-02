<!-- Thanks for contributing to Fidelity-portfolio-lab! -->

## What does this change?


## Why?


## Checklist
- [ ] `python scripts/check.py` passes locally (unit tests, byte-compile, data-safety, `node --check`, release dry run).
- [ ] New/changed behavior has tests (TDD); `python -m unittest discover -s tests` is green.
- [ ] **No real holdings or account identifiers** are committed (only `tests/sample_lots.csv`).
- [ ] Browser scripts remain **read-only** (no network/credential/navigation APIs) — `test_browser_safety.py` passes.
- [ ] Updated `CHANGELOG.md` under `## [Unreleased]` for any shipped-path change (`scripts/`, `tests/`, `SKILL.md`), or used `[skip changelog]` if truly N/A.
