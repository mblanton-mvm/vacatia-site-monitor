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

  // Stale HIDDEN Download nodes linger in the DOM and click silently, so filter on real
  // rendered geometry. Clicking the LINK (not the inner text span) is what carries the handler.
  window.__clickDownload = function () {
    const c = [...document.querySelectorAll('.p-menuitem-link')]
      .filter(e => /download/i.test(e.innerText || '') && VIS(e));
    if (!c.length) return 'NO_VISIBLE_DOWNLOAD_ITEM';
    c[0].click();
    return 'DOWNLOAD_CLICKED n=' + c.length;
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
