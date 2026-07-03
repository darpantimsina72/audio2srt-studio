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

echo "[2/4] ffmpeg / ffprobe into bin/ …"
mkdir -p bin
for tool in ffmpeg ffprobe; do
  if [ ! -f "bin/$tool" ]; then
    SRC="$(command -v $tool || true)"
    if [ -n "$SRC" ]; then
      cp "$SRC" "bin/$tool"; chmod +x "bin/$tool"
      echo "  copied $tool from $SRC"
    else
      echo "  WARNING: $tool not found. Install it (brew install ffmpeg) or drop a STATIC"
      echo "           build into bin/$tool before shipping. (Homebrew ffmpeg is dynamically"
      echo "           linked and may not run on machines without those libs.)"
    fi
  fi
done

echo "[3/4] PyInstaller…"
"$PY" -m PyInstaller --noconfirm --clean build/audio2srt.spec

echo "[4/4] Optional .dmg…"
if [ -d "dist/Audio2SRT Studio.app" ]; then
  hdiutil create -volname "Audio2SRT Studio" -srcfolder "dist/Audio2SRT Studio.app" \
    -ov -format UDZO "dist/Audio2SRT-Studio.dmg" >/dev/null 2>&1 && echo "  dist/Audio2SRT-Studio.dmg" || echo "  (dmg step skipped)"
fi
echo "Done. App: dist/Audio2SRT Studio.app"
echo "NOTE: unsigned app -> first launch needs right-click > Open (Gatekeeper)."
