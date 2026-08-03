#!/usr/bin/env python3
"""Pull the latest htvc_mdns_stbs capture for a site from staging and export it to CSV.

READ-ONLY. One SELECT for the row header, one for HEX(payload). No LIKE, no REGEXP:
the payload column is binary-collated, so it is hex-transported and parsed in Python.

This is the CASTING REGISTRY (what guest cast menus are built from). It is NOT
htvc_stb_monitoring — that surface has stb_id + power state. Never join rows across them
here; this script deliberately does no cross-surface enrichment.

Usage:
    export STAGING_MYSQL_URL='mysql://user:pass@host:port/dbname'
    python3 pull_mdns_registry.py [MVM784] [outdir]

Requires only the `mysql` CLI (no Python driver needed).
"""
import csv
import json
import os
import subprocess
import sys
from urllib.parse import urlparse, unquote

KINDS = ("htvc_mdns_stbs", "htvc_stb_monitoring", "htvc_guest_data")
SITES = ("MVM743", "MVM783", "MVM784")
COLS = ["id", "ip", "mac", "room"]


def mysql_args(url):
    # Diagnose without ever echoing the credential itself.
    if not url.strip():
        sys.exit("STAGING_MYSQL_URL is EMPTY. Nothing was captured into it — if you used "
                 "`read`, stdin gave no input; try: export STAGING_MYSQL_URL=\"$(pbpaste)\"")
    if url.strip().startswith("<") or " " in url.strip():
        sys.exit(f"STAGING_MYSQL_URL looks like placeholder text, not a URL "
                 f"({len(url)} chars, starts {url.strip()[:1]!r}). Substitute the real "
                 "mysql://... string from Railway.")
    u = urlparse(url.strip())
    if u.scheme not in ("mysql", "mysql2"):
        sys.exit(f"expected a mysql:// URL, got scheme {u.scheme!r} "
                 f"(value is {len(url.strip())} chars long)")
    if not u.hostname:
        sys.exit("URL parsed but has no host — check it was copied whole.")
    args = ["mysql", "--batch", "--raw", "--skip-column-names",
            "-h", u.hostname, "-P", str(u.port or 3306),
            "-u", unquote(u.username or "")]
    if u.password:
        args.append(f"-p{unquote(u.password)}")
    args.append((u.path or "/").lstrip("/") or "railway")
    return args


def query(url, sql):
    p = subprocess.run(mysql_args(url) + ["-e", sql],
                       capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit("mysql failed:\n" + p.stderr.strip())
    return p.stdout


def is_randomised(mac):
    """Locally-administered bit on the first octet. Never an OUI allowlist —
    an allowlist previously produced a false 'Cliffs has 0 real boxes'."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except (ValueError, IndexError, AttributeError):
        return None


def main():
    url = os.environ.get("STAGING_MYSQL_URL") or os.environ.get("MYSQL_PUBLIC_URL")
    if not url:
        sys.exit("set STAGING_MYSQL_URL (the Railway MYSQL_PUBLIC_URL) first")
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    sites = [a for a in args if a.upper().startswith("MVM")] or list(SITES)
    outdir = next((a for a in args if not a.upper().startswith("MVM")), ".")

    for site in sites:
        for kind in KINDS:
            one(url, site, kind, outdir)
    return


def one(url, site, kind, outdir):
    head = query(url, f"""
        SELECT id, captured_at FROM staging_site_captures
        WHERE site_code='{site}' AND kind='{kind}'
        ORDER BY id DESC LIMIT 1;
    """).strip()
    if not head:
        print(f"  {site} {kind}: NO CAPTURE FOUND")
        return
    cap_id, captured_at = head.split("\t", 1)
    print(f"  {site} {kind}: id={cap_id} captured_at={captured_at}")

    # TO_BASE64, not raw, and never LIKE: the payload column is binary-collated (per
    # docs/vacatia/mdns-registry-self-serve.md) and mangles through the mysql client.
    import base64
    b64 = query(url, f"SELECT TO_BASE64(payload) FROM staging_site_captures WHERE id={cap_id};")
    raw = base64.b64decode("".join(b64.split()))
    rows = json.loads(raw.decode("utf-8", errors="strict"))
    if isinstance(rows, dict):
        rows = next((v for v in rows.values() if isinstance(v, list)), [])
    if not isinstance(rows, list) or not rows:
        print(f"     payload not a non-empty array — skipped")
        return
    cols = sorted({k for r in rows if isinstance(r, dict) for k in r})

    # capture time goes in the FILENAME — freshness must never be inferred from mtime
    stamp = (captured_at.replace(" ", "T").replace(":", "").replace("-", "")[:13] + "Z")
    pref = "reg-" if kind == "htvc_mdns_stbs" else f"{kind}-"
    out = os.path.join(outdir, f"{pref}{site}-{stamp}.csv")
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols + (["mac_is_randomised"] if "mac" in cols else []))
        for r in rows:
            base = [r.get(c) for c in cols]
            w.writerow(base + ([is_randomised(r.get("mac"))] if "mac" in cols else []))

    if "mac" in cols:
        rand = sum(1 for r in rows if is_randomised(r.get("mac")))
        print(f"     {len(rows)} rows ({rand} randomised / {len(rows)-rand} real) -> {out}")
    else:
        print(f"     {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
