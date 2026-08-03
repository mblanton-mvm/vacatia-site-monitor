#!/usr/bin/env python3
"""Build the TV-level roster for the three Vacatia sites from the authoritative room lists.

The room lists under docs/vacatia/rooms/ are the ONLY source of truth for which units exist.
Never derive the room list by grouping observed labels — that sweeps in lockout parents
(606/1405/1504/1511 at Berkley), common-area TVs (GYM, REC.ROOM) and junk (1209G), which is
exactly the error that produced "402 units" instead of 395.

TV label convention:
  2-TV unit  -> <room>@B   (bedroom)   + <room>@L (living)
  3-TV unit  -> <room>@B1  + <room>@B2 + <room>@L      (743 two-bedroom units only)

Usage: python3 build_roster.py rooms/ roster.json
"""
import json
import re
import sys
import os
import collections

SITES = {
    'MVM784': {'file': 'MVM784-Berkley-rooms.md',   'name': 'The Berkley Las Vegas',
               'group_label': 'Floor'},
    'MVM783': {'file': 'MVM783-Grandview-rooms.md', 'name': 'The Grandview Las Vegas',
               'group_label': 'Bldg · Floor'},
    'MVM743': {'file': 'MVM743-Cliffs-rooms.md',    'name': 'The Cliffs at Peace Canyon',
               'group_label': 'Building'},
}
# Entries the room lists contain but which have no TVs. The room list is otherwise
# authoritative; these are known corrections it has not yet absorbed.
#   MVM784 102A/102B — breakrooms, no TVs (established 2026-08-03, commit a8a6ae3;
#   the room MD still lists them at 2 TVs each, which should be fixed upstream).
EXCLUDE = {
    'MVM784': {'102A': 'breakroom, no TVs', '102B': 'breakroom, no TVs'},
}

HEADING = re.compile(r'^##\s+(.+?)\s*(?:\(.*\))?\s*$')
# | 101A | 2 |            or  | 1001 | 3 | 2-bedroom |
ROW = re.compile(r'^\|\s*([A-Za-z0-9.\-]+)\s*\|\s*(\d+)\s*\|(?:\s*([^|]*?)\s*\|)?\s*$')


def tv_labels(room, ntv):
    if ntv == 3:
        return [f'{room}@B1', f'{room}@B2', f'{room}@L']
    if ntv == 2:
        return [f'{room}@B', f'{room}@L']
    # 1-TV or anything unexpected: living only, and flag it
    return [f'{room}@L'][:max(ntv, 1)]


def parse(path):
    group = None
    units = []
    seen = set()
    for line in open(path, encoding='utf-8'):
        line = line.rstrip('\n')
        h = HEADING.match(line)
        if h:
            group = h.group(1).strip()
            continue
        m = ROW.match(line)
        if not m:
            continue
        room, ntv, kind = m.group(1), m.group(2), (m.group(3) or '').strip()
        if room.lower() in ('room', 'total'):      # header row
            continue
        if not ntv.isdigit():
            continue
        if room in seen:
            raise SystemExit(f'DUPLICATE room {room} in {path} — room lists must be unique')
        seen.add(room)
        units.append({'room': room, 'tv_count': int(ntv), 'unit_type': kind or None,
                      'group': group})
    return units


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else 'rooms'
    out = sys.argv[2] if len(sys.argv) > 2 else 'roster.json'
    roster = {}
    for code, cfg in SITES.items():
        path = os.path.join(src, cfg['file'])
        units = parse(path)
        ex = EXCLUDE.get(code, {})
        if ex:
            present = {u['room'] for u in units} & set(ex)
            for room in sorted(present):
                print(f"  excluding {code} {room}: {ex[room]}")
            missing = set(ex) - present
            if missing:
                print(f"  note: {code} exclusions no longer in the room list "
                      f"(fixed upstream?): {sorted(missing)}")
            units = [u for u in units if u['room'] not in ex]
        tvs = []
        for u in units:
            for lab in tv_labels(u['room'], u['tv_count']):
                tvs.append({'site': code, 'room': u['room'], 'label': lab,
                            'position': lab.split('@')[1], 'group': u['group'],
                            'unit_type': u['unit_type'], 'tv_count': u['tv_count']})
        byn = collections.Counter(u['tv_count'] for u in units)
        roster[code] = {'site_name': cfg['name'], 'group_label': cfg['group_label'],
                        'unit_count': len(units), 'tv_count': len(tvs),
                        'tv_count_breakdown': {str(k): v for k, v in sorted(byn.items())},
                        'groups': sorted({u['group'] for u in units if u['group']}),
                        'units': units, 'tvs': tvs}
        print(f"{code}  {len(units):>5} units · {len(tvs):>5} TVs · "
              f"breakdown {dict(sorted(byn.items()))} · {len(roster[code]['groups'])} groups")
    json.dump(roster, open(out, 'w'), indent=1)
    print(f"\nwrote {out}")
    # assertions against the figures stated in the room-list headers, less exclusions
    expect = {'MVM784': (395 - 2, 790 - 4), 'MVM783': (2256, 4512), 'MVM743': (176, 464)}
    for code, (u, t) in expect.items():
        gu, gt = roster[code]['unit_count'], roster[code]['tv_count']
        status = 'OK' if (gu, gt) == (u, t) else 'MISMATCH'
        print(f"  check {code}: units {gu}/{u} · TVs {gt}/{t}  -> {status}")
        if status == 'MISMATCH':
            raise SystemExit(f'{code} roster does not match its own stated totals — stop and fix')


if __name__ == '__main__':
    main()
