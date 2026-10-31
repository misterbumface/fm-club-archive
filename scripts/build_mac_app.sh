#!/bin/zsh
# Build "FM Club Archive.app" from the source code (docs/mac-app-plan.md, part B).
#
#   scripts/build_mac_app.sh            build into dist/
#   scripts/build_mac_app.sh --install  build, then put it in /Applications (the previous app is kept, zipped)
#
# The archives are not touched: they live in ~/Library/Application Support/FM Club Archive, and the
# app brings each one up to its version when it opens it.
set -euo pipefail

ROOT="${0:A:h:h}"
cd "$ROOT"

if git rev-parse --short HEAD >/dev/null 2>&1; then
    VERSION="$(git rev-parse --short HEAD)$(git diff --quiet HEAD -- . ':!macapp/_version.py' || echo '+changes') ($(date '+%Y-%m-%d %H:%M'))"
else  # downloaded as a ZIP, without git
    VERSION="download ($(date '+%Y-%m-%d %H:%M'))"
fi
print "APP_VERSION = \"$VERSION\"" > macapp/_version.py
echo "Building FM Club Archive $VERSION"

.venv/bin/pyinstaller --noconfirm --clean --log-level WARN \
    --distpath dist --workpath build scripts/fm_club_archive.spec

BUILT="dist/FM Club Archive.app"
# The local signing certificate, when this Mac has it: the whole bundle signed with it in one step, so
# the app's identity stays the same from build to build.
IDENTITY="FM Club Archive Local Signing"
if security find-certificate -c "$IDENTITY" >/dev/null 2>&1; then
    codesign --force --deep --sign "$IDENTITY" "$BUILT"
    SIGNED="with $IDENTITY"
else
    SIGNED="ad hoc"
fi
codesign --verify --deep --strict "$BUILT"
echo "Signed $SIGNED: $BUILT"
du -sh "$BUILT"

if [[ "${1:-}" == "--install" ]]; then
    TARGET="/Applications/FM Club Archive.app"
    PREVIOUS="$HOME/Library/Application Support/FM Club Archive/previous app"
    if pgrep -f "$TARGET/Contents/MacOS" > /dev/null; then
        echo "Quit FM Club Archive first." >&2
        exit 1
    fi
    # Only the installed app may be an app: Launchpad and Spotlight list every copy they find. The previous
    # app is kept zipped (unzip it to go back), and the build is removed once installed.
    LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
    if [[ -d "$TARGET" ]]; then
        mkdir -p "$PREVIOUS"
        rm -rf "$PREVIOUS/FM Club Archive.app" "$PREVIOUS/FM Club Archive.zip"
        ditto -c -k --keepParent "$TARGET" "$PREVIOUS/FM Club Archive.zip"
        rm -rf "$TARGET"
        echo "The previous app is kept in: $PREVIOUS/FM Club Archive.zip"
    fi
    ditto "$BUILT" "$TARGET"
    "$LSREGISTER" -u "$BUILT" 2>/dev/null || true
    rm -rf "$BUILT"
    "$LSREGISTER" -f "$TARGET" 2>/dev/null || true
    echo "Installed: $TARGET"
fi
