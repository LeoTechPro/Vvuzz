## Context

The existing prototype has normalized SQLite archives and Python parsers. Existing archives may contain application users and ingest state; publishing a whole SQLite file is not equivalent to publishing admissions facts. The repository is private at intake, and its history must be reviewed before visibility changes.

## Decisions

- Use modernc SQLite for a separate per-user state database. Open historical SQLite with mode=ro and query_only; never migrate or write a supplied archive.
- Serve embedded static assets only on 127.0.0.1. Validate Host, Origin and a custom request header for mutations; no cross-origin API and no hosted identity model.
- Serialize source fetches with a conservative global interval, request/response bounds, timeouts, conditional requests and backoff. No CAPTCHA or authentication bypass, proxy rotation or automatic competition enumeration.
- Persist only selected watches, latest normalized list and bounded change history. Retain last good list on HTTP/schema failures and expose errors/freshness.
- A new desktop installation can start without an archive. Historical data is optional and explicit; demonstrate UI using synthetic fixtures only.
- MIT applies to original software only. Preserve provenance and do not claim third-party datasets are ours to relicense. Publish only after source and data review; unknown rights are recorded as unknown.
- Use native systray library and OS per-user installation/autostart mechanisms. Headless Linux remains available as an explicitly built variant.

## Risks and Mitigations

- Months of operation cannot be proven by a short test: test restart persistence, backoff and bounded state, state the observation duration honestly.
- Historical endpoint formats and seasonal availability can change: strict schema checks prevent successful replacement by an error page.
- Public Git history is an irreversible disclosure boundary: independent review before making the existing repo public; do not rewrite history without permission.
- Existing Python platform remains as historical source in the release rather than being deleted with its data.

## Delivery

Source, packaged artifacts, public visibility, dataset export, landing deployment and visual closure are independently evidenced. Only v0.0.1 is an intended release. Existing installs/data are never removed by an uninstaller.
