# Vacatia Site Monitor — build pipeline

Generates the page published at https://mblanton-mvm.github.io/vacatia-site-monitor/

Lived in a `/private/tmp` session scratchpad until 2026-08-03; moved here because macOS
purges that directory and the whole pipeline would have gone with it.

## Order

    build_roster.py rooms roster.json     # units/TVs from the authoritative room lists
    refresh.sh                            # punch pull -> merge -> lockouts -> page

`refresh.sh` does the whole cycle: re-pulls `punch_rooms` from Supabase for all three sites,
reads the newest `reg-MVM<site>-<stamp>.csv`, rebuilds `rooms-state.json` + `lockouts.json`,
regenerates the page, and runs a PII gate that ABORTS if a guest name reaches the HTML.

## Publishing

    cp artifact.html ~/Developer/vacatia-site-monitor/index.html
    cd ~/Developer/vacatia-site-monitor && git add -A && git commit -m "…" && git push

## Hard-won rules (do not relearn these)

- **Roster is truth.** Never derive rooms by grouping observed labels — that sweeps in lockout
  parents (606/1405/1504/1511), common-area TVs and junk, and gives 402 units instead of 395.
- **Label separators differ per site.** `@` at MVM783/784, a SPACE at MVM743 (`1003 B2`).
  At MVM743 a 1-bedroom labelled `@B1` is CORRECT, not an error.
- **MVM743 punch `room_id` carries a building letter** (`A1001`) that 783/784 do not. Strip it or
  the join silently returns nothing.
- **MVM783 never writes `doneAt`.** "Swept" = doneAt OR all four tech fields set.
- **iCX = Ethernet MAC; mDNS registry + RMMS hardwareId = Wi-Fi = Ethernet+1.** Join
  `registry[icx+1]`. Never a blind ±1.
- **Randomised MAC = locally-administered bit**, never an OUI prefix list.
- **iCX CSV timestamps are EASTERN wall clock**, not UTC. Registry captured_at and punch doneAt
  ARE UTC. Do not compare them without converting — filenames use `ET` for this reason.
- **Capture age goes in the FILENAME**, never inferred from mtime.
- **Registry presence != castable.** It is necessary, not sufficient.
- **RSSI "anomaly" means TOO STRONG** (−25 to −34 dBm), not weak. Measured 2026-08-03.
