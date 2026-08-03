#!/usr/bin/env python3
"""Per-device outage analysis across today's iCX Online-STB polls.

The Online-STBs export is a PRESENCE list: a device is in the file iff iCX saw it
report in that 15-minute window. So "had an issue" == absent from a poll it should
have been in, and the signal Michele wants is absence that PERSISTS across
back-to-back polls (a single-poll gap is normal churn on this fleet).

Timestamps come from the file CONTENTS, not the mtime, and are EASTERN wall clock
(the export header says UTC-04:00). Never treat them as UTC.
"""
import csv, glob, os, re, sys, json
from collections import defaultdict

SITES = {'MVM743': 'The Cliffs At Peace Canyon',
         'MVM783': 'The Grandview at Las Vegas',
         'MVM784': 'The Berkley Las Vegas'}
DAY = sys.argv[1] if len(sys.argv) > 1 else '20260803'
ICX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icx')


def load(handle):
    """-> (ordered window labels, {device_id: {window: room}})"""
    polls, seen = [], defaultdict(dict)
    for f in sorted(glob.glob(f'{ICX}/icx-online-stbs-{handle}-{DAY}T*ET.csv')):
        w = re.search(r'T(\d{4})ET', f).group(1)
        w = f'{w[:2]}:{w[2:]}'
        with open(f, encoding='utf-8-sig') as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            continue
        polls.append(w)
        for r in rows:
            did = (r.get('Device ID') or r.get('﻿Device ID') or '').strip()
            if did:
                seen[did][w] = (r.get('Room Number') or '').strip()
    return polls, seen


def runs_of_absence(present_flags, polls):
    """Maximal runs of consecutive False, as (start_window, end_window, length)."""
    out, i, n = [], 0, len(polls)
    while i < n:
        if not present_flags[i]:
            j = i
            while j + 1 < n and not present_flags[j + 1]:
                j += 1
            out.append((polls[i], polls[j], j - i + 1))
            i = j + 1
        else:
            i += 1
    return out


def analyse(handle):
    polls, seen = load(handle)
    if not polls:
        return None
    devices = []
    for did, wins in seen.items():
        flags = [w in wins for w in polls]
        first = flags.index(True)
        last = len(flags) - 1 - flags[::-1].index(True)
        room = next(iter(wins.values())) or '(no room)'
        # only count gaps BETWEEN first and last sighting, plus a trailing gap
        # (dark since); a leading gap just means the box joined the day late.
        interior = runs_of_absence(flags, polls)
        gaps = [g for g in interior
                if polls.index(g[0]) > first and polls.index(g[1]) <= last]
        trailing = [g for g in interior if polls.index(g[0]) > last]
        # poll spacing is NOT uniform today (some gaps are 90 min), so a run of N
        # polls is not N*15 min. Report the real wall-clock span: from the last
        # poll it WAS seen in, to the first poll it came back in.
        def mins(hhmm):
            return int(hhmm[:2]) * 60 + int(hhmm[3:])

        def span(g):
            i, j = polls.index(g[0]), polls.index(g[1])
            lo = polls[i - 1] if i > 0 else g[0]
            hi = polls[j + 1] if j + 1 < len(polls) else g[1]
            return mins(hi) - mins(lo)

        allg = [{'from': g[0], 'to': g[1], 'polls': g[2], 'minutes': span(g),
                 'trailing': g in trailing} for g in gaps + trailing]
        devices.append({
            'device': did, 'room': room,
            'polls_seen': sum(flags), 'polls_total': len(polls),
            'gaps': allg,
            'worst_gap': max([g['polls'] for g in allg], default=0),
            'worst_minutes': max([g['minutes'] for g in allg], default=0),
            'dark_since': trailing[0][0] if trailing else None,
            'last_seen': polls[last],
        })
    return {'handle': handle, 'name': SITES[handle], 'polls': polls,
            'devices': devices,
            'counts': {w: sum(1 for d in seen.values() if w in d) for w in polls}}


if __name__ == '__main__':
    out = {}
    for h in SITES:
        a = analyse(h)
        if a:
            out[h] = a
            sust = [d for d in a['devices'] if d['worst_gap'] >= 2]
            print(f"{h}: {len(a['polls'])} polls, {len(a['devices'])} devices ever seen, "
                  f"{len(sust)} with a 2+ poll outage, "
                  f"{sum(1 for d in a['devices'] if d['dark_since'])} dark at end of day")
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     'outages.json'), 'w'), indent=1)
    print('wrote outages.json')
