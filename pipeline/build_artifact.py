#!/usr/bin/env python3
"""Render state.json into a mobile-first Artifact page.

Payload is compacted (positional rows, code-numbered enums, colon-stripped MACs,
dictionary-encoded groups and timestamps) because this loads over hotel wifi on a phone.

Usage: python3 build_artifact.py state.json artifact.html
"""
import json
import os
import sys

PRES = ['OK', 'STALE', 'NEVER_SEEN_UNDER_THIS_LABEL']
CAST = ['OK', 'STRANDED_RANDOMISED', 'NO_REAL_MAC_LEFTOVER_PRESENT',
        'NO_REGISTRY_ENTRY', 'NO_REGISTRY_DATA']
ISSUE = ['NOT_RELABELLED', 'WRONG_POSITION_SUFFIX', 'UNKNOWN_ROOM']


def compact(state):
    out = {'now': state.get('generated_for'), 'staleDays': state.get('stale_days', 10), 'sites': {}}
    for code, s in state['sites'].items():
        groups = s['groups']
        gi = {g: i for i, g in enumerate(groups)}
        ts, ti = [], {}

        def tsi(v):
            if not v:
                return -1
            v = v[:16]
            if v not in ti:
                ti[v] = len(ts)
                ts.append(v)
            return ti[v]
        nm = lambda m: (m or '').replace(':', '')
        rows = []
        for r in s['tvs']:
            rows.append([
                r['room'], r['position'], gi.get(r['group'], -1),
                PRES.index(r['presence']), tsi(r['icx_last_seen']),
                r['days_since'] if r['days_since'] is not None else -1,
                nm(r['icx_mac']), CAST.index(r['casting']),
                nm(r.get('registry_real_mac')),
                [nm(x) for x in (r.get('registry_randomised_leftovers') or [])],
                (1 if r['punchlist_complete'] else 0) if r['punchlist_complete'] is not None else -1,
            ])
        un = [[u['icx_label'], u['room_guess'], u['suffix'] or '', nm(u['mac']),
               tsi(u['icx_last_seen']), u['days_since'], ISSUE.index(u['issue'])]
              for u in s['unmatched_devices']]
        out['sites'][code] = {
            'name': s['site_name'], 'groupLabel': s['group_label'], 'units': s['unit_count'],
            'tvCount': s['tv_count'], 'groups': groups, 'stamps': ts,
            'regLoaded': s['registry_loaded'], 'regRows': s['registry_rows'],
            'snaps': [[x['captured_at'][:16], x['devices']] for x in s['snapshots']],
            'rows': rows, 'un': un,
        }
    return out


PAGE = r"""<title>Vacatia Site Monitor</title>
<style>
:root{
 --ground:#f7f9fa; --panel:#ffffff; --sunk:#eef2f4; --line:#dae1e6;
 --ink:#0f1419; --dim:#5c6b78; --faint:#8b9bab;
 --accent:#0369a1; --accent-soft:#e0f2fe;
 --live:#047857; --gap:#b45309; --dead:#b91c1c;
 --live-bg:#ecfdf5; --gap-bg:#fffbeb; --dead-bg:#fef2f2;
 --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
 --ui:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}
@media (prefers-color-scheme:dark){:root{
 --ground:#0f1419; --panel:#171e26; --sunk:#111820; --line:#26313c;
 --ink:#e8edf2; --dim:#8b9bab; --faint:#5c6b78;
 --accent:#38bdf8; --accent-soft:#0c2a3d;
 --live:#34d399; --gap:#fbbf24; --dead:#f87171;
 --live-bg:#0d2b22; --gap-bg:#2e2410; --dead-bg:#2f1517;
}}
:root[data-theme=dark]{
 --ground:#0f1419; --panel:#171e26; --sunk:#111820; --line:#26313c;
 --ink:#e8edf2; --dim:#8b9bab; --faint:#5c6b78;
 --accent:#38bdf8; --accent-soft:#0c2a3d;
 --live:#34d399; --gap:#fbbf24; --dead:#f87171;
 --live-bg:#0d2b22; --gap-bg:#2e2410; --dead-bg:#2f1517;
}
:root[data-theme=light]{
 --ground:#f7f9fa; --panel:#ffffff; --sunk:#eef2f4; --line:#dae1e6;
 --ink:#0f1419; --dim:#5c6b78; --faint:#8b9bab;
 --accent:#0369a1; --accent-soft:#e0f2fe;
 --live:#047857; --gap:#b45309; --dead:#b91c1c;
 --live-bg:#ecfdf5; --gap-bg:#fffbeb; --dead-bg:#fef2f2;
}
*{box-sizing:border-box}
.sm{margin:0;background:var(--ground);color:var(--ink);font:15px/1.45 var(--ui);
 -webkit-text-size-adjust:100%;font-variant-numeric:tabular-nums}
.sm-hd{background:var(--panel);border-bottom:1px solid var(--line);padding:12px 14px;
 position:sticky;top:0;z-index:20}
.sm-hd h1{margin:0;font-size:15px;font-weight:650;letter-spacing:-.01em}
.sm-hd p{margin:3px 0 0;font-size:12px;color:var(--dim)}
.sm-body{padding:12px 14px 40px;max-width:1400px;margin:0 auto;display:flex;flex-direction:column;gap:14px}
.sm-sites{display:flex;gap:6px;overflow-x:auto;padding-bottom:2px;-webkit-overflow-scrolling:touch}
.sm-site{flex:0 0 auto;min-height:44px;display:flex;flex-direction:column;justify-content:center;
 padding:6px 14px;border:1px solid var(--line);border-radius:10px;background:var(--panel);
 color:var(--ink);font:inherit;font-weight:650;cursor:pointer;text-align:left}
.sm-site small{display:block;font-weight:400;font-size:11px;color:var(--dim)}
.sm-site[aria-pressed=true]{border-color:var(--accent);background:var(--accent-soft);color:var(--accent)}
.sm-site:focus-visible,.sm-chip:focus-visible,.sm-f:focus-visible,button:focus-visible{
 outline:2px solid var(--accent);outline-offset:2px}
.sm-stale{background:var(--gap-bg);border:1px solid var(--gap);border-radius:10px;padding:9px 12px;
 font-size:12.5px;color:var(--ink)}
.sm-stale b{color:var(--gap)}
.sm-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(104px,1fr));gap:8px}
.sm-tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:9px 11px;
 display:flex;flex-direction:column;gap:2px}
.sm-tile b{font-size:22px;line-height:1.1;font-weight:650}
.sm-tile span{font-size:10.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;line-height:1.25}
.sm-tile.live b{color:var(--live)} .sm-tile.gap b{color:var(--gap)} .sm-tile.dead b{color:var(--dead)}
.sm-controls{display:flex;flex-direction:column;gap:8px}
.sm-chips{display:flex;gap:6px;overflow-x:auto;padding-bottom:2px}
.sm-chip{flex:0 0 auto;min-height:38px;padding:7px 13px;border:1px solid var(--line);border-radius:99px;
 background:var(--panel);color:var(--dim);font:inherit;font-size:13px;font-weight:600;cursor:pointer}
.sm-chip[aria-pressed=true]{border-color:var(--accent);background:var(--accent-soft);color:var(--accent)}
.sm-row{display:flex;gap:8px;flex-wrap:wrap}
.sm-f{flex:1 1 140px;min-height:42px;background:var(--panel);border:1px solid var(--line);
 border-radius:9px;color:var(--ink);font:inherit;font-size:14px;padding:8px 10px}
.sm-count{font-size:12.5px;color:var(--dim);display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.sm-count button{background:none;border:0;color:var(--accent);font:inherit;font-size:12.5px;
 font-weight:600;cursor:pointer;padding:4px 0;text-decoration:underline}
.sm-cards{display:flex;flex-direction:column;gap:7px}
.sm-card{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--line);
 border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:6px}
.sm-card.s-live{border-left-color:var(--live)}
.sm-card.s-gap{border-left-color:var(--gap)}
.sm-card.s-dead{border-left-color:var(--dead)}
.sm-card-top{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.sm-lab{font-family:var(--mono);font-size:16px;font-weight:650;letter-spacing:-.02em}
.sm-grp{font-size:11.5px;color:var(--faint);text-align:right;flex:0 0 auto}
.sm-pills{display:flex;gap:5px;flex-wrap:wrap}
.pill{font-size:11px;font-weight:650;padding:2px 8px;border-radius:99px;border:1px solid}
.pill.live{color:var(--live);border-color:var(--live);background:var(--live-bg)}
.pill.gap{color:var(--gap);border-color:var(--gap);background:var(--gap-bg)}
.pill.dead{color:var(--dead);border-color:var(--dead);background:var(--dead-bg)}
.pill.mute{color:var(--dim);border-color:var(--line);background:var(--sunk)}
.sm-meta{font-family:var(--mono);font-size:11.5px;color:var(--dim);display:flex;flex-direction:column;gap:2px;word-break:break-all}
.sm-tablewrap{display:none}
.sm-empty{padding:26px 14px;text-align:center;color:var(--dim);font-size:13.5px;
 background:var(--panel);border:1px dashed var(--line);border-radius:10px}
h2.sm-h{margin:6px 0 0;font-size:13px;font-weight:650;letter-spacing:.03em;text-transform:uppercase;color:var(--dim)}
h2.sm-h+p{margin:0;font-size:12.5px;color:var(--dim)}
@media (min-width:760px){
 .sm-cards{display:none}
 .sm-tablewrap{display:block;overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}
 table{width:100%;border-collapse:collapse}
 th,td{padding:7px 10px;text-align:left;font-size:12.5px;white-space:nowrap;border-bottom:1px solid var(--line)}
 th{position:sticky;top:0;background:var(--sunk);font-size:10.5px;text-transform:uppercase;
  letter-spacing:.06em;color:var(--dim);cursor:pointer;font-weight:650}
 td.m{font-family:var(--mono);font-size:11.5px}
 tbody tr:last-child td{border-bottom:0}
}
@media (prefers-reduced-motion:no-preference){.sm-card,.sm-chip,.sm-site{transition:border-color .12s,background-color .12s}}
</style>
<div class="sm">
 <div class="sm-hd">
  <h1>Vacatia Site Monitor</h1>
  <p id="smStamp"></p>
 </div>
 <div class="sm-body">
  <div class="sm-sites" id="smSites" role="group" aria-label="Site"></div>
  <div class="sm-stale" id="smStale"></div>
  <div class="sm-tiles" id="smTiles"></div>
  <div class="sm-controls">
   <div class="sm-chips" id="smChips" role="group" aria-label="Quick filters"></div>
   <div class="sm-row">
    <input class="sm-f" type="search" id="smQ" placeholder="Room, TV label or MAC" aria-label="Search">
    <select class="sm-f" id="smG" aria-label="Group"></select>
   </div>
  </div>
  <div class="sm-count">
   <span id="smCount"></span>
   <button type="button" id="smCsv">Download this list</button>
   <button type="button" id="smReset">Show everything</button>
  </div>
  <div class="sm-cards" id="smCards"></div>
  <div class="sm-tablewrap"><table><thead id="smHead"></thead><tbody id="smTbody"></tbody></table></div>
  <h2 class="sm-h">TVs iCX reports under a label the roster doesn't have</h2>
  <p>No <span style="font-family:var(--mono)">@</span> position (never relabelled), a position that doesn't belong, or a room that isn't a real unit.</p>
  <div class="sm-chips" id="smIChips" role="group" aria-label="Issue filter"></div>
  <div class="sm-count"><span id="smCount2"></span></div>
  <div class="sm-cards" id="smCards2"></div>
  <div class="sm-tablewrap"><table><thead id="smHead2"></thead><tbody id="smTbody2"></tbody></table></div>
 </div>
</div>
<script>
(function(){
const D=__DATA__;
const PRES=['Reporting','Stale','Never under this label'];
const PSEV=['live','dead','gap'];
const CAST=['Castable','Stranded on randomised MAC','No real MAC (leftover present)','No registry entry','No registry export'];
const CSEV=['live','dead','dead','dead','mute'];
const ISSUE=['Never relabelled','Wrong position','Not a real unit'];
const ISEV=['gap','dead','dead'];
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const mac=h=>h?h.match(/../g).join(':'):'';
let site=Object.keys(D.sites)[0], only='attention', iss=-1, sk=null, sd=1;

const R={room:0,pos:1,grp:2,pres:3,ts:4,days:5,mac:6,cast:7,real:8,left:9,punch:10};
const lab=r=>r[R.room]+'@'+r[R.pos];
const stamp=(s,i)=>i<0?'':s.stamps[i];
// 'dead' = needs attention (not reporting, or reporting but cannot cast).
// 'mute'  = reporting fine, but we have no registry export so castability is unknown.
// 'live'  = reporting and castable.
function sev(r){
 const notSeen=r[R.pres]!==0, cannotCast=r[R.cast]>=1&&r[R.cast]<=3, noRegData=r[R.cast]===4;
 if(notSeen||cannotCast)return 'dead';
 if(noRegData)return 'mute';
 return 'live'}

function pass(s,r){
 if(only==='attention'&&sev(r)==='live')return false;
 if(only==='nocast'&&!(r[R.cast]>=1&&r[R.cast]<=3))return false;
 if(only==='dark'&&r[R.pres]===0)return false;
 if(only==='stranded'&&r[R.cast]!==1)return false;
 const g=$('smG').value;
 if(g!==''&&String(r[R.grp])!==g)return false;
 const q=$('smQ').value.trim().toLowerCase().replace(/:/g,'');
 if(q){const hay=(lab(r)+' '+r[R.mac]+' '+r[R.real]+' '+r[R.left].join(' ')).toLowerCase();
  if(!hay.includes(q))return false}
 return true}

function rows(){const s=D.sites[site];let out=s.rows.filter(r=>pass(s,r));
 if(sk!=null)out=out.slice().sort((a,b)=>{const x=a[sk],y=b[sk];
  return (x>y?1:x<y?-1:0)*sd});
 return out}

const COLS=[['TV',R.room],['Group',R.grp],['Presence',R.pres],['Last seen',R.ts],
 ['Days',R.days],['iCX MAC',R.mac],['Casting',R.cast],['Registry real MAC',R.real],
 ['Randomised leftovers',R.left],['Punchlist',R.punch]];

function render(){
 const s=D.sites[site];
 $('smStamp').textContent='State as of '+(D.now||'unknown')+' · roster from the room lists, not derived from labels';
 $('smSites').innerHTML=Object.entries(D.sites).map(([k,v])=>
  `<button class="sm-site" type="button" aria-pressed="${k===site}" data-s="${k}">${k}<small>${esc(v.name)}</small></button>`).join('');
 $('smSites').querySelectorAll('.sm-site').forEach(b=>b.onclick=()=>{site=b.dataset.s;sk=null;$('smG').value='';render()});
 const last=s.snaps.length?s.snaps[s.snaps.length-1]:null;
 $('smStale').innerHTML=(s.regLoaded
   ?`Casting registry loaded, <b>${s.regRows}</b> rows.`
   :`<b>No casting registry for this site.</b> Every casting result below reads “no registry export”.`)
  +(last?` Newest iCX pull <b>${esc(last[0])}</b> (${last[1]} TVs seen, ${s.snaps.length} pull${s.snaps.length>1?'s':''} loaded).`
        :` <b>No iCX pull loaded for this site.</b>`);
 let nLive=0,nDark=0,nCast=0,nNoCast=0;
 s.rows.forEach(r=>{if(r[R.pres]===0)nLive++;else nDark++;
  if(r[R.cast]===0)nCast++;else if(r[R.cast]>=1&&r[R.cast]<=3)nNoCast++});
 const nUn=s.un.length, nRe=s.un.filter(u=>u[6]===0).length;
 $('smTiles').innerHTML=[
  ['',s.units,'units'],['',s.tvCount,'TVs expected'],
  ['live',nLive,'reporting'],['dead',nDark,'not seen'],
  ['live',nCast,'castable'],['dead',nNoCast,'cannot cast'],
  ['gap',nRe,'never relabelled'],['gap',nUn-nRe,'label wrong'],
 ].map(([c,n,l])=>`<div class="sm-tile ${c}"><b>${n}</b><span>${l}</span></div>`).join('');
 const CH=[['attention','Needs attention'],['nocast','Cannot cast'],['dark','Not reporting'],
  ['stranded','Stranded MAC'],['all','Everything']];
 $('smChips').innerHTML=CH.map(([k,l])=>
  `<button class="sm-chip" type="button" aria-pressed="${only===k}" data-k="${k}">${l}</button>`).join('');
 $('smChips').querySelectorAll('.sm-chip').forEach(b=>b.onclick=()=>{only=b.dataset.k;render()});
 const gsel=$('smG');const cur=gsel.value;
 gsel.innerHTML=`<option value="">All ${esc(s.groupLabel).toLowerCase()}s</option>`
  +s.groups.map((g,i)=>`<option value="${i}">${esc(g)}</option>`).join('');
 gsel.value=cur;
 const Rs=rows();
 $('smCount').textContent=`${Rs.length} of ${s.tvCount} TVs`;
 $('smCards').innerHTML=Rs.length?Rs.slice(0,600).map(r=>{
  const lf=r[R.left].map(mac).join(', ');
  return `<div class="sm-card s-${sev(r)}">
   <div class="sm-card-top"><span class="sm-lab">${esc(lab(r))}</span>
    <span class="sm-grp">${esc(s.groups[r[R.grp]]||'')}</span></div>
   <div class="sm-pills"><span class="pill ${PSEV[r[R.pres]]}">${PRES[r[R.pres]]}</span>
    <span class="pill ${CSEV[r[R.cast]]}">${CAST[r[R.cast]]}</span>
    ${r[R.punch]===1?'<span class="pill mute">Punchlist done</span>':''}</div>
   <div class="sm-meta">
    ${r[R.mac]?`<span>iCX ${esc(mac(r[R.mac]))}${r[R.days]>=0?' · seen '+esc(stamp(s,r[R.ts]))+' ('+r[R.days]+'d)':''}</span>`:'<span>Not seen in any iCX pull</span>'}
    ${r[R.real]?`<span>Registry ${esc(mac(r[R.real]))}</span>`:''}
    ${lf?`<span>Leftover ${esc(lf)}</span>`:''}
   </div></div>`}).join('')
  +(Rs.length>600?`<div class="sm-empty">Showing the first 600. Narrow the filters or download the list.</div>`:'')
  :`<div class="sm-empty">Nothing matches these filters.</div>`;
 $('smHead').innerHTML='<tr>'+COLS.map(([l,k])=>
  `<th data-k="${k}">${l}${sk===k?(sd>0?' ▲':' ▼'):''}</th>`).join('')+'</tr>';
 $('smHead').querySelectorAll('th').forEach(th=>th.onclick=()=>{
  const k=+th.dataset.k; if(sk===k)sd*=-1; else{sk=k;sd=1} render()});
 $('smTbody').innerHTML=Rs.slice(0,1200).map(r=>'<tr>'+[
  ['m',esc(lab(r))],['',esc(s.groups[r[R.grp]]||'')],
  ['',`<span class="pill ${PSEV[r[R.pres]]}">${PRES[r[R.pres]]}</span>`],
  ['m',esc(stamp(s,r[R.ts]))||'—'],['',r[R.days]>=0?r[R.days]:'—'],
  ['m',esc(mac(r[R.mac]))||'—'],
  ['',`<span class="pill ${CSEV[r[R.cast]]}">${CAST[r[R.cast]]}</span>`],
  ['m',esc(mac(r[R.real]))||'—'],['m',esc(r[R.left].map(mac).join(', '))||'—'],
  ['',r[R.punch]<0?'—':(r[R.punch]?'yes':'no')],
 ].map(([c,v])=>`<td class="${c}">${v}</td>`).join('')+'</tr>').join('');
 // unmatched
 const IC=[[-1,'All'],[0,'Never relabelled'],[1,'Wrong position'],[2,'Not a real unit']];
 $('smIChips').innerHTML=IC.map(([k,l])=>
  `<button class="sm-chip" type="button" aria-pressed="${iss===k}" data-k="${k}">${l}</button>`).join('');
 $('smIChips').querySelectorAll('.sm-chip').forEach(b=>b.onclick=()=>{iss=+b.dataset.k;render()});
 const U=s.un.filter(u=>iss<0||u[6]===iss);
 $('smCount2').textContent=`${U.length} of ${s.un.length} TVs`;
 $('smCards2').innerHTML=U.length?U.slice(0,400).map(u=>`<div class="sm-card s-${ISEV[u[6]]}">
   <div class="sm-card-top"><span class="sm-lab">${esc(u[0])}</span>
    <span class="sm-grp">${esc(ISSUE[u[6]])}</span></div>
   <div class="sm-meta"><span>iCX ${esc(mac(u[3]))} · seen ${esc(stamp(s,u[4]))} (${u[5]}d)</span>
    <span>Room reads ${esc(u[1])}${u[2]?' · position '+esc(u[2]):' · no position'}</span></div></div>`).join('')
  +(U.length>400?`<div class="sm-empty">Showing the first 400.</div>`:'')
  :`<div class="sm-empty">Nothing matches.</div>`;
 $('smHead2').innerHTML='<tr>'+['iCX label','Room','Position','MAC','Last seen','Days','Issue']
  .map(l=>`<th>${l}</th>`).join('')+'</tr>';
 $('smTbody2').innerHTML=U.slice(0,1200).map(u=>'<tr>'+[
  ['m',esc(u[0])],['',esc(u[1])],['m',esc(u[2])||'—'],['m',esc(mac(u[3]))],
  ['m',esc(stamp(s,u[4]))],['',u[5]],
  ['',`<span class="pill ${ISEV[u[6]]}">${ISSUE[u[6]]}</span>`],
 ].map(([c,v])=>`<td class="${c}">${v}</td>`).join('')+'</tr>').join('');
}
$('smCsv').onclick=()=>{const s=D.sites[site],Rs=rows();
 const head=['site','tv_label','room','position','group','presence','icx_last_seen','days',
  'icx_mac','casting','registry_real_mac','randomised_leftovers','punchlist_complete'];
 const q=v=>{v=v==null?'':String(v);return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v};
 const body=Rs.map(r=>[site,lab(r),r[R.room],r[R.pos],s.groups[r[R.grp]]||'',PRES[r[R.pres]],
  stamp(s,r[R.ts]),r[R.days]>=0?r[R.days]:'',mac(r[R.mac]),CAST[r[R.cast]],mac(r[R.real]),
  r[R.left].map(mac).join(' '),r[R.punch]<0?'':(r[R.punch]?'yes':'no')].map(q).join(','));
 const b=new Blob([head.join(',')+'\n'+body.join('\n')],{type:'text/csv'});
 const a=document.createElement('a');a.href=URL.createObjectURL(b);
 a.download=site+'-site-monitor.csv';a.click();URL.revokeObjectURL(a.href)};
$('smReset').onclick=()=>{only='all';iss=-1;$('smQ').value='';$('smG').value='';sk=null;render()};
$('smQ').addEventListener('input',render);
$('smG').addEventListener('change',render);
render();
})();
</script>
"""


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else 'state.json'
    out = sys.argv[2] if len(sys.argv) > 2 else 'artifact.html'
    data = compact(json.load(open(src)))
    open(out, 'w', encoding='utf-8').write(
        PAGE.replace('__DATA__', json.dumps(data, separators=(',', ':'))))
    print(f"wrote {out}  {os.path.getsize(out)/1e6:.2f} MB "
          f"({sum(len(v['rows']) for v in data['sites'].values())} TV rows)")


if __name__ == '__main__':
    main()
