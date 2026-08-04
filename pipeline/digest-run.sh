#!/bin/bash
# 4-hourly Vacatia 3-site digest. Two artefacts, no iCX access, no network reads:
#   1. appends §C of the shared watch log that Jarran's Claude reads
#   2. writes a Teams-ready draft to DRAFT for review / paste / gated send
#
# It writes into a DEDICATED WORKTREE, never Michele's live working tree. The 15:17Z run
# committed onto whatever branch happened to be checked out and landed on `main` while she
# was mid-session — the collision CLAUDE.md §11 means by "one editor per branch at a time".
# The worktree is pinned to the long-lived branch behind PR #819, which is also the answer
# to `main` being branch-protected: the digest cannot push to main and must not try.
#
# Sending to Teams is NOT done here. Posting is an outward action; it happens through the
# gated path once the Graph send token is authenticated as Michele.
set -uo pipefail

PIPE="$HOME/Developer/vacatia-site-monitor/pipeline"
WT="$HOME/Developer/mvm-platform-watchlog"
BRANCH="docs/vacatia-shared-state"
export WATCHLOG="$WT/docs/vacatia/vacatia-3site-watch-log.md"
DRAFT="$PIPE/digest-latest-teams.md"
STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

echo "════ digest $STAMP ════"

if [ ! -f "$WATCHLOG" ]; then
  echo "  FATAL: worktree missing at $WT — recreate with:"
  echo "    git -C ~/Developer/mvm-platform worktree add $WT $BRANCH"
  exit 1
fi

cd "$PIPE" || exit 1

# Land any upstream edits (Jarran's Claude writes §A/§B here too) before appending, so a
# concurrent write on the other side is never clobbered.
git -C "$WT" pull -q --rebase 2>&1 | tail -2

python3 build_4h_digest.py --hours 4 --append-log >/dev/null && echo "  log: $WATCHLOG"
python3 build_4h_digest.py --hours 4 --teams -o "$DRAFT" 2>/dev/null && echo "  draft: $DRAFT"

# Commit ONLY the log. This tree carries other people's work; one `git add -A` here put
# 100 unrelated files into a PR once.
cd "$WT" || exit 1
if git diff --quiet -- "$WATCHLOG"; then
  echo "  git: log unchanged, nothing to commit"
else
  git add -- "$WATCHLOG"
  git commit -q -m "docs(vacatia): 4h 3-site watch digest $STAMP" -- "$WATCHLOG" \
    && echo "  git: committed on $BRANCH"
  git push -q && echo "  git: pushed to $BRANCH (PR #819)" \
    || echo "  git: push FAILED (left committed — check auth/conflict)"
fi

echo "  done"
