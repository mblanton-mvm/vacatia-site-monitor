// DOM-only iCX capture. Companion to icx-toolkit.js — inject that one FIRST (this file reuses
// its __selHandle and __dlOnline, both of which are pure DOM and work fine).
//
// WHY THIS EXISTS. Chrome's AppleScript `execute javascript` runs in an ISOLATED WORLD: same
// DOM, different JS context. The page's own fetch/XHR are therefore unhookable from here, so
// icx-toolkit.js's __icxResp capture stays empty and __buildTickRaw dies at
// `if (!upto) return {err:'NO_WINDOW'}` — forever, on every site, no matter what. That is what
// the unattended poller had been doing every 15 minutes since 20:47Z on 2026-08-04 while still
// republishing the page off stale counts. Proven read from the live tab:
//   {"ng":"undefined","zone":"undefined","webpack":"undefined","pageGlobals":[],"myMarker":"boolean"}
// See docs/session-handoffs/vacatia-icx-polling-2026-08-04-evening.md §2-3 and memory
// `reference-applescript-js-isolated-world`.
//
// So: read RENDERED TEXT, which is shared, and take the window from the per-device export's own
// row-2 timestamp instead of the XHR payload's dateUpto.
(function () {
  // Fire-and-park. AppleScript cannot serialise a Promise, so every async op stores its result
  // on window and the shell polls for it with real bash sleeps — those are immune to Chrome's
  // background-tab timer throttling in a way in-page setTimeout is not.
  window.__park = function (key, p) {
    window[key] = 'PENDING';
    Promise.resolve(p)
      .then(r => { window[key] = (typeof r === 'string') ? r : JSON.stringify(r); })
      .catch(e => { window[key] = 'ERR ' + String(e); });
    return 'STARTED';
  };

  const S = ms => new Promise(r => setTimeout(r, ms));
  const VIS = e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0 &&
           getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  };

  const labelText = () => {
    const l = document.querySelector('.p-multiselect-label');
    return l ? l.innerText.trim() : 'NO_LABEL';
  };
  // The label is the ONLY trustworthy read of how many sites the component thinks are selected.
  const count = () => {
    const s = labelText();
    if (/choose/i.test(s)) return 0;
    const m = s.match(/(\d+)\s*site/i);
    return m ? Number(m[1]) : -1;
  };

  // Replaces icx-toolkit.js's __selHandle on this path.
  //
  // WHY NOT __selHandle: it reuses whatever .p-multiselect-panel is already in the DOM, even a
  // CLOSED one. A closed panel is detached from the live component — its clicks are silently
  // swallowed AND its .p-highlight classes go stale. On 2026-08-04 23:00Z that made it read
  // MVM743 as already-selected (so it skipped the click), then deselect MVM784, leaving ZERO
  // sites and a label reading 'Choose filters'; MVM784's turn then died PANEL_DID_NOT_OPEN.
  //
  // So: insist on a VISIBLE panel, and drive off the label count rather than the classes.
  window.__selectOnly = async function (target) {
    const t = target.toLowerCase();
    const panels = () => [...document.querySelectorAll('.p-multiselect-panel')].filter(VIS);
    const hit = i => i.innerText.trim().toLowerCase().includes(t);

    let lastErr = '';
    for (let attempt = 1; attempt <= 3; attempt++) {
     // The panel can close under us at any moment — another actor clicking the page, a stray
     // document.body.click() from the export step, a re-render. Then panels() empties and p()
     // is undefined. Catch per-attempt and retry rather than rejecting the whole promise.
     try {
      // PrimeNG can leave earlier panels stacked; a reset is cheaper than guessing which is live.
      if (panels().length > 1) {
        location.hash = '#/pages/device/basic'; await S(3500);
        location.hash = '#/pages/information'; await S(9000);
      }
      if (!panels().length) {
        const box = document.querySelector('div.p-multiselect');
        if (!box) return { err: 'NO_MULTISELECT' };
        box.click(); await S(1400);
      }
      if (!panels().length) { await S(1600); }
      if (!panels().length) continue;

      const p = () => panels()[panels().length - 1];
      const items = () => [...p().querySelectorAll('.p-multiselect-item')];
      const setF = async v => {
        const f = p().querySelector('input.p-multiselect-filter');
        if (!f) return;
        Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(f, v);
        f.dispatchEvent(new Event('input', { bubbles: true }));
        await S(750);
      };

      // 1. filter down and select the target. A click TOGGLES, so confirm via the count which
      //    way it went instead of trusting the pre-click class.
      await setF(target);
      const tgt = items().find(hit);
      if (!tgt) { await setF(''); continue; }
      const before = count();
      tgt.click(); await S(1100);
      if (count() < before) { tgt.click(); await S(1100); }   // we had toggled it OFF

      // 2. clear the filter and drop every other selection, until the count says one.
      await setF(''); await S(600);
      let stuck = false;
      for (let k = 0; k < 25 && count() > 1; k++) {
        const other = items().find(i => i.classList.contains('p-highlight') && !hit(i));
        if (!other) { stuck = true; break; }   // classes disagree with the count — start over
        other.click(); await S(750);
      }
      if (stuck) continue;

      // 3. verify: exactly one, and it is ours.
      const mine = items().find(hit);
      if (count() === 1 && mine && mine.classList.contains('p-highlight')) {
        return { ok: true, target, attempt };
      }
     } catch (e) {
       lastErr = String(e);
       await S(2000);
     }
    }
    return { err: 'SELECT_UNVERIFIED', label: labelText(), lastErr };
  };

  // The export is TWO shell-driven steps with a real bash sleep between them, not one function
  // with an in-page await.
  //
  // WHY: .menu-icon TOGGLES. icx-toolkit.js's __dlOnline clicks the icon, awaits 1300ms, then
  // clicks Download — but if a menu was already open (a previous attempt, a prior card) that
  // first click CLOSES it, and the global .p-menuitem-link query then hits some other card's
  // stale menu. It reports DOWNLOAD_CLICKED and no file ever arrives. That is the MVM743
  // "stuck kebab", reproduced and fixed 2026-08-04 23:06Z: opening the menu and clicking
  // Download in two separate osascript calls downloaded all 461 rows first try.
  window.__openMenu = function () {
    document.body.click();   // dismiss anything already open, so the icon click OPENS
    const cards = [...document.querySelectorAll('*')].filter(e =>
      /Online STBs \(Last 15 min\)/.test(e.innerText || '') &&
      (e.innerText || '').length < 400 && e.children.length);
    if (!cards.length) return 'CARD_NOT_FOUND';
    const box = cards[cards.length - 1].closest('app-box');
    const icon = box && box.querySelector('.menu-icon');
    if (!icon) return 'MENU_ICON_NOT_FOUND';
    icon.click();
    return 'MENU_OPENED';
  };

  const dlItems = () => [...document.querySelectorAll('.p-menuitem-link')]
    .filter(e => /download/i.test(e.innerText || '') && VIS(e));

  window.__menuCount = () => String(dlItems().length);

  // PrimeNG closes overlays on a real Escape keydown; document.body.click() often does not.
  window.__closeMenus = function () {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', keyCode: 27, bubbles: true }));
    document.body.click();
    return String(dlItems().length);
  };

  // Stale HIDDEN Download nodes linger and click silently, so filter on rendered geometry, and
  // click the LINK (not the inner text span) because that is what carries the handler.
  //
  // REFUSE when more than one is visible instead of guessing. Stacked overlays mean the page is
  // polluted, and BOTH the first and the last candidate were proven dead at 23:38Z with six
  // stacked menus — a click reports success and no file arrives. The caller must reload
  // (prepare_page) rather than click into a wedged DOM.
  window.__clickDownload = function () {
    const c = dlItems();
    if (!c.length) return 'NO_VISIBLE_DOWNLOAD_ITEM';
    if (c.length > 1) return 'TOO_MANY_MENUS n=' + c.length;
    c[0].click();
    return 'DOWNLOAD_CLICKED n=1';
  };

  // ── health buckets ──────────────────────────────────────────────────────────
  // The four good/warn/bad triples live on two other pages and are read as TEXT, same as the
  // presence widget. Verified rendering 2026-08-05 03:22Z:
  //   #/pages/net_stats           -> "RSSI STB Count (Last 15 min) 4219 133 1"
  //                                  "INTERNET DISRUPTION STB Count (Last 15 min) 4329 11 13"
  //                                  "INTERNET AVAILABILITY STB Count (Last 15 min) 4336 1 2"
  //   #/pages/system_performance_1 -> "CPU UTILISATION ... 4187 143 0"
  //                                  "CPU TEMPERATURE ... 0 4353 0"
  //                                  "REBOOT COUNT STB Count (Last 15 min) 4352 1 0"
  // Thresholds (from the old API payload): reboot warn = 1-2 in the window, bad = 3+.
  const METRICS = {
    rssi: 'RSSI',
    disrupt: 'INTERNET DISRUPTION',
    netavail: 'INTERNET AVAILABILITY',
    cpu: 'CPU UTILISATION',
    cputemp: 'CPU TEMPERATURE',
    reboot: 'REBOOT COUNT'
  };

  window.__goPage = function (page) {
    document.body.click();
    location.hash = '#/pages/' + page;
    return 'NAV ' + page;
  };

  window.__readBuckets = function () {
    const txt = document.body.innerText.replace(/\s+/g, ' ');
    const out = {};
    for (const [key, label] of Object.entries(METRICS)) {
      const re = new RegExp(label + '\\s*STB Count \\(Last 15 min\\)\\s*([\\d,]+)\\s+([\\d,]+)\\s+([\\d,]+)');
      const m = txt.match(re);
      if (m) out[key] = m.slice(1, 4).map(v => Number(v.replace(/,/g, '')));
    }
    return JSON.stringify(out);
  };

  // ── per-device anomaly export ───────────────────────────────────────────────
  // Each card on these pages has its own export kebab, which is how the WHICH-BOXES data is
  // obtained (columns: Device ID, Average Net Disruption Count, Timestamp, Site, Room Number).
  // The nine app-box elements are in document order, three per metric:
  //   net_stats            0-2 RSSI | 3-5 INTERNET DISRUPTION | 6-8 INTERNET AVAILABILITY
  //   system_performance_1 0-2 CPU  | 3-5 CPU TEMPERATURE      | 6-8 REBOOT COUNT
  // Index alone would silently export the wrong metric if the layout ever changed, so the
  // caller passes the number it EXPECTS to find in that box and we refuse on a mismatch.
  window.__openMenuIdx = function (idx, expect) {
    document.body.click();
    const boxes = [...document.querySelectorAll('app-box')];
    if (boxes.length < 9) return 'ONLY_' + boxes.length + '_BOXES';
    const box = boxes[idx];
    if (!box) return 'NO_BOX_AT_' + idx;
    const shown = (box.innerText || '').replace(/[\s,]/g, '');
    if (String(expect) !== shown) return 'BOX_MISMATCH idx=' + idx + ' expected=' + expect + ' shows=' + shown;
    const icon = box.querySelector('.menu-icon');
    if (!icon) return 'NO_KEBAB idx=' + idx;   // zero-count cards have no export
    icon.click();
    return 'MENU_OPENED';
  };

  window.__selStart = label => window.__park('__selRes', window.__selectOnly(label));

  // The guard that caught a real two-sites-selected contamination on 2026-08-04 (Cliffs left
  // over from the prior sweep). Must read exactly '1 site selected'. Note .p-highlight is the
  // reliable selected-state signal in this build; aria-selected reads null.
  window.__selLabel = function () {
    const l = document.querySelector('.p-multiselect-label');
    return l ? l.innerText.trim() : 'NO_LABEL';
  };

  // Setting location.hash to the page we are ALREADY on is a no-op, so the widgets keep showing
  // the previous site's numbers with nothing to signal the staleness. Bounce via another route
  // to force the re-render.
  window.__goInfo = function () {
    document.body.click();
    if (location.hash.indexOf('information') >= 0) {
      location.hash = '#/pages/device/basic';
      setTimeout(() => { location.hash = '#/pages/information'; }, 2500);
      return 'NAV_BOUNCED';
    }
    location.hash = '#/pages/information';
    return 'NAV';
  };

  // Text order on this page is '... CONNECTED 780 NOT CONNECTED 0 ...', so the plain
  // /CONNECTED/ match lands on the connected figure, not inside 'NOT CONNECTED'. Verified
  // against the rendered page 2026-08-04 22:57Z.
  window.__domRead = function () {
    const txt = document.body.innerText.replace(/\s+/g, ' ');
    const num = re => { const m = txt.match(re); return m ? Number(m[1].replace(/,/g, '')) : null; };
    return JSON.stringify({
      stb: num(/Online STBs \(Last 15 min\)\s*([\d,]+)/),
      conn: num(/CONNECTED\s*([\d,]+)/),
      notconn: num(/NOT CONNECTED\s*([\d,]+)/),
      sel: window.__selLabel()
    });
  };
})();
'DOM_OK'
