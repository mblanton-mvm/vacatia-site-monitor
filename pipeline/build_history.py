#!/usr/bin/env python3
"""Per-box change history, replayed from every banked snapshot.

"1073 was relabeled to 1073@B1 at 14:56" is the fact that makes this view useful during a sweep —
a current-state page cannot tell you whether a room was just worked or has been wrong for a month.
Nothing records these transitions upstream, so we reconstruct them by replaying the snapshots we
already keep, in CAPTURE order (never file mtime).

Two surfaces, keyed independently and never mixed:
  iCX  (icx/*.csv)      -> Ethernet MAC + room label, captured in America/New_York WALL CLOCK
  mDNS (reg-*.csv)      -> Wi-Fi MAC + registry label + IP, captured in UTC

Events emitted per MAC, oldest first:
  icx_first / icx_label   a box appears, or its iCX label changes
  reg_first / reg_label   a registration appears, or its label changes
  reg_ip                  a registration's IP changes
  reg_gone                a registration present in an earlier capture is absent from a later one

A space-form label ("1073 B1") is legacy from the original install's abandoned identifier attempt —
the convention needs '@' AND an HTVC monitoring entry. So a space -> @ transition is the relabeling
work itself and is tagged `relabel`, not treated as an anomaly.

Usage: python3 build_history.py out.json [--icx-dir icx] [--reg-glob 'reg-MVM*.csv']
"""
import argparse
import collections
import csv
import glob
import json
import os
import re

SITE_RE = re.compile(r'(MVM\d{3,})')
AT_FORM = re.compile(r'^\s*\S+@\S+\s*$')
SPACE_FORM = re.compile(r'^\s*\d+\s+[A-Za-z]+\d?\s*$')


def load_icx(path):
    """-> (site, capture_ts, {mac: label}). capture_ts is the max row timestamp (ET wall clock)."""
    try:
        rows = list(csv.DictReader(open(path, encoding='utf-8-sig')))
    except Exception:
        return None, None, {}
    if not rows:
        return None, None, {}
    cols = {c.lower(): c for c in rows[0] if c}
    pick = lambda p: next((cols[c] for c in cols if p in c), None)
    rm, dv, ts, st = pick('room'), pick('device id'), pick('timestamp'), pick('site name')
    if not all([rm, dv, ts, st]):
        return None, None, {}
    site, cap, out = None, '', {}
    for r in rows:
        m = SITE_RE.search(r.get(st) or '')
        t = (r.get(ts) or '').strip()
        if not m or not re.match(r'20\d\d-\d\d-\d\d', t):
            continue
        site = site or m.group(1)
        cap = max(cap, t)
        mac = (r[dv] or '').strip().lower()
        if mac:
            out[mac] = (r[rm] or '').strip()
    return site, cap, out


def load_reg(path):
    """-> (site, capture_ts_utc, {mac: (label, ip)}). Stamp comes from the FILENAME."""
    m = re.search(r'reg-(MVM\d{3,})-(20\d{6})T(\d{4})Z', os.path.basename(path))
    if not m:
        return None, None, {}
    cap = f'{m.group(2)[:4]}-{m.group(2)[4:6]}-{m.group(2)[6:]} {m.group(3)[:2]}:{m.group(3)[2:]}Z'
    out = {}
    try:
        rows = list(csv.DictReader(open(path, encoding='utf-8-sig')))
    except Exception:
        return None, None, {}
    for r in rows:
        cols = {c.lower(): c for c in r if c}
        pk = lambda p: next((cols[c] for c in cols if c == p or p in c), None)
        mc, rm, ipc = pk('mac'), pk('room'), pk('ip')
        mac = (r.get(mc) or '').strip().lower() if mc else ''
        if mac and mac != '00:00:00:00:00:00':
            out[mac] = ((r.get(rm) or '').strip(), (r.get(ipc) or '').strip())
    return m.group(1), cap, out


def kind_of(before, after):
    """Classify a label transition so the view can say what actually happened."""
    if SPACE_FORM.match(before or '') and AT_FORM.match(after or ''):
        return 'relabel'          # the abandoned space convention -> the working @ convention
    if not AT_FORM.match(before or '') and AT_FORM.match(after or ''):
        return 'relabel'          # bare room number -> positioned
    if AT_FORM.match(before or '') and not AT_FORM.match(after or ''):
        return 'lost_position'
    return 'changed'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--icx-dir', action='append', default=['icx'])
    ap.add_argument('--reg-glob', default='reg-MVM*.csv')
    ap.add_argument('--sites', default='MVM784,MVM783,MVM743')
    a = ap.parse_args()

    # ---- iCX: dedupe identical captures (the same poll is banked under two names), replay in order
    snaps = collections.defaultdict(dict)          # site -> {capture_ts: {mac: label}}
    for d in a.icx_dir:
        for p in sorted(glob.glob(os.path.join(d, '*.csv'))):
            site, cap, macs = load_icx(p)
            if site and cap and macs:
                snaps[site].setdefault(cap, {}).update(macs)

    hist = {}
    for site, caps in snaps.items():
        seen, ev = {}, collections.defaultdict(list)
        for cap in sorted(caps):
            for mac, label in caps[cap].items():
                if mac not in seen:
                    ev[mac].append({'t': cap, 'k': 'icx_first', 'to': label})
                elif seen[mac] != label:
                    ev[mac].append({'t': cap, 'k': 'icx_label', 'from': seen[mac], 'to': label,
                                    'how': kind_of(seen[mac], label)})
                seen[mac] = label
        hist[site] = {'icx_captures': sorted(caps), 'events': dict(ev)}

    # ---- mDNS registry: same replay, plus disappearance, which matters for tombstones
    regs = collections.defaultdict(dict)           # site -> {capture_ts: {mac: (label, ip)}}
    for p in sorted(glob.glob(a.reg_glob)):
        site, cap, macs = load_reg(p)
        if site and cap and macs:
            regs[site][cap] = macs
    for site, caps in regs.items():
        h = hist.setdefault(site, {'icx_captures': [], 'events': {}})
        ev = collections.defaultdict(list, {k: list(v) for k, v in h['events'].items()})
        seen = {}
        for cap in sorted(caps):
            cur = caps[cap]
            for mac, (label, ip) in cur.items():
                if mac not in seen:
                    ev[mac].append({'t': cap, 'k': 'reg_first', 'to': label})
                else:
                    if seen[mac][0] != label:
                        ev[mac].append({'t': cap, 'k': 'reg_label', 'from': seen[mac][0],
                                        'to': label, 'how': kind_of(seen[mac][0], label)})
                    if seen[mac][1] != ip:
                        ev[mac].append({'t': cap, 'k': 'reg_ip', 'from': seen[mac][1], 'to': ip})
                seen[mac] = (label, ip)
            for mac in list(seen):
                if mac not in cur and seen[mac] is not None:
                    ev[mac].append({'t': cap, 'k': 'reg_gone', 'from': seen[mac][0]})
                    seen[mac] = None
        h['events'] = dict(ev)
        h['reg_captures'] = sorted(caps)

    # Keep only the Vacatia handles (the icx dir holds old exports from other properties) and only
    # MACs that actually changed — a first-sighting row for all 11,496 MACs at MVM783 would triple
    # the page for no information. The capture lists stay, so the view can still say how many polls
    # a box sat unchanged through.
    keep = set(a.sites.split(',')) if a.sites else None
    out = {}
    for site, h in hist.items():
        if keep and site not in keep:
            continue
        h.setdefault('reg_captures', [])
        ev = {m: e for m, e in h['events'].items()
              if any(x['k'] not in ('icx_first', 'reg_first') for x in e)}
        tracked = len(h['events'])
        h['events'] = ev
        out[site] = h
        print(f"{site}: {len(h['icx_captures'])} iCX captures, {len(h['reg_captures'])} registry "
              f"captures, {tracked} MACs tracked, {len(ev)} with a real change")

    json.dump(out, open(a.out, 'w'), indent=1)
    print(f"\nwrote {a.out}")


if __name__ == '__main__':
    main()
