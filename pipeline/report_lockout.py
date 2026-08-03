#!/usr/bin/env python3
"""Render the lockout report in Michele's spreadsheet layout.

One block per ROOM (summary + TV matrix + punchlist), then one detail table per LOCKOUT covering
every side. Emits CSV (openable in Excel/Sheets, same shape as the mockup) and a text render.

Usage:
  python3 report_lockout.py lockouts.json rooms-state.json --room 1405B          # one block
  python3 report_lockout.py lockouts.json rooms-state.json --site MVM784 --all -o out.csv
"""
import argparse
import csv
import datetime
import json
import re
import sys

PRES = {'ALL_PRESENT': 'All boxes seen', 'PARTIAL': 'Some boxes missing',
        'NONE': 'No boxes seen', 'EXTRA_BOXES': 'More boxes than expected'}
LABL = {'LABELLED_OK': 'Labelled', 'NOT_RELABELLED': 'Not relabelled',
        'PARTIALLY_LABELLED': 'Partly labelled', 'DUPLICATE_POSITION': 'Duplicate position',
        'WRONG_POSITION': 'Wrong position', 'NO_BOXES_SEEN': 'No boxes'}
CAST = {'ALL_CASTABLE': 'All Castable', 'SOME_CANNOT_CAST': 'Some cannot cast',
        'NONE_CASTABLE': 'None castable', 'NO_REGISTRY_DATA': 'No registry export'}


def pdt(iso):
    if not iso:
        return ''
    try:
        t = datetime.datetime.fromisoformat(iso.replace('Z', '+00:00'))
        t = t.astimezone(datetime.timezone(datetime.timedelta(hours=-7)))
        return t.strftime('%-m/%-d/%Y, %-I:%M:%S %p PDT')
    except Exception:
        return iso


def room_block(site, lock, room, rstate):
    """Rows for one room, in the mockup's order. Each row is a list of cells."""
    R = [r for r in lock['rooms'] if r['room'] == room][0]
    st = next((x for x in rstate['rooms'] if x['room'] == room), {}) if rstate else {}
    p = R.get('punch') or {}
    n = p.get('names') or {}
    order = [k for k in ('B', 'B1', 'B2', 'L') if k in R['positions']]
    names = [R['position_names'][k] for k in order]
    out = []
    out.append(['ROOM', 'Presence (ICX)', 'Labeling (ICX)', 'Casting (MDNS)'])
    out.append([room, PRES.get(st.get('presence'), st.get('presence', '')),
                LABL.get(st.get('labelling'), st.get('labelling', '')),
                CAST.get(st.get('casting'), st.get('casting', ''))])
    out.append([])
    unl = R.get('unlabeled') or []
    out.append(['TV'] + names + ([f"{room} (Not Labeled)"] if unl else []))
    out.append(['ICX'] + [(R['positions'][k]['icx_label'] if R['positions'][k] else 'Missing')
                          for k in order] + ([u['icx_label'] for u in unl] if unl else []))
    out.append(['MAC ID'] + [(R['positions'][k]['ethernet_mac'] if R['positions'][k] else '')
                             for k in order] + ([u['ethernet_mac'] for u in unl] if unl else []))
    out.append([])
    # which position is unlabelled/absent, plus the best guess for where a stray box belongs
    corr = [d['labeling_correction'] for d in lock['detail']
            if d['labeling_correction'] and d['icx_label'].startswith(lock['lockout'])
            and not re.match(rf"^{re.escape(lock['lockout'])}[ABS]", d['icx_label'])]
    out.append(['Missing Position', ', '.join(R['missing_positions']) or 'none',
                (corr[0].replace('Likely ', 'likely labeled ') if corr else '')])
    out.append(['Reswept', 'Completed' if R['punchlist_completed'] else 'Not completed',
                pdt((R.get('punch') or {}).get('done_at')) if R['punchlist_completed'] else ''])
    out.append([])
    out.append(['PUNCHLIST DATA', pdt(p.get('done_at'))])
    out.append(['Casting QR seen on', (p.get('qr_seen_on') or 'not recorded')
                + (' TV' if p.get('qr_seen_on') == 'neither' else '')])
    out.append(['Use device MAC confirmed', p.get('use_device_mac') or 'not recorded'])
    out.append(['Firmware', p.get('firmware') or 'not recorded'])
    out.append(['Live linear', p.get('live_linear_on') or 'not recorded'])
    out.append(['Guest Name', (n.get('guest_initials') or n.get('occupancy', '')).title()
                if n else '', 'Bedroom Displayed', n.get('bedroom_displayed', ''),
                'Livingroom Displayed', n.get('livingroom_displayed', ''),
                'Verdict', n.get('verdict', '')])
    return out


def detail_block(lock):
    out = [[]]
    out.append(['ICX Label', 'Ethernet Mac (ICX DeviceID)', 'WIFI MAC (RMMS2.0 / derived +1)',
                'Last Seen (ICX)', 'MDNS Label', 'MDNS MAC', 'MDNS IP', 'MDNS ?',
                'Labeling Correction'])
    for d in lock['detail']:
        out.append([d['icx_label'], d['ethernet_mac'], d['wifi_mac_derived'].upper(),
                    d['last_seen_icx'], d['mdns_label'], d['mdns_mac'], d['mdns_ip'],
                    d['mdns_note'], d['labeling_correction']])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('lockouts'); ap.add_argument('rooms_state')
    ap.add_argument('--site', default='MVM784')
    ap.add_argument('--room'); ap.add_argument('--lockout')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('-o', '--out')
    a = ap.parse_args()
    L = json.load(open(a.lockouts))['sites'][a.site]
    S = json.load(open(a.rooms_state))['sides' if False else 'sites'][a.site]

    if a.room:
        targets = [x for x in L['lockouts'] if a.room in x['sides']]
    elif a.lockout:
        targets = [x for x in L['lockouts'] if x['lockout'] == a.lockout]
    elif a.all:
        targets = L['lockouts']
    else:
        sys.exit('pass --room, --lockout or --all')
    if not targets:
        sys.exit('no matching lockout')

    rows = []
    for lock in targets:
        rows.append([f"LOCKOUT {lock['lockout']}  ({a.site} — sides {', '.join(lock['sides'])})"])
        for room in lock['sides']:
            if a.room and room != a.room and not a.all:
                pass  # still show the sibling for context
            rows += room_block(a.site, lock, room, S)
            rows.append([])
        rows += detail_block(lock)
        rows.append([]); rows.append([])

    if a.out:
        with open(a.out, 'w', newline='') as fh:
            csv.writer(fh).writerows(rows)
        print(f"wrote {a.out}  ({len(rows)} rows, {len(targets)} lockout(s))")
    else:
        w = max((len(str(c)) for r in rows for c in r), default=10)
        w = min(w, 30)
        for r in rows:
            print(' | '.join(str(c).ljust(w) if i == 0 else str(c) for i, c in enumerate(r)))


if __name__ == '__main__':
    main()
