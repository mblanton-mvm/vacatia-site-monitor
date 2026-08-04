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
# Sending to Teams IS done here, on Michele's explicit standing instruction (2026-08-04:
# "post a summary every 4 hours to mine and jarran's team chat"). Both Graph tokens were
# verified via /me as Michele Blanton <mblanton@mvmtechnology.com>, so posts appear under
# her name, not Jarran's.
set -uo pipefail

# MUST be Apple's python3. The default python3 here is python.org 3.14, which ships with no
# CA bundle on macOS, so every Graph call dies with CERTIFICATE_VERIFY_FAILED before it even
# authenticates. 3.9.6 has the system trust store and both scripts compile on it.
PY=/usr/bin/python3
PLATFORM="$HOME/Developer/mvm-platform"
# Michele <-> Jarran 1:1, confirmed LIVE by reading it (there is a dead duplicate 1:1 in
# Teams whose exact label ranks FIRST in the registry — never trust the label alone).
CHAT='19:00e7ade9-90d3-47ea-a785-9be19361c912_50bbea07-96de-4bee-a5ca-f349c42292cb@unq.gbl.spaces'

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

# ── post to Teams ─────────────────────────────────────────────────────────────
# Never hand-roll the HTML: Teams strips <p> margins so paragraphs arrive as one blob.
# format-message.py joins with <br><br>. It also needs FLAT bullets — the doc layout's
# indented continuation lines get un-wrapped into a run-on paragraph, which is why
# build_4h_digest.py has a separate --teams renderer.
HTML="$PIPE/digest-latest.html"
if "$PY" "$PLATFORM/.claude/skills/teams-chat/format-message.py" "$DRAFT" -o "$HTML" >/dev/null 2>&1; then
  if grep -q '\*\*' "$HTML"; then
    echo "  teams: ABORT — literal ** in output, would render as punctuation"
  else
    "$PY" "$PLATFORM/scripts/teams-graph/send-chat.py" send "$CHAT" --file "$HTML" \
      && echo "  teams: posted" || echo "  teams: send FAILED (draft kept at $DRAFT)"
  fi
else
  echo "  teams: formatter failed, nothing sent"
fi

echo "  done"
