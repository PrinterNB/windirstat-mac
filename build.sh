#!/bin/sh
# Builds the Finder-launchable Windirstat.app next to this project.
# No PyInstaller: this project is stdlib-only, so the bundle is assembled by hand
# in the classic app-bundle layout the launcher below lives in.
#   build/Windirstat.app/Contents/MacOS/Windirstat   <- the launcher (must be 0755)
set -e
cd "$(dirname "$0")"
PROJ=$(pwd)
PYBUILD=$(python3 -c 'import sys; print(sys.executable)')
APP="$PROJ/build/Windirstat.app"
ENTRY="$APP/Contents/MacOS/Windirstat"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Finder refuses to run a script launcher inside an app bundle, so the entry is a
# compiled binary (launcher.c). The shell version below is a terminal fallback only.
if command -v clang >/dev/null 2>&1 && clang -O2 -o "$ENTRY" launcher.c; then
    LAUNCHER="native binary (launcher.c)"
else
    echo "WARNING: no clang - writing a shell launcher, which Finder will not run"
    cat > "$ENTRY" <<EOF
#!/bin/sh
# Windirstat launcher. Finder starts a bare /bin/sh with no login PATH, so name
# the interpreter instead of trusting \#! /usr/bin/env python3.
HERE=\$(dirname "\$0")
PROJ=\$(cd "\$HERE/../../../.." && pwd)
LOG="\$PROJ/windirstat.log"
PY=""
for c in $PYBUILD /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \\
         /usr/local/bin/python3 /opt/homebrew/bin/python3 /usr/bin/python3; do
    [ -z "\$PY" ] && [ -x "\$c" ] && PY="\$c"
done
[ -z "\$PY" ] && { echo "no python3 found" > "\$LOG"; exit 1; }
echo "\$(date '+%H:%M:%S') starting: py=\$PY script=\$PROJ/windirstat.py" >> "\$LOG"
if [ -t 1 ]; then
    "\$PY" "\$PROJ/windirstat.py" "\$@"
else
    "\$PY" "\$PROJ/windirstat.py" "\$@" >>"\$LOG" 2>&1
fi
echo "\$(date '+%H:%M:%S') exited rc=\$?" >>"\$LOG"
EOF
fi

# An iconless bundle shows a generic blank page in Finder, so draw one with make_icon.py
# (stdlib PNG writer -> icon.iconset in the system temp area -> iconutil) and point
# CFBundleIconFile at it, the way the working neighbour bundle on this machine does:
# its plist says icon-windowed.icns and the file sits in Contents/Resources.
# This runs before Info.plist is written, and is deliberately not fatal: without
# iconutil, or if the drawing fails, the app still builds, launches, and the plist
# below leaves CFBundleIconFile out.
ICONFILE="AppIcon.icns"
ICONKEY=""
if python3 make_icon.py "$APP/Contents/Resources/$ICONFILE"; then
    ICONKEY="    <key>CFBundleIconFile</key><string>$ICONFILE</string>"
    echo "icon:   $APP/Contents/Resources/$ICONFILE"
else
    echo "note:   no icon (make_icon.py failed) - Finder will show the generic app page"
    rm -f "$APP/Contents/Resources/$ICONFILE"
fi

cat > "$APP/Contents/Info.md" <<EOF
Windirstat.app - built by build.sh on $(date '+%Y-%m-%d')
Launcher: Contents/MacOS/Windirstat ($LAUNCHER, mode 0755).
It runs $PROJ/windirstat.py in $PROJ; any output goes to $PROJ/windirstat.log.
Icon: Contents/Resources/$ICONFILE, named by CFBundleIconFile in Contents/Info.plist
(drawn by make_icon.py; left out if that step fails, which does not stop launching).
Move the whole project folder if you move the app, or rebuild it with sh build.sh.
Built on $(date '+%Y-%m-%d') by build.sh.
EOF

# Finder needs Contents/Info.plist to treat the folder as an application - a plain
# Info.md is ignored, which is what made the first build not launch at all.
# Keys mirror the layout of a bundle that is known to work on this machine.
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDisplayName</key><string>Windirstat</string>
    <key>CFBundleExecutable</key><string>Windirstat</string>
${ICONKEY}
    <key>CFBundleIdentifier</key><string>com.local.windirstat</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>CFBundleName</key><string>Windirstat</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

chmod 0755 "$ENTRY"
echo "built: $APP"
echo "launch it with:  open \"$APP\"      (or double-click it in Finder)"
