#!/bin/bash
# ============================================================
#  Audio to SRT  —  Premiere Pro Setup  (Mac)
#  Double-click this file in Finder to run.
# ============================================================

cd "$(dirname "$0")"
PROJ="$(pwd)"
EXT_ID="com.audiotosrt.cep"

clear
echo "============================================================"
echo "   Audio to SRT  —  Premiere Pro Setup  (Mac)"
echo "============================================================"
echo ""

# Guard: if only setup.command was copied out of the folder, nothing else
# can work — stop with a clear message instead of failing later.
if [ ! -d "$EXT_ID" ]; then
    echo "  ERROR: Project files not found next to setup.command."
    echo "  Keep setup.command inside the Premiere Pro version folder and run it there."
    read -rp "  Press Enter to exit..." _; exit 1
fi

# ── 1. Python ──────────────────────────────────────────────────
echo "[ 1 / 6 ]  Checking Python..."
# /usr/bin/python3 is only a stub until Xcode Command Line Tools are
# installed — running it pops Apple's install dialog and then fails.
CLT_OK=0
xcode-select -p >/dev/null 2>&1 && CLT_OK=1

python_ok() {
    [ "$1" = "/usr/bin/python3" ] && [ "$CLT_OK" = "0" ] && return 1
    [ -x "$1" ] && "$1" -c "import sys" >/dev/null 2>&1
}

PYTHON=""
for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if python_ok "$p"; then PYTHON="$p"; break; fi
done
if [ -z "$PYTHON" ] && command -v python3 &>/dev/null; then
    p="$(command -v python3)"
    python_ok "$p" && PYTHON="$p"
fi
if [ -z "$PYTHON" ]; then
    echo ""
    echo "  ERROR: No working Python 3 found."
    if [ "$CLT_OK" = "0" ]; then
        echo "  Easiest fix: install Apple's command line tools (includes Python)."
        echo "  An install dialog will open now — click Install, wait for it to"
        echo "  finish, then double-click setup.command again."
        xcode-select --install >/dev/null 2>&1
    else
        echo "  Install it with Homebrew:  brew install python"
        echo "  Or download from:          https://www.python.org/downloads/"
    fi
    read -rp "  Press Enter to exit..." _; exit 1
fi
echo "  OK — $("$PYTHON" --version)"

# ── 2. elevenlabs ──────────────────────────────────────────────
echo ""
echo "[ 2 / 6 ]  Installing elevenlabs..."
if "$PYTHON" -c "import elevenlabs" 2>/dev/null; then
    echo "  OK — already installed"
else
    # Some installs ship without pip — bootstrap it first.
    "$PYTHON" -m pip --version >/dev/null 2>&1 || "$PYTHON" -m ensurepip --upgrade >/dev/null 2>&1
    "$PYTHON" -m pip install --break-system-packages elevenlabs 2>/dev/null \
        || "$PYTHON" -m pip install elevenlabs \
        || "$PYTHON" -m pip install --user elevenlabs
    "$PYTHON" -c "import elevenlabs" 2>/dev/null && echo "  OK — installed" \
        || { echo "  ERROR: could not install elevenlabs. Try: pip3 install elevenlabs";
             read -rp "  Press Enter to exit..." _; exit 1; }
fi

# ── 3. ffmpeg (needed for silence cutting) ─────────────────────
echo ""
echo "[ 3 / 6 ]  Checking ffmpeg (needed for the Silence Cut feature)..."
if command -v ffmpeg &>/dev/null && command -v ffprobe &>/dev/null; then
    echo "  OK — $(ffmpeg -version | head -1 | cut -d' ' -f1-3)"
else
    echo "  NOTE: ffmpeg not found. Subtitles still work; Silence Cut will not."
    echo "        Install it with:  brew install ffmpeg"
fi

# ── 4. Save project path ───────────────────────────────────────
echo ""
echo "[ 4 / 6 ]  Saving project path..."
echo "$PROJ" > "$HOME/.audio_to_srt_premiere_path"
# Save the exact interpreter we just set up, so the panel uses the same
# Python that has elevenlabs installed.
"$PYTHON" -c "import sys; print(sys.executable)" > "$HOME/.audio_to_srt_python" 2>/dev/null \
    || echo "$PYTHON" > "$HOME/.audio_to_srt_python"
echo "  OK — path saved to ~/.audio_to_srt_premiere_path"

# ── 5. Enable CEP debug mode + install the panel ───────────────
echo ""
echo "[ 5 / 6 ]  Enabling Premiere extensions + installing the panel..."
for v in 8 9 10 11 12; do
    defaults write "com.adobe.CSXS.$v" PlayerDebugMode 1 2>/dev/null
done
killall cfprefsd 2>/dev/null

CEP_DIR="$HOME/Library/Application Support/Adobe/CEP/extensions"
mkdir -p "$CEP_DIR"
rm -rf "$CEP_DIR/$EXT_ID"
cp -R "$PROJ/$EXT_ID" "$CEP_DIR/$EXT_ID"
echo "  OK — panel installed to:"
echo "       $CEP_DIR/$EXT_ID"

# ── 6. API key ─────────────────────────────────────────────────
echo ""
echo "[ 6 / 6 ]  ElevenLabs API key"
if [ -f ".env" ] && grep -q "ELEVENLABS_API_KEY=." .env 2>/dev/null; then
    echo "  API key already saved in .env  —  skipping."
else
    echo "  Get your key at: https://elevenlabs.io/app/speech-synthesis/api"
    read -rp "  Paste your API key and press Enter: " APIKEY
    if [ -n "$APIKEY" ]; then
        echo "ELEVENLABS_API_KEY=$APIKEY" > .env
        echo "  OK — saved to .env"
    else
        echo "  Skipped. Add it later to $PROJ/.env"
    fi
fi

echo ""
echo "============================================================"
echo "   Setup complete!"
echo "============================================================"
echo ""
echo "   1. FULLY QUIT Premiere Pro (Cmd+Q) and reopen it"
echo "   2. Open a project + a sequence with an audio clip"
echo "   3. Menu:  Window  →  Extensions  →  Audio to SRT"
echo ""
read -rp "   Press Enter to close..." _
