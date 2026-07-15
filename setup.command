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

# Guard: if only setup.command was copied out of the folder, nothing else
# can work — stop with a clear message instead of failing later.
for f in transcribe.py loader.pyw dialog.py silence.py caption-bin.drb; do
    if [ ! -f "$f" ]; then
        echo "  ERROR: $f not found next to setup.command."
        echo "  Keep setup.command inside the complete Audio2SRT folder"
        echo "  (with loader.pyw, caption-bin.drb, etc.) and run it from there."
        read -rp "  Press Enter to exit..." _; exit 1
    fi
done

# ── 1. Python check ────────────────────────────────────────────
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
    echo ""
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
echo "[ 3 / 6 ]  Checking tkinter (needed for dialogs)..."
if "$PYTHON" -c "import tkinter" 2>/dev/null; then
    echo "  OK — already installed"
else
    PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    echo "  Installing python-tk@${PY_VER} via Homebrew..."
    if command -v brew &>/dev/null; then
        brew install --quiet "python-tk@${PY_VER}"
        if "$PYTHON" -c "import tkinter" 2>/dev/null; then
            echo "  OK"
        else
            echo ""
            echo "  WARNING: tkinter is still missing — the dialogs will not appear."
            echo "  If you installed Python from python.org, run its installer again"
            echo "  and keep the \"tcl/tk and IDLE\" option checked."
            read -rp "  Press Enter to continue anyway..." _
        fi
    else
        echo ""
        echo "  WARNING: Homebrew not found. Install it from https://brew.sh"
        echo "  Then run:  brew install python-tk@${PY_VER}"
        echo ""
        read -rp "  Press Enter to continue anyway..." _
    fi
fi

# ── 4. ffmpeg (optional — needed only for the Silence Cut feature) ─
echo ""
echo "[ 4 / 6 ]  Checking ffmpeg (optional, used by Silence Cut)..."
if command -v ffmpeg &>/dev/null || [ -x /opt/homebrew/bin/ffmpeg ] || [ -x /usr/local/bin/ffmpeg ]; then
    echo "  OK — ffmpeg found"
elif command -v brew &>/dev/null; then
    read -rp "  ffmpeg not found. Install it with Homebrew now? [Y/n] " FFANS
    if [ "$FFANS" = "n" ] || [ "$FFANS" = "N" ]; then
        echo "  Skipped. Subtitles still work; Silence Cut will not."
    else
        echo "  Installing ffmpeg (takes a few minutes)..."
        brew install --quiet ffmpeg && echo "  OK" \
            || echo "  WARNING: brew install ffmpeg failed. Silence Cut will not work."
    fi
else
    echo "  NOTE: ffmpeg not found. Subtitles still work; Silence Cut will not."
    echo "        Install Homebrew (https://brew.sh) then run:  brew install ffmpeg"
fi

# ── 5. Write config so the Lua script knows where files live ───
echo ""
echo "[ 5 / 6 ]  Saving project path..."
echo "$PROJ" > "$HOME/.audio_to_srt_path"
# Save the exact interpreter we just set up, so the Resolve script
# uses the same Python that has elevenlabs installed.
"$PYTHON" -c "import sys; print(sys.executable)" > "$HOME/.audio_to_srt_python" 2>/dev/null \
    || echo "$PYTHON" > "$HOME/.audio_to_srt_python"
echo "  OK — paths saved to ~/.audio_to_srt_path"

# ── 6. Install Lua script into Resolve ─────────────────────────
echo ""
echo "[ 6 / 6 ]  Installing audio_to_srt.lua into DaVinci Resolve..."
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
