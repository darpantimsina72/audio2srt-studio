#!/bin/bash
# Build Audio2SRT Studio.app on macOS.  Run:  bash build/build_mac.sh
set -e
cd "$(dirname "$0")/.."
APP="$(pwd)"
echo "== Audio2SRT Studio — macOS build =="

PY="${PYTHON:-python3}"
echo "[1/4] Python deps (pyinstaller, pywebview, elevenlabs)…"
if "$PY" -c "import PyInstaller, webview, elevenlabs, tkinter" 2>/dev/null; then
  echo "  deps already present — skipping pip"
else
  "$PY" -m pip install --quiet --upgrade pyinstaller pywebview elevenlabs pyobjc-framework-WebKit 2>/dev/null \
    || "$PY" -m pip install --break-system-packages --quiet --upgrade pyinstaller pywebview elevenlabs pyobjc-framework-WebKit
  "$PY" -c "import tkinter" 2>/dev/null || {
    echo "  ERROR: tkinter missing in $PY — the Resolve dialogs need it in the bundle."
    echo "         brew install python-tk@3.13   (match your Python version), then re-run."
    exit 1
  }
fi

echo "[2/4] Static ffmpeg / ffprobe into bin/ …"
mkdir -p bin
# A Homebrew-copied ffmpeg is dynamically linked against /opt/homebrew dylibs —
# it dies on any machine without Homebrew. Ship static builds instead
# (ffmpeg.martin-riedl.de publishes static macOS arm64/amd64 binaries; same
# role gyan.dev plays for the Windows build). Replace old dylib-linked copies.
if [ -f bin/ffmpeg ] && otool -L bin/ffmpeg 2>/dev/null | grep -q "/opt/homebrew"; then
  echo "  bin/ffmpeg is Homebrew-linked (not portable) — replacing with a static build"
  rm -f bin/ffmpeg bin/ffprobe
fi
if [ ! -f bin/ffmpeg ] || [ ! -f bin/ffprobe ]; then
  ARCH="$(uname -m | sed 's/x86_64/amd64/')"
  BASE_URL=$(curl -sIL -o /dev/null -w '%{url_effective}' \
    "https://ffmpeg.martin-riedl.de/redirect/latest/macos/${ARCH}/release/ffmpeg.zip" || true)
  BASE_URL="${BASE_URL%/ffmpeg.zip}"
  if [ -n "$BASE_URL" ]; then
    TMPD="$(mktemp -d)"
    for tool in ffmpeg ffprobe; do
      curl -sL "$BASE_URL/$tool.zip" -o "$TMPD/$tool.zip" \
        && unzip -o -q "$TMPD/$tool.zip" -d "$TMPD" \
        && mv "$TMPD/$tool" "bin/$tool" && chmod +x "bin/$tool" \
        && echo "  static $tool downloaded" || echo "  WARNING: $tool download failed"
    done
    rm -rf "$TMPD"
  fi
fi
if [ -f bin/ffmpeg ] && [ -f bin/ffprobe ]; then
  # Sanity: portable binaries must run and must not need Homebrew libs.
  "./bin/ffmpeg" -version >/dev/null 2>&1 || { echo "  ERROR: bin/ffmpeg does not run"; exit 1; }
  otool -L bin/ffmpeg | grep -q "/opt/homebrew" \
    && echo "  WARNING: bin/ffmpeg still links Homebrew dylibs — NOT portable" || true
else
  echo "  WARNING: no ffmpeg in bin/ — app will fall back to a system ffmpeg if present."
  echo "           Drop static builds into bin/ before shipping to others."
fi

echo "[3/4] PyInstaller…"
"$PY" -m PyInstaller --noconfirm --clean build/audio2srt.spec

echo "[4/4] Optional .dmg…"
if [ -d "dist/Audio2SRT Studio.app" ]; then
  hdiutil create -volname "Audio2SRT Studio" -srcfolder "dist/Audio2SRT Studio.app" \
    -ov -format UDZO "dist/Audio2SRT-Studio.dmg" >/dev/null 2>&1 && echo "  dist/Audio2SRT-Studio.dmg" || echo "  (dmg step skipped)"
fi
echo "Done. App: dist/Audio2SRT Studio.app"
echo "NOTE: unsigned app -> first launch needs right-click > Open (Gatekeeper)."
