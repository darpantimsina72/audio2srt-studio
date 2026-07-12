#!/bin/bash
# ============================================================
#  Audio to SRT for REAPER  —  Mac Setup
# ============================================================

cd "$(dirname "$0")"
PROJ="$(pwd)"

clear
echo "============================================================"
echo "   Audio to SRT for REAPER  —  Setup  (Mac)"
echo "============================================================"
echo ""

# Guard: if only setup.command was copied out of the folder, nothing else
# can work — stop with a clear message instead of failing later.
if [ ! -f "transcribe.py" ]; then
    echo "  ERROR: Project files not found next to setup.command."
    echo "  Keep setup.command inside the Reaper version folder and run it there."
    read -rp "  Press Enter to exit..." _; exit 1
fi

echo "[ 1 / 4 ]  Checking Python..."
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
if [ -z "$PYTHON" ] && command -v python3 >/dev/null 2>&1; then
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
    echo ""
    read -rp "  Press Enter to exit..." _
    exit 1
fi
echo "  OK — $("$PYTHON" --version)"

echo ""
echo "[ 2 / 4 ]  Installing Python dependencies..."
# Some installs ship without pip — bootstrap it first.
"$PYTHON" -m pip --version >/dev/null 2>&1 || "$PYTHON" -m ensurepip --upgrade >/dev/null 2>&1
"$PYTHON" -m pip install --break-system-packages -r requirements.txt 2>/dev/null \
    || "$PYTHON" -m pip install -r requirements.txt \
    || "$PYTHON" -m pip install --user -r requirements.txt
if ! "$PYTHON" -c "import elevenlabs" 2>/dev/null; then
    echo "  ERROR: Could not install elevenlabs."
    read -rp "  Press Enter to exit..." _
    exit 1
fi
echo "  OK"

echo ""
echo "[ 3 / 4 ]  Saving project path..."
echo "$PROJ" > "$HOME/.audio_to_srt_reaper_path"
# Save the exact interpreter we just set up, so the REAPER script uses the
# same Python that has elevenlabs installed.
"$PYTHON" -c "import sys; print(sys.executable)" > "$HOME/.audio_to_srt_python" 2>/dev/null \
    || echo "$PYTHON" > "$HOME/.audio_to_srt_python"
echo "  OK — path saved to ~/.audio_to_srt_reaper_path"

echo ""
echo "[ 4 / 4 ]  Installing script into REAPER..."
REAPER_SCRIPTS="$HOME/Library/Application Support/REAPER/Scripts"
mkdir -p "$REAPER_SCRIPTS"
cp audio_to_srt_reaper.lua "$REAPER_SCRIPTS/audio_to_srt_reaper.lua"
echo "  OK — copied to:"
echo "       $REAPER_SCRIPTS/audio_to_srt_reaper.lua"

echo ""
echo "------------------------------------------------------------"
if [ -f ".env" ] && grep -q "ELEVENLABS_API_KEY=." .env 2>/dev/null; then
    echo "  API key already saved in .env — skipping."
else
    echo "  ElevenLabs API key setup"
    echo "  Get your key at: https://elevenlabs.io/app/speech-synthesis/api"
    echo ""
    read -rp "  Paste your API key and press Enter: " APIKEY
    if [ -n "$APIKEY" ]; then
        echo "ELEVENLABS_API_KEY=$APIKEY" > .env
        echo "  OK — saved to .env"
    else
        echo "  Skipped. Add it later by editing .env in this folder."
    fi
fi

echo ""
echo "============================================================"
echo "   Setup complete!"
echo "============================================================"
echo ""
echo "   In REAPER:"
echo "   1. Open Actions"
echo "   2. Click ReaScript: Load"
echo "   3. Choose: $REAPER_SCRIPTS/audio_to_srt_reaper.lua"
echo "   4. Run the script with one media item selected"
echo "   5. Enter the output .srt file path when prompted"
echo ""
read -rp "   Press Enter to close..." _
