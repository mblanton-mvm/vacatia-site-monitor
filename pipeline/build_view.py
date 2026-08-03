#!/usr/bin/env python3
"""Render the site-monitoring view as ONE self-contained HTML file with the state embedded,
so it opens by double-click with no server and no network. Same pattern as the punch lists.

Usage: python3 build_view.py state.json site-monitor.html
"""
import json
import sys

HTML = r"""<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vacatia Site Monitor</title>
<style>
:root{--bg:#0f1115;--panel:#171a21;--line:#252a35;--tx:#e6e9ef;--dim:#8b93a5;
 --ok:#22c55e;--warn:#f59e0b;--bad:#ef4444;--info:#3b82f6;--acc:#a855f7}
@media(prefers-color-scheme:light){:root{--bg:#f6f7f9;--panel:#fff;--line:#e3e6ec;--tx:#15181e;--dim:#5b6270}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:14px 18px;border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:5}
h1{margin:0 0 3px;font-size:16px;letter-spacing:.2px}
.sub{color:var(--dim);font-size:12px}
.wrap{padding:14px 18px;max-width:1500px;margin:0 auto}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0}
.tab{padding:7px 13px;border:1px solid var(--line);border-radius:7px;background:var(--panel);cursor:pointer;font-weight:600;font-size:13px}
.tab.on{border-color:var(--info);color:var(--info)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px;margin:12px 0}
.tile{background:var(--panel);border:1px solid var(--line);border-left-width:3px;border-radius:8px;padding:10px 12px}
.tile b{display:block;font-size:21px;line-height:1.15;font-variant-numeric:tabular-nums}
.tile span{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.t-ok{border-left-color:var(--ok)}.t-warn{border-left-color:var(--warn)}
.t-bad{border-left-color:var(--bad)}.t-info{border-left-color:var(--info)}.t-acc{border-left-color:var(--acc)}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:12px 0}
input[type=search],select{background:var(--panel);border:1px solid var(--line);color:var(--tx);
 padding:7px 9px;border-radius:7px;font:inherit}
input[type=search]{min-width:210px}
button.act{background:var(--info);border:0;color:#fff;padding:7px 12px;border-radius:7px;cursor:pointer;font:inherit;font-weight:600}
button.gh{background:transparent;border:1px solid var(--line);color:var(--tx);padding:7px 12px;border-radius:7px;cursor:pointer;font:inherit}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th,td{padding:7px 9px;text-align:left;border-bottom:1px solid var(--line);font-size:12.5px;white-space:nowrap}
th{position:sticky;top:0;background:var(--panel);cursor:pointer;font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:var(--dim)}
tbody tr:hover{background:rgba(59,130,246,.07)}
.scroll{max-height:62vh;overflow:auto;border-radius:8px}
.pill{display:inline-block;padding:1px 7px;border-radius:99px;font-size:11px;font-weight:600;border:1px solid}
.p-ok{color:var(--ok);border-color:var(--ok)}.p-warn{color:var(--warn);border-color:var(--warn)}
.p-bad{color:var(--bad);border-color:var(--bad)}.p-dim{color:var(--dim);border-color:var(--line)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px}
.note{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--warn);
 border-radius:8px;padding:9px 12px;margin:10px 0;font-size:12.5px;color:var(--dim)}
.count{color:var(--dim);font-size:12px}
@media(max-width:700px){th,td{font-size:11.5px;padding:6px}.scroll{max-height:none}}
</style>
<header>
 <h1>Vacatia Site Monitor <span class="count" id="stamp"></span></h1>
 <div class="sub">Roster is truth (docs/vacatia/rooms/). iCX = DISH-side presence · mDNS Management = casting registry. Every figure carries its own capture time.</div>
</header>
<div class="wrap">
 <div class="tabs" id="tabs"></div>
 <div id="snapnote"></div>
 <div class="tiles" id="tiles"></div>
 <div class="bar">
  <input type="search" id="q" placeholder="room, label or MAC…">
  <select id="fpres"><option value="">presence: any</option></select>
  <select id="fcast"><option value="">casting: any</option></select>
  <select id="fgrp"><option value="">all groups</option></select>
  <select id="fpl"><option value="">punchlist: any</option><option value="yes">complete</option><option value="no">not complete</option></select>
  <button class="gh" id="clear">clear</button>
  <button class="act" id="csv">export filtered CSV</button>
  <span class="count" id="cnt"></span>
 </div>
 <div class="scroll"><table id="tbl"><thead></thead><tbody></tbody></table></div>
 <h1 style="font-size:14px;margin:20px 0 4px">Devices iCX reports that match no roster TV</h1>
 <div class="sub" style="margin-bottom:8px">Not relabelled (no <span class="mono">@</span> decorator), wrong position suffix, or a room the roster does not contain (lockout parent / common area / typo).</div>
 <div class="bar"><select id="fiss"><option value="">issue: any</option></select><span class="count" id="cnt2"></span></div>
 <div class="scroll"><table id="tbl2"><thead></thead><tbody></tbody></table></div>
</div>
<script>
const STATE = __STATE__;
let site = Object.keys(STATE.sites)[0], sortKey='label', sortDir=1;
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const PRES={OK:['p-ok','reporting'],STALE:['p-bad','stale >'+STATE.stale_days+'d'],
 NEVER_SEEN_UNDER_THIS_LABEL:['p-warn','never under this label']};
const CAST={OK:['p-ok','castable'],STRANDED_RANDOMISED:['p-bad','stranded (randomised only)'],
 NO_REAL_MAC_LEFTOVER_PRESENT:['p-bad','no real MAC (leftover present)'],
 NO_REGISTRY_ENTRY:['p-bad','no registry entry'],NO_REGISTRY_DATA:['p-dim','no registry export']};
const pill=(v,map)=>{const m=map[v]||['p-dim',v||'-'];return `<span class="pill ${m[0]}">${esc(m[1])}</span>`};

function tabs(){$('tabs').innerHTML=Object.entries(STATE.sites).map(([k,v])=>
 `<div class="tab ${k===site?'on':''}" data-s="${k}">${k} · ${esc(v.site_name)}</div>`).join('');
 document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{site=t.dataset.s;render()})}

function tiles(){const s=STATE.sites[site],p=s.summary.presence||{},c=s.summary.casting||{},l=s.summary.label_issues||{};
 const g=(o,k)=>o[k]||0;
 const T=[['t-info',s.unit_count,'units'],['t-info',s.tv_count,'TVs expected'],
  ['t-ok',g(p,'OK'),'reporting to iCX'],
  ['t-bad',g(p,'STALE')+g(p,'NEVER_SEEN_UNDER_THIS_LABEL'),'not seen under label'],
  ['t-ok',g(c,'OK'),'castable'],
  ['t-bad',g(c,'STRANDED_RANDOMISED')+g(c,'NO_REAL_MAC_LEFTOVER_PRESENT')+g(c,'NO_REGISTRY_ENTRY'),'cannot cast'],
  ['t-warn',g(l,'NOT_RELABELLED'),'not relabelled'],
  ['t-warn',g(l,'WRONG_POSITION_SUFFIX')+g(l,'UNKNOWN_ROOM'),'label wrong / unknown room']];
 $('tiles').innerHTML=T.map(([c2,n,lb])=>`<div class="tile ${c2}"><b>${n}</b><span>${lb}</span></div>`).join('');
 const sn=s.snapshots||[];const last=sn.length?sn[sn.length-1]:null;
 $('snapnote').innerHTML=`<div class="note"><b>${s.registry_loaded?'Registry loaded — '+s.registry_rows+' rows':'⚠ No mDNS registry export for this site — every casting verdict reads “no registry export”.'}</b>`
  +(last?` · newest iCX snapshot <span class="mono">${esc(last.captured_at)}</span> (${last.devices} devices, ${sn.length} snapshot${sn.length>1?'s':''} loaded)`
        :' · ⚠ no iCX snapshot loaded for this site')+`</div>`}

function fill(sel,vals,pfx){const e=$(sel),cur=e.value;
 e.innerHTML=`<option value="">${pfx}: any</option>`+vals.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('');
 e.value=cur}

const COLS=[['room','Room'],['label','TV label'],['group','Group'],['presence','Presence'],
 ['icx_last_seen','Last seen (iCX)'],['days_since','Days'],['icx_mac','iCX MAC'],
 ['casting','Casting'],['registry_real_mac','Registry real MAC'],
 ['registry_randomised_leftovers','Randomised leftovers'],['punchlist_complete','Punchlist']];

function rows(){const s=STATE.sites[site],q=$('q').value.trim().toLowerCase();
 return s.tvs.filter(r=>{
  if($('fpres').value&&r.presence!==$('fpres').value)return false;
  if($('fcast').value&&r.casting!==$('fcast').value)return false;
  if($('fgrp').value&&r.group!==$('fgrp').value)return false;
  const pl=$('fpl').value;
  if(pl==='yes'&&r.punchlist_complete!==true)return false;
  if(pl==='no'&&r.punchlist_complete===true)return false;
  if(q){const h=[r.room,r.label,r.icx_mac,r.registry_real_mac,(r.registry_randomised_leftovers||[]).join(' ')]
   .join(' ').toLowerCase();if(!h.includes(q))return false}
  return true}).sort((a,b)=>{const x=a[sortKey],y=b[sortKey];
   return ((x??'')>(y??'')?1:(x??'')<(y??'')?-1:0)*sortDir})}

function render(){tabs();tiles();const s=STATE.sites[site];
 fill('fpres',[...new Set(s.tvs.map(r=>r.presence))].sort(),'presence');
 fill('fcast',[...new Set(s.tvs.map(r=>r.casting))].sort(),'casting');
 fill('fgrp',s.groups,'group');
 const R=rows();$('cnt').textContent=`${R.length} of ${s.tvs.length} TVs`;
 $('tbl').tHead.innerHTML='<tr>'+COLS.map(([k,l])=>`<th data-k="${k}">${l}${sortKey===k?(sortDir>0?' ▲':' ▼'):''}</th>`).join('')+'</tr>';
 $('tbl').tHead.querySelectorAll('th').forEach(th=>th.onclick=()=>{
  if(sortKey===th.dataset.k)sortDir*=-1;else{sortKey=th.dataset.k;sortDir=1}render()});
 $('tbl').tBodies[0].innerHTML=R.map(r=>'<tr>'+[
  esc(r.room),`<span class="mono">${esc(r.label)}</span>`,esc(r.group),pill(r.presence,PRES),
  `<span class="mono">${esc(r.icx_last_seen||'—')}</span>`,r.days_since??'—',
  `<span class="mono">${esc(r.icx_mac||'—')}</span>`,pill(r.casting,CAST),
  `<span class="mono">${esc(r.registry_real_mac||'—')}</span>`,
  `<span class="mono">${esc((r.registry_randomised_leftovers||[]).join(', ')||'—')}</span>`,
  r.punchlist_complete===null?'—':(r.punchlist_complete?'<span class="pill p-ok">complete</span>':'<span class="pill p-dim">no</span>')
 ].map(c=>`<td>${c}</td>`).join('')+'</tr>').join('');
 const U=s.unmatched_devices||[];
 fill('fiss',[...new Set(U.map(r=>r.issue))].sort(),'issue');
 const UF=U.filter(r=>!$('fiss').value||r.issue===$('fiss').value);
 $('cnt2').textContent=`${UF.length} of ${U.length} devices`;
 const C2=[['icx_label','iCX label'],['room_guess','Room'],['suffix','Suffix'],['mac','MAC'],
  ['icx_last_seen','Last seen'],['days_since','Days'],['issue','Issue']];
 $('tbl2').tHead.innerHTML='<tr>'+C2.map(([,l])=>`<th>${l}</th>`).join('')+'</tr>';
 $('tbl2').tBodies[0].innerHTML=UF.map(r=>'<tr>'+[
  `<span class="mono">${esc(r.icx_label)}</span>`,esc(r.room_guess),esc(r.suffix||'—'),
  `<span class="mono">${esc(r.mac)}</span>`,`<span class="mono">${esc(r.icx_last_seen)}</span>`,
  r.days_since,`<span class="pill ${r.issue==='NOT_RELABELLED'?'p-warn':'p-bad'}">${esc(r.issue)}</span>`
 ].map(c=>`<td>${c}</td>`).join('')+'</tr>').join('');
 $('stamp').textContent='· state as of '+(STATE.generated_for||'unknown')}

$('csv').onclick=()=>{const R=rows();
 const head=COLS.map(c=>c[1]).concat(['site']).join(',');
 const body=R.map(r=>COLS.map(([k])=>{let v=r[k];if(Array.isArray(v))v=v.join(' ');
  v=v===null||v===undefined?'':String(v);return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v})
  .concat(site).join(',')).join('\n');
 const b=new Blob([head+'\n'+body],{type:'text/csv'});const a=document.createElement('a');
 a.href=URL.createObjectURL(b);a.download=`${site}-site-monitor-filtered.csv`;a.click()};
$('clear').onclick=()=>{['q','fpres','fcast','fgrp','fpl','fiss'].forEach(i=>$(i).value='');render()};
['q','fpres','fcast','fgrp','fpl','fiss'].forEach(i=>$(i).addEventListener('input',render));
render();
</script>
"""


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else 'state.json'
    out = sys.argv[2] if len(sys.argv) > 2 else 'site-monitor.html'
    state = json.load(open(src))
    html = HTML.replace('__STATE__', json.dumps(state, separators=(',', ':')))
    open(out, 'w', encoding='utf-8').write(html)
    n = sum(len(v['tvs']) for v in state['sites'].values())
    print(f"wrote {out}  ({n} TV rows, {os.path.getsize(out)/1e6:.2f} MB)")


if __name__ == '__main__':
    import os
    main()
