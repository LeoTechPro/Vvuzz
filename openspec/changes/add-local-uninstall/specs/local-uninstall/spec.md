## ADDED Requirements

### Requirement: Confirmed uninstall with data choice
The application SHALL offer uninstall from the tray and web settings and require an explicit choice between retaining local state and deleting it.

#### Scenario: Preserve data
- **WHEN** the user confirms uninstall with the keep-data option
- **THEN** local monitoring state remains available for a later installation.

#### Scenario: Delete local state
- **WHEN** the user confirms uninstall with the delete-data option
- **THEN** only the application-owned monitor database and its SQLite sidecars are eligible for removal after the process stops, and connected archives remain untouched.

#### Scenario: State in use
- **WHEN** another instance is using the data directory
- **THEN** state removal fails without deleting that data.

### Requirement: Open from Windows tray
The application SHALL open the local web interface on double left click of the Windows tray icon.

#### Scenario: Double-click
- **WHEN** the user double-clicks the tray icon
- **THEN** the local web interface opens in the default browser.
