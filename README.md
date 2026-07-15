# Audio to SRT — DaVinci Resolve & Premiere Pro

Transcribe a timeline's audio with **ElevenLabs Scribe**, turn it into styled
subtitles, and (optionally) **auto-cut the silence**. One shared engine, two NLE
front-ends.

## What the Resolve version can do (merged from SRT Sync Pro)

The DaVinci flow now opens a full **Audio2SRT window** (`loader.pyw`) instead of
small dialogs:

- **Languages** — 11 Indian languages; Hindi guarantees 100% Devanagari output
  (a built-in transliteration safety net catches stray Latin words).
- **Speaker detection** — ElevenLabs diarization; each speaker gets a colour
  from `speaker_colors.json`.
- **Caption styles** — classic **SRT (colored)**, **Plain Text**, or ten
  **animated Fusion Text+ styles** (Karaoke Highlight, Fade, Pop In, Slide Up,
  …) built from the `caption-bin.drb` template.
- **Smart cue building** — pause-aware splits, reading-speed (CPS) cap,
  minimum on-screen time, max words per caption, Hindi glue-word handling so
  auxiliaries never strand alone.
- **Censor words** — mask words listed in `censor_words.txt` (w**d).
- **Keyword highlight** — colour chosen words inside animated captions.
- **Presets & reel styles** — save/load full setting bundles; one-click looks.
- **Review pass** — edit captions in a list before they land on the timeline.
- **Update / Undo** — restyle existing animated captions without
  re-transcribing; remove the last generated caption track.
- **Silence cut** — same tightened-copy flow as before, now a toggle in the
  window.
- **Multi-clip / retake aware** — every clip on the chosen track is mapped and
  transcribed (even from different source files), and cues are clamped to each
  clip's timeline window.
- **Retries** — transient ElevenLabs/network errors are retried with backoff.

## ⚡ Easiest way — the standalone app (Mac + Windows)

No Python, no ffmpeg, no terminal. Grab the installer from the
[Releases page](../../releases) (built automatically for every tagged version):

| OS | Download | Install |
|---|---|---|
| **Windows** | `Audio2SRT-Studio-Setup.exe` | Double-click. SmartScreen may appear once: **More info → Run anyway**. No admin needed. |
| **macOS** | `Audio2SRT-Studio.dmg` | Open, drag to Applications. First launch: **right-click → Open** (unsigned). |

Then, inside the app:
1. Paste your **ElevenLabs API key** → Save (stored locally, once).
2. Either use the app directly (pick file → **Generate SRT** / **Cut Silence**), or
3. Click **Install Resolve script** / **Install Premiere panel** and restart your
   editor — subtitles then land straight on your timeline from inside the NLE:
   - **Resolve:** Workspace → Scripts → `audio_to_srt`
   - **Premiere:** Window → Extensions → **Audio to SRT**

### Updating
The app checks GitHub on launch. When a newer version exists, a bar appears at
the top — click **Get update** to open the download page, grab the new
installer, and run it (it installs over the old one). If the Resolve script /
Premiere panel were installed, the app refreshes them automatically after an
update, so subtitle-style and timing fixes reach your timeline without
re-clicking Install.

Everything below this line is the older script-based setup — only needed if you
don't want the app.

```
DaVinci Resolve            Premiere Pro
  audio_to_srt.lua           com.audiotosrt.cep  (CEP panel)
        \                          /
         \                        /
          transcribe.py   (ElevenLabs Scribe → SRT)
          silence.py      (ffmpeg silence detect → tightened clip + re-timed SRT)
          .env            (ELEVENLABS_API_KEY)
```

## Folders

| Folder | What it is |
|---|---|
| *(root)* | **DaVinci Resolve** version (Lua script run from Workspace → Scripts) |
| `Premiere Pro version/` | **Premiere Pro** version (CEP panel under Window → Extensions) |
| `Reaper version/` | Existing REAPER version (SRT export only) |
| `for others/` | Distribution copy of everything above |

## Shared engine

- **`transcribe.py`** — sends the clip to ElevenLabs Scribe, builds SRT cues
  (max chars/line, max lines, max seconds). Unchanged behaviour from the
  original; now also dumps raw word timings (`--words-out=`) so silence cutting
  reuses the transcript instead of re-billing the API.
- **`silence.py`** — detects silence with ffmpeg `silencedetect` (falls back to
  word-gaps if ffmpeg is absent), renders a **tightened copy** of the clip with
  the gaps removed, and writes a **re-timed SRT** that stays in sync with it.

## Silence cut — why a tightened clip, not a live ripple-delete?

Neither NLE exposes a reliable "razor + ripple-delete the timeline" scripting
API. Rendering a clean tightened clip (via ffmpeg) is the dependable approach:
it always works, never corrupts the edit, and the matching re-timed SRT keeps
captions aligned. You drop the tightened clip onto a fresh track.

## Setup

Each version is self-contained. Run its `setup.command` (Mac) or `setup.bat`
(Windows) once — see the `INSTALL.txt` inside each folder. Both need Python 3.10+
and an ElevenLabs API key; the silence feature additionally needs `ffmpeg`.

## Confidence (self-assessed)

| Piece | Score | Notes |
|---|---|---|
| DaVinci subtitles | 9.5/10 | Unchanged proven flow |
| DaVinci silence cut | 9/10 | ffmpeg render + Media Pool import; needs ffmpeg installed |
| Premiere transcription | 9/10 | Same engine; clip-path/timing read via ExtendScript |
| Premiere SRT import | 8.5/10 | `importFiles` is reliable; auto-placement on timeline is best-effort (one drag otherwise) |
| Premiere panel install | 7.5/10 | CEP debug-mode + restart required; version/OS dependent |
| Premiere silence cut | 9/10 | Same ffmpeg engine as DaVinci |
