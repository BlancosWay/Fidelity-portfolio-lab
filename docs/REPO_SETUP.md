# Repository setup (pipeline + approvals)

One-time steps to publish `BlancosWay/Fidelity-portfolio-lab` and turn on the same pipeline +
approval model as [BlancosWay/crucible](https://github.com/BlancosWay/crucible). Requires the
[`gh` CLI](https://cli.github.com/) authenticated as **BlancosWay** (`gh auth login`).

## 1. Create the public repo and push
From the repo root, with `main` up to date (see `RELEASING.md`/`CONTRIBUTING.md` for the branch flow):
```bash
gh repo create BlancosWay/Fidelity-portfolio-lab \
  --public \
  --description "Compliant, read-only, local analysis of your Fidelity holdings by tax lot." \
  --source . --remote origin --push
```
(If the repo already exists: `git remote add origin https://github.com/BlancosWay/Fidelity-portfolio-lab.git && git push -u origin main`.)

## 2. Turn on auto-merge and tidy merge settings
```bash
gh repo edit BlancosWay/Fidelity-portfolio-lab \
  --enable-auto-merge \
  --enable-squash-merge \
  --delete-branch-on-merge \
  --enable-merge-commit=false \
  --enable-rebase-merge=false
```

## 3. Allow Actions to auto-merge owner/Dependabot PRs
Settings needed by `.github/workflows/auto-merge.yml`:
```bash
# Let workflows create/approve PRs (needed for the auto-merge job's token).
gh api -X PUT repos/BlancosWay/Fidelity-portfolio-lab/actions/permissions/workflow \
  -f default_workflow_permissions=write \
  -F can_approve_pull_request_reviews=true
```

## 4. Branch ruleset for `main` (required PR + checks + code-owner review)
This makes every change go through a PR whose required checks are green and which `@BlancosWay`
(via `CODEOWNERS`) has approved — the same gate crucible uses.
```bash
cat > /tmp/ruleset.json <<'JSON'
{
  "name": "protect main",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/main"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 1,
        "require_code_owner_review": true,
        "dismiss_stale_reviews_on_push": true,
        "require_last_push_approval": false,
        "required_review_thread_resolution": true
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "Unit tests" },
          { "context": "Byte-compile" },
          { "context": "Data safety" },
          { "context": "JS syntax" },
          { "context": "Release dry run" },
          { "context": "Changelog entry" }
        ]
      }
    }
  ],
  "bypass_actors": [
    { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" }
  ]
}
JSON

gh api -X POST repos/BlancosWay/Fidelity-portfolio-lab/rulesets \
  --input /tmp/ruleset.json
```
Notes:
- `bypass_actors` role id `5` = **Admin**, so the owner can still merge their own auto-merged PRs
  once checks pass (Dependabot/owner PRs auto-merge via the workflow). Remove that entry to force
  even the owner through review.
- The `context` names must match the job `name:` fields in `.github/workflows/validate.yml`. If you
  rename a job, update the ruleset.
- `strict_required_status_checks_policy: true` requires branches to be up to date before merging.

## 5. (Recommended) Security features
```bash
gh api -X PATCH repos/BlancosWay/Fidelity-portfolio-lab \
  -f 'security_and_analysis[secret_scanning][status]=enabled' \
  -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled'
```
Dependabot is already configured in `.github/dependabot.yml` (weekly GitHub-Actions updates).

## 6. Cut the first release
Follow `RELEASING.md`: bump `VERSION`/`CHANGELOG.md` via a PR, then push `v0.1.0` on `main`;
`.github/workflows/release.yml` publishes the GitHub Release.

## How approvals work (summary)
- **Everyone** must open a PR; direct pushes to `main` are blocked by the ruleset.
- Required checks (`Unit tests`, `Byte-compile`, `Data safety`, `JS syntax`, `Release dry run`,
  `Changelog entry`) must pass.
- `@BlancosWay` is auto-requested via `CODEOWNERS` and code-owner review is required.
- The **owner's** and **Dependabot's** PRs get squash auto-merge enabled automatically once checks
  pass (`.github/workflows/auto-merge.yml`); outside PRs wait for the owner's manual review/merge.
