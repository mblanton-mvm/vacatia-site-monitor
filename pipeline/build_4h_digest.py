#!/usr/bin/env python3
"""4-hour Vacatia 3-site digest.

Two outputs from one pass over the banked windows:

  1. A markdown entry appended to the SHARED watch log in mvm-platform
     (docs/vacatia/vacatia-3site-watch-log.md). That file is the cross-Claude
     channel: per-user Claude memory does not sync between Michele and Jarran
     (CLAUDE.md §11), so anything Jarran's Claude must know when it reads and
     answers in Teams has to live in the repo, not in a memory file.
  2. A Teams-ready markdown draft (stdout or -o) for format-message.py.

Reads only what the poller already banks — no iCX access, so this is safe to run
any time and produces the same answer twice.

Sources per window (UTC, from the directory name):
  docs/vacatia/data-drops/icx-sweeps/<window>/dish-icx-vacatia3-site-<window>.csv
  docs/vacatia/data-drops/icx-sweeps/<window>/dish-icx-<handle>-health-<window>.csv
  pipeline/icx/icx-online-stbs-<handle>-<YYYYMMDD>T<HHMM>ET.csv   (per-device, for concentration)

How a window is judged (the §5b rules, encoded so the digest cannot cry wolf):
  * presence comes from online_stbs in the site CSV, never from a health bucket
    (buckets lag the widget by a minute or two)
  * a DIP is a window whose count is below the 4h median for that site
  * a dip is only ESCALATION-SHAPED if it is part of >=3 consecutive falling
    windows or availability went bad in the same window; a lone dip that recovers
    is reported as a dip and nothing more
  * for a dip at a site with a grouping rule, loss share / fleet share is computed
    per group and reported only when a group exceeds 3x its fleet share
"""
import argparse
import collections
import csv
import glob
import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone

PIPE = os.path.dirname(os.path.abspath(__file__))
DROPS = os.path.expanduser('~/Developer/mvm-platform/docs/vacatia/data-drops')
SWEEPS = f'{DROPS}/icx-sweeps'
# Overridable so the scheduled run can target a dedicated git worktree instead of
# Michele's live working tree. The 15:17Z run committed onto whatever branch happened to
# be checked out (it caught `main` mid-session) — exactly the collision CLAUDE.md §11
# means by "one editor per branch at a time".
LOG = os.environ.get('WATCHLOG') or os.path.expanduser(
    '~/Developer/mvm-platform/docs/vacatia/vacatia-3site-watch-log.md')

SITES = [
    ('MVM784', 'The Berkley', 'floor',
     lambda r: (re.match(r'^(\d{1,2})\d{2}', r).group(1)
                if re.match(r'^\d{3,4}', r) else '?')),
    ('MVM783', 'The Grandview', 'building',
     lambda r: (re.match(r'^(\d{4,5})', r).group(1)[:-3]
                if re.match(r'^\d{4,5}', r) else '?')),
    ('MVM743', 'The Cliffs', 'building',
     lambda r: (re.match(r'^(\d{1,2})', r).group(1)
                if re.match(r'^\d', r) else '?')),
]

CONCENTRATION_MIN_RATIO = 3.0   # below this a "concentration" is just fleet shape
ESCALATION_RUN = 3              # consecutive falling windows before it is a trend

# Thresholds that exist to stop the digest crying wolf. Every one of these was added
# after the first dry-run (2026-08-04 15:14Z) produced a "LOCALIZED at 333x fleet share"
# verdict off a SINGLE box whose room number would not parse.
MIN_LOSS_FOR_CONC = 5      # a 1-3 box loss has no spatial signal worth computing
MIN_GROUP_LOSS = 3         # one box in a small group is not a concentration
MIN_GROUP_FLEET = 20       # tiny denominators manufacture huge ratios
MIN_DROP_ABS = 5           # a -1 window is churn, not a dip
# 0.2%, not 0.5%: at Grandview's 4,351 boxes a 0.5% floor is 21 boxes, which silently
# suppressed the 14:00Z loss of 16 that was ENTIRELY inside buildings 1 and 11 — the one
# signature at this site worth catching. Size is not what makes a loss meaningful here;
# concentration is. Keep the floor low and let the concentration test do the judging.
MIN_DROP_FRAC = 0.002
NAVAIL_MIN_ABS = 10        # 1-2 availability-bad boxes is the normal floor
NAVAIL_MIN_FRAC = 0.01


def windows_in_range(since, until):
    out = []
    for d in sorted(glob.glob(f'{SWEEPS}/*Z')):
        w = os.path.basename(d)
        try:
            t = datetime.strptime(w, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if since <= t <= until:
            out.append((w, t, d))
    return out


def site_counts(d, w):
    f = f'{d}/dish-icx-vacatia3-site-{w}.csv'
    if not os.path.exists(f):
        return {}
    out = {}
    for r in csv.DictReader(open(f, encoding='utf-8-sig')):
        try:
            out[r['handle']] = int(r['total_devices'])
        except (KeyError, ValueError):
            pass
    return out


def health(d, w, handle):
    f = f'{d}/dish-icx-{handle.lower()}-health-{w}.csv'
    if not os.path.exists(f):
        return None
    rows = list(csv.DictReader(open(f, encoding='utf-8-sig')))
    return rows[0] if rows else None


def num(row, key):
    if not row:
        return None
    v = (row.get(key) or '').strip()
    try:
        return int(v)
    except ValueError:
        return None


def devices(handle, et_stamp):
    """Per-device export for one window, keyed device id -> room."""
    f = f'{PIPE}/icx/icx-online-stbs-{handle}-{et_stamp}.csv'
    if not os.path.exists(f):
        return None
    d = {}
    for r in csv.DictReader(open(f, encoding='utf-8-sig')):
        k = (r.get('Device ID') or r.get('﻿Device ID') or '').strip()
        if k:
            d[k] = (r.get('Room Number') or '').strip()
    return d


def et_stamp_for(t):
    """iCX per-device exports are stamped in EASTERN wall clock, one minute
    before the window end (reference-icx-export-timezone). UTC-4 in DST."""
    et = t - timedelta(hours=4, minutes=1)
    return et.strftime('%Y%m%dT%H%M') + 'ET'


def concentration(handle, grouping, keyfn, prev_t, cur_t):
    prev = devices(handle, et_stamp_for(prev_t))
    cur = devices(handle, et_stamp_for(cur_t))
    if not prev or not cur:
        return None
    lost = set(prev) - set(cur)
    if len(lost) < MIN_LOSS_FOR_CONC:
        # Not enough boxes to carry a spatial signal. Report the count, claim nothing.
        return {'lost': len(lost), 'grouping': grouping, 'rows': [],
                'concentrated': [], 'too_small': True, 'unparsed': 0}
    fleet = collections.Counter(keyfn(v) for v in prev.values())
    loss = collections.Counter(keyfn(prev[m]) for m in lost)
    # '?' is "room number did not parse", not a place. Left in, it becomes a tiny-fleet
    # group that manufactures a 300x ratio off one box.
    unparsed = loss.pop('?', 0)
    fleet.pop('?', None)
    total, L = sum(fleet.values()), sum(loss.values())
    if not total or not L:
        return {'lost': len(lost), 'grouping': grouping, 'rows': [],
                'concentrated': [], 'too_small': True, 'unparsed': unparsed}
    rows = []
    for g, n in loss.most_common():
        fs = fleet[g] / total
        rows.append({'group': g, 'lost': n, 'fleet': fleet[g],
                     'loss_share': n / L, 'fleet_share': fs,
                     'ratio': (n / L) / fs if fs else 0.0})
    conc = [r for r in rows
            if r['ratio'] >= CONCENTRATION_MIN_RATIO
            and r['lost'] >= MIN_GROUP_LOSS
            and r['fleet'] >= MIN_GROUP_FLEET]
    return {'lost': len(lost), 'grouping': grouping, 'rows': rows,
            'concentrated': conc, 'too_small': False, 'unparsed': unparsed,
            'groups_touched': len(rows), 'groups_total': len(fleet)}


def analyse(since, until):
    wins = windows_in_range(since, until)
    per = {h: [] for h, *_ in SITES}
    for w, t, d in wins:
        counts = site_counts(d, w)
        for handle, name, grouping, keyfn in SITES:
            if handle not in counts:
                continue
            hrow = health(d, w, handle)
            per[handle].append({
                'w': w, 't': t, 'online': counts[handle],
                'rssi_bad': num(hrow, 'rssi_bad'),
                'reboot_warn': num(hrow, 'reboot_warn'),
                'reboot_bad': num(hrow, 'reboot_bad'),
                'navail_bad': num(hrow, 'netavail_bad'),
                'disrupt_bad': num(hrow, 'disrupt_bad'),
            })

    report = {'since': since, 'until': until, 'windows': len(wins), 'sites': []}
    for handle, name, grouping, keyfn in SITES:
        s = per[handle]
        if not s:
            report['sites'].append({'handle': handle, 'name': name, 'no_data': True})
            continue
        counts = [x['online'] for x in s]
        med = statistics.median(counts)
        floor_drop = max(MIN_DROP_ABS, int(med * MIN_DROP_FRAC))
        dips, falling, run = [], [], 0
        for i, x in enumerate(s):
            delta = x['online'] - s[i - 1]['online'] if i else 0
            if i and delta < 0:
                run += 1
            else:
                run = 0
            # A dip is a REAL FALL of at least floor_drop boxes. Being under the period
            # median is not enough: a window that rose by 1 was landing in the dip list.
            if i and delta <= -floor_drop:
                dip = {'w': x['w'], 't': x['t'], 'online': x['online'],
                       'delta': delta, 'navail_bad': x['navail_bad'],
                       'run': run, 'conc': None}
                dip['conc'] = concentration(handle, grouping, keyfn,
                                            s[i - 1]['t'], x['t'])
                dips.append(dip)
            if run >= ESCALATION_RUN:
                falling.append(x['w'])
        reb = [(x['w'], x['reboot_warn'], x['reboot_bad']) for x in s
               if (x['reboot_warn'] or 0) or (x['reboot_bad'] or 0)]
        report['sites'].append({
            'handle': handle, 'name': name, 'no_data': False,
            'first': s[0], 'last': s[-1], 'n': len(s),
            'min': min(counts), 'max': max(counts), 'median': med,
            'rssi_bad_range': (min(x['rssi_bad'] for x in s if x['rssi_bad'] is not None),
                              max(x['rssi_bad'] for x in s if x['rssi_bad'] is not None))
            if any(x['rssi_bad'] is not None for x in s) else None,
            'navail_bad_windows': [
                x['w'] for x in s
                if (x['navail_bad'] or 0) >= max(NAVAIL_MIN_ABS, int(med * NAVAIL_MIN_FRAC))],
            'floor_drop': floor_drop,
            'reboots': reb,
            'dips': dips,
            'escalation_windows': falling,
        })
    return report


def hz(t):
    return t.strftime('%H:%MZ')


def verdict(site):
    if site['no_data']:
        return 'NO DATA', 'no windows banked in this period'
    if site['escalation_windows']:
        return 'ESCALATION-SHAPED', (
            f"{len(site['escalation_windows'])} window(s) inside a "
            f"{ESCALATION_RUN}+ consecutive falling run")
    conc = [d for d in site['dips'] if d['conc'] and d['conc']['concentrated']]
    if conc:
        worst = max(conc, key=lambda d: max(r['ratio'] for r in d['conc']['concentrated']))
        g = max(worst['conc']['concentrated'], key=lambda r: r['ratio'])
        return 'DIPS, LOCALIZED', (
            f"{len(conc)} dip(s) concentrated in {worst['conc']['grouping']} "
            f"{g['group']} at {g['ratio']:.1f}x its fleet share")
    if site['dips']:
        spread = [d for d in site['dips'] if d['conc'] and not d['conc']['too_small']]
        if spread:
            worst = max(spread, key=lambda d: -d['delta'])
            c = worst['conc']
            return 'DIPS, DIFFUSE', (
                f"{len(site['dips'])} dip(s), no group above "
                f"{CONCENTRATION_MIN_RATIO:.0f}x its fleet share "
                f"(largest spread over {c['groups_touched']} of {c['groups_total']} "
                f"{c['grouping']}s)")
        return 'DIPS, DIFFUSE', f"{len(site['dips'])} dip(s), too small to localize"
    return 'FLAT', f"no fall of {site['floor_drop']}+ boxes between windows"


def render_teams(report):
    """One self-contained line per site.

    The doc layout does NOT survive Teams: format-message.py un-wraps indented
    continuation lines into flowing paragraphs, so a site block plus its dip list
    arrives as one dense blob (the same unreadable result as trap 1 in the
    teams-chat skill, by a different route). Chat gets flat bullets, no indentation,
    each a complete sentence.
    """
    L = []
    a, b = hz(report['since']), hz(report['until'])
    # "(Claude)" matches the prefix Jarran's Claude already uses in this chat, so a human
    # skimming the thread can tell agent posts from ours at a glance.
    L.append(f'(Claude) Vacatia 3-site iCX watch, {a} to {b}. '
             f'{report["windows"]} sweep windows banked.')
    L.append('')
    for s in report['sites']:
        v, why = verdict(s)
        if s['no_data']:
            L.append(f'- **{s["handle"]} {s["name"]}** — {v}: {why}')
            L.append('')
            continue
        bits = [f'**{s["handle"]} {s["name"]}** — {v}.',
                f'Now {s["last"]["online"]}, range {s["min"]}–{s["max"]} over {s["n"]} windows.']
        if s['reboots']:
            tot = sum((w or 0) + (bd or 0) for _, w, bd in s['reboots'])
            bits.append(f'{tot} reboot flag(s).')
        else:
            bits.append('Zero reboots.')
        if s['dips']:
            parts = []
            for d in s['dips']:
                c = d['conc']
                where = ''
                if c and c['concentrated']:
                    gs = ', '.join(f'{c["grouping"]} {r["group"]}' for r in c['concentrated'])
                    where = f' ({gs})'
                parts.append(f'{hz(d["t"])} {d["delta"]:+d}{where}')
            bits.append('Dips: ' + '; '.join(parts) + '.')
        L.append('- ' + ' '.join(bits))
        L.append('')
    L.append('Verdicts: FLAT is no real fall between windows. DIPS, DIFFUSE means losses '
             'spread across many floors or buildings, which is usually nothing. DIPS, '
             'LOCALIZED means one building or floor took losses well above its share of '
             'the fleet, which is the signal that matters. ESCALATION-SHAPED means three '
             'or more consecutive falling windows, or availability went bad, so look now.')
    L.append('')
    L.append('Standing cautions and open questions are in '
             'docs/vacatia/vacatia-3site-watch-log.md (PR #819). That file is two-way, so '
             'your Claude can write into sections A and B as well.')
    return '\n'.join(L)


def render(report, teams=False):
    if teams:
        return render_teams(report)
    L = []
    a, b = hz(report['since']), hz(report['until'])
    day = report['until'].strftime('%-d %B %Y')
    head = f'Vacatia 3-site iCX watch — {a} to {b} ({day})'
    L.append(f'## {head}' if not teams else f'**{head}**')
    L.append('')
    L.append(f'{report["windows"]} sweep windows banked in this period.')
    L.append('')

    for s in report['sites']:
        v, why = verdict(s)
        if s['no_data']:
            L.append(f'- **{s["handle"]} {s["name"]}** — {v}: {why}')
            continue
        line = (f'- **{s["handle"]} {s["name"]}** — {v}. '
                f'Now {s["last"]["online"]}, range {s["min"]}–{s["max"]} '
                f'over {s["n"]} windows.')
        L.append(line)
        det = []
        if s['rssi_bad_range']:
            lo, hi = s['rssi_bad_range']
            det.append(f'RSSI-anomalous {lo}–{hi}')
        if s['reboots']:
            tot = sum((w or 0) + (bd or 0) for _, w, bd in s['reboots'])
            det.append(f'{tot} reboot flag(s) across {len(s["reboots"])} window(s)')
        else:
            det.append('zero reboots')
        if s['navail_bad_windows']:
            det.append(f'availability bad in {len(s["navail_bad_windows"])} window(s)')
        if det:
            L.append(f'  {" · ".join(det)}')
        if v != 'FLAT':
            L.append(f'  {why}')
            for d in s['dips']:
                bits = [f'{hz(d["t"])} {d["online"]} ({d["delta"]:+d})']
                c = d['conc']
                if c and c['concentrated']:
                    for r in c['concentrated']:
                        bits.append(f'{r["lost"]}/{c["lost"]} in '
                                    f'{c["grouping"]} {r["group"]} '
                                    f'({r["ratio"]:.1f}x fleet share)')
                elif c and not c['too_small']:
                    bits.append(f'spread over {c["groups_touched"]} '
                                f'{c["grouping"]}s, none concentrated')
                L.append(f'  - {" — ".join(bits)}')
    L.append('')
    return '\n'.join(L)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--hours', type=float, default=4.0)
    p.add_argument('--until', help='UTC ISO instant; default now')
    p.add_argument('--teams', action='store_true', help='Teams-draft shape')
    p.add_argument('--append-log', action='store_true',
                   help=f'append the entry to {LOG}')
    p.add_argument('-o', '--out')
    args = p.parse_args()

    until = (datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc)
             if args.until else datetime.now(timezone.utc))
    since = until - timedelta(hours=args.hours)
    rep = analyse(since, until)
    text = render(rep, teams=args.teams)

    if args.out:
        open(args.out, 'w').write(text + '\n')
        print(f'wrote {args.out}', file=sys.stderr)
    else:
        print(text)

    if args.append_log:
        if not os.path.exists(LOG):
            print(f'log missing, not appending: {LOG}', file=sys.stderr)
        else:
            body = open(LOG).read()
            marker = '<!-- DIGESTS BELOW — newest first -->'
            entry = render(rep, teams=False)
            if marker in body:
                body = body.replace(marker, marker + '\n\n' + entry, 1)
            else:
                body = body.rstrip() + '\n\n' + entry
            open(LOG, 'w').write(body)
            print(f'appended to {LOG}', file=sys.stderr)


if __name__ == '__main__':
    main()
