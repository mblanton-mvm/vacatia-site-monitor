// iCX coarse-poll toolkit — injected into the live TeICXDashboard tab.
//
// Everything here READS the SPA's own data. We never originate a call to
// dashboardbe-dms.dish.com: automation-originated requests hit a 422 cookie wall, and
// repeated scripted pulls trip F5 bot-defense. Reading the page's own responses has
// neither problem, which is what makes an all-day cadence sustainable.
//
// Idempotent: safe to inject repeatedly. Re-injection re-arms the hook after a reload.
(function () {
  const S = ms => new Promise(r => setTimeout(r, ms));
  const VIS = e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0 &&
           getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  };

  // ── passive response capture ────────────────────────────────────────────────
  // The metric name (RSSI / NET_AVAILABILITY / …) travels in the POST BODY, not the
  // query string. Keying on the URL alone collapses all four onto one key and they
  // silently overwrite each other — that bug cost a sweep on 2026-08-03.
  const KNOWN = ['NET_DISRUPTION_COUNT', 'NET_AVAILABILITY', 'REBOOT_COUNT', 'RSSI',
                 'CPU_INFO', 'CPU_TEMPERATURE', 'MEMORY_USAGE', 'TS_EPG_UPDATE'];
  const base = u => { const m = String(u || '').match(/live-data\/([a-z-]+)/i); return m ? m[1] : null; };
  const tok = (b, u) => { const hay = String(b || '') + ' ' + String(u || '');
                          return KNOWN.find(t => hay.indexOf(t) >= 0) || null; };
  const keyOf = (u, b) => { const k = base(u); if (!k) return null; const t = tok(b, u); return t ? k + ':' + t : k; };

  if (!window.__icxResp) window.__icxResp = {};
  if (!window.__respHooked) {
    window.__respHooked = true;
    const OF = window.fetch;
    window.fetch = async function (...a) {
      const r = await OF.apply(this, a);
      try {
        const k = keyOf((a[0] && a[0].url) || a[0], (a[1] && a[1].body) || '');
        if (k) { const c = r.clone(); c.json().then(j => { window.__icxResp[k] = j; }).catch(() => {}); }
      } catch (e) {}
      return r;
    };
    const OO = XMLHttpRequest.prototype.open, OS = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (m, u, ...r) { this.__u = u; return OO.call(this, m, u, ...r); };
    XMLHttpRequest.prototype.send = function (...a) {
      this.__b = a[0] || '';
      this.addEventListener('load', () => {
        try { const k = keyOf(this.__u, this.__b); if (k) window.__icxResp[k] = JSON.parse(this.responseText); } catch (e) {}
      });
      return OS.apply(this, a);
    };
  }

  // ── site selection ──────────────────────────────────────────────────────────
  window.__selHandle = async function (target) {
    const box = document.querySelector('div.p-multiselect');
    if (!box) return { err: 'NO_MULTISELECT' };
    // Always use the LAST panel in the DOM. PrimeNG can leave earlier panels behind, and a stale
    // one is detached from the live component — clicks on it are silently swallowed. Seen
    // 2026-08-04 with THREE stacked panels: selection appeared to invert and further clicks did
    // nothing at all. If more than one is present, reset the view rather than guessing.
    const panels = () => [...document.querySelectorAll('.p-multiselect-panel')];
    if (panels().length > 1) {
      location.hash = '#/pages/device/basic'; await S(3500);
      location.hash = '#/pages/information'; await S(9000);
    }
    if (!panels().length) { box.click(); await S(900); }
    const panel = () => panels()[panels().length - 1];
    if (!panel()) return { err: 'PANEL_DID_NOT_OPEN' };
    const setF = async v => {
      const f = panel().querySelector('input.p-multiselect-filter'); if (!f) return;
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(f, v);
      f.dispatchEvent(new Event('input', { bubbles: true })); await S(600);
    };
    const items = () => [...panel().querySelectorAll('.p-multiselect-item')];
    await setF(target);
    const tgt = items().find(i => i.innerText.trim().toLowerCase().includes(target.toLowerCase()));
    if (!tgt) return { err: 'TARGET_NOT_IN_LIST' };
    const was = tgt.classList.contains('p-highlight') || tgt.getAttribute('aria-selected') === 'true';
    if (!was) { tgt.click(); await S(900); }
    await setF(''); await S(400);
    for (let k = 0; k < 12; k++) {
      const sel = items().filter(i => (i.classList.contains('p-highlight') || i.getAttribute('aria-selected') === 'true')
                                   && !i.innerText.trim().toLowerCase().includes(target.toLowerCase()));
      if (!sel.length) break;
      sel[0].click(); await S(700);
    }
    return { ok: true, target };
  };

  // ── tick ────────────────────────────────────────────────────────────────────
  window.__buildTickRaw = function (HANDLE, NAME) {
    const R = window.__icxResp;
    const gb = p => {
      const b = R['goodbadboxes-count:' + p];
      if (!b || b.isSuccess === false || !b.data) return null;
      const d = b.data;
      return { good: Number(d.goodBoxCount || 0), warn: Number(d.warningBoxCount || 0),
               bad: Number(d.badBoxCount || 0), upto: d.dateUpto };
    };
    const rssi = gb('RSSI'), disrupt = gb('NET_DISRUPTION_COUNT'),
          reboot = gb('REBOOT_COUNT'), navail = gb('NET_AVAILABILITY');
    const txt = document.body.innerText.replace(/\s+/g, ' ');
    const num = re => { const m = txt.match(re); return m ? Number(m[1].replace(/,/g, '')) : null; };
    const onlineSTB = num(/Online STBs \(Last 15 min\)\s*([\d,]+)/);
    const pmsConn = num(/CONNECTED\s*([\d,]+)/), pmsNot = num(/NOT CONNECTED\s*([\d,]+)/) || 0;
    const upto = (rssi || disrupt || reboot || navail || {}).upto;
    if (!upto) return { err: 'NO_WINDOW' };
    const flat = String(upto).replace(/[-: ]/g, '');
    const compactISO = flat.slice(0, 8) + 'T' + flat.slice(8, 14) + 'Z';
    const uptoISO = String(upto).replace(' ', 'T') + 'Z';
    const v = o => o ? [o.good, o.warn, o.bad].join(',') : ',,';
    const csv = 'mvm_handle,taken_at,online_stbs,pms_connected,pms_not_connected,rssi_good,rssi_warn,'
      + 'rssi_bad,disrupt_good,disrupt_warn,disrupt_bad,reboot_good,reboot_warn,reboot_bad,'
      + 'netavail_good,netavail_warn,netavail_bad\n'
      + [HANDLE, uptoISO, onlineSTB, pmsConn, pmsNot, v(rssi), v(disrupt), v(reboot), v(navail)].join(',') + '\n';
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url; a.download = 'dish-icx-' + HANDLE.toLowerCase() + '-health-' + compactISO + '.csv';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    return { handle: HANDLE, onlineSTB, pmsConn, pmsNot, rssi, disrupt, reboot, navail,
             compactISO, uptoISO,
             siteRow: [HANDLE, NAME, onlineSTB, pmsConn, 0, 0, onlineSTB, pmsConn].join(',') };
  };

  // Guard. Switching sites updates the rendered widgets immediately but leaves the
  // previously captured XHR bodies in place, so a tick can report one site's health
  // buckets under another site's name. Bucket totals must match this site's own online
  // count. Caught a real cross-site contamination on 2026-08-03.
  window.__buildTick = function (h, n) {
    const t = window.__buildTickRaw(h, n);
    if (t && !t.err) {
      const tot = o => o ? (o.good | 0) + (o.warn | 0) + (o.bad | 0) : -1;
      const bad = ['rssi', 'disrupt', 'reboot', 'navail']
        .filter(k => Math.abs(tot(t[k]) - t.onlineSTB) > Math.max(25, t.onlineSTB * 0.02));
      if (bad.length) return { err: 'BUCKETS_FOREIGN', onlineSTB: t.onlineSTB,
                               totals: Object.fromEntries(bad.map(k => [k, tot(t[k])])) };
      // All four buckets must describe the SAME 15-minute window. Seen 2026-08-03: net_stats
      // still served :15 while system_performance_1 had already rolled to :30, producing a tick
      // that measured neither window. The count guard misses this — the totals still agree.
      const ws = [...new Set(['rssi', 'disrupt', 'reboot', 'navail']
        .map(k => t[k] && t[k].upto).filter(Boolean))];
      if (ws.length > 1) return { err: 'WINDOW_STRADDLE', windows: ws };
    }
    return t;
  };

  // ── per-device Online-STBs export ───────────────────────────────────────────
  window.__dlOnline = async function () {
    const cards = [...document.querySelectorAll('*')].filter(e =>
      /Online STBs \(Last 15 min\)/.test(e.innerText || '') && (e.innerText || '').length < 400 && e.children.length);
    if (!cards.length) return 'CARD_NOT_FOUND';
    const box = cards[cards.length - 1].closest('app-box');
    const icon = box && box.querySelector('.menu-icon');
    if (!icon) return 'MENU_ICON_NOT_FOUND';
    icon.click(); await S(1300);
    // visibility check: stale hidden "Download" nodes linger and produce silent
    // false-positive clicks that download nothing.
    const it = [...document.querySelectorAll('.p-menuitem-link,.p-menuitem-text')]
      .filter(e => /download/i.test(e.innerText || '') && VIS(e));
    if (!it.length) return 'NO_VISIBLE_DOWNLOAD_ITEM';
    (it[0].closest('.p-menuitem-link') || it[0]).click();
    return 'DOWNLOAD_CLICKED';
  };

  // ── one site, start to finish; result parked on window for the shell to collect ──
  // The health buckets only populate from net_stats (RSSI/disruption/availability) and
  // system_performance_1 (reboots). The information page alone never produces them.
  window.__runSite = function (handle, name, label) {
    window.__runState = { status: 'running', handle };
    (async () => {
      try {
        for (const k of Object.keys(window.__icxResp)) delete window.__icxResp[k];
        const sel = await window.__selHandle(label);
        if (sel.err) { window.__runState = { status: 'error', handle, err: sel.err }; return; }
        document.body.click();
        location.hash = '#/pages/net_stats'; await S(14000);
        location.hash = '#/pages/system_performance_1'; await S(13000);
        location.hash = '#/pages/information'; await S(12000);
        const t = window.__buildTick(handle, name);
        if (t.err) { window.__runState = { status: 'error', handle, err: t.err, detail: t }; return; }
        const dl = await window.__dlOnline();
        window.__runState = { status: 'done', handle, tick: t, dl };
      } catch (e) {
        window.__runState = { status: 'error', handle, err: String(e) };
      }
    })();
    return 'STARTED';
  };

  window.__health = function () {
    return JSON.stringify({
      hooked: !!window.__respHooked,
      onLogin: !!document.querySelector('input[type=password]'),
      hasSelector: !!document.querySelector('div.p-multiselect'),
      url: location.href.split('#')[0]
    });
  };
})();
'TOOLKIT_OK'
