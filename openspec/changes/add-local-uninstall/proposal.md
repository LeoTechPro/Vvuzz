## Why

Users need a clear way to stop and uninstall the local application without accidentally losing their archives.

## What Changes

- Add an uninstall action to the tray and settings, with an explicit choice to keep or delete local state.
- Open the local interface on a Windows tray double click.
- Stop and close the application before deleting its state. Keep historical archives and unrelated files.

## Capabilities

### New Capabilities

- `local-uninstall`: User-confirmed removal and state retention.

### Modified Capabilities

## Impact

Windows installer, Linux package, tray, local HTTP handler, web UI and local state.
