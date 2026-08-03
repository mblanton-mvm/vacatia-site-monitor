# Per-device Online-STBs export from iCX — the working procedure

Verified 2026-08-03 on MVM743 (produced `csvData (65).csv`, 458 rows, Device ID + Room Number).
This is the **per-device** export — the `csvData` shape the monitor needs to refresh rooms and
labels. It is NOT the same as the coarse-poll health CSV, which is site-level counts only.

## Why the earlier attempt failed

A **`.ngx-spinner-overlay`** covers the whole page while the SPA re-fetches after a site switch.
Any click during that window lands on the overlay, not the card. `document.elementFromPoint()`
returns `DIV.ngx-spinner-overlay` when this is happening — that is the tell. **Always wait the
spinner out before clicking anything on the card.**

Also: the `⋮` is a FontAwesome pseudo-element glyph. It is invisible to the accessibility tree and
to `textContent` searches — `find` and glyph-matching both fail on it. Locate it by class.

## Procedure (per site, after the site is selected and the hash-nav has landed on `#/pages/information`)

1. **Wait for the spinner to clear:**

```js
await new Promise(r=>{const t0=Date.now();const iv=setInterval(()=>{
  const sp=document.querySelector('.ngx-spinner-overlay');
  const vis=sp&&getComputedStyle(sp).display!=='none'&&sp.getBoundingClientRect().width>0;
  if(!vis||Date.now()-t0>20000){clearInterval(iv);r();}},400);});
```

2. **Open the Online-STBs card menu** (first gridster item is the Online STBs card):

```js
(()=>{const ic=[...document.querySelectorAll('gridster-item nb-icon.fa-ellipsis-v')][0];
 if(!ic) return 'NO_ELLIPSIS';
 (ic.closest('span.menu-icon')||ic.parentElement).click(); return 'MENU_OPENED';})()
```

3. **Click Download** (menu offers `View Details` and `Download`; it renders under `BODY`, not
   inside the card, so search the whole document):

```js
(()=>{const t=[...document.querySelectorAll('*')]
   .filter(e=>!e.children.length&&/^Download$/i.test((e.textContent||'').trim()))[0];
 if(!t) return 'NO_DOWNLOAD_ITEM'; t.click(); return 'DOWNLOAD_CLICKED';})()
```

4. **Wait ~5s**, then claim the newest `~/Downloads/csvData*.csv`. The browser can only write to
   Downloads root and names them `csvData (N).csv`, so claim by mtime and rename immediately —
   two sites in one sweep would otherwise be indistinguishable.

## Claiming + filing (Bash, per site)

```bash
f=$(ls -t ~/Downloads/csvData*.csv | head -1)
# verify it is the site you expect BEFORE renaming
awk -F, 'NR==2{print $4}' "$f"
mv "$f" ~/Downloads/MVM<N>/icx-online-stbs-MVM<N>-<compactISO>.csv
```

## Cautions

- **Verify the Site Name in the file before renaming.** The download is asynchronous; if it hasn't
  landed yet you will claim the *previous* site's file and mislabel it. Check column 4.
- The export reflects the **currently selected site only** — one download per site per sweep.
- Row count should match the tick's `onlineSTB` count. A mismatch means the download raced the
  site switch.
- Device ID in this file is the **ETHERNET MAC**. Wi-Fi (what mDNS carries) is +1.
