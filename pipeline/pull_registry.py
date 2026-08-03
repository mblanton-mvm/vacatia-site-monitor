#!/usr/bin/env python3
"""Pull the latest htvc_mdns_stbs casting registry for every Vacatia site from staging.

READ-ONLY: two SELECTs per site against `staging_site_captures`, nothing else. The staging user
(`mvm_readonly`) is granted USAGE + SELECT on that one table and INSERT is denied server-side, so a
bug here cannot damage staging.

Writes `reg-MVM<site>-<YYYYMMDDTHHMMZ>.csv` — the name the sitemon build already consumes, with the
capture stamp IN THE FILENAME because that is where merge_rooms/build_lockouts read freshness from
(never the mtime).

Credential: macOS keychain service `mvm-staging-mysql-url` (or $STAGING_MYSQL_URL). Never printed,
never passed in argv — the password goes to the client through MYSQL_PWD so it stays out of `ps`.

Three traps, all load-bearing and all learned the hard way (docs/vacatia/mdns-registry-self-serve.md):
  1. The staging user authenticates with caching_sha2_password, so the connection MUST be TLS or the
     server answers "Authentication requires secure connection" and it reads like a bad password.
     --ssl-mode=REQUIRED makes that explicit rather than relying on the client default.
  2. `payload` is binary-collated: parse it in Python, never with SQL LIKE (silently under-matches),
     and hex-transport it or the raw JSON mangles through the CLI.
  3. A randomized MAC is the locally-administered bit, NOT an OUI prefix list. Filtering on
     02/06/0a/0e misses 82:/ba: and undercounted MVM784 as 27 when the truth is 376.

Usage: python3 pull_registry.py [outdir] [SITE ...]
"""
import csv
import datetime
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlparse, unquote, quote

KIND = 'htvc_mdns_stbs'
SITES = ['MVM784', 'MVM783', 'MVM743']
KEYCHAIN_SERVICE = 'mvm-staging-mysql-url'
SITE_RE = re.compile(r'^MVM\d{3,}$')          # nothing unvalidated reaches the SQL text
STALE_HOURS = 2.0


def _keychain(service):
    p = subprocess.run(['security', 'find-generic-password', '-s', service, '-w'],
                       capture_output=True, text=True)
    v = p.stdout.strip() if p.returncode == 0 else ''
    # placeholder text pasted verbatim out of an instruction is a real failure mode — it happened
    # with '<host>:<port>' — and it must not be mistaken for a value
    if v and ('<' in v or '>' in v):
        sys.exit(f"keychain {service!r} contains PLACEHOLDER text, not a value "
                 f"({len(v)} chars, includes angle brackets). Overwrite it — note -U, the entry "
                 f"already exists:\n  security add-generic-password -a \"$USER\" -U -s {service} -w")
    return v


def credential():
    """Accepts either a whole mysql:// URL or just the password.

    The password manager hands over the fields separately, so what lands in the keychain is often
    the password alone. In that case the non-secret half (host:port) is read from
    `mvm-staging-mysql-host` / $STAGING_MYSQL_HOST and the rest is Jarran's documented default:
    user mvm_readonly, database railway.
    """
    raw = (os.environ.get('STAGING_MYSQL_URL') or os.environ.get('MYSQL_PUBLIC_URL')
           or _keychain(KEYCHAIN_SERVICE))
    if not raw:
        sys.exit(f"no credential: keychain service {KEYCHAIN_SERVICE!r} not found and "
                 "$STAGING_MYSQL_URL unset.\n  store it with: "
                 f'security add-generic-password -a "$USER" -U -s {KEYCHAIN_SERVICE} -w')

    if '://' in raw:
        u = urlparse(raw.strip())
        if u.scheme not in ('mysql', 'mysql2') or not u.hostname:
            sys.exit(f"expected a mysql:// URL with a host, got scheme {u.scheme!r} "
                     f"({len(raw.strip())} chars) — value not echoed")
        return u

    # bare password -> assemble around the non-secret host:port
    hostport = (os.environ.get('STAGING_MYSQL_HOST') or _keychain('mvm-staging-mysql-host')).strip()
    if not hostport:
        sys.exit(
            f"the keychain holds a {len(raw)}-char value with no '://' — that is the PASSWORD, not a\n"
            "connection string, so the host and port are still missing. Either:\n"
            "  a) store the whole URL (note -U, the entry already exists):\n"
            f'       security add-generic-password -a "$USER" -U -s {KEYCHAIN_SERVICE} -w\n'
            '     then paste  mysql://mvm_readonly:<password>@<host>:<port>/railway\n'
            "  b) or keep the password where it is and add just the host:port, which is not secret:\n"
            '       security add-generic-password -a "$USER" -s mvm-staging-mysql-host '
            "-w '<host>:<port>'")
    host, _, port = hostport.partition(':')
    return urlparse(f"mysql://{os.environ.get('STAGING_MYSQL_USER', 'mvm_readonly')}:"
                    f"{quote(raw, safe='')}@{host}:{port or '3306'}/"
                    f"{os.environ.get('STAGING_MYSQL_DB', 'railway')}")


def query(u, sql):
    args = ['mysql', '--batch', '--raw', '--skip-column-names',
            '--ssl-mode=REQUIRED',                      # trap 1
            '-h', u.hostname, '-P', str(u.port or 3306),
            '-u', unquote(u.username or ''),
            (u.path or '/').lstrip('/') or 'railway']
    env = dict(os.environ)
    if u.password:
        env['MYSQL_PWD'] = unquote(u.password)          # keeps it out of argv/ps
    p = subprocess.run(args + ['-e', sql], capture_output=True, text=True, env=env)
    if p.returncode != 0:
        err = p.stderr.strip()
        if 'secure connection' in err.lower():
            err += ('\n  -> TLS was not negotiated. caching_sha2_password requires it; this script '
                    'passes --ssl-mode=REQUIRED, so check the client build supports TLS.')
        sys.exit('mysql failed:\n' + err)
    return p.stdout


def is_randomized(mac):
    """Locally-administered bit on the first octet — never an OUI allowlist (trap 3)."""
    try:
        return bool(int(str(mac).split(':')[0], 16) & 0x02)
    except (ValueError, IndexError, AttributeError):
        return None


def pull(u, site, outdir, now):
    head = query(u, "SELECT id, captured_at FROM staging_site_captures "
                    f"WHERE site_code='{site}' AND kind='{KIND}' "
                    "ORDER BY id DESC LIMIT 1;").strip()
    if not head:
        print(f"  {site}: NO {KIND} capture in staging")
        return None
    cap_id, captured_at = head.split('\t', 1)
    hexed = query(u, f"SELECT HEX(payload) FROM staging_site_captures WHERE id={int(cap_id)};")
    rows = json.loads(bytes.fromhex(''.join(hexed.split())).decode('utf-8'))   # trap 2
    if not isinstance(rows, list):
        sys.exit(f"{site}: payload is {type(rows).__name__}, expected a JSON array")

    cap = datetime.datetime.strptime(captured_at[:19], '%Y-%m-%d %H:%M:%S').replace(
        tzinfo=datetime.timezone.utc)
    stamp = cap.strftime('%Y%m%dT%H%MZ')
    out = os.path.join(outdir, f'reg-{site}-{stamp}.csv')
    with open(out, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['mac', 'room', 'ip', 'stb_id', 'mac_is_randomized'])
        for r in rows:
            w.writerow([r.get('mac'), r.get('room'), r.get('ip'), r.get('id'),
                        is_randomized(r.get('mac'))])
    rand = sum(1 for r in rows if is_randomized(r.get('mac')))
    age = (now - cap).total_seconds() / 3600
    flag = '  <-- STALE, the collector on this appliance is behind' if age > STALE_HOURS else ''
    print(f"  {site}: {len(rows):>5} rows ({len(rows) - rand} real / {rand} randomized) "
          f"captured {cap.strftime('%Y-%m-%d %H:%MZ')}, {age:.1f} h old{flag}")
    print(f"         -> {os.path.basename(out)}")
    return out


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else '.'
    sites = [s.upper() for s in sys.argv[2:]] or SITES
    bad = [s for s in sites if not SITE_RE.match(s)]
    if bad:
        sys.exit(f"malformed handle(s): {bad} — expected MVM followed by 3+ digits")
    u = credential()
    now = datetime.datetime.now(datetime.timezone.utc)
    print(f"staging {u.hostname}:{u.port or 3306} · read-only · TLS required")
    for site in sites:
        pull(u, site, outdir, now)


if __name__ == '__main__':
    main()
