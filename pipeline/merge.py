#!/usr/bin/env python3
"""Merge the roster against iCX Online-STB pulls, the mDNS casting registry, and punch-list
completion, producing the state file the monitoring view reads.

Design rules, each learned the hard way:
  * The ROSTER is truth for which units/TVs exist. Never derive rooms from observed labels.
  * iCX lists a box under BOTH its Wi-Fi and Ethernet MAC, one hex apart. All MAC matching
    uses +/-1 tolerance.
  * Randomised vs real MAC is decided by the LOCALLY-ADMINISTERED BIT, never an OUI allowlist.
  * A randomised registry MAC is NOT deletable. Collapse on stb_id: if the same stb_id also has
    a real-MAC row the randomised one is a rotation leftover; if not, it is that room's ONLY
    casting registration.
  * "Absent from one Online-STB snapshot" != "dark". Liveness uses the newest sighting across
    every snapshot ever taken.

Usage: python3 merge.py roster.json out_state.json [--icx-dir DIR] [--registry FILE=SITE ...]
"""
import argparse
import collections
import csv
import datetime
import glob
import json
import os
import re

NULL_MAC = '00:00:00:00:00:00'
STALE_DAYS = 10

# Sites do NOT share a label convention. Berkley/Grandview decorate with '@' ("1711B@L");
# the Cliffs uses a SPACE and B1/B2 ("1003 B2"). Splitting on '@' only made every Cliffs
# bedroom TV read as an unknown room — always parse the separator per what's deployed.
POS_TOKENS = {'B': 'B', 'L': 'L', 'B1': 'B1', 'B2': 'B2',
              'BR': 'B', 'LR': 'L', 'LIV': 'L', 'BED': 'B'}
LABEL_RE = re.compile(r'^\s*(?P<room>.*?)\s*(?:[@ ]\s*(?P<pos>[A-Za-z]{1,3}\d?))?\s*$')


def split_label(raw):
    """-> (room, position or '') for any of the conventions in use across the three sites."""
    m = LABEL_RE.match(raw or '')
    if not m:
        return (raw or '').strip(), ''
    room, pos = m.group('room') or '', (m.group('pos') or '').upper()
    if pos and pos in POS_TOKENS:
        return room.strip(), POS_TOKENS[pos]
    # not a recognised position token -> it is part of the room name (e.g. "REC.ROOM", "3107-ADA")
    return (raw or '').strip(), ''


def mac_int(m):
    return int(m.replace(':', ''), 16) if re.fullmatch(r'[0-9a-f:]{17}', m or '') else None


def is_randomised(m):
    """Locally-administered bit on octet 1. An OUI allowlist previously produced a false
    'Cliffs has 0 real boxes' — never use one."""
    try:
        return bool(int(m.split(':')[0], 16) & 0x02)
    except (ValueError, IndexError, AttributeError):
        return None


def load_icx(path):
    """One MVM7xx Online-STBs export. Returns (site_code, rows)."""
    try:
        rows = list(csv.DictReader(open(path, encoding='utf-8-sig')))
    except Exception:
        return None, []
    if not rows:
        return None, []
    cols = {c.lower(): c for c in rows[0] if c}
    pick = lambda p: next((cols[c] for c in cols if p in c), None)
    room, dev, ts, site = pick('room'), pick('device id'), pick('timestamp'), pick('site name')
    if not all([room, dev, ts, site]):
        return None, []
    out, code = [], None
    for r in rows:
        s = r.get(site) or ''
        m = re.search(r'(MVM\d{3,})', s)
        t = r.get(ts) or ''
        if not m or not re.match(r'20\d\d-\d\d-\d\d', t):
            continue
        code = code or m.group(1)
        out.append({'mac': (r[dev] or '').strip().lower(), 'label': (r[room] or '').strip(),
                    'ts': t})
    return code, out


def load_registry(path):
    """mDNS Management STB list export (the casting registry)."""
    rows = list(csv.DictReader(open(path, encoding='utf-8-sig')))
    out = []
    for r in rows:
        cols = {c.lower(): c for c in r if c}
        pick = lambda p: next((cols[c] for c in cols if c == p or p in c), None)
        mc, rm, ip = pick('mac'), pick('room'), pick('ip')
        if not mc:
            continue
        mac = (r[mc] or '').strip().lower()
        if not mac or mac == NULL_MAC:
            continue
        out.append({'mac': mac, 'label': (r.get(rm) or '').strip(),
                    'ip': (r.get(ip) or '').strip()})
    return out


def load_ledger(path):
    """Appliance STB-monitoring ledger, for stb_id (stable across MAC rotation)."""
    if not path or not os.path.exists(path):
        return []
    return list(csv.DictReader(open(path, encoding='utf-8-sig')))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('roster')
    ap.add_argument('out')
    ap.add_argument('--icx-dir', action='append', default=[])
    ap.add_argument('--registry', action='append', default=[],
                    help='SITE=path to an mDNS registry export')
    ap.add_argument('--ledger', action='append', default=[], help='SITE=path to box-history CSV')
    ap.add_argument('--punchlist', action='append', default=[],
                    help='SITE=path to a completed-rooms CSV')
    ap.add_argument('--now', default=None, help='ISO timestamp treated as "now"')
    a = ap.parse_args()

    roster = json.load(open(a.roster))
    kv = lambda items: dict(x.split('=', 1) for x in items)
    regs, ledgers, punch = kv(a.registry), kv(a.ledger), kv(a.punchlist)

    # ---- iCX: newest sighting per MAC, and per (room, position), across every snapshot ----
    # Keyed on the PARSED (room, position), not the raw label, so a site using "1003 B2"
    # matches the roster's 1003/B2 exactly as one using "1003@B2" would.
    seen_mac, seen_label, snaps = {}, {}, collections.defaultdict(list)
    for d in a.icx_dir:
        for p in sorted(glob.glob(os.path.join(d, '*.csv'))):
            code, rows = load_icx(p)
            if not code or code not in roster:
                continue
            newest = max((r['ts'] for r in rows), default='')
            snaps[code].append({'file': os.path.basename(p), 'captured_at': newest,
                                'devices': len(rows)})
            for r in rows:
                k = (code, r['mac'])
                if r['ts'] > seen_mac.get(k, {}).get('ts', ''):
                    seen_mac[k] = {'ts': r['ts'], 'label': r['label']}
                rm, ps = split_label(r['label'])
                lk = (code, rm, ps)
                if r['ts'] > seen_label.get(lk, {}).get('ts', ''):
                    seen_label[lk] = {'ts': r['ts'], 'mac': r['mac'], 'raw': r['label']}
    now = a.now or max((s['captured_at'] for v in snaps.values() for s in v), default='')
    days = lambda t: round((datetime.datetime.fromisoformat(now)
                            - datetime.datetime.fromisoformat(t)).total_seconds() / 86400, 2)

    state = {'generated_for': now, 'stale_days': STALE_DAYS, 'sites': {}}
    for code, site in roster.items():
        reg = load_registry(regs[code]) if code in regs else None
        led = load_ledger(ledgers.get(code))
        # stb_id -> set of macs, and mac -> stb_id (real-MAC rows only for the "has real" test)
        mac_sid, sid_real = {}, collections.defaultdict(bool)
        for r in led:
            m = (r.get('appliance_mac') or '').strip().lower()
            if not m or m == NULL_MAC:
                continue
            sid = r.get('stb_id') or ''
            mac_sid[m] = sid
            if is_randomised(m) is False:
                sid_real[sid] = True
        reg_by_int = {}
        if reg is not None:
            for r in reg:
                n = mac_int(r['mac'])
                if n is not None:
                    reg_by_int[n] = r
        reg_labels = collections.defaultdict(list)
        for r in (reg or []):
            reg_labels[re.sub(r'@[A-Za-z0-9]+$', '', r['label'])].append(r)

        pl = set()
        if code in punch and os.path.exists(punch[code]):
            for r in csv.DictReader(open(punch[code], encoding='utf-8-sig')):
                v = next((r[k] for k in r if k and 'room' in k.lower()), None)
                if v:
                    pl.add(re.sub(r'@[A-Za-z0-9]+$', '', v.strip()))

        rows, matched_macs = [], set()
        for tv in site['tvs']:
            hit = seen_label.get((code, tv['room'], tv['position']))
            rec = dict(tv)
            rec['icx_raw_label'] = hit['raw'] if hit else None
            if hit:
                matched_macs.add(hit['mac'])
                rec['icx_mac'] = hit['mac']
                rec['icx_last_seen'] = hit['ts']
                rec['days_since'] = days(hit['ts'])
                rec['presence'] = 'OK' if rec['days_since'] <= STALE_DAYS else 'STALE'
            else:
                rec.update(icx_mac=None, icx_last_seen=None, days_since=None,
                           presence='NEVER_SEEN_UNDER_THIS_LABEL')
            # ---- casting registry ----
            if reg is None:
                rec['casting'] = 'NO_REGISTRY_DATA'
                rec['registry_note'] = 'no mDNS export loaded for this site'
            else:
                m = rec['icx_mac']
                n = mac_int(m) if m else None
                real_hit = None
                for c in ((n, n + 1, n - 1) if n is not None else ()):
                    r = reg_by_int.get(c)
                    if r and is_randomised(r['mac']) is False:
                        real_hit = r
                        break
                leftovers = [r for r in reg_labels.get(tv['room'], [])
                             if is_randomised(r['mac'])]
                stranded = []
                for r in leftovers:
                    sid = mac_sid.get(r['mac'], '')
                    if sid and not sid_real.get(sid):
                        stranded.append(r['mac'])
                rec['registry_real_mac'] = real_hit['mac'] if real_hit else None
                rec['registry_randomised_leftovers'] = [r['mac'] for r in leftovers]
                rec['registry_stranded_macs'] = stranded
                if real_hit:
                    rec['casting'] = 'OK'
                elif stranded:
                    rec['casting'] = 'STRANDED_RANDOMISED'
                elif leftovers:
                    rec['casting'] = 'NO_REAL_MAC_LEFTOVER_PRESENT'
                else:
                    rec['casting'] = 'NO_REGISTRY_ENTRY'
            rec['punchlist_complete'] = (tv['room'] in pl) if pl else None
            rows.append(rec)

        # ---- iCX devices whose label matches NO roster TV: unrelabelled or mislabelled ----
        roster_pairs = {(t['room'], t['position']) for t in site['tvs']}
        roster_rooms = {u['room'] for u in site['units']}
        by_room_positions = collections.defaultdict(set)
        for t in site['tvs']:
            by_room_positions[t['room']].add(t['position'])
        unmatched = []
        for (c, mac), v in seen_mac.items():
            if c != code or mac in matched_macs:
                continue
            lbl = v['label']
            room, pos = split_label(lbl)
            if (room, pos) in roster_pairs:
                continue
            if room in roster_rooms and not pos:
                kind = 'NOT_RELABELLED'          # real unit, no TV position on the label
            elif room in roster_rooms:
                kind = 'WRONG_POSITION_SUFFIX'   # e.g. a second @L, or B2 on a 1-bedroom unit
            else:
                kind = 'UNKNOWN_ROOM'            # lockout parent, common area, or a typo
            unmatched.append({'site': code, 'icx_label': lbl, 'room_guess': room,
                              'suffix': pos or None, 'mac': mac, 'icx_last_seen': v['ts'],
                              'days_since': days(v['ts']), 'issue': kind,
                              'expected_positions': sorted(by_room_positions.get(room, []))})
        cnt = lambda key, seq: collections.Counter(r[key] for r in seq)
        state['sites'][code] = {
            'site_name': site['site_name'], 'group_label': site['group_label'],
            'unit_count': site['unit_count'], 'tv_count': site['tv_count'],
            'groups': site['groups'], 'snapshots': sorted(snaps.get(code, []),
                                                          key=lambda s: s['captured_at']),
            'registry_loaded': reg is not None,
            'registry_rows': len(reg) if reg is not None else 0,
            'tvs': rows, 'unmatched_devices': sorted(unmatched, key=lambda r: r['icx_label']),
            'summary': {'presence': dict(cnt('presence', rows)),
                        'casting': dict(cnt('casting', rows)),
                        'label_issues': dict(cnt('issue', unmatched))},
        }
        s = state['sites'][code]['summary']
        print(f"{code}  {site['unit_count']} units / {site['tv_count']} TVs · "
              f"snapshots={len(snaps.get(code, []))} · registry={'yes' if reg is not None else 'NO'}")
        print(f"      presence {s['presence']}")
        print(f"      casting  {s['casting']}")
        print(f"      labels   {s['label_issues'] or '{}'}")
    json.dump(state, open(a.out, 'w'), indent=1)
    print(f"\nwrote {a.out}")


if __name__ == '__main__':
    main()
