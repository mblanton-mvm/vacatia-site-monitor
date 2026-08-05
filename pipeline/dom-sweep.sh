#!/bin/bash
# iCX sweep, DOM-ONLY capture path. Replaces the response-hook path in overnight-poll.sh.
#
# WHY: Chrome's AppleScript `execute javascript` runs in an isolated world, so the page's own
# fetch/XHR cannot be hooked from here and icx-toolkit.js's __icxResp stays empty forever —
# every site failed `NO_WINDOW` on every 15-min sweep from 20:47Z on 2026-08-04 while cycle.sh
# kept republishing the page with a fresh timestamp off stale counts. See icx-dom.js and
# docs/session-handoffs/vacatia-icx-polling-2026-08-04-evening.md §2-3.
#
# DESIGN: one osascript operation per call with REAL BASH SLEEPS between them. Bash sleeps are
# immune to Chrome's background-tab timer throttling; a single long in-page await is not.
#
# MVM743 runs FIRST: it is the site whose export click keeps sticking (stuck kebab), so it gets
# the freshest page state rather than the most-churned.
#
# The window comes from the EXPORT'S OWN row-2 timestamp, not from any API payload. That
# timestamp is EASTERN wall clock despite the column header naming AST and despite the filename
# suffix — see memory `reference-icx-export-timezone`.
set -uo pipefail
cd "$(dirname "$0")"
PIPE="$(pwd)"
DROPS=/Users/micheleblanton/Developer/mvm-platform/docs/vacatia/data-drops
DL=/Users/micheleblanton/Downloads
STAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
echo "════ dom-sweep $STAMP ════"

# ── single-writer lock ─────────────────────────────────────────────────────────
# There is ONE Chrome tab, ONE Downloads dir and ONE icx/ bank, so two concurrent sweeps
# corrupt each other. On 2026-08-04 a surviving 15-min heartbeat inside an abandoned Claude
# session swept at the same time as this one: it changed the site selection mid-capture
# (SELECTOR GUARD FAILED / PANEL_DID_NOT_OPEN), and its restricted run rewrote the window's
# combined site CSV over rows the other run had just banked. The launchd poller (:02/:17/:32/:47)
# and an in-session heartbeat (:03/:18/:33/:48) are one minute apart, so without this they
# overlap by design. mkdir is atomic; a lock older than 12 minutes is treated as stale.
LOCK="$PIPE/.sweep.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +12 2>/dev/null)" ]; then
    echo "stale lock (>12 min) — taking it over"
    rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null || true
  else
    echo "another sweep is already running (holder: $(cat "$LOCK/owner" 2>/dev/null || echo '?')) — exiting without touching anything"
    exit 0
  fi
fi
echo "pid $$ started $STAMP" > "$LOCK/owner"
trap 'rm -rf "$LOCK"' EXIT

SITES=(
  "MVM743|The Cliffs|The Cliffs"
  "MVM784|The Berkley|The Berkley"
  "MVM783|The Grandview|The Grandview"
)

# Optional args restrict the sweep to the named handles (e.g. `./dom-sweep.sh MVM743 MVM784`),
# for retrying the sites that failed without re-exporting the ones already banked.
if [ $# -gt 0 ]; then
  declare -a PICK=()
  for spec in "${SITES[@]}"; do
    for want in "$@"; do
      [[ "$spec" == "$want|"* ]] && PICK+=("$spec")
    done
  done
  SITES=("${PICK[@]}")
  echo "restricted to: $*"
fi

jsq() {
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

jsfile() {
  # base64, never string-escaping: a `//` comment flattened onto one line swallows the rest of
  # the file, and json.dumps's \uXXXX breaks AppleScript's own string parser on the U+2500
  # comment banners. Both were live bugs on 2026-08-04.
  local b64; b64=$(python3 -c "import base64,sys;print(base64.b64encode(open(sys.argv[1],'rb').read()).decode())" "$1")
  jsq "eval(new TextDecoder().decode(Uint8Array.from(atob('$b64'), function(c){return c.charCodeAt(0)})))"
}

# ── 0. gate ────────────────────────────────────────────────────────────────────
# These gate the iCX SWEEP ONLY and must never skip the rebuild: the mDNS registry pull does not
# touch Chrome at all. A Chrome preference should not be able to freeze the whole monitor.
ICX_OK=1
icx_skip() { echo "$1"; ICX_OK=0; }

# ── preflight: can this process actually SEE the download dir? ─────────────────
# macOS TCC protects ~/Downloads. A launchd agent without Full Disk Access can drive Chrome fine
# and read the rendered page fine, but every directory listing of ~/Downloads comes back EMPTY —
# so Chrome saves the export and the script concludes "NO FRESH EXPORT". That is what the 23:32Z
# and 23:47Z unattended runs did: 9-of-9 and 3-of-3 export failures whose files were sitting in
# ~/Downloads the whole time (csvData (69)/(70)/(71).csv, correctly sized for 460/772/4356 rows).
# Silently mis-reporting a permissions problem as missing data is the worst outcome, so say it.
if [ ! -r "$DL" ] || [ -z "$(ls -A "$DL" 2>/dev/null)" ]; then
  echo "!! CANNOT READ $DL (macOS TCC / Full Disk Access). Chrome will save exports there but this"
  echo "!! process cannot see them, so nothing can be banked. Grant Full Disk Access to whatever"
  echo "!! runs this job, or point Chrome's download dir somewhere unprotected."
  icx_skip "download dir unreadable — iCX sweep skipped (rebuild still running)"
fi

if ! pgrep -xq "Google Chrome"; then
  icx_skip "Chrome not running — iCX sweep skipped"
else
  H=$(jsq "window.__health?window.__health():'NO_TOOLKIT'")
  case "$H" in
    *NO_DASHBOARD_TAB*) icx_skip "no dashboard tab — iCX sweep skipped" ;;
    *"turned off"*)     icx_skip "AppleScript JS is disabled in Chrome (View > Developer > Allow JavaScript from Apple Events) — iCX sweep skipped; rebuild still running." ;;
  esac
  if [ "$ICX_OK" = 1 ]; then
    H=$(jsq "window.__health?window.__health():'NO_TOOLKIT'")
    if [[ "$H" == *'"onLogin":true'* ]]; then
      icx_skip "AUTH_LAPSED — portal session expired, needs a human login. iCX sweep skipped."
    fi
  fi
fi

# Reload the tab and re-inject, giving each site a CLEAN DOM.
#
# WHY PER SITE: PrimeNG overlays ACCUMULATE. Every __openMenu adds another Online-STBs menu that
# document.body.click() does not actually tear down, so the count climbs 1 -> 2 -> 3 across the
# three sites and reached SIX by 23:38Z. Once stacked, the Download click lands on a stale
# overlay: it reports DOWNLOAD_CLICKED and no file ever arrives — 9 of 9 attempts failed that way
# at 23:32Z, on all three sites, and clicking the LAST candidate instead of the first did not help
# either. The page has to be reset. Stale .p-multiselect-panel stacking (the cause of the earlier
# SELECTOR GUARD / PANEL_DID_NOT_OPEN failures) clears the same way.
#
# A reload is cheap and safe: the iCX bearer lives in localStorage (dish-dashboard-access-token /
# -refresh-token), so the tab returns ALREADY AUTHENTICATED, selection intact, with zero overlays.
# Measured 2026-08-04 23:39Z — ready in one 4s poll.
prepare_page() {
  jsq "location.reload(); 'RELOADING'" >/dev/null
  for _ in $(seq 1 25); do
    sleep 4
    R=$(jsq "JSON.stringify({sel:!!document.querySelector('div.p-multiselect'),pw:!!document.querySelector('input[type=password]')})")
    if [[ "$R" == *'"pw":true'* ]]; then echo "  AUTH_LAPSED after reload — needs a human login"; return 1; fi
    if [[ "$R" == *'"sel":true'* ]]; then
      jsfile "$PIPE/icx-toolkit.js" >/dev/null          # __dlOnline etc (pure DOM)
      D=$(jsfile "$PIPE/icx-dom.js")                    # __selectOnly / __domRead / __openMenu
      [[ "$D" == *DOM_OK* ]] || { echo "  icx-dom.js did not inject ($D)"; return 1; }
      return 0
    fi
  done
  echo "  dashboard did not come back after reload"
  return 1
}

# ── 1. per site ────────────────────────────────────────────────────────────────
NEWDATA=0
declare -a SITEROWS=()
WINDOW=""

if [ "$ICX_OK" = 1 ]; then
for spec in "${SITES[@]}"; do
  IFS='|' read -r HANDLE NAME LABEL <<< "$spec"
  echo "── $HANDLE ──"

  if ! prepare_page; then
    echo "  $HANDLE: page not ready — skipping site"; continue
  fi

  # a. select this site, and only this site. Poll generously: __selectOnly gets up to 3 attempts
  #    and each can include a ~13s view reset plus a dozen deselect clicks.
  jsq "window.__selStart('$LABEL')" >/dev/null
  SEL=""
  for _ in $(seq 1 70); do
    sleep 2
    SEL=$(jsq "window.__selRes||'PENDING'")
    [[ "$SEL" != *PENDING* ]] && break
  done
  if [[ "$SEL" != *'"ok":true'* ]]; then
    echo "  $HANDLE: SELECT FAILED ${SEL:0:160}"; continue
  fi

  # b. the guard that caught a real two-sites contamination — must be exactly one
  SL=$(jsq "window.__selLabel()")
  if [[ "$SL" != "1 site selected" ]]; then
    echo "  $HANDLE: SELECTOR GUARD FAILED (label reads '$SL') — discarding this site"; continue
  fi

  # c/d. information page, then POLL for the widgets rather than guessing a settle time. MVM783
  # is 4,300+ boxes and did not finish rendering inside a fixed 20s after a site switch.
  jsq "window.__goInfo()" >/dev/null
  sleep 8
  RD=""
  for _ in $(seq 1 20); do
    RD=$(jsq "window.__domRead()")
    if [[ "$RD" == *'"stb":'* ]] && [[ "$RD" != *'"stb":null'* ]]; then break; fi
    sleep 3
  done
  EXPECT=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['stb'])" "$RD" 2>/dev/null)
  CONN=$(python3   -c "import json,sys;print(json.loads(sys.argv[1])['conn'])" "$RD" 2>/dev/null)
  NOTCONN=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['notconn'])" "$RD" 2>/dev/null)
  if [ -z "${EXPECT:-}" ] || [ "$EXPECT" = "None" ]; then
    echo "  $HANDLE: could not read Online-STBs widget ($RD)"; continue
  fi
  echo "  widget: online=$EXPECT pms_conn=$CONN pms_not=$NOTCONN"

  # e. per-device export. Do NOT clear old csvData files — Downloads holds the user's own
  # unrelated exports. Stamp the clock instead and refuse anything older, so a leftover from
  # the SAME site cannot be filed as this window's data.
  # Open the menu and click Download in SEPARATE calls with a real sleep between — .menu-icon
  # toggles, so doing both inside one in-page await can close a menu instead of opening it and
  # then click some other card's stale entry, reporting success while nothing downloads.
  CSV=""
  for attempt in 1 2 3; do
    CLICKED_AT=$(date +%s)
    # Each failed attempt leaves its own overlay behind, so clear before reopening or the count
    # climbs and __clickDownload (rightly) refuses.
    [ "$attempt" -gt 1 ] && echo "  menus open before retry: $(jsq "window.__closeMenus()")"
    OM=$(jsq "window.__openMenu()")
    if [[ "$OM" != "MENU_OPENED" ]]; then
      echo "  $HANDLE: menu did not open ($OM) attempt $attempt"; sleep 3; continue
    fi
    sleep 3
    CD=$(jsq "window.__clickDownload()")
    if [[ "$CD" != *DOWNLOAD_CLICKED* ]]; then
      echo "  $HANDLE: no visible Download item ($CD) attempt $attempt"; sleep 3; continue
    fi
    for _ in $(seq 1 12); do
      C=$(ls -t "$DL"/csvData*.csv 2>/dev/null | head -1)  # find -newermt does not work on this box
      if [ -n "$C" ] && [ "$(stat -f %m "$C")" -ge "$CLICKED_AT" ]; then CSV="$C"; break; fi
      sleep 2
    done
    [ -n "$CSV" ] && break
    echo "  $HANDLE: clicked Download but no file after 24s — attempt $attempt"
  done
  if [ -z "$CSV" ]; then
    echo "  $HANDLE: widget read $EXPECT but NO FRESH EXPORT in 3 attempts — count NOT banked"; continue
  fi

  # f. guards: right site, and row count == the widget count
  SITENAME=$(awk -F, 'NR==2{print $4}' "$CSV")
  ROWS=$(( $(wc -l < "$CSV") - 1 ))
  SHORT=${LABEL#The }
  if [[ "$SITENAME" != *"$SHORT"* ]]; then
    echo "  $HANDLE: SITE MISMATCH (got '$SITENAME') — discarding"; rm -f "$CSV"; continue
  fi
  if [ "$ROWS" != "$EXPECT" ]; then
    echo "  $HANDLE: ROW MISMATCH ($ROWS rows vs widget $EXPECT) — discarding"; rm -f "$CSV"; continue
  fi

  # g. the window is the export's own timestamp, ceilinged to the 15-min boundary the way the
  #    API's dateUpto did. Eastern wall clock in, UTC out.
  TSRAW=$(awk -F, 'NR==2{print $2}' "$CSV")
  TS=$(echo "$TSRAW" | tr -d ' :-' | sed 's/\(.\{8\}\)\(.\{4\}\).*/\1T\2ET/')
  W=$(/usr/bin/python3 - "$TSRAW" <<'PY'
import sys, datetime
from zoneinfo import ZoneInfo
d = datetime.datetime.strptime(sys.argv[1].strip(), '%Y-%m-%d %H:%M:%S')
u = d.replace(tzinfo=ZoneInfo('America/New_York')).astimezone(datetime.timezone.utc)
u = u.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(minutes=(u.minute // 15 + 1) * 15)
print(u.strftime('%Y%m%dT%H%M%SZ'))
PY
)
  [ -n "$W" ] && WINDOW="$W"

  TARGET="$PIPE/icx/icx-online-stbs-$HANDLE-$TS.csv"
  if [ -f "$TARGET" ]; then
    echo "  $HANDLE: window $TS already banked (iCX has not advanced) — no new data"
    rm -f "$CSV"
  else
    cp "$CSV" "$TARGET"
    cp "$CSV" "$DROPS/icx-online-stbs/icx-online-stbs-$HANDLE-$TS.csv"
    mkdir -p "$DL/$HANDLE"; mv "$CSV" "$DL/$HANDLE/icx-online-stbs-$HANDLE-$TS.csv"
    echo "  $HANDLE: $ROWS boxes @ $TS  (window $W)"
    NEWDATA=1
  fi

  # h. health buckets, read as TEXT off the two other pages. Six metrics, good/warn/bad each.
  #    Any metric that cannot be read stays EMPTY in the CSV — blank means not measured, and must
  #    never be read as a zero.
  BK='{}'
  jsq "window.__goPage('net_stats')" >/dev/null
  sleep 8
  for _ in $(seq 1 15); do
    B1=$(jsq "window.__readBuckets()")
    [[ "$B1" == *'"disrupt"'* ]] && break
    sleep 3
  done

  # h2. per-device INTERNET DISRUPTION lists — this is the only way to learn WHICH boxes and how
  #     often (column: Average Net Disruption Count, plus Room Number). On net_stats the nine
  #     app-boxes run 0-2 RSSI | 3-5 disruption | 6-8 availability, so warn=4 and bad=5. Zero-count
  #     cards carry no kebab, so they are skipped rather than retried.
  DW=$(/usr/bin/python3 -c "import json,sys;d=json.loads(sys.argv[1]);print(d.get('disrupt',['','',''])[1])" "$B1" 2>/dev/null || echo '')
  DB=$(/usr/bin/python3 -c "import json,sys;d=json.loads(sys.argv[1]);print(d.get('disrupt',['','',''])[2])" "$B1" 2>/dev/null || echo '')
  for lvl in warn bad; do
    [ "$lvl" = warn ] && { IDX=4; CNT=$DW; } || { IDX=5; CNT=$DB; }
    [ -z "$CNT" ] || [ "$CNT" = "0" ] && continue
    A0=$(date +%s)
    OM=$(jsq "window.__openMenuIdx($IDX,$CNT)")
    if [[ "$OM" != "MENU_OPENED" ]]; then echo "  disruption-$lvl: $OM"; jsq "window.__closeMenus()" >/dev/null; continue; fi
    sleep 3
    CD=$(jsq "window.__clickDownload()")
    if [[ "$CD" != *DOWNLOAD_CLICKED* ]]; then echo "  disruption-$lvl: $CD"; jsq "window.__closeMenus()" >/dev/null; continue; fi
    AC=""
    for _ in $(seq 1 10); do
      C=$(ls -t "$DL"/csvData*.csv 2>/dev/null | head -1)
      if [ -n "$C" ] && [ "$(stat -f %m "$C")" -ge "$A0" ]; then AC="$C"; break; fi
      sleep 2
    done
    if [ -z "$AC" ]; then echo "  disruption-$lvl: clicked but no file"; jsq "window.__closeMenus()" >/dev/null; continue; fi
    AR=$(( $(wc -l < "$AC") - 1 ))
    if [ "$AR" != "$CNT" ]; then
      echo "  disruption-$lvl: ROW MISMATCH ($AR vs card $CNT) — discarding"; rm -f "$AC"
    else
      mkdir -p "$DROPS/icx-anomalies"
      cp "$AC" "$DROPS/icx-anomalies/disruption-$lvl-$HANDLE-$TS.csv"
      mv "$AC" "$DL/$HANDLE/disruption-$lvl-$HANDLE-$TS.csv"
      echo "  disruption-$lvl: $AR boxes -> icx-anomalies/disruption-$lvl-$HANDLE-$TS.csv"
    fi
    jsq "window.__closeMenus()" >/dev/null
  done

  jsq "window.__goPage('system_performance_1')" >/dev/null
  sleep 8
  for _ in $(seq 1 15); do
    B2=$(jsq "window.__readBuckets()")
    [[ "$B2" == *'"reboot"'* ]] && break
    sleep 3
  done
  BK=$(/usr/bin/python3 -c "import json,sys;a=json.loads(sys.argv[1] or '{}');a.update(json.loads(sys.argv[2] or '{}'));print(json.dumps(a))" "${B1:-{\}}" "${B2:-{\}}" 2>/dev/null || echo '{}')
  echo "  buckets: $BK"

  # h3. site-level health row
  if [ -n "$W" ]; then
    mkdir -p "$DROPS/icx-sweeps/$W"
    /usr/bin/python3 - "$DROPS/icx-sweeps/$W/dish-icx-$(echo "$HANDLE" | tr 'A-Z' 'a-z')-health-$W.csv" \
      "$HANDLE" "$W" "$EXPECT" "$CONN" "$NOTCONN" "$BK" <<'PY'
import json, sys
path, handle, w, online, conn, notconn, bk = sys.argv[1:8]
b = json.loads(bk or '{}')
taken = f"{w[0:4]}-{w[4:6]}-{w[6:8]}T{w[9:11]}:{w[11:13]}:{w[13:15]}Z"
# The original four metrics keep their original column positions so the existing archive stays
# readable; CPU utilisation and CPU temperature are appended.
order = ['rssi', 'disrupt', 'reboot', 'netavail', 'cpu', 'cputemp']
hdr = ['mvm_handle', 'taken_at', 'online_stbs', 'pms_connected', 'pms_not_connected']
for m in order:
    hdr += [f'{m}_good', f'{m}_warn', f'{m}_bad']
row = [handle, taken, online, conn, notconn]
for m in order:
    v = b.get(m)
    row += [str(x) for x in v] if v else ['', '', '']   # blank = not measured, never zero
open(path, 'w').write(','.join(hdr) + '\n' + ','.join(row) + '\n')
PY
  fi
  SITEROWS+=("$HANDLE,$NAME,$EXPECT,$CONN,0,0,$EXPECT,$CONN")
done
fi

# ── 2. combined site CSV ───────────────────────────────────────────────────────
# `${#SITEROWS[@]}` on an empty array trips `set -u` in macOS bash 3.2 — guard the expansion.
if [ "${#SITEROWS[@]:-0}" -gt 0 ] && [ -n "$WINDOW" ]; then
  OUT="$DROPS/icx-sweeps/$WINDOW/dish-icx-vacatia3-site-$WINDOW.csv"
  mkdir -p "$(dirname "$OUT")"
  # MERGE, never overwrite: a restricted run (`./dom-sweep.sh MVM783`) would otherwise clobber
  # the sites another run already banked for this same window, silently turning a 3-site record
  # into a 1-site one.
  # Rows go in as ARGV, not stdin: `python3 -` already takes its program from stdin, so a pipe
  # into it is silently swallowed and every row is lost (bit me at 23:19Z — the merge reported
  # "1 of 3 sites" right after successfully banking a second one).
  /usr/bin/python3 - "$OUT" "${SITEROWS[@]}" <<'PY'
import sys, os
HDR = 'handle,name,total_devices,pms_connected,s1_total,m1_total,m2_total,m2_pms_connected'
path = sys.argv[1]
rows = {}
if os.path.exists(path):
    for line in open(path):
        line = line.strip()
        if line and not line.startswith('handle,'):
            rows[line.split(',')[0]] = line
for line in sys.argv[2:]:
    line = line.strip()
    if line:
        rows[line.split(',')[0]] = line
order = ['MVM784', 'MVM783', 'MVM743']
out = [rows[h] for h in order if h in rows] + [v for k, v in rows.items() if k not in order]
open(path, 'w').write(HDR + '\n' + '\n'.join(out) + '\n')
print('   site CSV now holds %d of 3 sites: %s' % (len(out), ', '.join(r.split(',')[0] for r in out)))
PY
  echo "wrote $OUT"
fi

# ── 3. rebuild + publish ───────────────────────────────────────────────────────
[ "$NEWDATA" = "1" ] || echo "no new iCX window this sweep (registry may still have moved)"
./cycle.sh
echo "════ dom-sweep done $(date -u '+%H:%M:%SZ') ════"
