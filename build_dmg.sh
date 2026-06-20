#!/usr/bin/env bash
# Build worklog.app and wrap it in a DMG for distribution.
# Run from the project root:  bash build_dmg.sh
set -e

# Prefer the project venv; fall back to whatever python3 is on PATH.
VENV_PYTHON="$(dirname "$0")/.venv/bin/python"
if [ -x "$VENV_PYTHON" ]; then
    PYTHON="${PYTHON:-$VENV_PYTHON}"
else
    PYTHON="${PYTHON:-python3}"
fi

APP_NAME="worklog"
VERSION="0.2.0"
DMG_OUT="dist/${APP_NAME}-${VERSION}.dmg"

echo "==> Python: $($PYTHON --version)  ($PYTHON)"

# ── 1. install build deps ─────────────────────────────────────────────────────
echo "==> Installing build dependencies…"
$PYTHON -m pip install --quiet --upgrade pip setuptools wheel
$PYTHON -m pip install --quiet --upgrade \
    py2app \
    pyobjc \
    pyobjc-framework-Cocoa \
    pyobjc-framework-Quartz \
    pyobjc-framework-ApplicationServices
$PYTHON -c "import PyObjCTools; print('    OK: PyObjCTools available')"

# ── 2. clean previous build artifacts ────────────────────────────────────────
echo "==> Cleaning previous build…"
for _d in build dist; do
    [ -d "$_d" ] || continue
    # strip codesign/quarantine attrs and lock flags so rm -rf can succeed on macOS
    xattr -cr "$_d" 2>/dev/null || true
    chmod -R u+w "$_d" 2>/dev/null || true
    rm -rf "$_d"
done

# ── 3. generate app icon ──────────────────────────────────────────────────────
echo "==> Generating icon…"
$PYTHON make_icon.py

# ── 4. build .app ─────────────────────────────────────────────────────────────
# Hide pyproject.toml so setuptools doesn't inject install_requires (which py2app rejects).
echo "==> Building ${APP_NAME}.app…"
mv pyproject.toml pyproject.toml.bak
trap 'mv pyproject.toml.bak pyproject.toml' EXIT SIGINT SIGTERM
$PYTHON setup.py py2app 2>&1
mv pyproject.toml.bak pyproject.toml
trap - EXIT SIGINT SIGTERM

APP_PATH="dist/${APP_NAME}.app"
if [ ! -d "$APP_PATH" ]; then
    echo "ERROR: build failed — ${APP_PATH} not found" >&2
    exit 1
fi
echo "    OK: $APP_PATH"

# ── 5. bundle missing native libs ────────────────────────────────────────────
# py2app doesn't pull in non-system dylibs (.so files need @rpath/lib*.dylib).
# Scan every .so in lib-dynload and copy any missing @rpath deps.
FRAMEWORKS_DIR="${APP_PATH}/Contents/Frameworks"
LIB_DYNLOAD="${APP_PATH}/Contents/Resources/lib/python3.12/lib-dynload"
mkdir -p "$FRAMEWORKS_DIR"

# Derive the base Python prefix (works for venv, conda, Homebrew installs)
BASE_PREFIX=$($PYTHON -c "import sys; print(sys.base_prefix)")

# Candidate directories to search, in priority order
SEARCH_DIRS=(
    "$BASE_PREFIX/lib"
    "${CONDA_PREFIX:-/nonexistent}/lib"
    "$HOME/miniconda3/lib"
    "$HOME/miniforge3/lib"
    "$HOME/anaconda3/lib"
    /opt/homebrew/lib
    /usr/local/lib
)

echo "==> Bundling missing @rpath native libs…"
# Collect unique @rpath lib names and iterate safely line-by-line
otool -L "$LIB_DYNLOAD"/*.so 2>/dev/null \
    | grep '@rpath/' | awk '{print $1}' | sed 's|@rpath/||' | sort -u \
    | while IFS= read -r lib; do
        if [ -f "$FRAMEWORKS_DIR/$lib" ]; then
            continue  # already there
        fi
        found=""
        for dir in "${SEARCH_DIRS[@]}"; do
            if [ -f "$dir/$lib" ]; then
                found="$dir/$lib"
                break
            fi
        done
        if [ -n "$found" ]; then
            echo "    $lib  ←  $found"
            cp "$found" "$FRAMEWORKS_DIR/$lib"
        else
            echo "    WARNING: $lib not found in any search path" >&2
        fi
    done

# ── 6. create DMG ─────────────────────────────────────────────────────────────
echo "==> Creating DMG…"
rm -f "$DMG_OUT"
hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$APP_PATH" \
    -ov \
    -format UDZO \
    "$DMG_OUT"

echo ""
echo "Done!  →  $DMG_OUT"
