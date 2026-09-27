---
name: resolve-upstream-conflicts
description: Merge upstream fireshare-app/fireshare into this fork and resolve merge conflicts while keeping the fork's own changes. Use when the "Sync Upstream" workflow opened an "Upstream sync failed" issue, when asked to sync/update the fork from upstream, or to fix upstream merge conflicts.
---

# Resolve upstream merge conflicts

This repo is a fork of `fireshare-app/fireshare`. The `Sync Upstream` workflow
(`.github/workflows/sync-upstream.yml`) merges upstream `main` daily and opens an
issue labelled `upstream-sync` when that fails. Your job: do the merge locally,
resolve every conflict correctly, verify, and open a PR that closes the issue.

The guiding rule: **take upstream's changes, re-apply the fork's intent on top.**
Never silently drop either side.

## 1. Prepare

```bash
git status                      # must be clean; stop and ask if it is not
git remote get-url upstream || git remote add upstream https://github.com/fireshare-app/fireshare.git
git fetch origin && git fetch upstream
git checkout main && git pull --ff-only origin main
gh issue list --label upstream-sync --state open   # note the issue number, if any
```

Create a working branch and start the merge:

```bash
git checkout -b sync/upstream-$(date +%Y-%m-%d)
git merge --no-ff upstream/main
```

If the merge succeeds without conflicts, the workflow most likely failed at the
push (upstream touched `.github/workflows` and `GITHUB_TOKEN` may not push those).
Skip to step 4.

## 2. Understand both sides before editing

List conflicts with `git diff --name-only --diff-filter=U`. For **each** file:

- What the fork changed: `git log --oneline upstream/main..main -- <file>` and
  `git diff $(git merge-base main upstream/main) main -- <file>`
- What upstream changed: `git log --oneline main..upstream/main -- <file>` and
  `git diff $(git merge-base main upstream/main) upstream/main -- <file>`

Read the commit messages to learn *why* each side changed the code. Only then
edit the conflict hunks.

## 3. Resolve

Per hunk, decide:

- **Independent changes** (different concerns in the same region) → keep both.
- **Upstream refactored/renamed/moved code the fork modified** → take upstream's
  new structure and port the fork's modification into it. Grep for callers of any
  renamed symbol the fork added usages of.
- **Upstream fixed the same thing the fork patched** → prefer upstream's version
  and drop the fork's duplicate, but say so in the PR description.
- **Genuinely contradictory behaviour** → do not guess. Stop and ask the user,
  showing both versions and the commits behind them.

Project-specific hot spots:

- `migrations/versions/` (Alembic): if both sides added migrations, the history
  forks into two heads. Keep both files and point the fork's migration
  `down_revision` at upstream's newest revision (linearise), so there is a single
  head. Never edit an upstream migration.
- `app/client/package-lock.json` / root `package-lock.json`: do not hand-merge.
  Take upstream's version (`git checkout --theirs <file>`), then re-run
  `npm install` in that directory so the fork's extra dependencies are re-added.
- `app/server/requirements.txt`, `package.json`: union of both dependency sets;
  on version clashes prefer upstream's version unless the fork pinned it on purpose.
- Version bumps (`chore: bump version`): take upstream's version.
- `.github/workflows/docker-publish-*.yml`: take upstream's version, keeping any
  fork-specific edits (e.g. image name) re-applied.
- Never modify or remove `.github/workflows/sync-upstream.yml` or this skill
  because of upstream changes; they only exist in the fork.

After resolving a file, `git add <file>`. When all are done:

```bash
git diff --check                                   # whitespace/marker check
grep -rnE '^(<<<<<<<|=======|>>>>>>>)( |$)' --exclude-dir=node_modules . || echo "no markers"
```

## 4. Verify

Run what applies to the files that changed (there is no test suite):

```bash
python -m compileall -q app/server/fireshare migrations         # backend syntax
(cd app/client && npm ci && npm run build)                        # frontend build
grep -h "^down_revision" migrations/versions/*.py | sort | uniq -d  # must be empty (no two migrations with the same parent)
```

Optionally `docker build -t fireshare-sync-test .` if the Dockerfile or
entrypoints were touched. Fix any failures before continuing.

## 5. Commit and open a PR

```bash
git commit --no-edit            # keeps the default "Merge remote-tracking branch 'upstream/main'" message
git push -u origin HEAD
gh pr create --repo "$(gh repo view --json nameWithOwner -q .nameWithOwner)" --base main \
  --title "chore: merge upstream $(git rev-parse --short upstream/main)" \
  --body "<summary>"
```

Always pass `--repo` so the PR targets the fork, not upstream. The PR body must
list each conflicted file with a one-line explanation of how it was resolved,
anything dropped as superseded by upstream, the verification commands you ran,
and `Closes #<issue>` for the open `upstream-sync` issue.

Do not merge the PR yourself unless the user asks. Once it is merged, the next
`Sync Upstream` run finds the fork up to date and closes any leftover sync issues.
