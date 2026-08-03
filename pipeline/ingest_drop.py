#!/usr/bin/env python3
"""Turn a hand-saved HTVC mDNS export into a registry CSV the monitor consumes.

WHY THIS EXISTS. The normal path is on-box agent -> staging -> pull_registry.py. On 2026-08-03 the
staging MySQL volume hit 100% and every writer stopped, and only a Railway workspace admin can
resize it. This is the path that does not touch staging at all: Michele saves the appliance's own
mDNS JSON, drops it in a folder, and the next build cycle picks it up.

HOW TO GET THE FILE (about three clicks):
  1. Open the HTVC support tunnel in a **Firefox PRIVATE window** — Chrome, Chrome incognito and a
     normal Firefox window all fail with "could not open". This is measured, not superstition.
  2. Log into the appliance, then append the query to the address:  <appliance-url>/?refresh_mdns_stbs=1
     That is the same JSON endpoint the on-box agent scrapes, so the content is identical.
  3. Save the page (Cmd+S) into the drop folder with the handle in the filename, e.g.
     `MVM784-mdns.json`. The handle is how the site is identified — nothing else in the payload
     names it.

The file's modification time becomes the capture stamp: for a hand-saved export that IS when the
data was read off the appliance. A dropped file always wins over a staging pull of the same site,
because if you went and fetched it by hand it is the newer truth.

Usage: python3 ingest_drop.py [dropdir] [outdir]
"""
import csv
import datetime
import json
import os
import re
import sys

SITE_RE = re.compile(r'(MVM\d{3,})', re.I)
FIELDS = ('mac', 'room', 'ip')


def is_randomized(mac):
    try:
        return bool(int(str(mac).split(':')[0], 16) & 0x02)
    except (ValueError, IndexError, AttributeError):
        return None


def rows_from(path):
    """Accept the appliance JSON (array, or an object wrapping one) or an already-CSV export."""
    raw = open(path, 'rb').read()
    text = raw.decode('utf-8', errors='replace').lstrip()
    if text[:1] in '[{':
        data = json.loads(text)
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [])
        return [r for r in data if isinstance(r, dict)]
    out = []
    for r in csv.DictReader(text.splitlines()):
        cols = {c.lower(): c for c in r if c}
        pk = lambda p: next((cols[c] for c in cols if c == p or p in c), None)
        out.append({'mac': r.get(pk('mac') or '', ''), 'room': r.get(pk('room') or '', ''),
                    'ip': r.get(pk('ip') or '', ''), 'id': r.get(pk('id') or '', '')})
    return out


def main():
    drop = sys.argv[1] if len(sys.argv) > 1 else 'drop'
    outdir = sys.argv[2] if len(sys.argv) > 2 else '.'
    os.makedirs(drop, exist_ok=True)
    done = os.path.join(drop, 'ingested')
    os.makedirs(done, exist_ok=True)

    files = [f for f in sorted(os.listdir(drop))
             if os.path.isfile(os.path.join(drop, f)) and not f.startswith('.')]
    if not files:
        return 0
    for f in files:
        path = os.path.join(drop, f)
        m = SITE_RE.search(f)
        if not m:
            print(f"  {f}: SKIPPED — no MVM handle in the filename, so the site is unknown. "
                  f"Rename it like 'MVM784-mdns.json'.")
            continue
        site = m.group(1).upper()
        try:
            rows = rows_from(path)
        except Exception as e:  # noqa: BLE001
            print(f"  {f}: SKIPPED — could not parse ({e})")
            continue
        rows = [r for r in rows if str(r.get('mac') or '').strip()
                and str(r.get('mac')).strip() != '00:00:00:00:00:00']
        if not rows:
            print(f"  {f}: SKIPPED — parsed 0 usable rows (wrong page saved?)")
            continue
        cap = datetime.datetime.fromtimestamp(os.path.getmtime(path), datetime.timezone.utc)
        stamp = cap.strftime('%Y%m%dT%H%MZ')
        out = os.path.join(outdir, f'reg-{site}-{stamp}.csv')
        with open(out, 'w', newline='') as fh:
            w = csv.writer(fh)
            w.writerow(['mac', 'room', 'ip', 'stb_id', 'mac_is_randomized'])
            for r in rows:
                mac = str(r.get('mac') or '').strip().lower()
                w.writerow([mac, r.get('room', ''), r.get('ip', ''), r.get('id', ''),
                            is_randomized(mac)])
        rand = sum(1 for r in rows if is_randomized(str(r.get('mac') or '').strip().lower()))
        print(f"  {f}: {site} {len(rows)} rows ({len(rows)-rand} real / {rand} randomized) "
              f"read {cap.strftime('%Y-%m-%d %H:%MZ')} -> {os.path.basename(out)}")
        os.replace(path, os.path.join(done, f))
    return 0


if __name__ == '__main__':
    sys.exit(main())
