#!/usr/bin/env bash
# publish_to_data_live.sh <artifacts_dir> <branch>
# Force-push <artifacts_dir> as the single commit of an orphan branch.
# Keeps the branch tiny even after months of nightly runs.

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <artifacts_dir> <branch>" >&2
  exit 2
fi

ARTIFACTS="$1"
BRANCH="$2"

if [ ! -d "$ARTIFACTS" ]; then
  echo "publish: $ARTIFACTS is not a directory" >&2
  exit 1
fi

# Resolve to an absolute path: the worktree step below cd's elsewhere, so a
# relative ARTIFACTS (e.g. the workflow's plain "artifacts") must be pinned
# down before we change directories.
ARTIFACTS="$(cd "$ARTIFACTS" && pwd)"

WORKTREE="$(mktemp -d)"
trap 'rm -rf "$WORKTREE"' EXIT

# Isolate: build the branch in a throwaway worktree so the caller's tree is untouched.
git worktree add --detach "$WORKTREE" HEAD >/dev/null
(
  cd "$WORKTREE"
  # A previous publish may have left a local branch ref of the same name
  # behind (worktrees share refs with the main repo). Drop it so the orphan
  # checkout below doesn't fail with "branch already exists".
  git branch -D "$BRANCH" >/dev/null 2>&1 || true
  git checkout --orphan "$BRANCH" >/dev/null
  git rm -rf . >/dev/null 2>&1 || true

  cp -a "$ARTIFACTS/." .

  git add -A
  git -c user.email=ci@narrativesignal.local -c user.name="ci" \
    commit -q -m "nightly artifacts $(date -u +%Y-%m-%dT%H:%M:%SZ)"

  git push -q --force origin "$BRANCH"
)
git worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true

echo "publish: pushed $ARTIFACTS to origin/$BRANCH"
