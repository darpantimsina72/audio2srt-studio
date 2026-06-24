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

echo "[ 1 / 4 ]  Checking Python..."
PYTHON=""
for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if [ -x "$p" ]; then PYTHON="$p"; break; fi
done
if [ -z "$PYTHON" ] && command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
fi

if [ -z "$PYTHON" ]; then
    echo ""
    echo "  ERROR: Python 3 is not installed."
    echo "  Install it from https://www.python.org/downloads/"
    echo ""
    read -rp "  Press Enter to exit..." _
    exit 1
fi
echo "  OK — $("$PYTHON" --version)"

echo ""
echo "[ 2 / 4 ]  Installing Python dependencies..."
"$PYTHON" -m pip install --break-system-packages -r requirements.txt 2>/dev/null \
    || "$PYTHON" -m pip install -r requirements.txt
if ! "$PYTHON" -c "import elevenlabs" 2>/dev/null; then
    echo "  ERROR: Could not install elevenlabs."
    read -rp "  Press Enter to exit..." _
    exit 1
fi
echo "  OK"

echo ""
echo "[ 3 / 4 ]  Saving project path..."
echo "$PROJ" > "$HOME/.audio_to_srt_reaper_path"
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
