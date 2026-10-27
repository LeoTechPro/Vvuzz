## Context

The app stores its monitor state separately from read-only archives. Windows uses Inno Setup; Linux uses a deb package or a portable binary.

## Goals / Non-Goals

Offer two confirmed removal options without recursively deleting directories or touching archives. Keep data by default. Prevent removal when another instance holds the state lock. Do not silently modify the installed application while testing.

## Decisions

The existing local API boundary protects the uninstall action. Windows delegates installation removal to the Inno uninstaller and waits for the app process. Linux delegates deb removal to the system package manager with polkit. Only fixed monitor database files are eligible for data removal. Unsupported portable uninstall must report the limitation.

## Risks / Trade-offs

The desktop environment determines Linux tray activation. Windows tray implementation uses the pinned systray event and must be rechecked on upgrades. Visual and installation checks require isolated environments.
