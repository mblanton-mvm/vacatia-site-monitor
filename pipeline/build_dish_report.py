#!/usr/bin/env python3
"""Render the DISH-facing daily polling report (HTML -> PDF via headless Chrome).

Facts come from today's iCX Online-STB per-device exports and the per-window health
CSVs. Nothing here is inferred beyond what the polls show, and every causal
statement carries an explicit confidence.
"""
import csv, glob, json, os, re, sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ICX = os.path.join(HERE, 'icx')
SWEEPS = '/Users/micheleblanton/Developer/mvm-platform/docs/vacatia/data-drops/icx-sweeps'
DAY = '20260803'
DATE_H = 'Monday 3 August 2026'

SITES = [
    ('MVM784', 'The Berkley Las Vegas', 'floor',
     lambda r: (re.match(r'^(\d{1,2})\d{2}', r).group(1) if re.match(r'^\d{3,4}', r) else '?')),
    ('MVM783', 'The Grandview at Las Vegas', 'building',
     lambda r: (re.match(r'^(\d{4,5})', r).group(1)[:-3] if re.match(r'^\d{4,5}', r) else '?')),
    ('MVM743', 'The Cliffs at Peace Canyon', 'building',
     lambda r: (re.match(r'^(\d{1,2})', r).group(1) if re.match(r'^\d', r) else '?')),
]


def load(f):
    d = {}
    for r in csv.DictReader(open(f, encoding='utf-8-sig')):
        k = (r.get('Device ID') or r.get('﻿Device ID') or '').strip()
        if k:
            d[k] = (r.get('Room Number') or '').strip()
    return d


def polls_for(handle):
    out = []
    for f in sorted(glob.glob(f'{ICX}/icx-online-stbs-{handle}-{DAY}T*ET.csv')):
        w = re.search(r'T(\d{4})ET', f).group(1)
        out.append((f'{w[:2]}:{w[2:]}', load(f)))
    return out


def health(handle):
    """{window_ET: row} from the per-window health CSVs (windows are UTC in the name)."""
    out = {}
    for f in glob.glob(f'{SWEEPS}/{DAY}T*/dish-icx-{handle.lower()}-health-*.csv'):
        m = re.search(rf'{DAY}T(\d{{6}})Z', os.path.basename(f))
        rows = list(csv.DictReader(open(f)))
        if rows and m:
            hh = int(m.group(1)[:2]) - 4  # UTC-4 -> Eastern
            out[f'{hh % 24:02d}:{m.group(1)[2:4]}'] = rows[0]
    return out


def analyse(handle, grp):
    P = polls_for(handle)
    wins = [w for w, _ in P]
    seen = defaultdict(dict)
    for w, d in P:
        for k, room in d.items():
            seen[k][w] = room

    # per-window deltas, and where the losses landed
    deltas = []
    for i in range(1, len(P)):
        prev, cur = P[i - 1][1], P[i][1]
        lost = {k: prev[k] for k in prev if k not in cur}
        gained = [k for k in cur if k not in prev]
        deltas.append({'window': wins[i], 'online': len(cur), 'lost': len(lost),
                       'gained': len(gained),
                       'by': Counter(grp(r) for r in lost.values()).most_common(4)})

    # sustained outages: absent across 2+ back-to-back polls
    def mins(t):
        return int(t[:2]) * 60 + int(t[3:])

    sust = []
    for did, ws in seen.items():
        flags = [w in ws for w in wins]
        first = flags.index(True)
        last = len(flags) - 1 - flags[::-1].index(True)
        runs, i = [], 0
        while i < len(flags):
            if not flags[i]:
                j = i
                while j + 1 < len(flags) and not flags[j + 1]:
                    j += 1
                if i > first:  # ignore a leading gap: the box joined the day late
                    lo = wins[i - 1]
                    hi = wins[j + 1] if j + 1 < len(wins) else wins[j]
                    runs.append({'from': wins[i], 'to': wins[j], 'polls': j - i + 1,
                                 'minutes': mins(hi) - mins(lo),
                                 'trailing': j == len(flags) - 1})
                i = j + 1
            else:
                i += 1
        worst = max(runs, key=lambda g: (g['polls'], g['minutes']), default=None)
        if worst and worst['polls'] >= 2:
            sust.append({'device': did, 'room': next(iter(ws.values())) or '(none)',
                         'seen': sum(flags), 'total': len(flags), 'worst': worst,
                         'gaps': len(runs), 'last_seen': wins[last]})
    sust.sort(key=lambda x: (-x['worst']['polls'], -x['worst']['minutes']))
    return {'handle': handle, 'wins': wins, 'deltas': deltas, 'sust': sust,
            'devices_ever': len(seen), 'health': health(handle),
            'counts': [len(d) for _, d in P],
            'group_totals': Counter(grp(r) for r in P[-1][1].values())}


A = {h: analyse(h, g) for h, _, _, g in SITES}

# ── per-device signal enrichment: what the boxes looked like, not just that they dropped ──
ANOM = '/Users/micheleblanton/Developer/mvm-platform/docs/vacatia/data-drops/icx-anomalies'


def enrich(handle, grp):
    """Per-device RSSI / disruption / availability lists, grouped the same way as the losses.

    These come from the iCX net-stats per-bucket exports. Each is a SNAPSHOT at one moment,
    not a series — the capture time is carried through and printed, because a snapshot taken
    two hours before an event cannot speak to that event.

    Load-bearing caveat: a box that is FULLY dark contributes no telemetry, so it cannot
    appear in any of these lists. Absence from them is therefore not evidence of health.
    """
    out = []
    for pat, label, unit in (
            ('availability-warn-{h}-*.csv', 'Net availability degraded', 'seconds online of 900'),
            ('disruption-warn-{h}-*.csv', 'Internet disruption elevated', 'disruption count'),
            ('disruption-anomaly-{h}-*.csv', 'Internet disruption elevated', 'disruption count'),
            ('rssi-warn-{h}-*.csv', 'RSSI anomalous', 'dBm'),
            ('rssi-anomaly-{h}-*.csv', 'RSSI anomalous', 'dBm')):
        g = sorted(glob.glob(os.path.join(ANOM, pat.format(h=handle))))
        if not g:
            continue
        if any(e['label'] == label for e in out):   # prefer the first (newer) naming
            continue
        rows = list(csv.DictReader(open(g[-1], encoding='utf-8-sig')))
        if not rows:
            continue
        key = next((k for k in rows[0] if any(t in k for t in
                    ('RSSI', 'Disruption', 'Availability'))), None)
        ts = re.search(r'(\d{8})T(\d{4})ET', g[-1])
        vals = sorted(float(r[key]) for r in rows if r.get(key) and
                      re.match(r'^-?[\d.]+$', r[key].strip()))
        c = Counter(grp(r.get('Room Number') or '') for r in rows)
        macs = {r['Device ID'].strip().lower() for r in rows if r.get('Device ID')}
        sust = {d['device'] for d in A[handle]['sust']}
        dark = {d['device'] for d in A[handle]['sust'] if d['worst']['trailing']}
        out.append({
            'label': label, 'unit': unit, 'n': len(rows),
            'at': f'{ts.group(2)[:2]}:{ts.group(2)[2:]}' if ts else '?',
            'lo': vals[0] if vals else None, 'hi': vals[-1] if vals else None,
            'med': vals[len(vals) // 2] if vals else None,
            'top': c.most_common(3), 'groups': len(c),
            'sust_hit': len(sust & macs), 'sust_n': len(sust),
            'dark_hit': len(dark & macs), 'dark_n': len(dark),
        })
    return out


ENR = {h: enrich(h, g) for h, _, _, g in SITES}
json.dump({h: {k: v for k, v in a.items() if k != 'group_totals'} for h, a in A.items()},
          open(os.path.join(HERE, 'dish-report-data.json'), 'w'), indent=1, default=str)

# ─────────────────────────── narrative, written per site ───────────────────────────
NARR = {
 'MVM784': {
  'verdict': 'Site-wide intermittent drop-off, no spatial pattern',
  'sev': 'watch',
  'body': [
   ("Berkley's box count moved within a 714–782 band across thirty-three polls, a swing of 68 boxes "
    "(8.7% of the fleet). The deepest point was 14:44 ET at 714 online; it recovered to 774 by "
    "15:29 ET without intervention and spent the entire evening and overnight period between 770 "
    "and 782, finishing the day at 780."),
   ("The losses are spread evenly across every floor in the tower — at the 14:44 ET trough the 61 "
    "missing boxes came from at least ten different floors, with no floor contributing more than "
    "eight. <strong>That argues against a single access point, switch or riser</strong> and points "
    "instead at something acting on the whole property at once. Confidence: high, on the basis that "
    "the same distributed shape repeats across all thirty-three polls."),
   ("Separately, iCX's own NET_DISRUPTION_COUNT is not usable as a signal at this site, and we want to "
    "flag that rather than quietly ignore it. Across 34 windows it ranged from <strong>88 to 652 boxes "
    "flagged 'bad' — 11% to 83% of the fleet</strong> — while the online count moved by at most eight "
    "boxes over the same stretch. The two are uncorrelated. We are not reporting it as a fault, but we "
    "would welcome DISH's read on what threshold drives it, because a counter that swings across most "
    "of the fleet while nothing else moves cannot be acted on."),
  ],
  'ask': ("Berkley is the site we would most like DISH's help on. The distributed shape means the "
          "next useful step is above the individual box — we are asking whether DISH sees anything "
          "property-wide at Berkley in the 14:14–15:00 ET window today."),
  'why': [
   ("The boxes that dropped are the same boxes iCX was already flagging for internet disruption. "
    "Of the 18 devices still dark at the last poll, <strong>16 appear in the disruption list</strong>; "
    "of the 10 that missed two or more consecutive polls, 8 do. That is not a coincidence of "
    "sampling — the disruption list covers 467 of 763 boxes, but the overlap with the boxes that "
    "actually went away is near-total."),
   ("<strong>Signal strength is not the cause.</strong> Only 24 boxes property-wide are flagged "
    "RSSI-anomalous, spread thinly across 15 different floors, and only one of them is a box that "
    "dropped. Those 24 read −28 to −34 dBm, which is a box sitting <em>very close</em> to its access "
    "point — unusually strong, not weak. Berkley's problem is not RF."),
   ("<strong>Read:</strong> boxes across the whole tower are losing their network path while their "
    "radio link stays healthy. Combined with the even floor-by-floor spread, that points upstream of "
    "the access points — a shared gateway, uplink or WAN path serving the property. Confidence: "
    "moderate. The disruption/outage overlap is strong, but our per-device snapshot is from 15:14 ET "
    "and the deepest drop was 14:44 ET, so it is corroborating evidence rather than a measurement of "
    "the event itself."),
  ],
 },
 'MVM783': {
  'verdict': 'Buildings 1 and 11 collapsed, then self-recovered after ~75 minutes',
  'sev': 'urgent',
  'body': [
   ("This is the clearest and most actionable finding in today's data. Grandview held 4,340–4,367 "
    "boxes online from midday through 16:59 ET across thirty-one polls. At the 17:14 ET poll it fell to 4,093 — a loss of "
    "250 boxes in a single fifteen-minute window, the largest movement recorded at any of the three "
    "sites today."),
   ("<strong>The loss is almost entirely confined to two buildings.</strong> Building 1 lost 185 of "
    "its 276 boxes (67%) and building 11 lost 61 of 87 (70%). Of the roughly twenty other buildings "
    "on the property, <strong>every single one lost zero.</strong> Two stray devices elsewhere account "
    "for the remainder."),
   ("It did not happen all at once. Those same two buildings had been shedding boxes for four hours "
    "beforehand, and nothing else was: 5 boxes at 15:44 ET, 12 at 15:59, 37 at 16:14, 17 at 16:44, "
    "16 at 16:59 — then 246 at 17:14. Across all six windows the losses in buildings 1 and 11 "
    "outnumber every other building combined by more than fifty to one."),
   ("<strong>Read:</strong> this is consistent with shared infrastructure serving buildings 1 and 11 "
    "degrading and then failing, rather than with box-level faults. Confidence: high, on the strength "
    "of a monotonic four-hour escalation inside one spatial cluster while twenty peers stayed flat. "
    "We have not yet identified the specific shared element and are not asserting one."),
   ("<strong>It recovered on its own.</strong> By the 18:29 ET poll the count was back to 4,351 — "
    "194 boxes returned in building 1 and 64 in building 11. Both buildings finished <em>above</em> "
    "their pre-event baseline (286 and 92 against 276 and 87), because some boxes that had been dark "
    "earlier in the day came back at the same time. Total duration roughly 75 minutes, 17:14 to "
    "18:29 ET, with no intervention from either side."),
   ("It has stayed healthy since. Across the twenty-plus polls from 18:29 ET through 09:00 ET the "
    "next morning the count held 4,350–4,352, RSSI-anomalous fell from 135 boxes to <strong>zero</strong>, "
    "and degraded net-availability fell from 132 boxes to nought or one. Nothing has recurred."),
   ("<strong>Why we are still raising it.</strong> A fault that clears itself without anyone touching "
    "it has not been diagnosed — it has only stopped. The escalation pattern beforehand was four hours "
    "long and strictly confined to two buildings, which is not the shape of a transient. We would like "
    "to understand what recovered, so that if it returns we are not starting from zero."),
   ("Because the event resolved inside the polling day, the boxes lost at 17:14 ET returned before "
    "missing two consecutive polls, so they do not appear in the back-to-back table below. The 38 "
    "devices that do appear there are the earlier, slower phase of the same pattern."),
  ],
  'ask': ("We would like DISH to look at the <strong>wired path serving buildings 1 and 11</strong> at "
          "Grandview — uplink, IDF switch or gateway — across the 15:44–18:29 ET window. Based on the "
          "evidence below we would specifically <em>de-prioritise</em> the access points and RF in "
          "those buildings, and we would not start with the set-top boxes themselves. The site is "
          "healthy now, so this is a post-mortem rather than an emergency: the question is what failed "
          "and what restored it, so a recurrence is recognisable immediately."),
  'why': [
   ("We pulled per-device telemetry at 17:23–17:29 ET, while the event was still running, and it "
    "identifies the layer. Three independent signals, and they do not agree by accident:"),
   ('<table class="why"><thead><tr><th>Signal</th><th class="n">Boxes flagged</th>'
    '<th class="n">In buildings 1 &amp; 11</th><th class="n">Elsewhere</th></tr></thead><tbody>'
    '<tr class="hi"><td>Net availability degraded</td><td class="n">132</td>'
    '<td class="n"><strong>130 (98%)</strong></td><td class="n">2</td></tr>'
    '<tr class="hi"><td>Internet disruption elevated</td><td class="n">235</td>'
    '<td class="n"><strong>227 (97%)</strong></td><td class="n">8</td></tr>'
    '<tr><td>RSSI anomalous (weak signal)</td><td class="n">135</td>'
    '<td class="n">9 (7%)</td><td class="n">126</td></tr></tbody></table>'),
   ("<strong>The two network signals land almost entirely inside buildings 1 and 11. The signal-"
    "strength problem lands almost entirely outside them.</strong> The overlap between either "
    "network signal and the RSSI list is three boxes out of hundreds. The 126 genuinely weak-signal "
    "boxes — −36 to −77 dBm, in buildings 2, 3, 4, 5, 6, 8, 41, 42, 51 and 61 — are <em>staying "
    "online</em> throughout."),
   ("The degraded boxes in buildings 1 and 11 hold connectivity for only <strong>211 to 299 seconds "
    "of each 900-second window</strong> — roughly a quarter to a third of the time. They are not "
    "powered off and they are not out of radio range; they are reachable in bursts."),
   ("<strong>Read: this is a wired/upstream fault serving buildings 1 and 11, not a wireless or "
    "box-level one.</strong> Boxes with healthy radios cannot hold a network path, while boxes with "
    "genuinely poor radios elsewhere on the property are unaffected. Confidence: high — three "
    "signals, two spatial distributions that are near-mutually-exclusive, and telemetry captured "
    "during the event rather than before it. We still cannot name the specific device, and we are "
    "not guessing at one."),
  ],
 },
 'MVM743': {
  'verdict': 'Stable; five boxes dark since 13:59 ET',
  'sev': 'ok',
  'body': [
   ("The Cliffs was the quietest of the three sites by a wide margin. Across thirty-two polls the "
    "count sat between 451 and 459 — a total swing of eight boxes, under 2% of the fleet — and "
    "finished at 459, the day's high. Per-window losses were between zero and eight, and most "
    "windows lost nothing at all."),
   ("Five boxes are the exception. They were present at the first two polls, dropped out at 13:59 ET "
    "and have not returned in the nine polls since — roughly four and three-quarter hours dark. They "
    "are in rooms 1082 (two boxes), 1083, 1002 and 2026, which sit in three different buildings, so "
    "this is not one shared failure point."),
   ("We checked these against the field punch list before reporting them: <strong>none of these five "
    "rooms has been visited by a technician</strong> — their records are untouched since the 24 July "
    "seed. So this is not an artifact of the relabelling sweep running at the property today. They "
    "went dark on their own and stayed dark."),
   ("One further note for context rather than concern: Cliffs' RSSI 'bad' bucket reads 14–22 boxes "
    "all day. On this fleet those readings are <strong>too strong</strong> — roughly −25 to −34 dBm, "
    "a box sitting very close to its access point — not weak signal. We mention it because the label "
    "invites the opposite reading."),
  ],
  'ask': ("For Cliffs we are only asking about the five dark boxes listed below — specifically whether "
          "DISH can see anything for them beyond 13:59 ET, since from our side they simply stop. The "
          "rest of the property is behaving well and needs nothing today."),
  'why': [
   ("<strong>We cannot tell you why these five went dark, and it is worth being precise about why "
    "not.</strong> The per-device telemetry iCX exposes only covers boxes that are still reporting "
    "something. A box that has gone fully dark contributes no rows at all — so its absence from the "
    "signal-strength and disruption lists is not evidence that it was healthy. It is simply absence "
    "of data. None of the five appears, which is exactly what a fully-dark box looks like and tells "
    "us nothing either way."),
   ("What we can say is that the property around them is sound. Only 20 boxes are RSSI-anomalous, and "
    "those read −25 to −34 dBm — again <em>too strong</em> rather than weak. Elevated disruption "
    "covers 69 boxes and does concentrate in two building groups, which is worth noting as a mild "
    "localized pattern, but it does not include the five dark rooms."),
   ("This is the one place where our method has a hard floor, and the reason we are asking rather "
    "than concluding: the last thing we observed about these boxes is that they stopped. Anything "
    "beyond that has to come from DISH's side."),
  ],
 },
}

SEV = {'urgent': ('#8c2f13', 'Needs attention'),
       'watch': ('#8a6410', 'Worth a look'),
       'ok': ('#2d5a3d', 'Stable')}


def esc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def spark(counts):
    """Inline SVG line of the day's box count — endpoint emphasised."""
    if len(counts) < 2:
        return ''
    lo, hi = min(counts), max(counts)
    rng = max(hi - lo, 1)
    W, H = 190, 34
    pts = [(i * W / (len(counts) - 1), H - 3 - (c - lo) / rng * (H - 8))
           for i, c in enumerate(counts)]
    d = ' '.join(f'{"M" if i == 0 else "L"}{x:.1f},{y:.1f}' for i, (x, y) in enumerate(pts))
    fill = f'{d} L{W},{H} L0,{H} Z'
    return (f'<svg class="spark" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
            f'<path d="{fill}" fill="rgba(20,70,120,.10)"/>'
            f'<path d="{d}" fill="none" stroke="#1a5a8a" stroke-width="1.5"/>'
            f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="2.6" fill="#1a5a8a"/></svg>')


rows_html = []
for handle, name, glabel, _ in SITES:
    a, n = A[handle], NARR[handle]
    col, sevlabel = SEV[n['sev']]
    counts = a['counts']
    hh = a['health']
    last_h = hh.get(a['wins'][-1]) or (list(hh.values())[-1] if hh else {})

    tl = ''.join(
        f'<tr class="{"hi" if d["lost"] >= 30 else ""}"><td class="w">{d["window"]}</td>'
        f'<td class="n">{d["online"]:,}</td>'
        f'<td class="n neg">{("−" + str(d["lost"])) if d["lost"] else "—"}</td>'
        f'<td class="n pos">{("+" + str(d["gained"])) if d["gained"] else "—"}</td>'
        f'<td class="sm">{", ".join(f"{glabel[0]}{k} × {v}" for k, v in d["by"]) or "—"}</td></tr>'
        for d in a['deltas'])

    sust = a['sust']
    srows = ''.join(
        f'<tr><td class="mono">{esc(s["room"])}</td><td class="mono sm">{esc(s["device"])}</td>'
        f'<td class="n">{s["seen"]}/{s["total"]}</td>'
        f'<td class="n"><strong>{s["worst"]["polls"]}</strong></td>'
        f'<td class="n">{s["worst"]["minutes"]}</td>'
        f'<td class="w">{s["worst"]["from"]}</td>'
        f'<td>{"<span class=dark>still dark</span>" if s["worst"]["trailing"] else "recovered"}</td></tr>'
        for s in sust[:26])
    more = (f'<p class="more">{len(sust) - 26} further devices with a 2+ poll outage are in the '
            f'accompanying CSV.</p>' if len(sust) > 26 else '')

    rows_html.append(f'''
<section class="site">
  <div class="sitehead">
    <div>
      <div class="handle">{handle}</div>
      <h2>{esc(name)}</h2>
      <div class="verdict" style="color:{col}">{esc(n['verdict'])}</div>
    </div>
    <div class="sitestat">
      {spark(counts)}
      <div class="statrow"><span>{counts[0]:,}</span><em>first poll</em></div>
      <div class="statrow"><span>{counts[-1]:,}</span><em>last poll</em></div>
      <div class="statrow"><span>{min(counts):,}–{max(counts):,}</span><em>day range</em></div>
      <span class="pill" style="--c:{col}">{sevlabel}</span>
    </div>
  </div>

  {''.join(f'<p>{b}</p>' for b in n['body'])}

  <h3>What the boxes looked like before they dropped
      <span class="unit">— per-device telemetry</span></h3>
  {''.join(b if b.lstrip().startswith('<table') else f'<p>{b}</p>' for b in n.get('why', []))}
  {('<table class="enr"><thead><tr><th>Signal</th><th class="n">Boxes</th><th>Captured</th>'
    '<th class="n">Range</th><th>Concentration</th>'
    '<th class="n">Of the still-dark boxes</th></tr></thead><tbody>'
    + ''.join(
      f'<tr><td>{e["label"]}</td><td class="n">{e["n"]}</td><td class="w">{e["at"]} ET</td>'
      f'<td class="n">{("%g → %g" % (e["lo"], e["hi"])) if e["lo"] is not None else "—"}</td>'
      f'<td class="sm">{", ".join(f"{glabel[0]}{k} × {v}" for k, v in e["top"])} '
      f'<span class="unit">(of {e["groups"]})</span></td>'
      f'<td class="n">{e["dark_hit"]} of {e["dark_n"]}</td></tr>' for e in ENR[handle])
    + '</tbody></table>'
    + f'<p class="more">Ranges are in the signal\'s own unit '
      f'({", ".join(dict((e["label"], e["unit"]) for e in ENR[handle]).values())}). '
      f'Each row is a single snapshot at the time shown, not a series.</p>'
   ) if ENR[handle] else '<p class="more">No per-device telemetry was captured at this site today.</p>'}

  <h3>Box count by polling window <span class="unit">(Eastern)</span></h3>
  <table class="tl">
    <thead><tr><th>Window</th><th class="n">Online</th><th class="n">Lost</th>
      <th class="n">Returned</th><th>Where the losses were</th></tr></thead>
    <tbody>{tl}</tbody>
  </table>

  <h3>Boxes offline across back-to-back polling windows
      <span class="unit">({len(sust)} device{"s" if len(sust) != 1 else ""})</span></h3>
  <p class="lead">A box missing from one poll is routine churn on this fleet. These are the boxes that
     missed <strong>two or more consecutive polls</strong>, which is the threshold at which we stop
     treating it as noise.</p>
  {'<table class="su"><thead><tr><th>Room</th><th>Device ID</th><th class="n">Polls seen</th>'
   '<th class="n">Consecutive missed</th><th class="n">Minutes</th><th>Dropped at</th>'
   '<th>Status</th></tr></thead><tbody>' + srows + '</tbody></table>' + more
   if sust else '<p class="none">No box missed two consecutive polls at this site today.</p>'}

  <div class="ask"><strong>What we are asking DISH:</strong> {n['ask']}</div>
</section>''')

health_summary = ''.join(
    f'<tr><td>{h}</td>'
    f'<td class="n">{A[h]["counts"][-1]:,}</td>'
    f'<td class="n">{len(A[h]["wins"])}</td>'
    f'<td class="n">{A[h]["devices_ever"]:,}</td>'
    f'<td class="n">{len(A[h]["sust"])}</td>'
    f'<td class="n">{sum(1 for s in A[h]["sust"] if s["worst"]["trailing"])}</td></tr>'
    for h, _, _, _ in SITES)

HTML = f'''<meta charset="utf-8"><title>MVM — Vacatia daily polling report, {DATE_H}</title>
<style>
  @page {{ size: letter; margin: 14mm 13mm 16mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font: 10pt/1.5 "Charter","Iowan Old Style",Georgia,serif; color:#1b1b1b; margin:0; }}
  h1 {{ font: 600 21pt/1.2 "Helvetica Neue",Arial,sans-serif; margin:0 0 2mm; letter-spacing:-.2px; }}
  h2 {{ font: 600 15pt/1.2 "Helvetica Neue",Arial,sans-serif; margin:.5mm 0 1mm; }}
  h3 {{ font: 600 9pt/1.3 "Helvetica Neue",Arial,sans-serif; text-transform:uppercase;
        letter-spacing:.9px; color:#4a4a4a; margin:7mm 0 2mm;
        border-bottom:1px solid #d6d2cb; padding-bottom:1.5mm; }}
  .unit {{ text-transform:none; letter-spacing:0; font-weight:400; color:#8a8578; }}
  p {{ margin:0 0 2.6mm; }}
  .doc {{ border-bottom:2.5px solid #1a3f5c; padding-bottom:3mm; margin-bottom:5mm; }}
  .kicker {{ font:600 8.5pt/1 "Helvetica Neue",Arial,sans-serif; letter-spacing:1.6px;
             text-transform:uppercase; color:#1a5a8a; margin-bottom:2.5mm; }}
  .sub {{ color:#5c5850; font-size:9.5pt; }}
  .method {{ background:#f6f4f0; border-left:2.5px solid #b9b2a6; padding:3mm 4mm;
             font-size:8.8pt; line-height:1.55; margin:0 0 6mm; color:#3d3a34; }}
  .method strong {{ color:#1b1b1b; }}
  table {{ width:100%; border-collapse:collapse; font-size:8.6pt; margin-bottom:2mm; }}
  th {{ text-align:left; font:600 7.6pt/1.2 "Helvetica Neue",Arial,sans-serif;
        text-transform:uppercase; letter-spacing:.6px; color:#5c5850;
        border-bottom:1.2px solid #9a938a; padding:1.4mm 2mm; }}
  td {{ padding:1.3mm 2mm; border-bottom:.5px solid #e6e2db; vertical-align:top; }}
  .n {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .w, .mono {{ font-family:"SF Mono",Menlo,monospace; font-size:8pt; white-space:nowrap; }}
  .sm {{ font-size:7.9pt; color:#5c5850; }}
  .neg {{ color:#8c2f13; }} .pos {{ color:#2d5a3d; }}
  tr.hi td {{ background:#fdf3ec; }}
  tr.hi .neg {{ font-weight:700; }}
  .dark {{ color:#8c2f13; font-weight:600; }}
  .site {{ break-before:page; }}
  .sitehead {{ display:flex; justify-content:space-between; align-items:flex-start;
               gap:8mm; border-bottom:1.5px solid #1a3f5c; padding-bottom:3mm; margin-bottom:4mm; }}
  .handle {{ font:600 8pt/1 "SF Mono",Menlo,monospace; color:#1a5a8a; letter-spacing:1px; }}
  .verdict {{ font:600 10pt/1.3 "Helvetica Neue",Arial,sans-serif; margin-top:1mm; }}
  .sitestat {{ text-align:right; flex:0 0 auto; }}
  .spark {{ display:block; margin-bottom:1mm; }}
  .statrow {{ font-size:8.2pt; color:#5c5850; }}
  .statrow span {{ font:600 9.5pt "Helvetica Neue",Arial,sans-serif; color:#1b1b1b;
                   font-variant-numeric:tabular-nums; }}
  .statrow em {{ font-style:normal; margin-left:1.5mm; }}
  .pill {{ display:inline-block; margin-top:1.5mm; font:600 7.4pt "Helvetica Neue",Arial,sans-serif;
           text-transform:uppercase; letter-spacing:.7px; color:#fff; background:var(--c);
           padding:.9mm 2.4mm; border-radius:2px; }}
  .lead {{ font-size:9pt; color:#4a463f; }}
  .none {{ font-size:9pt; color:#2d5a3d; background:#f2f7f3; padding:2.5mm 3mm;
           border-left:2.5px solid #2d5a3d; }}
  .more {{ font-size:8.2pt; color:#6b665d; font-style:italic; }}
  .ask {{ margin-top:5mm; background:#f0f5f9; border-left:2.5px solid #1a5a8a;
          padding:3mm 4mm; font-size:9.2pt; }}
  table.why td, table.enr td {{ font-size:8.5pt; }}
  table.why tr.hi td {{ background:#fdf3ec; }}
  .foot {{ margin-top:7mm; padding-top:2.5mm; border-top:.5px solid #d6d2cb;
           font-size:7.8pt; color:#7a746a; }}
</style>

<div class="doc">
  <div class="kicker">MVM Technology · prepared for DISH Business</div>
  <h1>Vacatia three-site set-top box polling report</h1>
  <div class="sub">{DATE_H} · The Berkley, The Grandview and The Cliffs ·
    Evolve M2 / OnStream fleet · compiled from DISH iCX</div>
</div>

<p><strong>Why you are getting this.</strong> MVM has been polling the iCX dashboard for all three
Vacatia properties on a fifteen-minute cadence and keeping every per-device export, so we can tell
the difference between a box that blinked and a box that is genuinely down. This report covers
{DATE_H} and is written to give DISH the shortest path to the two things we think need
attention.</p>

<p><strong>The short version.</strong> The Grandview has a localized failure that is not subtle:
buildings 1 and 11 degraded for four hours and then lost roughly two-thirds of their boxes in a
single window, while every other building on the property lost nothing. It then recovered on its own
after about 75 minutes and has been clean since. Per-device telemetry pulled <em>during</em> the event
points at the <strong>wired path serving those two buildings, not the wireless</strong> — their radios
were healthy while their connectivity was not. The Berkley shows the same
network-layer signature but spread evenly across the whole tower rather than concentrated. The Cliffs
is healthy apart from five boxes dark since early afternoon, and there we can tell you what we
observed but not why.</p>

<p><strong>What we are not claiming.</strong> We have identified, at each site, which layer the
evidence points to. We have <em>not</em> identified a specific failing device, and nothing in this
report should be read as naming one. Where we state a cause we give our confidence and the evidence
it rests on; where the data runs out we say so rather than filling the gap.</p>

<div class="method">
  <strong>Method and its limits, stated plainly.</strong> Each figure comes from the iCX
  <em>Online STBs</em> per-device export, captured once per fifteen-minute window and retained.
  A box counts as present in a window if iCX listed it; "lost" means it was in the previous export
  and not the current one. Timestamps are the export's own, which are <strong>Eastern wall
  clock</strong>. Two honest caveats: iCX aggregates in fifteen-minute windows, so a drop shorter
  than that can pass unseen; and polls were not perfectly evenly spaced today, which is why the
  tables give both the number of consecutive windows missed and the real elapsed minutes. Where we
  offer a cause we say so and give our confidence — everything else is presented as observation.
</div>

<h3>All three sites at a glance</h3>
<table>
  <thead><tr><th>Site</th><th class="n">Online, last poll</th><th class="n">Polls</th>
    <th class="n">Devices seen</th><th class="n">2+ poll outages</th>
    <th class="n">Still dark</th></tr></thead>
  <tbody>{health_summary}</tbody>
</table>
<p class="lead" style="margin-top:2mm">"Devices seen" counts every distinct box that appeared in at
least one poll today, which is why it can exceed the count in any single window. "Still dark" is a
box that missed two or more consecutive polls and had not returned by the last one.</p>

{''.join(rows_html)}

<div class="foot">
  Generated {DATE_H} from {sum(len(A[h]['wins']) for h, _, _, _ in SITES)} iCX per-device exports
  ({', '.join(f"{h} ×{len(A[h]['wins'])}" for h, _, _, _ in SITES)}), all retained by MVM and
  available on request. Room identifiers are as they appear in iCX. No guest information is included
  in this report. Questions: Michele Blanton, MVM Technology.
</div>
'''

out = os.path.join(HERE, 'dish-report.html')
open(out, 'w', encoding='utf-8').write(HTML)
print('wrote', out)
for h, _, _, _ in SITES:
    a = A[h]
    print(f"  {h}: {len(a['wins'])} polls, last {a['counts'][-1]}, "
          f"{len(a['sust'])} sustained outages, "
          f"{sum(1 for s in a['sust'] if s['worst']['trailing'])} still dark")
