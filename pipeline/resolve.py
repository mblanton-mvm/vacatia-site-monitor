#!/usr/bin/env python3
"""For every TV whose iCX label is not a roster TV, infer which unit it REALLY belongs to.

Each row carries the evidence and a confidence, plus the check that would settle it, because
a wrong room assignment sends a tech to the wrong door. Nothing here is applied automatically.

Usage: python3 resolve.py state.json roster.json out.csv
"""
import collections
import csv
import itertools
import json
import re
import sys

COMMON = re.compile(r'^(GYM|REC[.\s]?ROOM|LOBBY|MAINTENANCE|POOL|CLUB|OFFICE|FITNESS)', re.I)


def transpositions(s):
    """Adjacent-digit swaps of the numeric part — how 1403B becomes 1430B."""
    out = set()
    for i in range(len(s) - 1):
        if s[i].isdigit() and s[i + 1].isdigit():
            out.add(s[:i] + s[i + 1] + s[i] + s[i + 2:])
    return out - {s}


def main():
    state = json.load(open(sys.argv[1] if len(sys.argv) > 1 else 'state.json'))
    roster = json.load(open(sys.argv[2] if len(sys.argv) > 2 else 'roster.json'))
    out_path = sys.argv[3] if len(sys.argv) > 3 else 'resolution.csv'
    rows = []
    for code, s in state['sites'].items():
        units = {u['room'] for u in roster[code]['units']}
        # which positions each roster room is currently MISSING in iCX
        have = collections.defaultdict(set)
        for t in s['tvs']:
            if t['presence'] != 'NEVER_SEEN_UNDER_THIS_LABEL':
                have[t['room']].add(t['position'])
        want = collections.defaultdict(set)
        for t in s['tvs']:
            want[t['room']].add(t['position'])
        missing = {r: sorted(want[r] - have[r]) for r in want}

        for u in s['unmatched_devices']:
            lbl, room, pos = u['icx_label'], u['room_guess'], u['suffix'] or ''
            cand, why, conf, settle = '', '', '', ''
            if u['issue'] == 'NOT_RELABELLED':
                gaps = missing.get(room, [])
                cand = room
                why = (f"room is a real unit; label carries no TV position. "
                       f"Unfilled positions for this unit: {', '.join(gaps) or 'none'}")
                conf = 'HIGH (room certain, position unknown)'
                settle = (f"In the room, note which TV this box drives, then label it "
                          f"{room}@{gaps[0] if gaps else 'B or L'}")
            elif u['issue'] == 'WRONG_POSITION_SUFFIX':
                gaps = missing.get(room, [])
                cand = room
                why = (f"room is a real unit but position '{pos}' is not one this unit has "
                       f"({', '.join(u.get('expected_positions') or [])}); "
                       f"unfilled: {', '.join(gaps) or 'none'}")
                conf = 'HIGH (room certain, position wrong)'
                settle = f"Confirm which TV it drives; correct to one of {', '.join(u.get('expected_positions') or [])}"
            elif COMMON.match(lbl) or re.fullmatch(r'\d{1,3}', lbl.strip()):
                cand = '(not a guest unit)'
                why = ('common-area or unnumbered TV — matches no unit in the room list'
                       if COMMON.match(lbl) else
                       'bare 1-3 digit label — not a unit number at this site')
                conf = 'HIGH (exclude from unit counts)'
                settle = 'Confirm it is a common-area TV and keep it out of the room roster'
            else:
                # lockout parent: <label> + A/B/S is a real unit that is missing this position
                kids = sorted(x for x in units
                              if x.startswith(room) and x != room and len(x) == len(room) + 1)
                kid_gaps = [(k, missing.get(k, [])) for k in kids]
                short = [k for k, g in kid_gaps if g]
                if kids and short:
                    cand = ' or '.join(short)
                    why = (f"lockout parent label: '{room}' is not a unit, but {', '.join(kids)} "
                           f"are, and {', '.join(f'{k} is missing {chr(44).join(g)}' for k, g in kid_gaps if g)}")
                    conf = 'MEDIUM-HIGH' if len(short) == 1 else 'MEDIUM (two candidates)'
                    settle = f"Check which side ({', '.join(short)}) the TV is physically in, then relabel"
                else:
                    tp = sorted(transpositions(room) & units)
                    if tp:
                        cand = tp[0]
                        why = (f"digit transposition of '{room}' -> '{tp[0]}', which is a real unit"
                               + (f" missing {', '.join(missing.get(tp[0], []))}"
                                  if missing.get(tp[0]) else ''))
                        conf = 'HIGH' if missing.get(tp[0]) else 'MEDIUM'
                        settle = f"Confirm the TV is in {tp[0]}, then correct the label"
                    else:
                        cand = 'UNRESOLVED'
                        why = 'no unit in the room list matches this label by prefix or digit swap'
                        conf = 'NONE'
                        settle = 'Walk the label; it may be a retired room, a typo, or a new unit'
            rows.append({'site': code, 'icx_label': lbl, 'device_id_mac': u['mac'],
                         'icx_room_reads': room, 'icx_position_reads': pos or '(none)',
                         'issue': u['issue'], 'probable_real_unit': cand,
                         'confidence': conf, 'evidence': why, 'how_to_settle': settle,
                         'icx_last_seen': u['icx_last_seen'], 'days_since': u['days_since']})
    order = {'UNKNOWN_ROOM': 0, 'WRONG_POSITION_SUFFIX': 1, 'NOT_RELABELLED': 2}
    rows.sort(key=lambda r: (order[r['issue']], r['site'], r['icx_label']))
    with open(out_path, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out_path}  ({len(rows)} devices)")
    for code in state['sites']:
        sub = [r for r in rows if r['site'] == code]
        print(f"  {code}: {len(sub)}  " + str(dict(collections.Counter(r['issue'] for r in sub))))
    print("\n=== UNKNOWN_ROOM — the ones genuinely in question ===")
    for r in rows:
        if r['issue'] != 'UNKNOWN_ROOM':
            continue
        print(f"  {r['site']} {r['icx_label']:<13} {r['device_id_mac']}  ->  "
              f"{r['probable_real_unit']:<22} [{r['confidence']}]")
        print(f"        {r['evidence']}")


if __name__ == '__main__':
    main()
