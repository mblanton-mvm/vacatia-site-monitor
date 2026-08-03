#!/bin/bash
# One refresh of the whole monitor. Run after each 15-min iCX sweep has banked its
# per-device Online-STBs CSVs into ./icx/.
#
#   1. re-pull punch_rooms from Supabase (read-only publishable key, all 3 sites)
#   2. re-read the newest mDNS registry export present  (see MDNS NOTE below)
#   3. rebuild room state + lockout detail
#   4. rebuild the artifact page
#
# MDNS NOTE: the casting registry cannot be pulled from this machine. It lives in
# staging `staging_site_captures` (kind=htvc_mdns_stbs), which needs the Railway
# MYSQL_PUBLIC_URL Jarran holds, or a manual export. So step 2 reuses the newest
# reg-<SITE>.csv on disk and the build stamps its age. Set STAGING_MYSQL_URL and this
# script will pull fresh instead.
set -uo pipefail
cd "$(dirname "$0")"

SUPA_URL='https://zhqyjmugyoqvztqqegis.supabase.co'
SUPA_KEY='sb_publishable_Ehu06hwE9_VJyT_ivtteyg_bmos1qeN'   # publishable, read-only by design
LEDGER=/Users/micheleblanton/Developer/mvm-platform/docs/vacatia/verification/MVM784-box-history-enriched-2026-07-31.csv
PUNCHVERIFY=/Users/micheleblanton/Developer/mvm-platform/docs/vacatia/verification/MVM784-completed-rooms-label-verify-2026-07-31.csv

echo "── 1. punch_rooms ──"
for p in 784 783 743; do
  n=0
  for start in 0 1000 2000 3000; do
    out="punch-$p.page$start.json"
    code=$(curl -s -o "$out" -w '%{http_code}' --max-time 40 \
      -H "apikey: $SUPA_KEY" -H "Authorization: Bearer $SUPA_KEY" \
      -H "Range: $start-$((start+999))" \
      "$SUPA_URL/rest/v1/punch_rooms?property=eq.$p&select=room_id,data,updated_at")
    [ "$code" != "200" ] && { echo "   MVM$p page $start HTTP $code — keeping previous pull"; rm -f "$out"; break; }
    got=$(python3 -c "import json,sys;print(len(json.load(open('$out'))))" 2>/dev/null || echo 0)
    n=$((n+got)); [ "$got" -lt 1000 ] && break
  done
  if ls punch-$p.page*.json >/dev/null 2>&1; then
    python3 - "$p" << 'PY'
import glob, json, sys
p = sys.argv[1]
rows = []
for f in sorted(glob.glob(f'punch-{p}.page*.json')):
    rows += json.load(open(f))
json.dump(rows, open(f'punch-{p}.json', 'w'))
print(f"   MVM{p}: {len(rows)} punch rooms")
PY
    rm -f punch-$p.page*.json
  fi
done

echo "── 2. mDNS registry ──"
# Pull it straight from staging (read-only, TLS, keychain credential). Superseded the manual
# HTVC-GUI export on 2026-08-03. If the pull fails for any reason the build still runs on the
# newest export already on disk and the page stamps its real age, which is the whole point of
# the per-source freshness panel.
if ! python3 pull_registry.py .; then
  echo "   !! staging pull FAILED — falling back to the newest export on disk"
fi
# Hand-saved appliance exports (see ingest_drop.py). The path that does NOT depend on staging,
# which matters whenever the staging volume is full and every writer is blocked.
python3 ingest_drop.py drop . || echo "   !! drop ingest error (continuing)"
for p in 784 783 743; do
  f=$(ls reg-MVM$p-*.csv 2>/dev/null | sort | tail -1)
  if [ -z "$f" ]; then
    echo "   MVM$p: NO registry export — casting reads 'no registry export'"
  fi
done

echo "── 3. rebuild state ──"
# newest registry per site; the capture stamp lives in the FILENAME (never the mtime)
REG=(); for p in 784 783 743; do
  f=$(ls reg-MVM$p-*.csv 2>/dev/null | sort | tail -1)
  [ -n "$f" ] && REG+=(--registry "MVM$p=$f")
done
PJ=();  for p in 784 783 743; do [ -f "punch-$p.json" ] && PJ+=(--punchjson "MVM$p=punch-$p.json"); done

python3 merge_rooms.py roster.json rooms-state.json --icx-dir icx \
  "${REG[@]}" "${PJ[@]}" --ledger "MVM784=$LEDGER" --punchlist "MVM784=$PUNCHVERIFY" || exit 1
python3 build_lockouts.py roster.json lockouts.json lockout-detail.csv --icx-dir icx \
  "${REG[@]}" "${PJ[@]}" || exit 1

echo "── 3b. change history ──"
python3 build_history.py history.json || exit 1

echo "── 4. rebuild page ──"
python3 build_artifact2.py rooms-state.json artifact.html lockouts.json history.json || exit 1
python3 - << 'PY'
# fail loudly rather than publish a page with leaked guest names
import re
h = open('artifact.html', encoding='utf-8').read()
assert not re.search(r'guestLocked|bedName|livName', h), 'PII FIELD LEAKED — not publishing'
bad = [m for m in re.findall(r'"([A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}[^"]*)"', h)
       if 'Las Vegas' not in m and 'Peace Canyon' not in m]
assert not bad, f'NAME-SHAPED STRINGS IN PAGE: {bad[:3]}'
print('   PII gate: clean')
PY
echo "── refresh complete ──"
