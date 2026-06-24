# Audio2SRT Studio

One standalone app that does everything — like AutoCut, but for ElevenLabs
subtitles + silence cutting. **Bundles Python + ffmpeg**, so end users install
nothing. It can also install the Resolve script / Premiere panel so subtitles
land straight on the timeline.

```
Audio2SRT Studio (one binary)
 ├── double-click            -> GUI window (pick file → SRT + silence-cut clip)
 ├── called with args        -> CLI (used by the NLE bridges)
 └── "Install Resolve/Premiere" buttons
        └── bridges call this same binary via ~/.audio2srt_app (no system Python)
```

## What it does
- **Transcribe → SRT** (ElevenLabs Scribe).
- **Cut silence** (ffmpeg): renders a tightened clip + a synced SRT.
- **Editor integration:**
  - **DaVinci Resolve** — subtitles dropped on the timeline + styled; silence shown
    as **markers** and exported as a tightened clip (Resolve's API can't
    ripple-delete the timeline — markers + clean clip is the honest best).
  - **Premiere Pro** — subtitles imported as captions; silence either exported as
    a tightened clip (reliable) **or** ripple-cut on the live timeline (QE DOM,
    *beta*, opt-in checkbox).

## For end users (after you ship a build)
1. Install: **macOS** open the `.dmg`, drag to Applications (first launch:
   right-click → Open). **Windows** unzip and run `Audio2SRT Studio.exe`.
2. Open the app, paste your ElevenLabs API key, click Save.
3. Pick a media file + output folder → **Generate SRT** or **Cut Silence**.
4. (optional) Click **Install Resolve script** / **Install Premiere panel** to work
   inside your editor. Restart the editor; the script/panel appears in its menu.

There is no Python, pip, ffmpeg, or PlayerDebugMode step for the end user — the app
does it all.

## Build it (developer machine)
Needs Python **3.10–3.13** (PyInstaller doesn't support 3.14 yet).

### Windows (primary target)
```
cd "Audio2SRT App"
build\build_win.bat
```
This is turnkey: installs deps, **auto-downloads a static ffmpeg** into `bin\`,
runs PyInstaller, and — if Inno Setup (`iscc`) is on PATH — produces a single
**`dist\Audio2SRT-Studio-Setup.exe`** one-click installer (per-user, no admin/UAC).
Without Inno Setup you get `dist\Audio2SRT Studio\` — zip and ship it.
Unsigned: first run shows SmartScreen → **More info → Run anyway** (one time).

### macOS
```
cd "Audio2SRT App"
pip install -r requirements.txt
bash build/build_mac.sh         # -> dist/Audio2SRT Studio.app (+ .dmg)
```
The Mac script copies the system `ffmpeg` into `bin/`; for distribution replace it
with a **static** build (https://evermeet.cx/ffmpeg/) so it runs on other Macs.
Unsigned: first launch needs right-click → Open.

### Signing (so it opens without scary warnings)
- **macOS:** unsigned works with right-click → Open. To remove the warning entirely
  you need an Apple Developer ID + notarization (`codesign` + `xcrun notarytool`).
- **Windows:** unsigned triggers SmartScreen ("More info → Run anyway"). An EV/OV
  code-signing certificate removes it.

## CLI (what the bridges call)
```
app transcribe <audio> <srt_out> [chars lines secs srcStart srcEnd offset] [--words-out PATH]
app silence    <media> --out <path> [--srt-out P --words P --threshold -30dB --min-silence 0.5 --pad 0.05 ...]
app detect     <media> [--threshold -30dB --min-silence 0.5 --pad 0.05] [--lines]
app set-key <KEY> | where | install-resolve | install-premiere | dialog ...
```

## Layout
```
app.py                      entry: GUI vs CLI dispatch, config, install marker
installers.py               install Resolve script / Premiere panel
dialog.py                   tkinter dialogs (Resolve bridge uses these via `app dialog`)
engine/transcribe.py        ElevenLabs Scribe -> SRT
engine/silence.py           ffmpeg silence -> tightened clip + retimed SRT
ui/index.html               the app window (pywebview)
bridges/resolve/…           app-aware Resolve Lua script
bridges/premiere/…          app-aware Premiere CEP panel (+ QE DOM ripple-cut)
build/audio2srt.spec        PyInstaller spec
build/build_mac.sh|win.bat  one-command build
bin/                        bundled ffmpeg/ffprobe (filled by build scripts)
```
