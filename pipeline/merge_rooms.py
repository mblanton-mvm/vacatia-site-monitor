#!/usr/bin/env python3
"""Room-level merge: iCX presence tested by ROOM NUMBER, labelling verified separately.

Why this shape. Only Berkley is substantially relabelled. Grandview and the Cliffs still report
most boxes as a bare room number, so testing presence against "<room>@B"/"<room>@L" made every
un-relabelled TV look missing. Presence must therefore be asked at the ROOM level — did iCX see
the expected number of boxes for this unit, however they are labelled — and correct labelling is a
SECOND, independent question that only becomes meaningful as a room gets relabelled.

We also record every device MAC against the room iCX currently reports it under. That is the
record which later lets us trace a mislabelled box back to where it originally belonged.

Verdicts
  presence : ALL_PRESENT | PARTIAL | NONE | EXTRA_BOXES
  labelling: LABELLED_OK | NOT_RELABELLED | PARTIALLY_LABELLED | DUPLICATE_POSITION
             | WRONG_POSITION
  casting  : ALL_CASTABLE | SOME_CANNOT_CAST | NONE_CASTABLE | NO_REGISTRY_DATA

Usage: python3 merge_rooms.py roster.json out.json [--icx-dir D] [--registry SITE=f] ...
"""
import argparse
import collections
import csv
import datetime
import glob
import json
import os
import re
import zoneinfo

# The three feeds do NOT share a clock, and the view compares their ages side by side:
#   iCX Online-STBs export  -> America/New_York WALL CLOCK, no zone marker. MEASURED 2026-08-03:
#      content max 13:59:58 in a file written 14:06:16 EDT (=18:06:16Z). UTC is refuted (it would
#      make a live dashboard export 4 h stale); property-local PDT is refuted (13:59 PDT = 20:59Z,
#      the future). The Z in the FILENAMES is mislabelled — the content is Eastern.
#   mDNS registry export    -> UTC, explicit 'Z' (taken from the reg-*.csv filename)
#   punch list doneAt       -> UTC ISO8601 with the Z sliced off for display
# Display strings are left exactly as each source emits them; these helpers add a parallel UTC
# instant purely so "how old is this feed" is answered in one clock.
ICX_TZ = zoneinfo.ZoneInfo('America/New_York')


def _iso(s):
    return (s or '').strip().replace(' ', 'T').rstrip('Z')


def icx_utc(s):
    """Eastern wall-clock string -> UTC ISO8601 Z."""
    try:
        d = datetime.datetime.fromisoformat(_iso(s)).replace(tzinfo=ICX_TZ)
    except ValueError:
        return ''
    return d.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def utc_utc(s):
    """Already-UTC string (with or without the Z) -> UTC ISO8601 Z."""
    try:
        d = datetime.datetime.fromisoformat(_iso(s)).replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return ''
    return d.strftime('%Y-%m-%dT%H:%M:%SZ')

NULL_MAC = '00:00:00:00:00:00'
STALE_DAYS = 10
POS_TOKENS = {'B': 'B', 'L': 'L', 'B1': 'B1', 'B2': 'B2',
              'BR': 'B', 'LR': 'L', 'LIV': 'L', 'BED': 'B'}
LABEL_RE = re.compile(r'^\s*(?P<room>.*?)\s*(?:[@ ]\s*(?P<pos>[A-Za-z]{1,3}\d?))?\s*$')


def split_label(raw):
    m = LABEL_RE.match(raw or '')
    if not m:
        return (raw or '').strip(), ''
    room, pos = (m.group('room') or '').strip(), (m.group('pos') or '').upper()
    if pos and pos in POS_TOKENS:
        return room, POS_TOKENS[pos]
    return (raw or '').strip(), ''


def mac_int(m):
    return int(m.replace(':', ''), 16) if re.fullmatch(r'[0-9a-f:]{17}', m or '') else None


def is_randomised(m):
    try:
        return bool(int(m.split(':')[0], 16) & 0x02)
    except (ValueError, IndexError, AttributeError):
        return None


def load_icx(path):
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
        m = re.search(r'(MVM\d{3,})', r.get(site) or '')
        t = r.get(ts) or ''
        if not m or not re.match(r'20\d\d-\d\d-\d\d', t):
            continue
        code = code or m.group(1)
        out.append({'mac': (r[dev] or '').strip().lower(),
                    'label': (r[room] or '').strip(), 'ts': t})
    return code, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('roster')
    ap.add_argument('out')
    ap.add_argument('--icx-dir', action='append', default=[])
    ap.add_argument('--registry', action='append', default=[],
                    help='SITE=path[@capturedAtISO] — the capture time is load-bearing, not the mtime')
    ap.add_argument('--ledger', action='append', default=[])
    ap.add_argument('--punchlist', action='append', default=[])
    ap.add_argument('--punchjson', action='append', default=[],
                    help='SITE=path to a punch_rooms JSON dump from Supabase')
    a = ap.parse_args()
    roster = json.load(open(a.roster))
    kv = lambda xs: dict(x.split('=', 1) for x in xs)
    regs, ledgers, punch = kv(a.registry), kv(a.ledger), kv(a.punchlist)
    pjson = kv(a.punchjson)

    # newest sighting per (site, mac)
    seen, snaps = {}, collections.defaultdict(list)
    # ...and the mac set of each site's NEWEST poll file, so "never reported" (absent from every
    # poll) can be told apart from "reported before, absent now" (in the union, not in the latest
    # export). A per-device timestamp comparison cannot do this: boxes inside one 15-min export
    # carry timestamps spread over the whole window, so "older than the newest stamp" would flag
    # boxes that were in fact present in the latest poll.
    poll_macs = collections.defaultdict(list)

    # TODAY IS PATIENT ZERO. Every tally at the top of the view counts only captures from the
    # CURRENT DAY, per site. The icx/ folder keeps months of exports — MVM743 reaches back to
    # 2025-11-03 — and folding those into "boxes seen" counted long-gone boxes forever: it is why
    # 743 reported 467 seen against 464 expected and a nonsensical "-3 missing". Scoped to today
    # the figures answer the question actually being asked, and subtract the way you expect:
    #     expected - seen today          = boxes never seen today
    #     seen today - seen this poll    = boxes not seen this poll
    # History is deliberately NOT scoped — build_history.py still replays every banked snapshot,
    # because "when did this label change" needs the whole span.
    loaded = []
    for d in a.icx_dir:
        for p in sorted(glob.glob(os.path.join(d, '*.csv'))):
            code, rows = load_icx(p)
            if not code or code not in roster or not rows:
                continue
            loaded.append((code, max(r['ts'] for r in rows), p, rows))
    today = {}
    for code, cap, _p, _rows in loaded:
        today[code] = max(today.get(code, ''), cap[:10])
    for code, cap, p, rows in loaded:
        if cap[:10] != today[code]:
            continue                      # an earlier day — out of scope for the tallies
        snaps[code].append({'file': os.path.basename(p), 'captured_at': cap,
                            'devices': len(rows)})
        poll_macs[code].append((cap, {r['mac'] for r in rows}))
        for r in rows:
            k = (code, r['mac'])
            if r['ts'] > seen.get(k, {}).get('ts', ''):
                seen[k] = {'ts': r['ts'], 'label': r['label']}
    for code in sorted(today):
        kept = sum(1 for c, cap, _, _ in loaded if c == code and cap[:10] == today[code])
        drop = sum(1 for c, _, _, _ in loaded if c == code) - kept
        print(f"{code}: tallies scoped to {today[code]} — {kept} captures used, {drop} earlier ignored")
    latest_macs = {c: max(v, key=lambda x: x[0])[1] for c, v in poll_macs.items() if v}
    latest_poll = {c: max(v, key=lambda x: x[0])[0] for c, v in poll_macs.items() if v}
    now = max((s['captured_at'] for v in snaps.values() for s in v), default='')
    days = lambda t: round((datetime.datetime.fromisoformat(now)
                            - datetime.datetime.fromisoformat(t)).total_seconds() / 86400, 2)

    state = {'now': now, 'now_utc': icx_utc(now), 'stale_days': STALE_DAYS, 'sites': {}}
    for code, site in roster.items():
        expected = {u['room']: u['tv_count'] for u in site['units']}
        exp_pos = collections.defaultdict(list)
        for t in site['tvs']:
            exp_pos[t['room']].append(t['position'])
        group_of = {u['room']: u['group'] for u in site['units']}

        # registry
        # Per-source freshness. Jarran's Claude: MVM784's mDNS collector decayed for FIVE DAYS
        # (11 captures on 7/30 -> 1 today) while htvc_stb_monitoring on the same appliance held at
        # 24/day. A single per-site "as of" hides that. Every source carries its OWN captured_at.
        reg_captured = None
        if code in regs:
            m = re.search(r'(20\d{6})T(\d{4})Z', regs[code])
            if m:
                d, t = m.group(1), m.group(2)
                reg_captured = f'{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:]}Z'
        reg = None
        if code in regs and os.path.exists(regs[code]):
            reg = []
            for r in csv.DictReader(open(regs[code], encoding='utf-8-sig')):
                cols = {c.lower(): c for c in r if c}
                pick = lambda p: next((cols[c] for c in cols if c == p or p in c), None)
                mc, rm = pick('mac'), pick('room')
                mac = (r[mc] or '').strip().lower() if mc else ''
                if mac and mac != NULL_MAC:
                    reg.append({'mac': mac, 'label': (r.get(rm) or '').strip()})
        reg_int = {mac_int(r['mac']): r for r in (reg or []) if mac_int(r['mac']) is not None}
        reg_by_room = collections.defaultdict(list)
        for r in (reg or []):
            reg_by_room[split_label(r['label'])[0]].append(r)

        # ledger, for stb_id -> does this box have a real-MAC row
        mac_sid, sid_real = {}, collections.defaultdict(bool)
        if code in ledgers and os.path.exists(ledgers[code]):
            for r in csv.DictReader(open(ledgers[code], encoding='utf-8-sig')):
                m = (r.get('appliance_mac') or '').strip().lower()
                if not m or m == NULL_MAC:
                    continue
                mac_sid[m] = r.get('stb_id') or ''
                if is_randomised(m) is False:
                    sid_real[r.get('stb_id') or ''] = True
        # ---- guest-name verification ------------------------------------------------
        # punch_rooms.data.guestLocked holds the checked-in guest's INITIALS at entry time
        # (or 'VACANT'); data.tv.{bed,liv}.name holds the name the tech read off that TV, and
        # .display is 'name' when a name was on screen or 'welcome' when it was the generic
        # screen. We compare the two HERE and emit only the verdict + the initials. The name
        # string never leaves this function — it is guest PII and the repo rule is
        # rooms-and-initials-only.
        def initials_of(name):
            parts = [p for p in re.split(r'[\s,]+', str(name or '').strip()) if p]
            if not parts:
                return ''
            first, last = parts[0], parts[-1]
            return (first[:1] + last[:1]).upper() if len(parts) > 1 else first[:1].upper()

        def norm_init(v):
            return re.sub(r'[^A-Z]', '', str(v or '').upper())

        def name_check(d):
            gl = str(d.get('guestLocked') or '').strip()
            tv = d.get('tv') if isinstance(d.get('tv'), dict) else {}
            occ = ('vacant' if gl.upper() == 'VACANT' else 'occupied' if gl else 'unknown')
            # MVM743's punch app writes the v2 flat shape ("_v2": true): the bedroom TV is under
            # tv["b1"] (its units are Bedroom 1 / Bedroom 2 / Living), and the live-linear answer is a
            # flat "linear" key. MVM784's app nests answers under "picks" and uses tv["bed"]. Reading only
            # the 784 shape made every relabelled 743 room report its bedroom as "Welcome" when the tech
            # had actually recorded a name -- room 2049 showed "Welcome" against a punch entry reading
            # "Judson York". Accept both shapes; prefer the explicit bedroom key when present.
            bedkey = ('bed' if isinstance(tv.get('bed'), dict)
                      else 'b1' if isinstance(tv.get('b1'), dict)
                      else 'b' if isinstance(tv.get('b'), dict) else 'bed')
            per = {}
            for pos in (bedkey, 'liv'):
                p = tv.get(pos) if isinstance(tv.get(pos), dict) else {}
                shown = str(p.get('name') or '').strip()
                disp = str(p.get('display') or '')
                if not shown and disp != 'name':
                    per[pos] = 'Welcome'
                elif not shown:
                    per[pos] = 'Name shown, not recorded'
                elif occ == 'occupied' and norm_init(gl) and initials_of(shown) == norm_init(gl):
                    per[pos] = 'Matches guest'
                elif occ == 'occupied':
                    per[pos] = 'Different name'
                else:
                    # A name IS on screen, but with no guest on file there is nothing to compare
                    # it against. Calling that "Different name" reads as an error we cannot
                    # actually claim — room 2049 showed a name on both TVs with guestLocked empty.
                    per[pos] = 'Name shown (no guest on file)'
            b, l = per[bedkey], per['liv']
            named = lambda v: v != 'Welcome'
            if occ == 'vacant' and (named(b) or named(l)):
                v = 'NAME_SHOWN_WHILE_VACANT'
            elif occ == 'occupied' and b == 'Welcome' and l == 'Welcome':
                v = 'NO_NAME_ON_EITHER_TV'
            elif occ == 'occupied' and (b == 'Welcome') != (l == 'Welcome'):
                v = 'NAME_ON_ONE_TV_ONLY'
            elif occ == 'occupied' and 'Different name' in (b, l) and 'Matches guest' in (b, l):
                v = 'TVS_DISAGREE'
            elif occ == 'occupied' and b == 'Different name' and l == 'Different name':
                v = 'WRONG_NAME_BOTH_TVS'
            elif occ == 'occupied' and b == 'Matches guest' and l == 'Matches guest':
                v = 'BOTH_CORRECT'
            elif occ == 'vacant':
                v = 'VACANT_INCONCLUSIVE'
            else:
                v = 'NOT_ASSESSABLE'
            tvn = lambda k: str(((tv.get(k) or {}) if isinstance(tv, dict) else {}).get('name')
                                 or '').strip()
            return {'occupancy': occ, 'guest_initials': gl if occ == 'occupied' else None,
                    'bed': b, 'liv': l, 'verdict': v,
                    # PRIVATE: only emitted into an authenticated build (see build_artifact2
                    # --with-names). Never include these in a public page or the repo.
                    'guest_name': gl if occ == 'occupied' else '',
                    'bed_name': tvn(bedkey), 'liv_name': tvn('liv')}

        # ---- punch-list tech answers ----------------------------------------------
        # DELIBERATELY EXCLUDED: data.tv.{bed,liv}.name, data.bedName/livName and
        # data.guestLocked all carry GUEST NAMES. They must never leave Supabase into a
        # file or a published page. Only the tech-check answers are read here.
        pdata = {}
        if code in pjson and os.path.exists(pjson[code]):
            for x in json.load(open(pjson[code])):
                d = x.get('data') or {}
                if not isinstance(d, dict):
                    continue
                picks = d.get('picks') if isinstance(d.get('picks'), dict) else {}
                q = picks.get('qr') if isinstance(picks.get('qr'), dict) else None
                if q is None:
                    qr = None
                elif q.get('bed') and q.get('liv'):
                    qr = 'BOTH'
                elif q.get('bed'):
                    qr = 'BEDROOM_ONLY'
                elif q.get('liv'):
                    qr = 'LIVING_ONLY'
                elif q.get('neither'):
                    qr = 'NEITHER'
                else:
                    qr = None
                norm = lambda v: (str(v).strip().lower() or None) if v is not None else None
                # MVM743's punch list keys rooms as BUILDING LETTER + number ("A1001"),
                # while the roster and iCX use the bare number ("1001"). Verified 2026-08-03:
                # stripping the letter gives 176 unique keys matching all 176 roster rooms with
                # zero collisions, and the letter agrees with the roster's building on all 176.
                _rid = split_label(str(x.get('room_id') or '').strip())[0]
                _rid = re.sub(r'^[A-Z](\d+)$', r'\1', _rid)
                pdata[_rid] = {
                    'relabel': norm(d.get('relabel')), 'fw': norm(d.get('fw')),
                    'devmac': norm(d.get('devmac')),
                    # 784 nests this under picks.ltv as a per-TV object; 743's v2 app writes a
                    # flat yes/no. Take whichever the app actually used.
                    'linear': ((lambda o: (None if not isinstance(o, dict)
                                else 'neither' if o.get('neither')
                                else '/'.join(n for k, n in (('bed', 'Bedroom'), ('liv', 'Livingroom'))
                                              if o.get(k)) or 'neither'))(picks.get('ltv'))
                               if picks.get('ltv') is not None else norm(d.get('linear'))),
                    'qr': qr, 'flag': norm(d.get('flag')),
                    'comment': (str(d.get('commentText')).strip()
                                if str(d.get('commentChoice')) == 'yes'
                                and str(d.get('commentText') or '').strip() else None),
                    'done_at': d.get('doneAt') or None,
                    'tech_fields_set': sum(1 for k in ('relabel', 'fw', 'devmac', 'linear')
                                           if str(d.get(k) or '').strip()),
                    'swept': bool(d.get('doneAt')) or all(
                        str(d.get(k) or '').strip() for k in ('relabel', 'fw', 'devmac', 'linear')),
                    'started': any(str(d.get(k) or '').strip()
                                   for k in ('relabel', 'fw', 'devmac', 'linear')),
                    'names': name_check(d),
                }
        pl = set()
        if code in punch and os.path.exists(punch[code]):
            for r in csv.DictReader(open(punch[code], encoding='utf-8-sig')):
                v = next((r[k] for k in r if k and 'room' in k.lower()), None)
                if v:
                    pl.add(split_label(v.strip())[0])

        # group every sighted device under the room iCX reports it in
        by_room, orphans = collections.defaultdict(list), []
        for (c, mac), v in seen.items():
            if c != code:
                continue
            rm, pos = split_label(v['label'])
            dev = {'mac': mac, 'icx_label': v['label'], 'position': pos or None,
                   'last_seen': v['ts'], 'days': days(v['ts'])}
            if rm in expected:
                by_room[rm].append(dev)
            else:
                orphans.append({**dev, 'room_reads': rm})

        # ---- where does an off-roster device belong? --------------------------------
        # Conservative by design. An earlier pass rated a lockout-parent guess MEDIUM-HIGH on
        # nothing but a label prefix; Michele showed MAC blocks are NOT room-contiguous here, so
        # neither MAC proximity nor prefix is evidence alone. HIGH requires TWO independent
        # signals agreeing (the string AND a matching unfilled position).
        COMMON = re.compile(r'^(GYM|REC[.\s_-]?ROOM|LOBBY|MAINTENANCE|POOL|CLUB|OFFICE|'
                            r'FITNESS|BREAK|SPA|BAR|CAFE)', re.I)
        gaps = {r: sorted(set(exp_pos[r]) - {d['position'] for d in by_room.get(r, [])
                                             if d['position']}) for r in expected}

        def _swaps(x):
            return {x[:i] + x[i + 1] + x[i] + x[i + 2:] for i in range(len(x) - 1)
                    if x[i].isdigit() and x[i + 1].isdigit()} - {x}

        def _drop1(x):
            m = re.match(r'^(\d+)([A-Za-z]*)$', x)
            if not m or len(m.group(1)) < 5:
                return set()
            d, tail = m.group(1), m.group(2)
            return {d[:i] + d[i + 1:] + tail for i in range(len(d))} - {x}

        def infer_home(lbl, room, pos):
            if COMMON.match(lbl) or re.fullmatch(r'\d{1,3}', room):
                return ('', 'HIGH — exclude', 'common-area or unnumbered TV; matches no unit in '
                        'the room list', 'confirm it is common-area and keep it out of the roster')
            for cand in sorted(_swaps(room)):
                if cand in expected:
                    if pos and pos in gaps.get(cand, []):
                        return (f'{cand}@{pos}', 'HIGH',
                                f"digit transposition '{room}'->'{cand}', AND {cand} is missing "
                                f"its @{pos} — two independent signals agree",
                                f'confirm the TV is physically in {cand}, then relabel')
                    return (f'{cand}@{pos}' if pos else cand, 'LOW',
                            f"digit transposition '{room}'->'{cand}', but {cand} is not missing "
                            f"that position", 'walk the room — the swap may be coincidence')
            sibs = sorted(x for x in expected if x.startswith(room) and x != room
                          and len(x) == len(room) + 1)
            if sibs:
                short = [x for x in sibs if pos and pos in gaps.get(x, [])]
                if len(short) == 1:
                    return (f'{short[0]}@{pos}', 'MEDIUM',
                            f"'{room}' is not a unit but {', '.join(sibs)} are, and only "
                            f"{short[0]} is missing @{pos} — circumstantial; the gap is the only "
                            f"evidence", f'read the label off the TV in {short[0]}')
                if len(short) > 1:
                    return ('', 'LOW', f"lockout parent; {' and '.join(short)} are BOTH missing "
                            f"@{pos} — cannot choose",
                            f'walk {short[0]} and {short[1]}, or wait until one is relabeled')
                return ('', 'NOT YET', f"'{room}' is not a unit, and no sibling of "
                        f"{', '.join(sibs)} is missing @{pos or '?'} — nothing to place it in yet",
                        'resweep the lockout; a gap has to appear before this is decidable')
            cands = sorted(x for x in _drop1(room) if x in expected)
            if len(cands) == 1:
                return (f'{cands[0]}@{pos}' if pos else cands[0], 'MEDIUM',
                        f"'{room}' is '{cands[0]}' with one extra digit",
                        f'confirm the TV is in {cands[0]}')
            if len(cands) > 1:
                return ('', 'LOW', f"dropping a digit gives several real units: "
                        f"{', '.join(cands)}", 'walk the room to choose')
            return ('', 'NOT YET', 'no unit matches by prefix, digit swap or dropped digit',
                    'walk the label — retired room, typo, or a unit missing from the room list')

        for o in orphans:
            sug, conf, basis, settle = infer_home(o['icx_label'], o['room_reads'],
                                                  o['position'] or '')
            o['suggested_label'], o['confidence'] = sug, conf
            o['basis'], o['what_would_settle_it'] = basis, settle

        rooms = []
        for room, exp in sorted(expected.items()):
            devs = sorted(by_room.get(room, []), key=lambda d: d['last_seen'], reverse=True)
            # iCX reports the box's ETHERNET MAC; the mDNS casting registry (and the appliance
            # ledger) carry its WI-FI MAC, which is ETHERNET + 1. Measured 2026-08-03 on MVM784:
            # registry==ledger exactly 583/583, and iCX == registry-1 in 574/583 with ZERO
            # ambiguous matches. So the correct lookup is registry[icx_mac + 1].
            # We keep the exact-match branch too (in case a site ever reports the Wi-Fi MAC in
            # iCX) but deliberately do NOT search -1: that direction never occurs, and only the
            # accident that no two iCX IDs are closer than 3 kept it from cross-linking a
            # neighbouring box. Never widen this back to a blind +-1.
            WIFI_OFFSET = 1
            for d in devs:
                n = mac_int(d['mac'])
                real = None
                for cand in ((n + WIFI_OFFSET, n) if n is not None else ()):
                    r = reg_int.get(cand)
                    if r and is_randomised(r['mac']) is False:
                        real = r['mac']
                        break
                left = [r['mac'] for r in reg_by_room.get(room, []) if is_randomised(r['mac'])]
                stranded = [m for m in left
                            if mac_sid.get(m) and not sid_real.get(mac_sid[m])]
                d['registry_real_mac'] = real
                d['registry_leftovers'] = left
                d['stranded_macs'] = stranded
                d['casting'] = ('NO_REGISTRY_DATA' if reg is None else
                                'OK' if real else
                                'STRANDED_RANDOMISED' if stranded else
                                'NO_REAL_MAC_LEFTOVER_PRESENT' if left else 'NO_REGISTRY_ENTRY')
                d['stale'] = d['days'] > STALE_DAYS
                # reported in the site's most recent poll? (no polls loaded -> unknown, treat as
                # present so a missing feed never masquerades as a fleet of dark boxes)
                d['in_latest_poll'] = (d['mac'] in latest_macs[code]
                                       if code in latest_macs else True)
            # MVM743 1-bedroom units expect @B/@L, but a tech labelling the single bedroom
            # @B1 is CORRECT, not an error. Normalise B1 -> B only when the unit has no B2.
            if set(exp_pos[room]) == {'B', 'L'}:
                for d in devs:
                    if d['position'] == 'B1':
                        d['position_as_labelled'] = 'B1'
                        d['position'] = 'B'
            n_seen = len(devs)
            presence = ('NONE' if n_seen == 0 else 'EXTRA_BOXES' if n_seen > exp
                        else 'ALL_PRESENT' if n_seen == exp else 'PARTIAL')
            pos_list = [d['position'] for d in devs if d['position']]
            want = sorted(exp_pos[room])
            dupes = [p for p, k in collections.Counter(pos_list).items() if k > 1]
            wrong = [p for p in pos_list if p not in want]
            if not devs:
                labelling = 'NO_BOXES_SEEN'
            elif not pos_list:
                labelling = 'NOT_RELABELLED'
            elif dupes:
                labelling = 'DUPLICATE_POSITION'
            elif wrong:
                labelling = 'WRONG_POSITION'
            elif len(pos_list) < n_seen:
                labelling = 'PARTIALLY_LABELLED'
            elif sorted(set(pos_list)) == want:
                labelling = 'LABELLED_OK'
            else:
                labelling = 'PARTIALLY_LABELLED'
            # Casting is judged against the unit's EXPECTED TV count, not against the boxes iCX
            # happens to report. A TV that is missing cannot cast, so a room short a box must
            # never read ALL_CASTABLE. (Caught 2026-08-03 by Michele on 1405B: one box present
            # and castable, @B absent entirely, and the room still read "all castable".)
            cs = [d['casting'] for d in devs]
            n_castable = sum(1 for x in cs if x == 'OK')
            casting = ('NO_REGISTRY_DATA' if reg is None or not cs else
                       'ALL_CASTABLE' if n_castable >= exp else
                       'NONE_CASTABLE' if n_castable == 0 else 'SOME_CANNOT_CAST')
            rooms.append({'room': room, 'group': group_of[room], 'expected': exp,
                          'seen': n_seen, 'presence': presence, 'labelling': labelling,
                          'casting': casting, 'missing_positions': sorted(set(want) - set(pos_list)),
                          'stale_boxes': sum(1 for d in devs if d['stale']),
                          # two different problems: a box the roster expects that has NEVER
                          # reported (install / inventory), vs one that has reported before but
                          # was absent from the latest poll (it is dark right now)
                          'never_seen': max(0, exp - n_seen),
                          'not_in_latest': sum(1 for d in devs if not d['in_latest_poll']),
                          'punchlist_complete': (room in pl) if pl else None,
                          'punch': pdata.get(room),
                          'devices': devs})
        # box-level totals (tiles are about BOXES for some rows, ROOMS for others)
        boxes_expected = sum(r['expected'] for r in rooms)
        boxes_seen_overall = sum(r['seen'] for r in rooms)
        newest = max((x['captured_at'] for x in snaps.get(code, [])), default='')
        boxes_seen_current = next((x['devices'] for x in snaps.get(code, [])
                                   if x['captured_at'] == newest), 0)
        devices_cannot_cast = sum(1 for r in rooms for d in r['devices']
                                  if d['casting'] not in ('OK', 'NO_REGISTRY_DATA'))
        stale_boxes = sum(1 for x in (reg or []) if is_randomised(x['mac']))
        cnt = lambda k: dict(collections.Counter(r[k] for r in rooms))
        ncnt = dict(collections.Counter(((r['punch'] or {}).get('names') or {}).get('verdict')
                                        or '(no punch entry)' for r in rooms))
        qcnt = dict(collections.Counter((r['punch'] or {}).get('qr') or '(not recorded)'
                                        for r in rooms))
        dcnt = dict(collections.Counter((r['punch'] or {}).get('devmac') or '(not recorded)'
                                        for r in rooms))
        state['sites'][code] = {
            'name': site['site_name'], 'group_label': site['group_label'],
            'units': site['unit_count'], 'tvs': site['tv_count'],
            'groups': site['groups'], 'registry_loaded': reg is not None,
            'registry_rows': len(reg) if reg is not None else 0,
            # Per-SOURCE freshness. MVM784's mDNS collector decayed for five days (11 captures on
            # 7/30 -> 1 today) while htvc_stb_monitoring on the same appliance held 24/day. One
            # per-site "as of" hides that; every source must carry its own captured_at.
            'sources': [
                {'name': 'iCX Online STBs', 'captured_at': newest,
                 'captured_utc': icx_utc(newest),
                 'detail': f"{boxes_seen_current} boxes, {len(snaps.get(code, []))} polls loaded"},
                {'name': 'mDNS casting registry', 'captured_at': reg_captured or '',
                 'captured_utc': utc_utc(reg_captured or ''),
                 'detail': (f"{len(reg)} rows" if reg is not None else 'NOT LOADED')},
                {'name': 'Punch list (field sweep)',
                 'captured_at': (max((((r['punch'] or {}).get('done_at') or '')
                                      for r in rooms), default='') or '')[:16].replace('T', ' '),
                 'captured_utc': utc_utc(max((((r['punch'] or {}).get('done_at') or '')
                                              for r in rooms), default='')[:19]),
                 'detail': f"{sum(1 for r in rooms if (r['punch'] or {}).get('swept'))} swept, "
                           f"{sum(1 for r in rooms if (r['punch'] or {}).get('started'))} started, "
                           f"of {len(rooms)}"},
            ],
            'snapshots': sorted(snaps.get(code, []), key=lambda s: s['captured_at']),
            'rooms': rooms, 'orphans': sorted(orphans, key=lambda d: d['icx_label']),
            'summary': {'presence': cnt('presence'), 'labelling': cnt('labelling'),
                        'casting': cnt('casting'), 'punch_qr': qcnt, 'punch_devmac': dcnt,
                        'name_check': ncnt,
                        'boxes_expected': boxes_expected,
                        'boxes_seen_overall': boxes_seen_overall,
                        'boxes_seen_current': boxes_seen_current,
                        'boxes_missing': boxes_expected - boxes_seen_overall,
                        'devices_cannot_cast': devices_cannot_cast,
                        'stale_boxes': stale_boxes,
                        'boxes_seen': boxes_seen_overall,
                        'orphans': len(orphans)},
        }
        s = state['sites'][code]['summary']
        print(f"{code}  {site['unit_count']} units · boxes expected {s['boxes_expected']} · "
              f"seen overall {s['boxes_seen_overall']} · seen this poll {s['boxes_seen_current']} · "
              f"missing {s['boxes_missing']} · cannot cast (devices) {s['devices_cannot_cast']} · "
              f"stale (randomised mDNS) {s['stale_boxes']}")
        print(f"      presence  {s['presence']}")
        print(f"      labelling {s['labelling']}")
        print(f"      casting   {s['casting']}")
        print(f"      punch QR  {s['punch_qr']}")
        print(f"      punch dev {s['punch_devmac']}")
        print(f"      names     {s['name_check']}")
    json.dump(state, open(a.out, 'w'), indent=1)
    print(f"\nwrote {a.out}")


if __name__ == '__main__':
    main()
