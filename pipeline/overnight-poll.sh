#!/bin/bash
# Unattended iCX sweep: all three Vacatia sites, then rebuild + publish.
#
# Runs from launchd every 15 minutes. Drives the logged-in TeICXDashboard tab in Chrome via
# AppleScript, because the iCX bearer is browser-minted and short-lived — there is no service
# account, so the warm browser session IS the credential. Nothing here originates a backend
# call; it reads the SPA's own responses (see icx-toolkit.js for why that matters).
#
# REQUIRES: Chrome > View > Developer > "Allow JavaScript from Apple Events" (one-time toggle).
#
# Exits quietly and leaves the published page alone whenever it cannot get good data:
# a lapsed portal session, a closed browser, a window that has not advanced. A stale-but-honest
# page beats a fresh-looking wrong one.
set -uo pipefail
cd "$(dirname "$0")"
PIPE="$(pwd)"
STAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
DROPS=/Users/micheleblanton/Developer/mvm-platform/docs/vacatia/data-drops
DL=/Users/micheleblanton/Downloads
echo "════ sweep $STAMP ════"

# site handle | display name | selector text
SITES=(
  "MVM784|The Berkley|The Berkley"
  "MVM783|The Grandview|The Grandview"
  "MVM743|The Cliffs|The Cliffs"
)

jsq() {  # run JS in the dashboard tab, print its result
  osascript <<APPLESCRIPT 2>&1
tell application "Google Chrome"
  repeat with w in windows
    repeat with t in tabs of w
      if URL of t contains "dashboardfe-dms" then
        return (execute t javascript "$1")
      end if
    end repeat
  end repeat
  return "NO_DASHBOARD_TAB"
end tell
APPLESCRIPT
}

jsfile() {  # inject a whole file (escaped for AppleScript's string literal)
  local f="$1"
  local esc; esc=$(python3 - "$f" <<'PY'
import sys, json
src = open(sys.argv[1], encoding='utf-8').read()
# AppleScript string: escape backslashes and double quotes, strip newlines to spaces
print(json.dumps(src)[1:-1].replace('\\n', ' ').replace('\\r', ' '))
PY
)
  osascript <<APPLESCRIPT 2>&1
tell application "Google Chrome"
  repeat with w in windows
    repeat with t in tabs of w
      if URL of t contains "dashboardfe-dms" then
        return (execute t javascript "$esc")
      end if
    end repeat
  end repeat
  return "NO_DASHBOARD_TAB"
end tell
APPLESCRIPT
}

# ── 0. is Chrome even up, and is the session alive? ────────────────────────────
if ! pgrep -xq "Google Chrome"; then echo "Chrome not running — skipping"; exit 0; fi

H=$(jsq "window.__health?window.__health():'NO_TOOLKIT'")
case "$H" in
  *NO_DASHBOARD_TAB*) echo "no dashboard tab — skipping"; exit 0 ;;
  *"turned off"*)     echo "AppleScript JS is disabled in Chrome — enable View > Developer > Allow JavaScript from Apple Events"; exit 1 ;;
esac
if [[ "$H" == *NO_TOOLKIT* ]]; then
  echo "injecting toolkit"; jsfile "$PIPE/icx-toolkit.js" >/dev/null
  H=$(jsq "window.__health?window.__health():'NO_TOOLKIT'")
fi
if [[ "$H" == *'"onLogin":true'* ]]; then
  echo "AUTH_LAPSED — portal session expired, needs a human login. Page left alone."; exit 0
fi
if [[ "$H" != *'"hasSelector":true'* ]]; then
  echo "dashboard not ready ($H) — skipping"; exit 0
fi

# ── 1. sweep each site ─────────────────────────────────────────────────────────
NEWDATA=0
declare -a SITEROWS=()
for spec in "${SITES[@]}"; do
  IFS='|' read -r HANDLE NAME LABEL <<< "$spec"
  # Do NOT delete pre-existing csvData files — Downloads holds dozens of unrelated old
  # exports that belong to the user. Instead stamp the clock and refuse anything older,
  # so a stale leftover can never be filed as this window's data. (A site-name check alone
  # is not enough: a leftover from the SAME site would sail through it.)
  CLICKED_AT=$(date +%s)
  jsq "window.__runSite('$HANDLE','$NAME','$LABEL')" >/dev/null

  RES=""
  for _ in $(seq 1 30); do                       # the site run takes ~40s of settle
    sleep 4
    RES=$(jsq "JSON.stringify(window.__runState||{})")
    [[ "$RES" == *'"status":"done"'* || "$RES" == *'"status":"error"'* ]] && break
  done

  if [[ "$RES" != *'"status":"done"'* ]]; then
    echo "  $HANDLE: FAILED ${RES:0:160}"; continue
  fi

  EXPECT=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['tick']['onlineSTB'])" "$RES" 2>/dev/null)
  WINDOW=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['tick']['compactISO'])" "$RES" 2>/dev/null)
  ROW=$(python3    -c "import json,sys;print(json.loads(sys.argv[1])['tick']['siteRow'])"    "$RES" 2>/dev/null)

  # claim the per-device export; the browser can only write to Downloads root
  CSV=""
  for _ in $(seq 1 15); do
    C=$(ls -t "$DL"/csvData*.csv 2>/dev/null | head -1)
    if [ -n "$C" ] && [ "$(stat -f %m "$C")" -ge "$CLICKED_AT" ]; then CSV="$C"; break; fi
    sleep 2
  done
  if [ -z "$CSV" ]; then echo "  $HANDLE: tick ok ($EXPECT) but NO FRESH EXPORT downloaded"; continue; fi

  # the export must be for the site we think it is, and match the tick's own count
  SITENAME=$(awk -F, 'NR==2{print $4}' "$CSV")
  ROWS=$(( $(wc -l < "$CSV") - 1 ))
  SHORT=${LABEL#The }
  if [[ "$SITENAME" != *"$SHORT"* ]]; then
    echo "  $HANDLE: SITE MISMATCH (got '$SITENAME') — discarding"; rm -f "$CSV"; continue
  fi
  if [ "$ROWS" != "$EXPECT" ]; then
    echo "  $HANDLE: ROW MISMATCH ($ROWS vs tick $EXPECT) — discarding"; rm -f "$CSV"; continue
  fi

  TS=$(awk -F, 'NR==2{print $2}' "$CSV" | tr -d ' :-' | sed 's/\(.\{8\}\)\(.\{4\}\).*/\1T\2ET/')
  TARGET="$PIPE/icx/icx-online-stbs-$HANDLE-$TS.csv"
  if [ -f "$TARGET" ]; then
    echo "  $HANDLE: window $TS already banked (iCX has not advanced) — no new data"
    rm -f "$CSV"
  else
    cp "$CSV" "$TARGET"
    cp "$CSV" "$DROPS/icx-online-stbs/icx-online-stbs-$HANDLE-$TS.csv"
    mkdir -p "$DL/$HANDLE"; mv "$CSV" "$DL/$HANDLE/icx-online-stbs-$HANDLE-$TS.csv"
    echo "  $HANDLE: $ROWS boxes @ $TS"
    NEWDATA=1
  fi
  SITEROWS+=("$ROW")

  # bank the health CSV the tick emitted
  HF=$(ls -t "$DL"/dish-icx-$(echo "$HANDLE" | tr 'A-Z' 'a-z')-health-*.csv 2>/dev/null | head -1)
  if [ -n "$HF" ] && [ -n "$WINDOW" ]; then
    mkdir -p "$DROPS/icx-sweeps/$WINDOW" "$DL/$HANDLE"
    cp "$HF" "$DROPS/icx-sweeps/$WINDOW/"; mv "$HF" "$DL/$HANDLE/"
  fi
done

# ── 2. combined site CSV for the window ────────────────────────────────────────
if [ ${#SITEROWS[@]} -gt 0 ] && [ -n "${WINDOW:-}" ]; then
  OUT="$DROPS/icx-sweeps/$WINDOW/dish-icx-vacatia3-site-$WINDOW.csv"
  mkdir -p "$(dirname "$OUT")"
  { echo 'handle,name,total_devices,pms_connected,s1_total,m1_total,m2_total,m2_pms_connected'
    printf '%s\n' "${SITEROWS[@]}"; } > "$OUT"
fi

# ── 3. rebuild + publish ───────────────────────────────────────────────────────
# Always run the cycle, even when iCX did not advance: cycle.sh also re-pulls the mDNS
# casting registry, which moves on the appliance collector's schedule, not iCX's. It
# publishes only when the rendered page actually changed, so an idle cycle costs a log
# line and nothing else.
[ "$NEWDATA" = "1" ] || echo "no new iCX window this sweep (registry may still have moved)"
./cycle.sh
echo "════ sweep done $(date -u '+%H:%M:%SZ') ════"
