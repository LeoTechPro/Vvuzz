#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
out=${1:?usage: package.sh absolute-output-directory}
case "$out" in /*) ;; *) echo 'output must be absolute' >&2; exit 1;; esac
mkdir -p "$out"
stage="$out/deb-root"
if [ -e "$stage" ]; then echo 'deb-root already exists; use a fresh output directory' >&2; exit 1; fi
mkdir -p "$stage/DEBIAN" "$stage/usr/bin" "$stage/usr/share/applications" "$stage/usr/share/icons/hicolor/128x128/apps" "$stage/usr/share/doc/vvuzz" "$stage/usr/lib/systemd/user"
cd "$repo"
go build -trimpath -ldflags='-s -w' -o "$stage/usr/bin/vvuzz" ./cmd/vvuzz
go build -tags headless -trimpath -ldflags='-s -w' -o "$out/vvuzz-0.0.1-linux-amd64-headless" ./cmd/vvuzz
install -m 644 packaging/linux/vvuzz.desktop "$stage/usr/share/applications/"
install -m 644 packaging/linux/vvuzz.service "$stage/usr/lib/systemd/user/"
install -m 644 cmd/vvuzz/icon.png "$stage/usr/share/icons/hicolor/128x128/apps/vvuzz.png"
install -m 644 LICENSE "$stage/usr/share/doc/vvuzz/copyright"
cp -R packaging/licenses "$stage/usr/share/doc/vvuzz/licenses"
cat > "$stage/DEBIAN/control" <<'EOF'
Package: vvuzz
Version: 0.0.1
Section: education
Priority: optional
Architecture: amd64
Depends: libgtk-3-0, libayatana-appindicator3-1
Maintainer: intData
Homepage: https://intdata.pro/vvuzz/
Description: Local admissions list monitor with a web UI and desktop tray
 Tracks selected public admission lists and keeps data locally.
EOF
dpkg-deb --root-owner-group --build "$stage" "$out/vvuzz-0.0.1-linux-amd64.deb"
tar -czf "$out/vvuzz-0.0.1-linux-amd64.tar.gz" -C "$stage/usr/bin" vvuzz -C "$repo" LICENSE packaging/licenses packaging/linux/vvuzz.desktop packaging/linux/vvuzz.service
