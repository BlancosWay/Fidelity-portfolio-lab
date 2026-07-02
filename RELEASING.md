# Releasing

Releases are cut from `main` by pushing an annotated `vX.Y.Z` tag. The
`.github/workflows/release.yml` workflow then publishes a GitHub Release whose notes come from
`CHANGELOG.md`. Nothing auto-bumps the version — a release is always a deliberate, human step.

## Steps
1. Ensure `main` is green and everything you want to ship is merged.
2. Move the `## [Unreleased]` items in `CHANGELOG.md` into a new `## [X.Y.Z] - YYYY-MM-DD` section,
   and update the compare/tag links at the bottom.
3. Bump the `VERSION` file to `X.Y.Z` (must match the tag you will push).
4. Open a PR with those changes; merge once checks pass.
5. Tag the merge commit on `main` and push the tag:
   ```
   git checkout main && git pull
   git tag -a v0.1.0 -m "v0.1.0"
   git push origin v0.1.0
   ```

## What the workflow enforces
- The tag commit must be an ancestor of `origin/main` (no releasing from unmerged branches).
- The tag (`vX.Y.Z`) must equal the `VERSION` file (`X.Y.Z`).
- The `X.Y.Z` section of `CHANGELOG.md` must be non-empty; those lines become the Release notes.

The same deterministic checks run on every PR/push via the **Release dry run** job in
`validate.yml`, so a broken release is caught before tagging.

## Versioning
[Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.
- PATCH — bug fixes, selector re-tuning.
- MINOR — new analyzer subcommands or capabilities (backward compatible).
- MAJOR — breaking changes to the CSV schema or CLI.
