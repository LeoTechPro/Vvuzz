## Why

Owner direction, 2026-10-25: retire the hosted-platform product in favour of a free local Go application for applicants and researchers. Preserve the summer collection; publish safe source and verified distributables as one 0.0.1 release, with an intdata.pro/vvuzz landing page.

Specification level: full. Triggers: public repository visibility, access boundary replacement, multi-platform release and cross-repository landing/publication. Legacy databases and unrelated runtime remain protected.

## What Changes

- A loopback-only Go process with an embedded responsive UI, Windows/Linux tray, read-only legacy archive support, selected-list monitoring, persisted state and bounded change history.
- No hosted accounts, server ingest or remote database in the new default runtime.
- One explicit 0.0.1 release with Windows and Linux installation packages, checksums and reproducible build workflow.
- A source/data publication audit and a provenance manifest that distinguishes original data from our code license.
- A public landing page in the owning web repository, subject to its independent Git and deployment rules.

## Capabilities

### New Capabilities
- `local-monitor`: local admissions research and monitoring without a hosted platform.

## Impact

Go runtime, embedded UI, documentation, packaging and release workflow. Existing Python tools and historical databases remain preserved; they cease to be the supported application entrypoint. No live service shutdown, data deletion, history rewrite, backup or credential change is authorized by implementation.
