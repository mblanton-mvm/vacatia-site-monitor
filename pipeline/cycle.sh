#!/bin/bash
# One scheduled cycle: re-pull the casting registry from staging, rebuild state + history + page,
# and publish ONLY if the page actually changed.
#
# Why publish-on-change: this runs every 15 minutes. Committing an identical 2.7 MB file 96 times a
# day would bury the real edits in noise and push ~250 MB/day at GitHub for nothing. The registry
# pull still happens every cycle, so the moment the appliance collector writes a new capture we
# pick it up — but a cycle where nothing moved leaves no trace except this log.
#
# Note the ceiling honestly: pulling more often cannot make the registry fresher. The HTVC
# appliance collector writes it (currently hourly at best, and degraded at MVM784). This job
# guarantees we are never MORE than one cycle behind whatever the collector has managed.
set -uo pipefail
cd "$(dirname "$0")"
REPO=/Users/micheleblanton/Developer/vacatia-site-monitor   # this pipeline lives at $REPO/pipeline
STAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

echo "════ cycle $STAMP ════"
if ! ./refresh.sh; then
  echo "refresh FAILED — leaving the published page alone"; exit 1
fi

# the PII gate lives inside refresh.sh and exits non-zero; belt and braces before anything ships
if grep -qE 'guestLocked|bedName|livName' artifact.html; then
  echo "PII GATE TRIPPED — not publishing"; exit 1
fi

if cmp -s artifact.html "$REPO/index.html"; then
  echo "no change — nothing to publish"
  exit 0
fi
cp artifact.html "$REPO/index.html"
cd "$REPO"
git add index.html pipeline
git commit -q -m "auto: refresh $STAMP" || { echo "commit produced nothing"; exit 0; }
if git push -q origin HEAD 2>/dev/null; then
  echo "published $STAMP"
else
  # the other session pushes this repo too; rebase onto its commit rather than fighting it
  echo "push rejected — pulling --rebase and retrying"
  git pull -q --rebase origin main && git push -q origin HEAD && echo "published after rebase"
fi
