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

  window.__selStart = label => window.__park('__selRes', window.__selHandle(label));
  window.__dlStart = () => window.__park('__dlRes', window.__dlOnline());

  // The guard that caught a real two-sites-selected contamination on 2026-08-04 (Cliffs left
  // over from the prior sweep). Must read exactly '1 site selected'. Note .p-highlight is the
  // reliable selected-state signal in this build; aria-selected reads null.
  window.__selLabel = function () {
    const l = document.querySelector('.p-multiselect-label');
    return l ? l.innerText.trim() : 'NO_LABEL';
  };

  window.__goInfo = function () {
    document.body.click();
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
