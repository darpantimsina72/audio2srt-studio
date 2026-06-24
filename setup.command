#!/bin/bash
# ============================================================
#  Audio to SRT  —  Mac Setup
#  Double-click this file in Finder to run.
# ============================================================

# Always run from this script's own folder
cd "$(dirname "$0")"
PROJ="$(pwd)"

clear
echo "============================================================"
echo "   Audio to SRT  —  Setup  (Mac)"
echo "============================================================"
echo ""

# ── 1. Python check ────────────────────────────────────────────
echo "[ 1 / 5 ]  Checking Python..."
PYTHON=""
for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if [ -x "$p" ]; then PYTHON="$p"; break; fi
done
if [ -z "$PYTHON" ] && command -v python3 &>/dev/null; then
    PYTHON="$(command -v python3)"
fi

if [ -z "$PYTHON" ]; then
    echo ""
    echo "  ERROR: Python 3 is not installed."
    echo ""
    echo "  Install it with Homebrew:  brew install python"
    echo "  Or download from:          https://www.python.org/downloads/"
    echo ""
    read -rp "  Press Enter to exit..." _; exit 1
fi
echo "  OK — $("$PYTHON" --version)"

# ── 2. elevenlabs ──────────────────────────────────────────────
echo ""
echo "[ 2 / 5 ]  Installing elevenlabs..."
if "$PYTHON" -c "import elevenlabs" 2>/dev/null; then
    echo "  OK — already installed"
else
    "$PYTHON" -m pip install --break-system-packages elevenlabs 2>/dev/null \
        || "$PYTHON" -m pip install elevenlabs
    if "$PYTHON" -c "import elevenlabs" 2>/dev/null; then
        echo "  OK — installed"
    else
        echo ""
        echo "  ERROR: Could not install elevenlabs."
        echo "  Try manually:  pip3 install elevenlabs"
        read -rp "  Press Enter to exit..." _; exit 1
    fi
fi

# ── 3. tkinter ─────────────────────────────────────────────────
echo ""
echo "[ 3 / 5 ]  Checking tkinter (needed for dialogs)..."
if "$PYTHON" -c "import tkinter" 2>/dev/null; then
    echo "  OK — already installed"
else
    PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    echo "  Installing python-tk@${PY_VER} via Homebrew..."
    if command -v brew &>/dev/null; then
        brew install --quiet "python-tk@${PY_VER}"
        echo "  OK"
    else
        echo ""
        echo "  WARNING: Homebrew not found. Install it from https://brew.sh"
        echo "  Then run:  brew install python-tk@${PY_VER}"
        echo ""
        read -rp "  Press Enter to continue anyway..." _
    fi
fi

# ── 4. Write config so the Lua script knows where files live ───
echo ""
echo "[ 4 / 5 ]  Saving project path..."
echo "$PROJ" > "$HOME/.audio_to_srt_path"
echo "  OK — path saved to ~/.audio_to_srt_path"

# ── 5. Install Lua script into Resolve ─────────────────────────
echo ""
echo "[ 5 / 5 ]  Installing audio_to_srt.lua into DaVinci Resolve..."
USER_RESOLVE_SCRIPTS="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
SYSTEM_RESOLVE_SCRIPTS="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"

mkdir -p "$USER_RESOLVE_SCRIPTS"
cp audio_to_srt.lua "$USER_RESOLVE_SCRIPTS/audio_to_srt.lua"
echo "  OK — installed for current user:"
echo "       $USER_RESOLVE_SCRIPTS"

if [ -d "$SYSTEM_RESOLVE_SCRIPTS" ] && [ -w "$SYSTEM_RESOLVE_SCRIPTS" ]; then
    cp audio_to_srt.lua "$SYSTEM_RESOLVE_SCRIPTS/audio_to_srt.lua"
    echo "  OK — updated shared install:"
    echo "       $SYSTEM_RESOLVE_SCRIPTS"
else
    echo "  Note — shared scripts folder not writable, skipped:"
    echo "       $SYSTEM_RESOLVE_SCRIPTS"
fi

# ── API key ────────────────────────────────────────────────────
echo ""
echo "------------------------------------------------------------"
if [ -f ".env" ] && grep -q "ELEVENLABS_API_KEY=." .env 2>/dev/null; then
    echo "  API key already saved in .env  —  skipping."
else
    echo "  ElevenLabs API key setup"
    echo "  Get your key at: https://elevenlabs.io/app/speech-synthesis/api"
    echo ""
    read -rp "  Paste your API key and press Enter: " APIKEY
    if [ -n "$APIKEY" ]; then
        echo "ELEVENLABS_API_KEY=$APIKEY" > .env
        echo "  OK — saved to .env"
    else
        echo "  Skipped. Add it later:  echo 'ELEVENLABS_API_KEY=your_key' > \"$PROJ/.env\""
    fi
fi

# ── Done ───────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "   Setup complete!"
echo "============================================================"
echo ""
echo "   Open DaVinci Resolve"
echo "   Go to:  Workspace  →  Scripts  →  audio_to_srt"
echo ""
read -rp "   Press Enter to close..." _
