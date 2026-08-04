#!/usr/bin/env python3
"""Lockout-level detail: every TV in a lockout across iCX, RMMS-derived Wi-Fi MAC, and mDNS.

A LOCKOUT is the parent unit number; its sides are A / B / (sometimes) S at MVM783 and MVM784.
MVM743 is not a lockout site — each room stands alone, with Bedroom 1 / Bedroom 2 / Livingroom.

Surfaces and how they key (measured 2026-08-03, see reference-icx-mdns-mac-offset):
  iCX  Device ID      = ETHERNET MAC
  RMMS hardwareId     = factory WI-FI MAC = Ethernet + 1
  mDNS registry MAC   = the Wi-Fi MAC the box was ADVERTISING when it registered
                        (factory Wi-Fi if "Use device MAC" is on; a randomised value if not)
So the join is: iCX Ethernet + 1 -> mDNS. The Wi-Fi column here is DERIVED from that +1 and is
marked as such; a per-device RMMS pull would confirm it (join on hardwareId, never wlan0.mac).

Outputs: lockouts.json (for the view) and a flat CSV for reporting.

Usage: python3 build_lockouts.py roster.json out.json out.csv [--icx-dir D] [--registry S=f] ...
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
POS_TOKENS = {'B': 'B', 'L': 'L', 'B1': 'B1', 'B2': 'B2',
              'BR': 'B', 'LR': 'L', 'LIV': 'L', 'BED': 'B'}
POS_NAME = {'B': 'Bedroom', 'L': 'Livingroom', 'B1': 'Bedroom 1', 'B2': 'Bedroom 2'}
LABEL_RE = re.compile(r'^\s*(?P<room>.*?)\s*(?:[@ ]\s*(?P<pos>[A-Za-z]{1,3}\d?))?\s*$')
SIDE_RE = re.compile(r'^(?P<lock>\d{3,4})(?P<side>[ABS])$')


def split_label(raw):
    m = LABEL_RE.match(raw or '')
    if not m:
        return (raw or '').strip(), ''
    room, pos = (m.group('room') or '').strip(), (m.group('pos') or '').upper()
    if pos and pos in POS_TOKENS:
        return room, POS_TOKENS[pos]
    return (raw or '').strip(), ''


def mac_int(m):
    return int(m.replace(':', ''), 16) if re.fullmatch(r'[0-9a-f:]{17}', (m or '').lower()) else None


def mac_hex(n):
    return ':'.join(f'{n:012x}'[i:i + 2] for i in range(0, 12, 2)) if n is not None else ''


def is_randomised(m):
    try:
        return bool(int(m.split(':')[0], 16) & 0x02)
    except (ValueError, IndexError, AttributeError):
        return None


def lockout_of(room):
    m = SIDE_RE.match(room or '')
    return m.group('lock') if m else room


def load_icx(path):
    try:
        rows = list(csv.DictReader(open(path, encoding='utf-8-sig')))
    except Exception:
        return None, []
    if not rows:
        return None, []
    cols = {c.lower(): c for c in rows[0] if c}
    pick = lambda p: next((cols[c] for c in cols if p in c), None)
    rm, dv, ts, st = pick('room'), pick('device id'), pick('timestamp'), pick('site name')
    if not all([rm, dv, ts, st]):
        return None, []
    out, code = [], None
    for r in rows:
        m = re.search(r'(MVM\d{3,})', r.get(st) or '')
        t = r.get(ts) or ''
        if not m or not re.match(r'20\d\d-\d\d-\d\d', t):
            continue
        code = code or m.group(1)
        out.append({'mac': (r[dv] or '').strip().lower(),
                    'label': (r[rm] or '').strip(), 'ts': t})
    return code, out


def guest_check(d):
    """Compare the tech-recorded on-screen name against the checked-in guest's INITIALS.
    Returns verdict + per-TV state only — the guest name string never leaves this function."""
    gl = str(d.get('guestLocked') or '').strip()
    tv = d.get('tv') if isinstance(d.get('tv'), dict) else {}
    occ = 'vacant' if gl.upper() == 'VACANT' else ('occupied' if gl else 'unknown')
    ini = lambda s: (lambda p: (p[0][:1] + p[-1][:1]).upper() if len(p) > 1
                     else (p[0][:1].upper() if p else ''))(
        [x for x in re.split(r'[\s,]+', str(s or '').strip()) if x])
    norm = lambda v: re.sub(r'[^A-Z]', '', str(v or '').upper())
    # MVM743's punch app writes the v2 flat shape ("_v2": true): the bedroom TV is under
    # tv["b1"] (its units are Bedroom 1 / Bedroom 2 / Living), and the live-linear answer is a
    # flat "linear" key. MVM784's app nests answers under "picks" and uses tv["bed"]. Reading only
    # the 784 shape made every relabelled 743 room report its bedroom as "Welcome" when the tech
    # had actually recorded a name -- room 2049 showed "Welcome" against a punch entry reading
    # "Judson York". Accept both shapes; prefer the explicit bedroom key when present.
    # A Cliffs 2-bedroom unit has TWO bedroom TVs; both are reported in the one bedroom cell,
    # split by " | ". Kept identical to merge_rooms.name_check so the drill and the lockout
    # table cannot disagree about the same room.
    bedkeys = [k for k in ('bed', 'b', 'b1', 'b2') if isinstance(tv.get(k), dict)] or ['bed']
    per = {}
    for k in bedkeys + ['liv']:
        p = tv.get(k) if isinstance(tv.get(k), dict) else {}
        shown, disp = str(p.get('name') or '').strip(), str(p.get('display') or '')
        if not shown and disp != 'name':
            per[k] = 'Welcome'
        elif not shown:
            per[k] = 'Name shown, not recorded'
        elif occ == 'occupied' and norm(gl) and ini(shown) == norm(gl):
            per[k] = 'Matches guest'
        elif occ == 'occupied':
            per[k] = 'Different name'
        else:
            per[k] = 'Name shown (no guest on file)'
    # Reads EVERY TV in the unit. Collapsing two bedrooms to one scalar always loses a defect —
    # see the note in merge_rooms.name_check. For a 2-TV unit this is the old pair logic exactly.
    bvals, l = [per[k] for k in bedkeys], per['liv']
    vals = bvals + [l]
    named = lambda v: v != 'Welcome'
    if occ == 'vacant':
        v = ('Vacant / inconclusive' if not any(named(x) for x in vals)
             else 'NAME SHOWING WHILE VACANT')
    elif occ == 'occupied':
        if all(x == 'Welcome' for x in vals):
            v = 'NO NAME ON EITHER TV'
        elif any(x == 'Welcome' for x in vals):
            v = 'NAME ON ONE TV ONLY'
        elif 'Different name' in vals and 'Matches guest' in vals:
            v = 'TVS DISAGREE'
        elif all(x == 'Different name' for x in vals):
            v = 'WRONG NAME BOTH TVS'
        else:
            v = 'Both correct'
    else:
        v = 'Occupancy unknown'
    return {'occupancy': occ, 'guest_initials': gl if occ == 'occupied' else None,
            'bedroom_displayed': ' | '.join(bvals), 'livingroom_displayed': l, 'verdict': v}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('roster'); ap.add_argument('out_json'); ap.add_argument('out_csv')
    ap.add_argument('--icx-dir', action='append', default=[])
    ap.add_argument('--registry', action='append', default=[])
    ap.add_argument('--punchjson', action='append', default=[])
    a = ap.parse_args()
    roster = json.load(open(a.roster))
    kv = lambda xs: dict(x.split('=', 1) for x in xs)
    regs, pj = kv(a.registry), kv(a.punchjson)

    # Scoped to the current day per site, exactly like merge_rooms — otherwise the lockout tables
    # would list boxes from months-old exports that the tallies above no longer count, and the two
    # halves of the page would disagree about which boxes exist.
    seen = {}
    loaded = []
    for d in a.icx_dir:
        for p in sorted(glob.glob(os.path.join(d, '*.csv'))):
            code, rows = load_icx(p)
            if not code or code not in roster or not rows:
                continue
            loaded.append((code, max(r['ts'] for r in rows), rows))
    today = {}
    for code, cap, _ in loaded:
        today[code] = max(today.get(code, ''), cap[:10])
    for code, cap, rows in loaded:
        if cap[:10] != today[code]:
            continue
        for r in rows:
            k = (code, r['mac'])
            if r['ts'] > seen.get(k, {}).get('ts', ''):
                seen[k] = {'ts': r['ts'], 'label': r['label']}
    now = max((v['ts'] for v in seen.values()), default='')
    days = lambda t: round((datetime.datetime.fromisoformat(now)
                            - datetime.datetime.fromisoformat(t)).total_seconds() / 86400, 2)

    out = {'now': now, 'sites': {}}
    flat = []
    for code, site in roster.items():
        exp_pos = collections.defaultdict(list)
        for t in site['tvs']:
            exp_pos[t['room']].append(t['position'])
        units = {u['room']: u for u in site['units']}
        group_of = {u['room']: u['group'] for u in site['units']}

        reg = []
        if code in regs and os.path.exists(regs[code]):
            for r in csv.DictReader(open(regs[code], encoding='utf-8-sig')):
                cols = {c.lower(): c for c in r if c}
                pk = lambda p: next((cols[c] for c in cols if c == p or p in c), None)
                mc, rm, ipc = pk('mac'), pk('room'), pk('ip')
                mac = (r[mc] or '').strip().lower() if mc else ''
                if mac and mac != NULL_MAC:
                    reg.append({'mac': mac, 'label': (r.get(rm) or '').strip(),
                                'ip': (r.get(ipc) or '').strip()})
        reg_by_int = {mac_int(x['mac']): x for x in reg if mac_int(x['mac']) is not None}
        reg_by_lock = collections.defaultdict(list)
        for x in reg:
            reg_by_lock[lockout_of(split_label(x['label'])[0])].append(x)

        punch = {}
        if code in pj and os.path.exists(pj[code]):
            for x in json.load(open(pj[code])):
                d = x.get('data') or {}
                if not isinstance(d, dict):
                    continue
                pk = d.get('picks') if isinstance(d.get('picks'), dict) else {}
                trio = lambda o: ('neither' if not isinstance(o, dict) or o.get('neither')
                                  else '/'.join(POS_NAME[k.upper()[:1] if k != 'liv' else 'L']
                                                for k in ('bed', 'liv') if o.get(k)) or 'neither')
                # MVM743's punch list keys rooms as BUILDING LETTER + number ("A1001"),
                # while the roster and iCX use the bare number ("1001"). Verified 2026-08-03:
                # stripping the letter gives 176 unique keys matching all 176 roster rooms with
                # zero collisions, and the letter agrees with the roster's building on all 176.
                _rid = split_label(str(x.get('room_id') or '').strip())[0]
                _rid = re.sub(r'^[A-Z](\d+)$', r'\1', _rid)
                punch[_rid] = {
                    'done_at': d.get('doneAt') or None,
                    'qr_seen_on': trio(pk.get('qr')),
                    'live_linear_on': (trio(pk.get('ltv')) if pk.get('ltv') is not None
                                    else (d.get('linear') or None)),
                    'use_device_mac': d.get('devmac') or None,
                    'firmware': d.get('fw') or None,
                    'relabel': d.get('relabel') or None,
                    'flag': d.get('flag') or None,
                    'comment': (str(d.get('commentText')).strip()
                                if str(d.get('commentChoice')) == 'yes'
                                and str(d.get('commentText') or '').strip() else None),
                    'names': guest_check(d),
                }

        # every iCX box, grouped by the lockout its label points at
        icx_by_lock = collections.defaultdict(list)
        for (c, mac), v in seen.items():
            if c != code:
                continue
            rm, pos = split_label(v['label'])
            icx_by_lock[lockout_of(rm)].append({'mac': mac, 'label': v['label'], 'room': rm,
                                                'pos': pos, 'ts': v['ts'], 'days': days(v['ts'])})

        locks = collections.defaultdict(list)
        for room in units:
            locks[lockout_of(room)].append(room)

        site_locks = []
        for lock in sorted(locks):
            rooms_out = []
            for room in sorted(locks[lock]):
                want = sorted(exp_pos[room])
                devs = [d for d in icx_by_lock.get(lock, []) if d['room'] == room]
                # MVM743 1-bedroom units expect @B/@L, but a tech labelling the single bedroom
                # @B1 is CORRECT, not an error. merge_rooms.py has always normalised that; this
                # file did not, so the two halves of the page disagreed — room 2049 read
                # "All boxes seen / Labeled" in the pills while the Bedroom panel said MISSING
                # and All ICX Data plainly showed 2049@B1. Same rule, same guard: only fold
                # B1 -> B when the unit has no B2 to confuse it with.
                if set(want) == {'B', 'L'}:
                    for d in devs:
                        if d['pos'] == 'B1':
                            d['pos_as_labelled'] = 'B1'
                            d['pos'] = 'B'
                positions = {}
                for w in want:
                    hit = next((d for d in devs if d['pos'] == w), None)
                    positions[w] = ({'icx_label': hit['label'], 'ethernet_mac': hit['mac'],
                                     'wifi_mac_derived': mac_hex((mac_int(hit['mac']) or 0) + 1),
                                     'last_seen': hit['ts'], 'days': hit['days']}
                                    if hit else None)
                unlab = [d for d in devs if not d['pos']]
                have = {d['pos'] for d in devs if d['pos']}
                pd = punch.get(room)
                rooms_out.append({
                    'room': room, 'group': group_of.get(room), 'expected': len(want),
                    'seen': len(devs), 'positions': positions,
                    'position_names': {w: POS_NAME.get(w, w) for w in want},
                    'unlabeled': [{'icx_label': d['label'], 'ethernet_mac': d['mac'],
                                   'wifi_mac_derived': mac_hex((mac_int(d['mac']) or 0) + 1),
                                   'last_seen': d['ts']} for d in unlab],
                    'missing_positions': [POS_NAME.get(w, w) for w in want if w not in have],
                    'punchlist_completed': bool(pd and pd.get('done_at')),
                    'punch': pd,
                })
            # ---- lockout detail table: every iCX box in the lockout + mDNS-only rows ----
            detail, matched = [], set()
            for d in sorted(icx_by_lock.get(lock, []), key=lambda x: x['label']):
                n = mac_int(d['mac'])
                wifi = (n + 1) if n is not None else None
                r = reg_by_int.get(wifi)
                if r:
                    matched.add(r['mac'])
                mdns_room = split_label(r['label'])[0] if r else ''
                if not r:
                    note = 'Not in mDNS'
                elif r['label'] != d['label']:
                    note = ('Room# != iCX' if mdns_room != d['room']
                            else 'Position missing in mDNS')
                else:
                    note = ''
                corr = ''
                if d['room'] not in units:
                    sibs = sorted(x for x in units if x.startswith(d['room'])
                                  and len(x) == len(d['room']) + 1)
                    short = [s for s in sibs
                             if d['pos'] and d['pos'] not in
                             {y['pos'] for y in icx_by_lock.get(lock, []) if y['room'] == s}]
                    corr = (f"Likely {short[0]}@{d['pos']}" if len(short) == 1 else
                            f"Likely one of {', '.join(short)} @{d['pos']}" if short else
                            'Not a real unit — needs a walk')
                detail.append({'icx_label': d['label'], 'ethernet_mac': d['mac'],
                               'wifi_mac_derived': mac_hex(wifi), 'last_seen_icx': d['ts'],
                               'days_since': d['days'],
                               'mdns_label': r['label'] if r else '', 'mdns_mac': r['mac'] if r else '',
                               'mdns_ip': r['ip'] if r else '', 'mdns_note': note,
                               'labeling_correction': corr, 'source': 'iCX',
                               # which SIDE of the lockout this row belongs to. The view groups the
                               # table by this, so an mDNS-only row has to be attributed by the room
                               # in its OWN label — it has no iCX side to inherit from.
                               'room': d['room']})
            for r in sorted(reg_by_lock.get(lock, []), key=lambda x: x['label']):
                if r['mac'] in matched:
                    continue
                detail.append({'icx_label': '', 'ethernet_mac': '', 'wifi_mac_derived': '',
                               'last_seen_icx': '', 'days_since': '',
                               'mdns_label': r['label'], 'mdns_mac': r['mac'], 'mdns_ip': r['ip'],
                               'mdns_note': ('Stale MAC (randomized)' if is_randomised(r['mac'])
                                             else 'In mDNS, no iCX box'),
                               'labeling_correction': '', 'source': 'mDNS only',
                               'room': split_label(r['label'])[0]})
            site_locks.append({'lockout': lock, 'rooms': rooms_out, 'detail': detail,
                               'sides': [r['room'] for r in rooms_out]})
            # reswept is a ROOM fact; a detail row belongs to the lockout, so carry the
            # per-side answer alongside it rather than pretending it is device-level
            resw = {r['room']: ((r.get('punch') or {}).get('done_at') or '') for r in rooms_out}
            any_done = any(resw.values())
            all_done = all(resw.values()) if resw else False
            for row in detail:
                own = next((rm for rm in sorted(resw) if row['icx_label'].startswith(rm)
                            or row['mdns_label'].startswith(rm)), '')
                # `**row` last would clobber `room` with the row's own parsed side; the CSV's
                # room column means "the side we could RECONCILE it to", so it wins here.
                flat.append({**row, 'site': code, 'lockout': lock,
                             'room': own, 'reswept': ('yes' if resw.get(own) else
                                                      'no' if own else
                                                      ('partial' if any_done and not all_done
                                                       else 'yes' if all_done else 'no')),
                             'reswept_at': resw.get(own, '')})
        out['sites'][code] = {'name': site['site_name'], 'lockouts': site_locks,
                              'registry_loaded': bool(reg)}
        nl = len(site_locks)
        multi = sum(1 for x in site_locks if len(x['sides']) > 1)
        print(f"{code}: {nl} lockouts ({multi} with 2+ sides) · "
              f"{sum(len(x['detail']) for x in site_locks)} detail rows")

    json.dump(out, open(a.out_json, 'w'), indent=1)
    with open(a.out_csv, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(flat[0]))
        w.writeheader(); w.writerows(flat)
    print(f"\nwrote {a.out_json} and {a.out_csv} ({len(flat)} rows)")


if __name__ == '__main__':
    main()
