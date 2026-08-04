#!/usr/bin/env python3
"""Render rooms-state.json into the mobile-first Artifact page.

Room-level: presence is asked by room number (works whether or not a room is relabelled),
labelling is a separate verdict, and every device MAC is kept against the room iCX reports it
under so a mislabelled box can later be traced back to where it belonged.

Usage: python3 build_artifact2.py rooms-state.json artifact.html
"""
import json
import os
import sys

PRES = ['ALL_PRESENT', 'PARTIAL', 'NONE', 'EXTRA_BOXES']
LABL = ['LABELLED_OK', 'NOT_RELABELLED', 'PARTIALLY_LABELLED', 'DUPLICATE_POSITION',
        'WRONG_POSITION', 'NO_BOXES_SEEN']
CAST = ['ALL_CASTABLE', 'SOME_CANNOT_CAST', 'NONE_CASTABLE', 'NO_REGISTRY_DATA']
DCAST = ['OK', 'STRANDED_RANDOMISED', 'NO_REAL_MAC_LEFTOVER_PRESENT', 'NO_REGISTRY_ENTRY',
         'NO_REGISTRY_DATA']


def compact(st, locks=None, with_names=False, hist=None):
    out = {'now': st['now'], 'nowUtc': st.get('now_utc', ''),
           'staleDays': st['stale_days'], 'sites': {}}
    hist = hist or {}
    for code, s in st['sites'].items():
        gi = {g: i for i, g in enumerate(s['groups'])}
        stamps, si = [], {}

        def ts(v):
            v = (v or '')[:16]
            if not v:
                return -1
            if v not in si:
                si[v] = len(stamps)
                stamps.append(v)
            return si[v]
        nm = lambda m: (m or '').replace(':', '')
        # lockout detail, keyed by room -> the rows for that room's whole lockout
        det_by_room, lock_of, lkrooms = {}, {}, {}
        if locks and code in locks.get('sites', {}):
            for lk in locks['sites'][code]['lockouts']:
                rowset = [[d['icx_label'], nm(d['ethernet_mac']), nm(d['wifi_mac_derived']),
                           ts(d['last_seen_icx']) if d['last_seen_icx'] else -1,
                           d['mdns_label'], nm(d['mdns_mac']), d['mdns_ip'],
                           d['mdns_note'], d['labeling_correction'],
                           d.get('room', '')] for d in lk['detail']]
                for rm in lk['sides']:
                    det_by_room[rm] = rowset
                    lock_of[rm] = lk['lockout']
                for rr in lk['rooms']:
                    lkrooms[rr['room']] = rr
        rooms = []
        for r in s['rooms']:
            devs = [[nm(d['mac']), d['icx_label'], d['position'] or '', ts(d['last_seen']),
                     d['days'], DCAST.index(d['casting']), nm(d.get('registry_real_mac')),
                     [nm(x) for x in (d.get('registry_leftovers') or [])],
                     1 if d['stale'] else 0,
                     1 if d.get('in_latest_poll', True) else 0] for d in r['devices']]
            p = r.get('punch') or {}
            # punch answers, PII-free by construction (see merge_rooms.py)
            nc = (p.get('names') or {}) if p else {}
            pk = ([p.get('qr') or '', p.get('devmac') or '', p.get('relabel') or '',
                   p.get('fw') or '', p.get('linear') or '', p.get('flag') or '',
                   p.get('comment') or '', nc.get('occupancy') or '',
                   nc.get('guest_initials') or '', nc.get('bed') or '', nc.get('liv') or '',
                   nc.get('verdict') or '', p.get('done_at') or ''
                   , 1 if p.get('swept') else 0, 1 if p.get('started') else 0]
                  + ([nc.get('guest_name') or '', nc.get('bed_name') or '',
                      nc.get('liv_name') or ''] if with_names else [])) if p else []
            det = det_by_room.get(r['room'], [])
            rooms.append([r['room'], gi.get(r['group'], -1), r['expected'], r['seen'],
                          PRES.index(r['presence']), LABL.index(r['labelling']),
                          CAST.index(r['casting']), r['missing_positions'],
                          (1 if r['punchlist_complete'] else 0)
                          if r['punchlist_complete'] is not None else -1, devs, pk,
                          det, lock_of.get(r['room'], ''),
                          {k: ([v['icx_label'], nm(v['ethernet_mac'])] if v else None)
                           for k, v in (lkrooms.get(r['room'], {}).get('positions') or {}).items()},
                          sorted((lkrooms.get(r['room'], {}).get('positions') or {}).keys()),
                          [[u['icx_label'], nm(u['ethernet_mac'])]
                           for u in (lkrooms.get(r['room'], {}).get('unlabeled') or [])],
                          r.get('never_seen', 0), r.get('not_in_latest', 0)])
        orph = [[nm(o['mac']), o['icx_label'], o['room_reads'], ts(o['last_seen']), o['days'],
                 o.get('suggested_label', ''), o.get('confidence', ''), o.get('basis', ''),
                 o.get('what_would_settle_it', '')] for o in s['orphans']]
        out['sites'][code] = {
            'name': s['name'], 'groupLabel': s['group_label'], 'units': s['units'],
            'tvs': s['tvs'], 'groups': s['groups'], 'stamps': stamps,
            'regLoaded': s['registry_loaded'], 'regRows': s['registry_rows'],
            'boxesSeen': s['summary']['boxes_seen'],
            'boxesExpected': s['summary']['boxes_expected'],
            'boxesSeenOverall': s['summary']['boxes_seen_overall'],
            'boxesSeenCurrent': s['summary']['boxes_seen_current'],
            'boxesMissing': s['summary']['boxes_missing'],
            'devicesCannotCast': s['summary']['devices_cannot_cast'],
            'staleBoxes': s['summary']['stale_boxes'],
            'snaps': [[x['captured_at'][:16], x['devices']] for x in s['snapshots']],
            'sources': s.get('sources', []),
            # per-MAC change timeline, only for MACs that actually changed (build_history.py)
            'hist': {nm(m): [[e['t'], e['k'], e.get('from', ''), e.get('to', ''), e.get('how', '')]
                             for e in evs]
                     for m, evs in (hist.get(code, {}).get('events') or {}).items()},
            'caps': [len(hist.get(code, {}).get('icx_captures') or []),
                     len(hist.get(code, {}).get('reg_captures') or [])],
            'rooms': rooms, 'orphans': orph}
    return out


PAGE = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vacatia Site Monitor</title>
<style>
:root{--ground:#f7f9fa;--panel:#fff;--sunk:#eef2f4;--line:#dae1e6;--ink:#0f1419;--dim:#5c6b78;
 --faint:#8b9bab;--accent:#0369a1;--accent-soft:#e0f2fe;--live:#047857;--gap:#b45309;--dead:#b91c1c;
 --live-bg:#ecfdf5;--gap-bg:#fffbeb;--dead-bg:#fef2f2;
 --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
 --ui:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media(prefers-color-scheme:dark){:root{--ground:#0f1419;--panel:#171e26;--sunk:#111820;--line:#26313c;
 --ink:#e8edf2;--dim:#8b9bab;--faint:#5c6b78;--accent:#38bdf8;--accent-soft:#0c2a3d;--live:#34d399;
 --gap:#fbbf24;--dead:#f87171;--live-bg:#0d2b22;--gap-bg:#2e2410;--dead-bg:#2f1517}}
:root[data-theme=dark]{--ground:#0f1419;--panel:#171e26;--sunk:#111820;--line:#26313c;--ink:#e8edf2;
 --dim:#8b9bab;--faint:#5c6b78;--accent:#38bdf8;--accent-soft:#0c2a3d;--live:#34d399;--gap:#fbbf24;
 --dead:#f87171;--live-bg:#0d2b22;--gap-bg:#2e2410;--dead-bg:#2f1517}
:root[data-theme=light]{--ground:#f7f9fa;--panel:#fff;--sunk:#eef2f4;--line:#dae1e6;--ink:#0f1419;
 --dim:#5c6b78;--faint:#8b9bab;--accent:#0369a1;--accent-soft:#e0f2fe;--live:#047857;--gap:#b45309;
 --dead:#b91c1c;--live-bg:#ecfdf5;--gap-bg:#fffbeb;--dead-bg:#fef2f2}
*{box-sizing:border-box}
.sm{margin:0;background:var(--ground);color:var(--ink);font:15px/1.45 var(--ui);
 -webkit-text-size-adjust:100%;font-variant-numeric:tabular-nums}
.sm-hd{background:var(--panel);border-bottom:1px solid var(--line);padding:12px 14px;position:sticky;top:0;z-index:20}
.sm-hd h1{margin:0;font-size:15px;font-weight:650}
.sm-hd p{margin:3px 0 0;font-size:12px;color:var(--dim)}
.sm-hd p .ok{color:var(--live);font-weight:650}
.sm-hd p .warn{color:var(--gap);font-weight:650}
.sm-hd p .bad{color:var(--dead);font-weight:650}
.sm-body{padding:12px 14px 40px;max-width:1400px;margin:0 auto;display:flex;flex-direction:column;gap:13px}
.sm-sites{display:flex;gap:6px;overflow-x:auto;-webkit-overflow-scrolling:touch}
.sm-site{flex:0 0 auto;min-height:44px;padding:6px 14px;border:1px solid var(--line);border-radius:10px;
 background:var(--panel);color:var(--ink);font:inherit;font-weight:650;cursor:pointer;text-align:left}
.sm-site small{display:block;font-weight:400;font-size:11px;color:var(--dim)}
.sm-site small.cnt{font-size:10.5px;color:var(--faint);font-variant-numeric:tabular-nums}
.sm-site[aria-pressed=true]{border-color:var(--accent);background:var(--accent-soft);color:var(--accent)}
.sm-site:focus-visible,.sm-tile:focus-visible,.sm-f:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.sm-hint{font-size:11.5px;color:var(--faint);margin:-4px 0 0}
.newbar{background:var(--accent);color:var(--panel);border-radius:9px;padding:9px 12px;
 font-size:13px;font-weight:650;display:flex;align-items:center;justify-content:space-between;gap:12px}
.newbar button{background:var(--panel);color:var(--accent);border:0;border-radius:7px;
 padding:6px 14px;font:inherit;font-size:12.5px;font-weight:700;cursor:pointer;min-height:34px}
.seg{display:flex;gap:6px}
.seg:empty{display:none}
.seg button{flex:1 1 0;min-height:40px;padding:5px 12px;border:1px solid var(--line);
 border-radius:9px;background:var(--panel);color:var(--dim);font:inherit;font-size:12px;
 font-weight:650;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:8px}
.seg button b{font-size:15px;color:var(--ink)}
.seg button[aria-pressed=true]{border-color:var(--accent);background:var(--accent-soft);
 color:var(--accent)}
.seg button[aria-pressed=true] b{color:var(--accent)}
.sm-tiles{display:flex;flex-direction:column;gap:8px}
/* every tile the same width on every row: a FIXED column count, not auto-fit — auto-fit sized
   each row to its own tile count, so the 4-tile row was wider than the 6-tile row */
.sm-trow{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
@media(min-width:560px){.sm-trow{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(min-width:900px){.sm-trow{grid-template-columns:repeat(6,minmax(0,1fr))}}
.sm-tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:9px 11px;
 display:flex;flex-direction:column;gap:2px;text-align:left;font:inherit;color:var(--ink);cursor:pointer;
 min-height:60px}
.sm-tile[data-f=""]{cursor:default}
.sm-tile b{font-size:21px;line-height:1.1;font-weight:650}
.sm-tile span{font-size:10.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;line-height:1.25}
.sm-tile.live b{color:var(--live)}.sm-tile.gap b{color:var(--gap)}.sm-tile.dead b{color:var(--dead)}
.sm-tile[aria-pressed=true]{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent);background:var(--accent-soft)}
.sm-row{display:flex;gap:8px;flex-wrap:wrap}
.sm-f{flex:1 1 150px;min-height:42px;background:var(--panel);border:1px solid var(--line);border-radius:9px;
 color:var(--ink);font:inherit;font-size:14px;padding:8px 10px}
.sm-count{font-size:12.5px;color:var(--dim);display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.sm-count button{background:none;border:0;color:var(--accent);font:inherit;font-size:12.5px;font-weight:600;
 cursor:pointer;padding:4px 0;text-decoration:underline}
.sm-cards{display:flex;flex-direction:column;gap:7px}
.sm-card{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--line);
 border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:7px}
.sm-card.s-live{border-left-color:var(--live)}.sm-card.s-gap{border-left-color:var(--gap)}
.sm-card.s-dead{border-left-color:var(--dead)}
.sm-top{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.sm-rm{font-family:var(--mono);font-size:17px;font-weight:650;letter-spacing:-.02em}
.sm-grp{font-size:11.5px;color:var(--faint);text-align:right}
.sm-pills{display:flex;gap:5px;flex-wrap:wrap}
.pill{font-size:11px;font-weight:650;padding:2px 8px;border-radius:99px;border:1px solid}
.pill.live{color:var(--live);border-color:var(--live);background:var(--live-bg)}
.pill.gap{color:var(--gap);border-color:var(--gap);background:var(--gap-bg)}
.pill.dead{color:var(--dead);border-color:var(--dead);background:var(--dead-bg)}
.pill.mute{color:var(--dim);border-color:var(--line);background:var(--sunk)}
.sm-devs{display:flex;flex-direction:column;gap:4px;border-top:1px solid var(--line);padding-top:6px}
.sm-dev{font-family:var(--mono);font-size:11.5px;color:var(--dim);display:flex;gap:8px;flex-wrap:wrap;align-items:baseline}
.sm-dev b{color:var(--ink);font-weight:650}
.sm-dev .x{color:var(--dead)}
.sm-none{color:var(--dead);font-size:12px}
.sm-card.open{border-color:var(--accent)}
.sm-more{border-top:1px solid var(--line);padding-top:7px;display:flex;flex-direction:column;gap:7px}
.sm-sec{font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--faint);font-weight:650}
.sm-kv{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;font-size:12px}
.sm-kv dt{color:var(--dim)}
.sm-kv dd{margin:0;font-weight:600}
.sm-kv dd.bad{color:var(--dead)}.sm-kv dd.good{color:var(--live)}.sm-kv dd.warn{color:var(--gap)}
.sm-cmt{font-size:12px;background:var(--sunk);border-radius:7px;padding:6px 9px;color:var(--ink)}
.sm-tap{font-size:11px;color:var(--accent);font-weight:600}
.cmp{overflow-x:auto;-webkit-overflow-scrolling:touch;background:var(--panel)}
/* table-layout:fixed with no per-column widths = every column exactly the same width */
.cmp table{width:100%;border-collapse:collapse;min-width:1000px;table-layout:fixed}
.cmp th{text-align:left;padding:5px 8px;background:var(--sunk);border-bottom:1px solid var(--line);
 font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim);font-weight:650;white-space:nowrap;vertical-align:top}
.cmp th span{display:block;text-transform:none;letter-spacing:0;font-weight:400;font-size:9.5px;color:var(--faint)}
/* wrap, never ellipsis: equal columns are worth having but not at the price of clipping a note
   ("Stale MAC (randomised)" was being cut to "Stale MAC (randomis…") */
.cmp td{padding:5px 8px;border-bottom:1px solid var(--line);font-size:11.5px;
 white-space:normal;overflow-wrap:anywhere;vertical-align:top}
.cmp td.m{font-family:var(--mono);font-size:11px}
.cmp td.bad{color:var(--dead);font-weight:650}
.cmp td.warn{color:var(--gap);font-weight:650}
.cmp tr.mo td{background:color-mix(in srgb,var(--sunk) 60%,transparent)}
/* the side band. It used to be sunk-grey like the header row and vanished into the data —
   now it is an accent band with a rule above it, so each side reads as its own block. */
.cmp tr.gh td{background:var(--accent-soft);color:var(--accent);border-top:1px solid var(--accent);
 border-bottom:1px solid var(--accent);font-family:var(--mono);font-weight:700;font-size:13px;
 letter-spacing:.01em;padding:7px 8px}
.cmp tr.gh em{font-style:normal;font-family:var(--ui);font-size:9px;font-weight:700;
 background:var(--accent);color:var(--panel);text-transform:uppercase;letter-spacing:.07em;
 margin-left:9px;padding:2px 6px;border-radius:99px;vertical-align:1px}
.cmp .q{color:var(--faint);font-style:italic}
.cmp tbody tr:last-child td{border-bottom:0}
/* ---- drill: stacked sections, each an equal-width grid ---- */
.dg{display:flex;flex-direction:column;gap:10px}
.dsec{border:1px solid var(--line);border-radius:9px;background:var(--panel);overflow:hidden}
.dsh{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;
 padding:6px 10px;background:var(--sunk);border-bottom:1px solid var(--line);
 font-size:9.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);font-weight:700}
.dsh b{font-family:var(--mono);font-size:11px;text-transform:none;letter-spacing:0;
 color:var(--dim);font-weight:400;text-align:right}
.dsh b.warn{color:var(--gap);font-weight:650}
.dsh b.done{color:var(--live);font-weight:700;text-transform:uppercase;letter-spacing:.05em;font-size:9.5px;font-family:var(--ui)}
/* gap:1px over a line-coloured background draws the hairlines between equal cells */
.grid{display:grid;gap:1px;background:var(--line);
 grid-template-columns:repeat(var(--n,4),minmax(0,1fr))}
/* on a phone the 4-answer grids go 2-up, but the TV grid stacks 1-up: its panel count is odd as
   often as not, and an empty grid area shows the hairline background as a phantom grey cell */
@media(max-width:640px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}
 .grid.tvg{grid-template-columns:minmax(0,1fr)}}
.grid>div{background:var(--panel);padding:8px 10px;min-width:0}
.grid .ch{font-size:9px;text-transform:uppercase;letter-spacing:.07em;color:var(--faint);
 font-weight:700;margin-bottom:4px;line-height:1.3}
.grid .cv{font-size:12.5px;font-weight:650;line-height:1.35;overflow-wrap:anywhere}
.grid .cm{font-family:var(--mono);font-size:11px;font-weight:400;color:var(--dim);
 line-height:1.4;overflow-wrap:anywhere}
.grid .cv+.cv{margin-top:5px}
.grid .cm+.cv{margin-top:5px}
.grid .q{color:var(--faint);font-weight:400}
.grid .ok{color:var(--live)} .grid .bad{color:var(--dead)} .grid .warn{color:var(--gap)}
/* a position with no box: the panel itself goes red — this replaces the Missing Position row */
.grid>div.miss{background:var(--dead-bg)}
.grid>div.miss .ch{color:var(--dead)}
.grid>div.miss .cv{color:var(--dead);font-weight:700;letter-spacing:.04em}
.grid>div.allc{background:color-mix(in srgb,var(--sunk) 55%,transparent)}
/* reported before, absent from the latest poll */
.grid>div.dkc{background:var(--gap-bg)}
.grid .dk{font-size:8.5px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
 color:var(--panel);background:var(--gap);padding:2px 5px;border-radius:99px;
 margin-left:6px;vertical-align:1px;white-space:nowrap}
.dcmt{font-size:12px;font-style:italic;color:var(--ink);background:var(--sunk);
 border-top:1px solid var(--line);padding:7px 10px;line-height:1.45}
.dsec .sm-none{padding:9px 10px}
/* ---- change timeline ---- */
.chq{padding:9px 10px;font-size:12px;color:var(--dim)}
.chl{display:flex;flex-direction:column}
.chr{display:grid;grid-template-columns:130px 130px 1fr;gap:10px;padding:6px 10px;
 border-bottom:1px solid var(--line);font-size:12px;align-items:baseline}
.chr:last-child{border-bottom:0}
@media(max-width:640px){.chr{grid-template-columns:1fr;gap:2px}}
.cht{font-family:var(--mono);font-size:11px;color:var(--ink);font-weight:650;white-space:nowrap}
.chm{font-family:var(--mono);font-size:10.5px;color:var(--faint)}
.chw{line-height:1.45;overflow-wrap:anywhere}
.chw .was{color:var(--dim);text-decoration:line-through}
.chw b{font-family:var(--mono);font-size:11.5px}
.tag{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
 padding:2px 6px;border-radius:99px;margin-right:6px;white-space:nowrap}
.tag.ok{background:var(--live);color:var(--panel)}
.tag.bad{background:var(--dead);color:var(--panel)}
/* ---- Jarran's punchlist button + the field work order ---- */
.sm-site.jb{border-color:var(--accent);border-style:dashed}
.sm-site.jb[aria-pressed=true]{background:var(--accent);color:var(--panel);border-style:solid}
.sm-site.jb[aria-pressed=true] small,.sm-site.jb[aria-pressed=true] small.cnt{color:var(--panel);opacity:.85}
/* a form is read at a fixed measure, not stretched to 1400px */
.dsec.ai{border-color:var(--accent);max-width:780px}
.dsec.ai>.dsh{background:var(--accent-soft);color:var(--accent);border-bottom-color:var(--accent)}
.aip{border-bottom:1px solid var(--line)}
.air{display:flex;align-items:center;gap:14px;padding:6px 10px;
 font-size:12px;font-weight:600;border-bottom:1px solid var(--line)}
.air>span:first-child{flex:0 0 300px}
/* on a phone a 300px label left ~40px of select — stack instead, full-width tap target */
@media(max-width:640px){.air{flex-direction:column;align-items:stretch;gap:5px}
 .air>span:first-child{flex:none}}
.air:last-child{border-bottom:0}
.aib{border-top:1px solid var(--line)}
.aih{padding:5px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
 color:var(--dim);background:color-mix(in srgb,var(--sunk) 60%,transparent)}
.ait{width:100%;border-collapse:collapse;table-layout:fixed}
.ait th:first-child,.ait td:first-child{width:30%}
.ait th:nth-child(2),.ait td:nth-child(2){width:36%}
.ait th{text-align:left;padding:4px 10px;font-size:9px;text-transform:uppercase;letter-spacing:.07em;
 color:var(--faint);font-weight:700;border-bottom:1px solid var(--line)}
.ait td{padding:5px 10px;font-size:12px;border-bottom:1px solid var(--line);vertical-align:middle;
 overflow-wrap:anywhere}
.ait tbody tr:last-child td{border-bottom:0}
.ait td:first-child{font-size:10.5px;color:var(--dim);font-weight:650;text-transform:uppercase;
 letter-spacing:.04em}
.ait .ok{color:var(--live);font-weight:650} .ait .bad{color:var(--dead);font-weight:650}
.ait .q{color:var(--faint);font-weight:400}
/* real inputs — this form is typed on site, on a phone, so the targets are finger-sized and the
   font is >=16px to stop iOS zooming the page on focus */
.aii,.ais{width:100%;min-height:34px;background:var(--panel);color:var(--ink);
 border:1px solid var(--line);border-radius:6px;font:inherit;font-size:16px;padding:5px 8px}
.aii:focus,.ais:focus{outline:2px solid var(--accent);outline-offset:-1px;border-color:var(--accent)}
.aii:not(:placeholder-shown),.ais:valid{border-color:var(--accent)}
.ais{appearance:none;-webkit-appearance:none;
 background-image:linear-gradient(45deg,transparent 50%,var(--faint) 50%),
  linear-gradient(135deg,var(--faint) 50%,transparent 50%);
 background-position:calc(100% - 14px) 50%,calc(100% - 9px) 50%;
 background-size:5px 5px,5px 5px;background-repeat:no-repeat;padding-right:26px}
.cmtc:not([data-has="Yes"]) input.aii{display:none}
.aif{font-size:10.5px;color:var(--dim);background:var(--sunk);border-top:1px solid var(--line);
 padding:6px 10px;line-height:1.4}
.syn{font-size:11.5px;font-weight:650}
.syn.ok{color:var(--live)} .syn.warn{color:var(--gap)} .syn.bad{color:var(--dead)}
.syn.q{color:var(--faint)}
@media(min-width:900px){.aii,.ais{font-size:13px;min-height:30px}}
@media print{
 @page{margin:12mm}
 .sm-hd,.sm-sites,.sm-stale,.sm-tiles,.sm-hint,.sm-note,.sm-row,.sm-count,#smOrphSec{display:none!important}
 .sm,.sm-body{background:#fff}
 .sm-body{padding:0;max-width:none;gap:6px}
 .sm-tablewrap,.dsec{border-color:#999}
 tr.dt>td{background:#fff;padding:0 0 10px}
 tr.rw{break-inside:avoid}
 .dsec{break-inside:avoid;page-break-inside:avoid}
 .dsec.ai{break-before:page;page-break-before:always}
 .cmp table{min-width:0;font-size:9px}
 .sm-cards{display:none}
 /* a printed work order should show what was typed, on a line, not in a form box */
 .aii,.ais{border:0;border-bottom:1px solid #999;border-radius:0;font-size:11px;min-height:0;
  padding:1px 2px;background:transparent;appearance:none;background-image:none}
 .aif{display:none}}
.sm-card{cursor:pointer}
.sm-empty{padding:26px 14px;text-align:center;color:var(--dim);font-size:13.5px;background:var(--panel);
 border:1px dashed var(--line);border-radius:10px}
h2.sm-h{margin:8px 0 0;font-size:13px;font-weight:650;letter-spacing:.03em;text-transform:uppercase;color:var(--dim)}
h2.sm-h+p{margin:0;font-size:12.5px;color:var(--dim)}
.sm-tablewrap{display:none}
@media(min-width:820px){
 .sm-cards{display:none}
 .sm-tablewrap{display:block;overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}
 table{width:100%;border-collapse:collapse}
 th,td{padding:7px 10px;text-align:left;font-size:12.5px;border-bottom:1px solid var(--line);vertical-align:top}
 th{position:sticky;top:0;background:var(--sunk);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;
  color:var(--dim);font-weight:650;white-space:nowrap}
 td.m{font-family:var(--mono);font-size:11.5px;white-space:nowrap}
 td.dl{font-family:var(--mono);font-size:11px;line-height:1.5}
 td.wrap{white-space:normal;max-width:420px;font-size:11.5px}
 td .ok{color:var(--live);font-weight:650} td .warn{color:var(--gap);font-weight:650}
 td .q{color:var(--faint)}
 tbody tr:last-child td{border-bottom:0}
 tr.rw{cursor:pointer}
 tr.rw:hover td{background:var(--sunk)}
 tr.rw:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
 tr.dt>td{background:var(--sunk);padding:10px 14px}
 tr.dt .sm-more{border-top:0;padding-top:0;max-width:900px}
 tr.dt .sm-kv{grid-template-columns:190px 1fr}
}
@media(prefers-reduced-motion:no-preference){.sm-card,.sm-tile,.sm-site{transition:border-color .12s,background-color .12s}}
</style>
<div class="sm">
 <div class="sm-hd"><h1>Vacatia Site Monitor</h1><p id="smStamp"></p></div>
 <div class="sm-body">
  <div class="sm-sites" id="smSites" role="group" aria-label="Site"></div>
  <div class="seg" id="smSeg" role="group" aria-label="Punchlist status"></div>
  <div id="smNew"></div>
  <div class="sm-tiles" id="smTiles" role="group" aria-label="Filter by issue"></div>
  <p class="sm-hint">Tap any figure above to filter the list. Tap it again to clear.</p>
  <div class="sm-row">
   <input class="sm-f" type="search" id="smQ" placeholder="Room, lockout, Ethernet MAC or Wi-Fi MAC" aria-label="Search">
   <select class="sm-f" id="smG" aria-label="Group"></select>
  </div>
  <div class="sm-count"><span id="smCount"></span><span class="syn" id="smSync"></span>
   <button type="button" id="smCsv">Download this list</button>
   <button type="button" id="smAll">Open all in this list</button>
   <button type="button" id="smAiCsv">Download work orders</button>
   <button type="button" id="smPrint">Print work order</button>
   <button type="button" id="smReset">Clear filters</button></div>
  <div class="sm-cards" id="smCards"></div>
  <div class="sm-tablewrap"><table><thead id="smHead"></thead><tbody id="smTbody"></tbody></table></div>
  <div id="smOrphSec">
   <h2 class="sm-h">Devices under a room the roster doesn't have</h2>
   <p id="smOrphNote"></p>
   <div class="sm-cards" id="smOrph"></div>
   <div class="sm-tablewrap"><table><thead id="smOHead"></thead><tbody id="smOBody"></tbody></table></div>
  </div>
 </div>
</div>
<script>
(function(){
const D=__DATA__;
const PRES=['All boxes seen','Some boxes missing','No boxes seen','More boxes than expected'];
const PSEV=['live','dead','dead','gap'];
const LABL=['Labeled','Not relabeled','Partly labeled','Duplicate position','Wrong position','No boxes'];
const LSEV=['live','gap','gap','dead','dead','dead'];
const CAST=['Both in registry','Some not in registry','None in registry','No registry export'];
const CSEV=['live','dead','dead','mute'];
const DC=['real MAC in registry','stranded on randomized MAC','no real MAC (leftover present)','no registry entry','no registry export'];
const R={room:0,grp:1,exp:2,seen:3,pres:4,labl:5,cast:6,miss:7,punch:8,devs:9,pk:10,det:11,
 lock:12,pos:13,posOrder:14,unlab:15,never:16,notnow:17};
const T={lbl:0,eth:1,wifi:2,ts:3,mlbl:4,mmac:5,mip:6,note:7,corr:8,room:9};
const Dv={mac:0,label:1,pos:2,ts:3,days:4,cast:5,real:6,left:7,stale:8,cur:9};
const P={qr:0,devmac:1,relabel:2,fw:3,linear:4,flag:5,comment:6,occ:7,init:8,bed:9,liv:10,nv:11,done:12,swept:13,started:14,gname:15,bname:16,lname:17};
const WITHNAMES=__WITHNAMES__;
// swept = an explicit doneAt OR all four tech fields filled (MVM783 never writes doneAt)
const reswept=r=>!!((r[R.pk]||[])[P.swept]);
const started=r=>!!((r[R.pk]||[])[P.started]);
const NV={BOTH_CORRECT:['live','Names correct on both TVs'],
 VACANT_INCONCLUSIVE:['mute','Vacant / inconclusive'],
 NO_NAME_ON_EITHER_TV:['dead','Occupied but NO name on either TV'],
 NAME_ON_ONE_TV_ONLY:['dead','Name on one TV only'],
 TVS_DISAGREE:['dead','TVs show different names'],
 WRONG_NAME_BOTH_TVS:['dead','Wrong name on both TVs'],
 NAME_SHOWN_WHILE_VACANT:['gap','Name showing but room vacant'],
 NOT_ASSESSABLE:['mute','Nothing to check']};
const LTV=v=>v==='Bedroom/Livingroom'?'<span class="ok">Bedroom/Livingroom</span>'
 :v==='neither'?'<span class="bad">neither TV</span>':v?esc(v):'<span class="q">not recorded</span>';
const TVN={matches_guest:['good','matches the checked-in guest'],
 different_name:['bad','a different name'],no_name:['warn','no name shown'],
 name_shown_unreadable:['warn','name shown, not recorded']};
const QR={BOTH:['good','both TVs'],BEDROOM_ONLY:['warn','bedroom only'],
 LIVING_ONLY:['warn','living room only'],NEITHER:['bad','neither TV']};
let open=new Set();
// Staleness thresholds, in minutes. iCX drives labels/presence and a tech can invalidate it in one
// visit, so it goes loud fast; the registry is written by the appliance collector every few hours
// at best, so its bar is set where "the collector is behind" starts rather than at its cadence.
const ICX_WARN=20, ICX_BAD=45, REG_WARN=90, REG_BAD=240;
// a click that must NOT collapse the room it happened inside
const noToggle=e=>!!(e.target&&e.target.closest
 &&e.target.closest('input,select,textarea,button,label,option,.dsec.ai'));
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const mac=h=>h?h.match(/../g).join(':'):'';
let site=Object.keys(D.sites)[0], f=null, sk=null, sd=1;

// A punchlist answer is only trustworthy if the room was swept AFTER the last change that altered
// what the answer MEANS — and that moment is PER SITE, not global:
//   MVM784  2026-07-31  the @ separator was found in the HTVC. Rooms swept 7/29-7/30 were
//                       relabelled without the decorator, so their QR / on-screen-name answers
//                       describe a labelling that no longer exists.
//   MVM743  2026-08-04  the punch list was rebuilt to match 784's questions. "Live linear content
//   MVM783  2026-08-04  works" became "Live TV worked when entering" with per-TV box-reset
//                       follow-ups, so the earlier answers answer a DIFFERENT question. Commit
//                       times: 743 fb9e535 14:49:41Z, 783 d12fb41 14:25:44Z.
// Compared as instants, not calendar days: both apps changed mid-morning, so a date-only cutoff
// would silently readmit rooms swept earlier the same day against the old questions.
const PUNCH_WIN={MVM784:['2026-07-31T00:00:00-07:00','the @ separator was found in the HTVC'],
 MVM783:['2026-08-04T10:25:44-04:00','the punch list questions changed'],
 MVM743:['2026-08-04T10:49:41-04:00','the punch list questions changed']};
const PUNCH_DEF=PUNCH_WIN.MVM784;
const punchWin=code=>PUNCH_WIN[code||site]||PUNCH_DEF;
const ptStamp=iso=>{const t=Date.parse(iso||''); return isNaN(t)?'':new Date(t)
 .toLocaleString('en-US',{timeZone:'America/Los_Angeles',dateStyle:'medium',timeStyle:'short'})};
// no doneAt -> cannot be dated -> cannot be trusted (MVM783 never writes one)
const inWin=r=>{const t=Date.parse(((r[R.pk]||[])[P.done])||'');
 return !isNaN(t)&&t>=Date.parse(punchWin()[0])};

// each tile declares the predicate it filters by; rows match the deck Michele laid out
const TILES=s=>[[
 // everything here counts TODAY only (merge_rooms scopes the captures), so the whole deck
 // subtracts against "boxes expected" on the site button and nothing needs a mental correction.
 {k:'',      c:'',    n:s.boxesSeenOverall,  l:'boxes seen today'},
 // ROSTER-SCOPED, not s.boxesSeenCurrent. That counter is every row in the newest export, orphan
 // devices included, while never-seen / not-seen-now count only boxes belonging to roster rooms.
 // Mixing the two populations made the tiles un-subtractable: at MVM783 the displayed figure sat
 // 27 devices above the roster count and 4512-4350 came out at 162 against a real 189.
 {k:'',      c:'',    n:sum(s,r=>r[R.devs].filter(d=>d[Dv.cur]).length),
  l:'boxes seen this poll'},
 // the complement of the row-3 "boxes not in casting registry" tile: devices whose CURRENT real
 // MAC is present in the mDNS registry (DCAST index 0). Registry presence is necessary but not
 // sufficient for a guest to actually cast.
 {k:'',      c:'',    n:sum(s,r=>r[R.devs].filter(d=>d[Dv.cast]===0).length),
  l:'boxes in casting registry'},
 {k:'resw',  c:'',    n:cnt(s,reswept),      l:'reswept'},
 {k:'noresw',c:'gap', n:cnt(s,r=>!reswept(r)), l:'NOT reswept'},
],[
 // "missing" split in two, because they are different jobs: a box the roster expects that has
 // NEVER reported in any poll is an install/inventory problem; a box that HAS reported before but
 // was absent from the latest poll is dark right now. Both are room-derived, so neither can go
 // negative the way the old site-level (expected - seen) did (MVM743 read "-3").
 // BOTH measured against BOXES EXPECTED, which is how these get read in practice:
 //   expected - seen today      = never reported at all today
 //   expected - seen this poll  = not reporting right now (a superset: it includes the never-seen)
 // Deliberately plain site-level subtraction, not a per-room sum. Scoped to today the union can no
 // longer exceed expected, so the clamping that used to be needed only made the tiles disagree
 // with the arithmetic anyone does in their head. Rooms holding MORE boxes than the roster expects
 // are surfaced by the "extra boxes" tile instead of quietly inflating these.
 {k:'never', c:Math.max(0,s.boxesExpected-s.boxesSeenOverall)>0?'dead':'',
  n:Math.max(0,s.boxesExpected-s.boxesSeenOverall), l:'never seen today'},
 {k:'notnow',c:Math.max(0,s.boxesExpected-sum(s,r=>r[R.devs].filter(d=>d[Dv.cur]).length))>0?'dead':'',
  n:Math.max(0,s.boxesExpected-sum(s,r=>r[R.devs].filter(d=>d[Dv.cur]).length)),
  l:'not seen this poll'},
 {k:'pres2', c:'dead',n:cnt(s,r=>r[R.pres]===2), l:'no boxes at all'},
 {k:'pres3', c:'gap', n:cnt(s,r=>r[R.pres]===3), l:'extra boxes'},
 {k:'notrel',c:'gap', n:cnt(s,r=>r[R.labl]===1), l:'not relabeled'},
 {k:'badlbl',c:'dead',n:cnt(s,r=>[2,3,4].includes(r[R.labl])), l:'label wrong'},
],[
 {k:'nocast',c:'dead',n:s.devicesCannotCast, l:'boxes not in casting registry'},
 {k:'stale', c:'gap', n:s.staleBoxes,        l:'stale boxes (random MAC)'},
 {k:'qrno',  c:'dead',n:cnt(s,PRED.qrno),    l:'QR missing on a TV'},
 {k:'noname',c:'dead',n:cnt(s,PRED.noname),  l:'occupied, no name'},
 {k:'onename',c:'dead',n:cnt(s,PRED.onename),l:'name on 1 TV only'},
 {k:'vacname',c:'gap',n:cnt(s,PRED.vacname), l:'name while vacant'},
]];
function cnt(s,p){return s.rooms.filter(p).length}
function sum(s,f){return s.rooms.reduce((a,r)=>a+f(r),0)}
const PRED={pres0:r=>r[R.pres]===0,pres1:r=>r[R.pres]===1,pres2:r=>r[R.pres]===2,
 pres3:r=>r[R.pres]===3,notrel:r=>r[R.labl]===1,badlbl:r=>[2,3,4].includes(r[R.labl]),
 // the tiles count BOXES; the filters show the rooms those boxes belong to
 // the tiles count BOXES against expected; the filters show the ROOMS those boxes are short in
 never:r=>r[R.seen]<r[R.exp],
 notnow:r=>r[R.devs].filter(d=>d[Dv.cur]).length<r[R.exp],
 nocast:r=>r[R.devs].some(d=>d[Dv.cast]>=1&&d[Dv.cast]<=3),
 // the tile counts randomised REGISTRY ROWS; the filter shows the rooms carrying one —
 // either an mDNS-only randomised registration or a box stranded on a randomised MAC
 stale:r=>(r[R.det]||[]).some(t=>t[T.room]===r[R.room]&&/randomi[sz]ed/i.test(t[T.note]||''))
   ||r[R.devs].some(d=>d[Dv.cast]===1||(d[Dv.left]||[]).length>0),
 noname:r=>inWin(r)&&(r[R.pk]||[])[P.nv]==='NO_NAME_ON_EITHER_TV',
 onename:r=>inWin(r)&&(r[R.pk]||[])[P.nv]==='NAME_ON_ONE_TV_ONLY',
 vacname:r=>inWin(r)&&(r[R.pk]||[])[P.nv]==='NAME_SHOWN_WHILE_VACANT',
 qrno:r=>inWin(r)&&['NEITHER','BEDROOM_ONLY','LIVING_ONLY'].includes((r[R.pk]||[])[P.qr]),
 resw:reswept, noresw:r=>!reswept(r), started:r=>started(r)&&!reswept(r)};
// the target list for a field day: the four things a tech can actually fix on the box, ORed.
// name-on-1-TV keeps the post-@-separator window the tile uses, so this list never dispatches
// anyone on a pre-07/31 answer that describes a labelling that no longer exists.
// A room qualifies on the fault, and DROPS OFF once its work order is finished: every field
// answered on both TVs and no comment flagged on either. A comment means something still needs a
// human, so the room stays on the list no matter how complete the rest of the sheet is.
const AIREQ=['fw','label','devmac','wifi','qr','screen'];
function aiDone(r){
 const room=r[R.room], g=(...p)=>aiGet([site,room,...p].join('|'));
 if(!AICHECKS.every(([id])=>g(id))) return false;
 const order=['B','B1','B2','L'].filter(p=>(r[R.posOrder]||[]).includes(p));
 if(!order.length) return false;
 return order.every(p=>AIREQ.every(id=>g(p,'orig',id)&&g(p,'rev',id))
   && g(p,'hascomment')==='No');
}
const jarranFault=r=>PRED.pres2(r)||PRED.notrel(r)||PRED.badlbl(r)||PRED.onename(r);
PRED.jarran=r=>jarranFault(r)&&!aiDone(r);
PRED.jarrandone=r=>jarranFault(r)&&aiDone(r);
const JWHY=r=>[PRED.pres2(r)?'no boxes at all':'',PRED.notrel(r)?'not relabeled':'',
 PRED.badlbl(r)?'label wrong':'',PRED.onename(r)?'name on 1 TV':''].filter(Boolean);
const sev=r=>([1,2].includes(r[R.pres])||[3,4].includes(r[R.labl])||[1,2].includes(r[R.cast]))?'dead'
 :(r[R.pres]===3||[1,2].includes(r[R.labl])||r[R.devs].some(d=>d[Dv.stale]))?'gap'
 :(r[R.cast]===3?'mute':'live');

function rows(){const s=D.sites[site];let out=s.rooms.filter(r=>{
 if(f&&PRED[f]&&!PRED[f](r))return false;
 const g=$('smG').value; if(g!==''&&String(r[R.grp])!==g)return false;
 const q=$('smQ').value.trim().toLowerCase().replace(/[:\-\s]/g,'');
 if(q){
  // searchable: room, lockout, every iCX label, and EVERY mac on any surface —
  // Ethernet (iCX), Wi-Fi (RMMS/derived +1), the mDNS registry mac and its leftovers.
  const parts=[r[R.room], r[R.lock]||''];
  r[R.devs].forEach(d=>{ parts.push(d[Dv.label], d[Dv.mac], d[Dv.real]||'');
    const n=parseInt(d[Dv.mac],16); if(!isNaN(n)) parts.push((n+1).toString(16).padStart(12,'0'));
    (d[Dv.left]||[]).forEach(x=>parts.push(x)); });
  (r[R.det]||[]).forEach(t=>parts.push(t[T.lbl],t[T.eth],t[T.wifi],t[T.mlbl],t[T.mmac],t[T.mip]));
  (r[R.unlab]||[]).forEach(u=>{ parts.push(u[0],u[1]);
    const n=parseInt(u[1],16); if(!isNaN(n)) parts.push((n+1).toString(16).padStart(12,'0')); });
  const hay=parts.join(' ').toLowerCase().replace(/[:\-]/g,'');
  if(!hay.includes(q))return false}
 return true});
 if(sk!=null)out=out.slice().sort((a,b)=>{const x=a[sk],y=b[sk];return (x>y?1:x<y?-1:0)*sd});
 return out}

function drill(s,r){
 const pk=r[R.pk]||[];
 const POSN={B:'Bedroom',L:'Livingroom',B1:'Bedroom 1',B2:'Bedroom 2'};
 // positions this unit expects, in bedroom-then-living order
 const order=['B','B1','B2','L'].filter(p=>(r[R.posOrder]||[]).includes(p));
 const unl=r[R.unlab]||[];
 // Last column = EVERY iCX box this room reports, not just the unlabelled ones. Built from the
 // lockout detail rows so a box in a position the roster never expected (a duplicate @B, a
 // stray @B2) still shows up — those are invisible to r[R.pos] and r[R.unlab] alike.
 let all=(r[R.det]||[]).filter(t=>t[T.lbl]&&t[T.room]===r[R.room]).map(t=>[t[T.lbl],t[T.eth]]);
 if(!all.length) all=unl.map(u=>[u[0],u[1]]);
 const yn=v=>v==='yes'?`<span class="ok">yes</span>`:v==='no'?`<span class="bad">no</span>`
   :`<span class="q">not recorded</span>`;
 const qr=pk[P.qr], qrTxt=qr==='NEITHER'?'<span class="bad">neither TV</span>'
   :qr==='BOTH'?'<span class="ok">both TVs</span>'
   :qr==='BEDROOM_ONLY'?'<span class="bad">Bedroom only</span>'
   :qr==='LIVING_ONLY'?'<span class="bad">Livingroom only</span>':'<span class="q">not recorded</span>';
 const nvBad=/NO NAME|ONE TV|DISAGREE|WRONG|WHILE VACANT/i.test(pk[P.nv]||'');
 const corr=(r[R.det]||[]).map(t=>t[T.corr]).filter(Boolean)[0]||'';
 // ---- section 1: one equal-width panel per TV position, + All ICX Data ----
 // A missing position is called out by the panel itself going red, which is why the old
 // missing-position summary row is gone. (Keep double-quoted Capitalised pairs out of these
 // comments — the PII gate in refresh.sh reads them as a name and blocks the publish.)
 // a box present in an earlier poll but absent from the latest one is dark RIGHT NOW — say so on
 // the box itself, otherwise the "not seen this poll" filter hands you a room and no suspect
 const dark=new Set(r[R.devs].filter(d=>!d[Dv.cur]).map(d=>d[Dv.mac]));
 const tag=m=>dark.has(m)?' <span class="dk">dark now</span>':'';
 const tv=order.map(p=>{const d=(r[R.pos]||{})[p];
   return `<div class="${d?(dark.has(d[1])?'dkc':''):'miss'}"><div class="ch">${POSN[p]||p}</div>`
     +(d?`<div class="cv">${esc(d[0])}${tag(d[1])}</div><div class="cm">${esc(mac(d[1]))}</div>`
        :`<div class="cv">MISSING</div><div class="cm">&mdash;</div>`)+`</div>`}).join('')
   +`<div class="allc"><div class="ch">All ICX Data</div>`
   +(all.length?all.map(u=>`<div class="cv">${esc(u[0])}${tag(u[1])}</div>
       <div class="cm">${esc(mac(u[1]))}</div>`).join('')
     :`<div class="cv q">&mdash;</div>`)+`</div>`;
 // ---- section 2: the field sweep, four answers straight across ----
 const sweep=[['Casting QR seen on',qrTxt],['Use device MAC',yn(pk[P.devmac])],
   ['Firmware',yn(pk[P.fw])],['Live linear',LTV(pk[P.linear])]]
   .map(([k,v])=>`<div><div class="ch">${k}</div><div class="cv">${v}</div></div>`).join('');
 const stamp=pk[P.done]
   ? esc(new Date(pk[P.done]).toLocaleString('en-US',{timeZone:'America/Los_Angeles'})+' PT')
     +(inWin(r)?'':` <span class="bad">&mdash; swept before ${esc(punchWin()[1])}
        (${esc(ptStamp(punchWin()[0]))} PT); these four answers are NOT in the figures at the
        top</span>`)
   : reswept(r)?'<span class="warn">completed, no timestamp</span>'
   :'<span class="bad">not completed</span>';
 // ---- section 3: the on-screen name check ----
 const names=[['Guest',esc((WITHNAMES&&pk[P.gname])||pk[P.init]
     ||(pk[P.occ]||'').replace(/^./,c=>c.toUpperCase()))||'<span class="q">&mdash;</span>'],
   ['Bedroom shows',esc((WITHNAMES&&pk[P.bname])||pk[P.bed]||'')||'<span class="q">&mdash;</span>'],
   ['Livingroom shows',esc((WITHNAMES&&pk[P.lname])||pk[P.liv]||'')||'<span class="q">&mdash;</span>'],
   ['Verdict',`<span class="${nvBad?'bad':'warn'}">${esc(NV[pk[P.nv]]?NV[pk[P.nv]][1]:(pk[P.nv]||''))
     ||'<span class="q">&mdash;</span>'}</span>`]]
   .map(([k,v])=>`<div><div class="ch">${k}</div><div class="cv">${v}</div></div>`).join('');
 return `<div class="dg">
  <div class="dsec"><div class="dsh"><span>TVs in this room</span>${
     corr?`<b class="warn">${esc(corr)}</b>`:''}</div>
   <div class="grid tvg" style="--n:${order.length+1}">${tv}</div></div>
  <div class="dsec"><div class="dsh"><span>Field sweep</span><b>${stamp}</b></div>
   <div class="grid" style="--n:4">${sweep}</div>${
   pk[P.comment]?`<div class="dcmt">&ldquo;${esc(pk[P.comment])}&rdquo;</div>`:''}</div>
  <div class="dsec"><div class="dsh"><span>On-screen name check</span></div>
   <div class="grid" style="--n:4">${names}</div></div>
  <div class="dsec"><div class="dsh"><span>iCX &middot; RMMS &middot; mDNS</span>
    <b>lockout ${esc(r[R.lock]||r[R.room])} &middot; all sides</b></div>
   ${cmpTable(s,r)}</div>
  ${changes(s,r)}
  ${(f==='jarran'||f==='jarrandone')&&jarranFault(r)?actionItems(r,order,POSN):''}
 </div>`}

// ---- the field work order, only on rooms that qualify for Jarran's punchlist ----------------
// Every field is BLANK and typed on site. Nothing is pre-filled from the old sweep on purpose:
// Original is what the tech finds when they first walk into the room, Revised is what it does
// after the logs are pulled / app data cleared / box power-cycled. Seeding Original from the
// 7/29-8/02 punch answers would be putting words in the tech's mouth about a different visit.
//
// Entries sync live, the same way the punch lists do: the same Supabase project and the same
// publishable key, upserting into `punch_rooms` under its own `property` namespace (MVM784 ->
// "784ai"). Nothing else touches that namespace — every existing reader (the punch apps,
// punch-alerts, punch-backup's per-table dump, this build's own punch pull) filters on
// property=eq.784/783/743 — so the work orders ride along without a schema change and get picked
// up by the nightly backup for free. A dedicated table would be tidier; it needs DDL, which the
// publishable key cannot do.
//
// Conflict model is per-FIELD last-write-wins on a client timestamp, not per-row: two techs in
// the same lockout editing different TVs must not overwrite each other, which a whole-row upsert
// would do. localStorage is the offline buffer — edits are kept and replayed when signal returns.
const SUPA='https://zhqyjmugyoqvztqqegis.supabase.co';
const SUPA_KEY='sb_publishable_Ehu06hwE9_VJyT_ivtteyg_bmos1qeN';
const AI_NS=c=>String(c).replace(/^MVM0*/,'')+'ai';
const AI_LS='mvmActionItems';
let AI={},REM={},DIRTY=new Set(),PULLED=new Set();
try{AI=JSON.parse(localStorage.getItem(AI_LS)||'{}')||{}}catch(e){AI={}}
// migrate the pre-sync format (bare strings, no timestamp) so nothing typed before today is lost
Object.keys(AI).forEach(k=>{if(typeof AI[k]==='string')AI[k]={v:AI[k],t:1}});
try{DIRTY=new Set(JSON.parse(localStorage.getItem(AI_LS+':dirty')||'[]'))}catch(e){}
const aiSave=()=>{try{localStorage.setItem(AI_LS,JSON.stringify(AI));
 localStorage.setItem(AI_LS+':dirty',JSON.stringify([...DIRTY]))}catch(e){}};
const aiGet=k=>(AI[k]&&AI[k].v)||'';
// a cleared field is stored as an empty value WITH a timestamp, never deleted — otherwise the
// clear would never beat the old value on another device
const aiSet=(k,v)=>{AI[k]={v:v||'',t:Date.now()};
 const p=k.split('|');DIRTY.add(p[0]+'|'+p[1]);aiSave();queuePush()};

let pushTimer=null,SYNC='idle';
const setSync=s=>{SYNC=s;const el=$('smSync');if(!el)return;
 const m={live:['ok','● Live — synced'],saving:['warn','Saving…'],
  offline:['bad','● Offline — '+DIRTY.size+' room(s) queued'],idle:['q','']};
 const [c,t]=m[s]||m.idle;el.className='syn '+c;el.textContent=t};
const queuePush=()=>{clearTimeout(pushTimer);setSync('saving');pushTimer=setTimeout(pushNow,800)};

function pushNow(){
 if(!DIRTY.size){setSync('live');return}
 const bySite={};
 [...DIRTY].forEach(rk=>{const i=rk.indexOf('|');
  (bySite[rk.slice(0,i)]=bySite[rk.slice(0,i)]||[]).push(rk.slice(i+1))});
 Object.keys(bySite).forEach(st=>{
  const rows=bySite[st].map(room=>{
   // start from what the server last told us, then let newer local fields win — this is what
   // keeps a concurrent editor's other fields intact through our write
   const f=Object.assign({},REM[st+'|'+room]||{});
   Object.keys(AI).forEach(k=>{const p=k.split('|');
    if(p[0]===st&&p[1]===room){const fk=p.slice(2).join('|');
     if(!f[fk]||(AI[k].t||0)>=(f[fk].t||0))f[fk]={v:AI[k].v,t:AI[k].t||0}}});
   return {property:AI_NS(st),room_id:room,data:{f:f}}});
  fetch(SUPA+'/rest/v1/punch_rooms',{method:'POST',
   headers:{apikey:SUPA_KEY,Authorization:'Bearer '+SUPA_KEY,
    'Content-Type':'application/json','Prefer':'resolution=merge-duplicates'},
   body:JSON.stringify(rows)}).then(res=>{
    if(!res.ok)throw new Error('HTTP '+res.status);
    rows.forEach(r=>{REM[st+'|'+r.room_id]=r.data.f;DIRTY.delete(st+'|'+r.room_id)});
    aiSave();setSync(DIRTY.size?'offline':'live')
   }).catch(()=>setSync('offline'))})}

function pullNow(st){
 fetch(SUPA+'/rest/v1/punch_rooms?property=eq.'+AI_NS(st)+'&select=room_id,data',
  {headers:{apikey:SUPA_KEY,Authorization:'Bearer '+SUPA_KEY}})
 .then(res=>{if(!res.ok)throw new Error('HTTP '+res.status);return res.json()})
 .then(rows=>{const changed=[];
  rows.forEach(r=>{const f=(r.data&&r.data.f)||{};REM[st+'|'+r.room_id]=f;
   Object.keys(f).forEach(fk=>{const k=st+'|'+r.room_id+'|'+fk;
    if((f[fk].t||0)>((AI[k]&&AI[k].t)||0)){AI[k]={v:f[fk].v,t:f[fk].t||0};changed.push(k)}})});
  PULLED.add(st);
  if(changed.length){aiSave();
   // patch the inputs in place; a re-render would yank focus out from under whoever is typing
   changed.forEach(k=>document.querySelectorAll('[data-ai]').forEach(el=>{
    if(el.dataset.ai===k&&el!==document.activeElement)el.value=aiGet(k)}))}
  setSync(DIRTY.size?'offline':'live')})
 .catch(()=>setSync('offline'))}

setInterval(()=>{if(document.querySelector('.dsec.ai'))pullNow(site)},12000);
setInterval(()=>{if(DIRTY.size)pushNow()},15000);
window.addEventListener('online',()=>{pushNow();pullNow(site)});
const AIROWS=[['fw','Firmware','text'],['label','Box Label','text'],
 ['devmac','Use Device MAC',['Yes','No']],['wifi','WiFi Enabled',['Yes','No']],
 ['qr','Casting QR Code',['Yes','No']],
 ['screen','Name On Screen',['Welcome','Guest','Name']],['name','Name','text']];
const AICHECKS=[['logs','Downloaded logs',['Before changes','After changes','Before & after','No']],
 ['cleared','App data cleared before final inputs',['Yes','No']],
 ['cycled','Box power cycled before final inputs',['Yes','No']]];

function actionItems(r,order,POSN){
 const room=r[R.room];
 const key=(...p)=>[site,room,...p].join('|');
 const ti=k=>`<input class="aii" type="text" data-ai="${esc(k)}" value="${esc(aiGet(k))}"
   autocomplete="off" spellcheck="false">`;
 const sel=(k,opts)=>`<select class="ais" data-ai="${esc(k)}">`
   +['',...opts].map(o=>`<option value="${esc(o)}"${aiGet(k)===o?' selected':''}>${o?esc(o):'&mdash;'}</option>`).join('')
   +`</select>`;
 const fld=(k,kind)=>kind==='text'?ti(k):sel(k,kind);
 const block=p=>`<div class="aib"><div class="aih">${POSN[p]||p}</div>
   <table class="ait"><thead><tr><th></th><th>Original</th><th>Revised</th></tr></thead><tbody>
   ${AIROWS.map(([id,lbl,kind])=>`<tr><td>${lbl}</td>
     <td>${fld(key(p,'orig',id),kind)}</td>
     <td>${fld(key(p,'rev',id),kind)}</td></tr>`).join('')}
   <tr><td>Comments</td><td>${sel(key(p,'hascomment'),['Yes','No'])}</td>
       <td class="cmtc" data-has="${esc(aiGet(key(p,'hascomment')))}">${ti(key(p,'comments'))}</td></tr>
   </tbody></table></div>`;
 // The list is not re-rendered while you type — a room vanishing mid-entry would be worse than a
 // stale list — so a finished sheet says so here and drops off the next time the list redraws.
 return `<div class="dsec ai"><div class="dsh"><span>Action items &mdash; ${esc(room)}</span>
   ${aiDone(r)?'<b class="done">complete &mdash; drops off the list on refresh</b>'
     :`<b class="warn">${JWHY(r).join(' &middot; ')}</b>`}</div>
  <div class="aip">${AICHECKS.map(([id,lbl,opts])=>
    `<div class="air"><span>${lbl}</span>${sel(key(id),opts)}</div>`).join('')}</div>
  ${order.map(block).join('')}
  <div class="aif">Syncs live to Supabase as you type, the same as the punch list &mdash; edits show
   up for everyone on this list. Offline entries are kept and sent when signal returns.</div></div>`}

// ---- what changed on this room's boxes, and when -------------------------------------------
// Reconstructed by replaying banked snapshots (build_history.py). The two surfaces keep their own
// clocks: an iCX stamp is Eastern wall time and a registry stamp is UTC, so each row is labelled
// with the clock it came from rather than being silently normalised into one.
const HE={t:0,k:1,from:2,to:3,how:4};
function changes(s,r){
 const macs=new Set(r[R.devs].map(d=>d[Dv.mac]));
 (r[R.det]||[]).forEach(t=>{if(t[T.room]===r[R.room]){
   if(t[T.eth])macs.add(t[T.eth]); if(t[T.mmac])macs.add(t[T.mmac])}});
 const rows=[];
 macs.forEach(m=>((s.hist||{})[m]||[]).forEach(e=>rows.push([m,e])));
 if(!rows.length) return `<div class="dsec"><div class="dsh"><span>Changes</span>
   <b>${(s.caps||[0,0])[0]} iCX captures held</b></div>
   <div class="chq">No label or registry change on this room&rsquo;s boxes across every capture
   we hold.</div></div>`;
 rows.sort((a,b)=>a[1][HE.t]<b[1][HE.t]?1:a[1][HE.t]>b[1][HE.t]?-1:0);   // newest first
 const arrow=(f,t)=>`<span class="was">${esc(f)||'&mdash;'}</span> &rarr; <b>${esc(t)||'&mdash;'}</b>`;
 const what=e=>{const k=e[HE.k];
   if(k==='icx_label') return (e[HE.how]==='relabel'
     ? `<span class="tag ok">relabeled</span> iCX label ${arrow(e[HE.from],e[HE.to])}`
     : e[HE.how]==='lost_position'
     ? `<span class="tag bad">lost @position</span> iCX label ${arrow(e[HE.from],e[HE.to])}`
     : `iCX label ${arrow(e[HE.from],e[HE.to])}`);
   if(k==='icx_first') return `first seen in iCX as <b>${esc(e[HE.to])}</b>`;
   if(k==='reg_label') return `mDNS registry label ${arrow(e[HE.from],e[HE.to])}`;
   if(k==='reg_ip')    return `mDNS IP ${arrow(e[HE.from],e[HE.to])}`;
   if(k==='reg_first') return `registered in mDNS as <b>${esc(e[HE.to])}</b>`;
   if(k==='reg_gone')  return `<span class="tag bad">registration gone</span> was
     <b>${esc(e[HE.from])}</b>`;
   return esc(k)};
 // a stamp ending in Z is the registry's UTC; everything else is the iCX dashboard's Eastern
 const when=t=>/Z$/.test(t)?`${esc(t)}`:`${esc(t.slice(0,16))} ET`;
 return `<div class="dsec"><div class="dsh"><span>Changes</span>
   <b>${rows.length} across ${(s.caps||[0,0])[0]} iCX / ${(s.caps||[0,0])[1]} registry captures</b></div>
  <div class="chl">${rows.map(([m,e])=>`<div class="chr">
    <div class="cht">${when(e[HE.t])}</div>
    <div class="chm">${esc(mac(m))}</div>
    <div class="chw">${what(e)}</div></div>`).join('')}</div></div>`}

function cmpTable(s,r){
 const D2=r[R.det]||[];
 const noteCls=n=>/not in mdns|stale|!=|no icx/i.test(n)?'bad':n?'warn':'';
 if(!D2.length) return `<div class="sm-none">No iCX or mDNS rows for this lockout.</div>`;
 // Grouped by the SIDE each row belongs to — the open room first, then the other sides of the
 // lockout, then anything whose label points at no side at all (a bare lockout number, a typo).
 // mDNS-only rows sit in the group their own registry label names, which is how a stale
 // randomised MAC ends up under the room it is squatting on.
 const cur=r[R.room], g=new Map();
 D2.forEach(t=>{const k=t[T.room]||''; if(!g.has(k))g.set(k,[]); g.get(k).push(t)});
 const ks=[...g.keys()].sort((a,b)=>a===cur?-1:b===cur?1:a>b?1:a<b?-1:0);
 // MDNS IP is dropped from the table but stays in the search index — the only thing it was
 // carrying is WHICH subnet a registration came from, and the note column already names the
 // fault. Ask for it back as a subnet tag if the VLAN question resurfaces.
 const row=t=>`<tr class="${t[T.lbl]?'':'mo'}">
     <td class="m">${esc(t[T.lbl])||'<span class="q">— mDNS only</span>'}</td>
     <td class="m">${esc(mac(t[T.eth]))||'—'}</td>
     <td class="m">${esc(mac(t[T.wifi]).toUpperCase())||'—'}</td>
     <td class="m">${esc(t[T.mlbl])||'—'}</td>
     <td class="m">${esc(mac(t[T.mmac]))||'—'}</td>
     <td class="${noteCls(t[T.note])}">${esc(t[T.note])||''}</td>
     <td class="warn">${esc(t[T.corr])||''}</td>
     <td class="m">${t[T.ts]>=0?esc(s.stamps[t[T.ts]]||''):'—'}</td></tr>`;
 return `<div class="cmp"><table>
   <thead><tr><th>ICX Label</th><th>Ethernet MAC<br><span>iCX DeviceID</span></th>
    <th>WIFI MAC<br><span>RMMS2.0 / derived +1</span></th>
    <th>MDNS Label</th><th>MDNS MAC</th><th>MDNS ?</th>
    <th>Labeling Correction</th><th>Last Seen<br><span>iCX</span></th></tr></thead><tbody>
   ${ks.map(k=>`<tr class="gh"><td colspan="8">${esc(k)||'no room in the label'}${
       k===cur?'<em>this room</em>':''}</td></tr>`+g.get(k).map(row).join('')).join('')}
   </tbody></table></div>`}

function devLine(s,d){
 const bits=[`<b>${esc(mac(d[Dv.mac]))}</b>`,
  `label ${esc(d[Dv.label])}`,
  d[Dv.pos]?`pos ${esc(d[Dv.pos])}`:`<span class="x">no position</span>`,
  `seen ${esc(s.stamps[d[Dv.ts]]||'—')}${d[Dv.stale]?` <span class="x">(${d[Dv.days]}d)</span>`:''}`,
  d[Dv.cast]===0?'castable':`<span class="x">${DC[d[Dv.cast]]}</span>`];
 if(d[Dv.real])bits.push(`registry ${esc(mac(d[Dv.real]))}`);
 return `<div class="sm-dev">${bits.join(' · ')}</div>`}

function render(){
 const s=D.sites[site];
 // Per-SITE, never the global max. D.now is the newest iCX stamp across ALL THREE sites, so a site
 // whose export is lagging silently borrowed another site's freshness — MVM743 read as current off
 // 783's poll while its own labels were 53 min stale and a tech was actively relabeling rooms.
 const srcOf=n=>(s.sources||[]).find(x=>x.name===n)||{};
 const icxSrc=srcOf('iCX Online STBs'), regSrc=srcOf('mDNS casting registry');
 const mins=v=>{const t=Date.parse(String(v||''));return t?(Date.now()-t)/60000:null};
 const icxAge=mins(icxSrc.captured_utc), regAge=mins(regSrc.captured_utc);
 const ago=m=>m==null?'unknown age':m<1?'just now':m<90?`${Math.round(m)} min old`
   :`${(m/60).toFixed(1)} h old`;
 const cls=(m,w,b)=>m==null?'bad':m>b?'bad':m>w?'warn':'ok';
 // One freshness line, in the header. This replaced both a red warning bar and the three-column
 // per-source panel that used to sit above the tiles — all three said the same thing three times.
 // The punch age is folded in here so nothing was lost when those went.
 const punchSrc=srcOf('Punch list (field sweep)'), punchAge=mins(punchSrc.captured_utc);
 $('smStamp').innerHTML=`${site} &middot; iCX labels as of <b>${esc((icxSrc.captured_at||'—')
   .slice(11,16))} ET</b> <span class="${cls(icxAge,ICX_WARN,ICX_BAD)}">(${ago(icxAge)})</span>
   &middot; casting registry <span class="${cls(regAge,REG_WARN,REG_BAD)}">${ago(regAge)}</span>
   <span class="q">(${esc(regSrc.captured_at||'—')}, ${(s.regRows||0)} rows)</span>
   &middot; punch list <span class="${cls(punchAge,720,2880)}">${ago(punchAge)}</span>`;
 $('smSites').innerHTML=Object.entries(D.sites).map(([k,v])=>
  `<button class="sm-site" type="button" aria-pressed="${k===site}" data-s="${k}">${k}<small>${esc(v.name)}</small>
   <small class="cnt">${v.units} units &middot; ${v.boxesExpected} boxes expected</small></button>`).join('')
  +`<button class="sm-site jb" type="button" aria-pressed="${f==='jarran'||f==='jarrandone'}" data-f="jarran">
     Jarran&rsquo;s punchlist<small>${cnt(s,PRED.jarran)} rooms to work at ${site}${
       cnt(s,PRED.jarrandone)?` &middot; ${cnt(s,PRED.jarrandone)} done`:''}</small>
     <small class="cnt">no boxes &middot; not relabeled &middot; label wrong &middot; name on 1 TV</small></button>`;
 $('smSites').querySelectorAll('.sm-site[data-s]').forEach(b=>b.onclick=()=>{site=b.dataset.s;f=null;sk=null;$('smG').value='';$('smQ').value='';render()});
 $('smSites').querySelectorAll('.sm-site[data-f]').forEach(b=>b.onclick=()=>{
  f=(f===b.dataset.f)?null:b.dataset.f;$('smG').value='';$('smQ').value='';render()});
 const last=s.snaps.length?s.snaps[s.snaps.length-1]:null;
 // per-SOURCE age, not per-site. A stale registry beside a fresh iCX poll is exactly the
 // failure that hid MVM784's dying mDNS collector for five days.
 // Ages are computed from the parallel UTC instants the build emits, NEVER from the display
 // strings: iCX prints Eastern wall clock, the registry and punch stamps are UTC, and comparing
 // those directly is what made this panel read "-225 min old" while under-reporting a 6 h stale
 // mDNS registry as 2.1 h. The strings below still show each source's own clock, untouched.
 const nowMs=Date.parse(D.nowUtc||'')||Date.parse((D.now||'').replace(' ','T')+'Z')||Date.now();
 const ageOf=v=>{const t=Date.parse(String(v||''));
   if(!t) return null; return (nowMs-t)/3600000};
 $('smTiles').innerHTML=TILES(s).map(row=>`<div class="sm-trow">`+row.map(t=>
  `<button class="sm-tile ${t.c}" type="button" data-f="${t.k}"${t.k?` aria-pressed="${f===t.k}"`:''}>
   <b>${t.n}</b><span>${t.l}</span></button>`).join('')+`</div>`).join('');
 $('smTiles').querySelectorAll('.sm-tile').forEach(b=>{if(!b.dataset.f)return;
  b.onclick=()=>{f=(f===b.dataset.f)?null:b.dataset.f;render()}});
 const gs=$('smG'),cur=gs.value;
 gs.innerHTML=`<option value="">All ${esc(s.groupLabel).toLowerCase()}s</option>`
  +s.groups.map((g,i)=>`<option value="${i}">${esc(g)}</option>`).join('');
 gs.value=cur;
 const Rs=rows();
 $('smCount').textContent=`${Rs.length} of ${s.units} units`+(f?` · filtered`:'');
 // pull the shared work orders the first time this site's list is opened, then the 12s timer
 // keeps it current for as long as a work order is on screen
 const inJ=f==='jarran'||f==='jarrandone';
 if(inJ){if(!PULLED.has(site))pullNow(site); else setSync(DIRTY.size?'offline':'live')}
 else if($('smSync')){$('smSync').textContent='';$('smSync').className='syn q'}
 // completed / not-completed is a FILTER on the punchlist, not a hidden toggle
 $('smSeg').innerHTML=inJ?[['jarran','Still to work',cnt(s,PRED.jarran)],
   ['jarrandone','Completed',cnt(s,PRED.jarrandone)]].map(([k,l,n])=>
   `<button type="button" data-f="${k}" aria-pressed="${f===k}">${l}
     <b>${n}</b></button>`).join(''):'';
 $('smSeg').querySelectorAll('button').forEach(b=>b.onclick=()=>{f=b.dataset.f;render()});
 $('smCards').innerHTML=Rs.length?Rs.slice(0,400).map(r=>{const isOpen=open.has(r[R.room]);
  const pk=r[R.pk]||[], qr=QR[pk[P.qr]];
  return `<div class="sm-card s-${sev(r)}${isOpen?' open':''}" role="button" tabindex="0"
    aria-expanded="${isOpen}" data-room="${esc(r[R.room])}">
   <div class="sm-top"><span class="sm-rm">${esc(r[R.room])}</span></div>
   <div class="sm-pills"><span class="pill ${PSEV[r[R.pres]]}">${PRES[r[R.pres]]}</span>
    <span class="pill ${LSEV[r[R.labl]]}">${LABL[r[R.labl]]}</span>
    <span class="pill ${CSEV[r[R.cast]]}">${CAST[r[R.cast]]}</span>
    ${reswept(r)?'<span class="pill live">reswept</span>':'<span class="pill gap">not reswept</span>'}</div>
   ${isOpen?drill(s,r):`<div class="sm-tap">Tap for every MAC in this room and the tech’s answers</div>`}
  </div>`}).join('')+(Rs.length>400?`<div class="sm-empty">Showing the first 400 — narrow the filter or download.</div>`:'')
  :`<div class="sm-empty">Nothing matches.</div>`;
 const toggle=el=>{const rm=el.dataset.room; open.has(rm)?open.delete(rm):open.add(rm); render()};
 // On a phone the whole card is the tap target, so a tap on a work-order dropdown bubbled up here,
 // collapsed the room and threw you back to the list before the options could open. Anything
 // interactive — and the work order as a whole, which you should not be able to shut by accident
 // mid-entry — is excluded; collapse from the room header instead. Space in a text field hit the
 // keydown path the same way, so that only fires when the card itself has focus.
 $('smCards').querySelectorAll('.sm-card[data-room]').forEach(el=>{
  el.onclick=e=>{if(noToggle(e))return;toggle(el)};
  el.onkeydown=e=>{if(e.target!==el)return;
   if(e.key==='Enter'||e.key===' '){e.preventDefault();toggle(el)}}});
 const C=[['Room',R.room],['Presence',R.pres],['Labeling',R.labl],['Casting',R.cast],['Reswept',null]];
 $('smHead').innerHTML='<tr>'+C.map(([l,k])=>
  `<th${k!=null?` data-k="${k}"`:''}>${l}${sk===k&&k!=null?(sd>0?' ▲':' ▼'):''}</th>`).join('')+'</tr>';
 $('smHead').querySelectorAll('th[data-k]').forEach(th=>th.onclick=()=>{
  const k=+th.dataset.k; if(sk===k)sd*=-1; else{sk=k;sd=1} render()});
 $('smTbody').innerHTML=Rs.slice(0,600).map(r=>{
  const isOpen=open.has(r[R.room]);
  const main=`<tr class="rw" role="button" tabindex="0" aria-expanded="${isOpen}" data-room="${esc(r[R.room])}">`+[
   ['m',`${isOpen?'▾':'▸'} ${esc(r[R.room])}`],
   ['',`<span class="pill ${PSEV[r[R.pres]]}">${PRES[r[R.pres]]}</span>`],
   ['',`<span class="pill ${LSEV[r[R.labl]]}">${LABL[r[R.labl]]}</span>`],
   ['',`<span class="pill ${CSEV[r[R.cast]]}">${CAST[r[R.cast]]}</span>`],
   ['',reswept(r)?'<span class="pill live">yes</span>'
      :started(r)?'<span class="pill gap">part</span>':'<span class="pill gap">no</span>'],
  ].map(([c,v])=>`<td class="${c}">${v}</td>`).join('')+'</tr>';
  const det=isOpen?`<tr class="dt"><td colspan="${C.length}">${drill(s,r)}</td></tr>`:'';
  return main+det}).join('');
 const tog=el=>{const rm=el.dataset.room; open.has(rm)?open.delete(rm):open.add(rm); render()};
 $('smTbody').querySelectorAll('tr.rw').forEach(el=>{
  el.onclick=e=>{if(noToggle(e))return;tog(el)};
  el.onkeydown=e=>{if(e.target!==el)return;
   if(e.key==='Enter'||e.key===' '){e.preventDefault();tog(el)}}});
 const O=s.orphans;
 $('smOrphNote').textContent=O.length
  ? `${O.length} device${O.length>1?'s':''} report a room number that is not a unit in the room list — lockout parents, common-area TVs, or a mistyped room. Their MACs are recorded here so they can be traced back.`
  : 'None — every device iCX reports maps to a unit in the room list.';
 $('smOrph').innerHTML=O.map(o=>`<div class="sm-card s-dead">
   <div class="sm-top"><span class="sm-rm">${esc(o[1])}</span><span class="sm-grp">reads room ${esc(o[2])}</span></div>
   <div class="sm-dev"><b>${esc(mac(o[0]))}</b> · seen ${esc(s.stamps[o[3]]||'—')} (${o[4]}d)</div>
   <div class="sm-pills">${o[5]?`<span class="pill live">should be ${esc(o[5])}</span>`:''}
    <span class="pill ${/^HIGH/.test(o[6])?'live':/MEDIUM/.test(o[6])?'gap':'mute'}">${esc(o[6])}</span></div>
   <div class="sm-dev" style="white-space:normal">${esc(o[7])}</div></div>`).join('');
 $('smOHead').innerHTML='<tr>'+['iCX label','Room reads','Device MAC','Last seen','Should be labeled','Confidence','Why / what would settle it']
  .map(l=>`<th>${l}</th>`).join('')+'</tr>';
 const cc=c=>/^HIGH/.test(c)?'ok':/MEDIUM/.test(c)?'warn':'q';
 $('smOBody').innerHTML=O.map(o=>'<tr>'+[
  ['m',esc(o[1])],['m',esc(o[2])],['m',esc(mac(o[0]))],['m',esc(s.stamps[o[3]]||'')],
  ['m',esc(o[5])||'<span class="q">—</span>'],
  ['',`<span class="${cc(o[6])}">${esc(o[6])}</span>`],
  ['wrap',esc(o[7])+(o[8]?` <span class="q">&rarr; ${esc(o[8])}</span>`:'')],
 ].map(([c,v])=>`<td class="${c}">${v}</td>`).join('')+'</tr>').join('');
}
$('smCsv').onclick=()=>{const s=D.sites[site],Rs=rows();
 const head=['site','room','group','boxes_seen','boxes_expected','presence','labeling','casting',
  'missing_positions','punchlist_complete','punch_qr_seen_on','punch_use_device_mac',
  'punch_relabel','punch_fw','punch_linear','punch_flag','punch_comment',
  'room_occupancy','guest_initials','bedroom_tv_name','living_tv_name','name_verdict',
  'reswept','reswept_at',
  'device_mac','device_icx_label','device_position',
  'device_last_seen','device_days','device_casting','registry_real_mac','registry_leftovers'];
 const q=v=>{v=v==null?'':String(v);return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v};
 const lines=[];
 Rs.forEach(r=>{const pk=r[R.pk]||[];
  const base=[site,r[R.room],s.groups[r[R.grp]]||'',r[R.seen],r[R.exp],
   PRES[r[R.pres]],LABL[r[R.labl]],CAST[r[R.cast]],r[R.miss].join(' '),
   r[R.punch]<0?'':(r[R.punch]?'yes':'no'),
   pk[P.qr]||'',pk[P.devmac]||'',pk[P.relabel]||'',pk[P.fw]||'',pk[P.linear]||'',
   pk[P.flag]||'',pk[P.comment]||'',
   pk[P.occ]||'',pk[P.init]||'',pk[P.bed]||'',pk[P.liv]||'',pk[P.nv]||'',
   reswept(r)?'yes':'no',pk[P.done]||''];
  if(!r[R.devs].length){lines.push(base.concat(['','','','','','','','']).map(q).join(','));return}
  r[R.devs].forEach(d=>lines.push(base.concat([mac(d[Dv.mac]),d[Dv.label],d[Dv.pos],
   s.stamps[d[Dv.ts]]||'',d[Dv.days],DC[d[Dv.cast]],mac(d[Dv.real]),
   d[Dv.left].map(mac).join(' ')]).map(q).join(',')))});
 const b=new Blob([head.join(',')+'\n'+lines.join('\n')],{type:'text/csv'});
 const a=document.createElement('a');a.href=URL.createObjectURL(b);
 a.download=site+'-rooms-and-devices.csv';a.click();URL.revokeObjectURL(a.href)};
// autosave every work-order field. Delegated, because the drill is re-rendered constantly and
// per-element handlers would not survive it; values are re-read from the store on each render.
document.addEventListener('input',e=>{const t=e.target;
 if(t&&t.dataset&&t.dataset.ai)aiSet(t.dataset.ai,t.value)});
document.addEventListener('change',e=>{const t=e.target;
 if(!(t&&t.dataset&&t.dataset.ai))return;
 aiSet(t.dataset.ai,t.value);
 // Comments Yes opens the note field, No hides it AND clears it — a hidden leftover note would
 // otherwise sit in a room that the completion rule counts as comment-free.
 if(t.dataset.ai.endsWith('|hascomment')){
  const cell=t.closest('tr')&&t.closest('tr').querySelector('.cmtc');
  if(cell){cell.dataset.has=t.value;
   if(t.value!=='Yes'){const inp=cell.querySelector('input.aii');
    if(inp&&inp.value){inp.value='';aiSet(inp.dataset.ai,'')}}}}});

$('smAiCsv').onclick=()=>{
 // one row per room+position, for every room that has anything filled in
 const rowsOut=[], seen=new Set();
 Object.keys(AI).forEach(k=>{const p=k.split('|'); if(p.length>=3) seen.add(p[0]+'|'+p[1])});
 [...seen].sort().forEach(rk=>{const [st,room]=rk.split('|');
  const s2=D.sites[st]; if(!s2) return;
  const rr=s2.rooms.find(x=>x[R.room]===room); if(!rr) return;
  const order=['B','B1','B2','L'].filter(p=>(rr[R.posOrder]||[]).includes(p));
  const g=(...p)=>aiGet([st,room,...p].join('|'));
  (order.length?order:['B']).forEach(p=>rowsOut.push([st,room,
    g('logs'),g('cleared'),g('cycled'),p,
    ...AIROWS.flatMap(([id])=>[g(p,'orig',id),g(p,'rev',id)]),
    g(p,'comments')]))});
 const head=['site','room','downloaded_logs','app_data_cleared','box_power_cycled','position',
  ...AIROWS.flatMap(([id])=>['orig_'+id,'rev_'+id]),'comments'];
 if(!rowsOut.length){alert('No work-order entries saved on this device yet.');return}
 const q=v=>{v=v==null?'':String(v);return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v};
 const b=new Blob([[head,...rowsOut].map(l=>l.map(q).join(',')).join('\n')],{type:'text/csv'});
 const a=document.createElement('a');a.href=URL.createObjectURL(b);
 a.download='action-items-'+site+'.csv';a.click();URL.revokeObjectURL(a.href)};

// open/close every room in the current list — needed before printing a packet, since a collapsed
// room renders no work order at all
$('smAll').onclick=()=>{const Rs=rows();
 if(Rs.every(r=>open.has(r[R.room]))) Rs.forEach(r=>open.delete(r[R.room]));
 else Rs.forEach(r=>open.add(r[R.room]));
 render()};
$('smPrint').onclick=()=>{rows().forEach(r=>open.add(r[R.room]));render();window.print()};
$('smReset').onclick=()=>{f=null;sk=null;$('smQ').value='';$('smG').value='';render()};
// ---- "you are looking at a cached page" guard -------------------------------------------
// This page republishes every 15 minutes and GitHub Pages serves it with a cache header, so a
// browser will happily show a build from an hour ago with no hint that it has. That cost real
// confusion today: a fix was live and verified while the screen still showed the old value.
// A HEAD request is cheap (no 2.6 MB body) and the ETag changes on every publish.
let ETAG0=null;
function checkFresh(){
 fetch(location.pathname,{method:'HEAD',cache:'no-store'}).then(r=>{
  const t=r.headers.get('etag')||r.headers.get('last-modified');
  if(!t) return;
  if(ETAG0===null){ETAG0=t;return}
  if(t!==ETAG0) $('smNew').innerHTML=`<div class="newbar"><span>Newer data has been published
    &mdash; this page is showing an older build.</span>
    <button type="button" onclick="location.reload(true)">Refresh</button></div>`;
 }).catch(()=>{});
}
checkFresh(); setInterval(checkFresh,300000);
$('smQ').addEventListener('input',render);$('smG').addEventListener('change',render);
render();})();
</script>
"""


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else 'rooms-state.json'
    out = sys.argv[2] if len(sys.argv) > 2 else 'artifact.html'
    lk = None
    lkp = next((x for x in sys.argv[3:] if x.endswith('.json')), 'lockouts.json')
    if os.path.exists(lkp):
        lk = json.load(open(lkp))
    wn = '--with-names' in sys.argv
    hp = next((x for x in sys.argv[3:] if x.endswith('history.json')), 'history.json')
    hs = json.load(open(hp)) if os.path.exists(hp) else {}
    data = compact(json.load(open(src)), lk, wn, hs)
    page = PAGE.replace('__WITHNAMES__', 'true' if wn else 'false')
    open(out, 'w', encoding='utf-8').write(page.replace('__DATA__', json.dumps(data, separators=(',', ':'))))
    if wn:
        print('   ⚠ built WITH GUEST NAMES — authenticated hosting only, never publish')
    nr = sum(len(v['rooms']) for v in data['sites'].values())
    nd = sum(len(r[9]) for v in data['sites'].values() for r in v['rooms'])
    print(f"wrote {out}  {os.path.getsize(out)/1e6:.2f} MB  ({nr} rooms, {nd} devices)")


if __name__ == '__main__':
    main()
