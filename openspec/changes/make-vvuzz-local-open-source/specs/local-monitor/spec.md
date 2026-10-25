## ADDED Requirements

### Requirement: Local ownership
The application SHALL serve only a loopback interface and SHALL store state on the user's machine without hosted accounts or telemetry.

#### Scenario: A browser on another origin attempts a write
- **WHEN** a cross-origin or invalid Host mutation is sent
- **THEN** the application rejects it without changing local state

### Requirement: Historical data preservation
The application SHALL open supplied archives read-only and SHALL keep its own monitor state separate.

#### Scenario: An archived applicant is inspected
- **WHEN** an applicant ID is searched in a supported normalized archive
- **THEN** the application shows matching competitions, original rank, places and consent counts without writing to the archive

### Requirement: Sustainable selected-list monitoring
The application SHALL monitor only explicitly selected supported public lists with bounded responses, timeouts, global rate limiting and backoff. It SHALL retain the last valid list on errors and persist state across restart.

#### Scenario: The source is unavailable or changes schema
- **WHEN** the source returns an error, block page or unrecognized JSON
- **THEN** the last valid list remains available and the UI exposes the failed update and freshness

### Requirement: Honest public release
The project SHALL distinguish software licensing, dataset provenance, observed tests and unverified claims. Public source history and data SHALL pass publication review before disclosure.

#### Scenario: A collected database contains runtime accounts
- **WHEN** a dataset is prepared for publication
- **THEN** only reviewed admissions fields may be exported, runtime tables remain private, and original files remain intact

### Requirement: Desktop distribution
The project SHALL provide Windows and Linux tray builds, local browser UI and explicit per-user startup support as release 0.0.1.

#### Scenario: A user closes the browser
- **WHEN** the browser tab is closed
- **THEN** the monitor continues until the user quits the process or operating system ends it
