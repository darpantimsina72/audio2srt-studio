#!/usr/bin/env python3
"""Audio2SRT Studio — one binary, two modes.

  Double-click (no args)  ->  GUI window (pywebview).
  Called with arguments   ->  CLI, used by the Resolve / Premiere bridges
                              and for scripting.

Everything is bundled (Python + ffmpeg), so the end user installs nothing.
"""

import json
import os
import sys

# ── Locate our own files (works both as source and as a PyInstaller bundle) ──────
FROZEN = getattr(sys, "frozen", False)
RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

# Make the engine importable in both modes.
sys.path.insert(0, RES_DIR)
sys.path.insert(0, os.path.join(RES_DIR, "engine"))

# Point the engine at the bundled ffmpeg/ffprobe if we shipped them.
_BIN = os.path.join(RES_DIR, "bin")
if os.path.isdir(_BIN):
    os.environ.setdefault("AUDIO2SRT_FFMPEG_DIR", _BIN)

from engine import transcribe as t_engine   # noqa: E402
from engine import silence as s_engine       # noqa: E402

APP_NAME = "Audio2SRT Studio"
APP_VERSION = "1.0.0"


# ── User config (API key) + install marker ───────────────────────────────────────
def config_dir():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    d = os.path.join(base, "Audio2SRT")
    os.makedirs(d, exist_ok=True)
    return d


def _key_file():
    return os.path.join(config_dir(), "config.json")


def load_config():
    try:
        with open(_key_file(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    with open(_key_file(), "w", encoding="utf-8") as f:
        json.dump(cfg, f)


def get_api_key():
    return (load_config().get("api_key")
            or os.environ.get("ELEVENLABS_API_KEY", "")).strip()


def set_api_key(key):
    cfg = load_config()
    cfg["api_key"] = key.strip()
    save_config(cfg)


def app_executable():
    """The path the NLE bridges should call. The app binary when frozen,
    else 'python app.py' equivalent for dev."""
    if FROZEN:
        return sys.executable
    return os.path.abspath(__file__)


def marker_file():
    return os.path.join(os.path.expanduser("~"), ".audio2srt_app")


def write_marker():
    """Record how the bridges should invoke us (path + whether it's a script)."""
    info = {"frozen": FROZEN, "exe": app_executable()}
    if not FROZEN:
        info["python"] = sys.executable
    with open(marker_file(), "w", encoding="utf-8") as f:
        json.dump(info, f)
    return info


def ffmpeg_ok():
    return s_engine.have("ffmpeg") and s_engine.have("ffprobe")


# ── CLI ───────────────────────────────────────────────────────────────────────
def _kv(args):
    """Parse '--flag value' pairs and bare positionals."""
    pos, opts = [], {}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                opts[a[2:]] = args[i + 1]
                i += 2
            else:
                opts[a[2:]] = True
                i += 1
        else:
            pos.append(a)
            i += 1
    return pos, opts


def cli(argv):
    if not argv:
        print("commands: transcribe | silence | detect | set-key | where | "
              "install-resolve | install-premiere")
        return 1

    cmd, rest = argv[0], argv[1:]

    try:
        if cmd == "where":
            print(json.dumps({
                "app": APP_NAME, "version": APP_VERSION, "frozen": FROZEN,
                "exe": app_executable(), "ffmpeg": ffmpeg_ok(),
                "ffmpeg_dir": os.environ.get("AUDIO2SRT_FFMPEG_DIR", ""),
                "has_key": bool(get_api_key()),
            }))
            return 0

        if cmd == "set-key":
            if not rest:
                print("ERROR: usage: set-key <ELEVENLABS_API_KEY>")
                return 1
            set_api_key(rest[0])
            print("OK: key saved")
            return 0

        if cmd == "transcribe":
            pos, opts = _kv(rest)
            if len(pos) < 2:
                print("ERROR: usage: transcribe <audio> <srt_out> "
                      "[chars lines secs srcStart srcEnd offset] [--words-out PATH]")
                return 1
            count = t_engine.generate_srt(
                audio_path=pos[0], srt_output=pos[1],
                max_chars=int(pos[2]) if len(pos) > 2 else 10,
                max_lines=int(pos[3]) if len(pos) > 3 else 1,
                max_secs=float(pos[4]) if len(pos) > 4 else 5.0,
                source_start=float(pos[5]) if len(pos) > 5 else 0.0,
                source_end=float(pos[6]) if len(pos) > 6 else 0.0,
                timeline_offset=float(pos[7]) if len(pos) > 7 else 0.0,
                words_out=opts.get("words-out"),
                api_key=get_api_key(),
            )
            print(json.dumps({"status": "ok", "cues": count, "srt": pos[1]}))
            return 0

        if cmd == "detect":
            pos, opts = _kv(rest)
            if not pos:
                print("ERROR: usage: detect <media> [--threshold -30dB] "
                      "[--min-silence 0.5] [--pad 0.05]")
                return 1
            info = s_engine.detect_cuts(
                pos[0],
                threshold=opts.get("threshold", "-30dB"),
                min_silence=opts.get("min-silence", 0.5),
                pad=opts.get("pad", 0.05),
            )
            if opts.get("lines") or opts.get("format") == "lines":
                # Plain output for the Lua bridge (Resolve has no JSON parser).
                print("DURATION %.4f" % info["duration"])
                for cs, ce in info["cuts"]:
                    print("%.4f %.4f" % (cs, ce))
            else:
                print(json.dumps(info))
            return 0

        if cmd == "silence":
            pos, opts = _kv(rest)
            if not pos or not opts.get("out"):
                print("ERROR: usage: silence <media> --out <path> "
                      "[--srt-out PATH --words PATH --threshold --min-silence --pad "
                      "--max-chars --max-lines --max-secs]")
                return 1
            result = s_engine.run_silence(
                pos[0], opts["out"], srt_out=opts.get("srt-out"),
                words_path=opts.get("words"),
                threshold=opts.get("threshold", "-30dB"),
                min_silence=opts.get("min-silence", 0.5),
                pad=opts.get("pad", 0.05),
                max_chars=opts.get("max-chars", 10),
                max_lines=opts.get("max-lines", 1),
                max_secs=opts.get("max-secs", 5.0),
            )
            print(json.dumps(result))
            return 0

        if cmd == "dialog":
            # Reused by the Resolve bridge (Resolve Lua has no native dialogs).
            import dialog as dialog_mod
            sys.argv = ["app"] + rest  # rest[0] = alert|alert_error|pick|input
            dialog_mod.main()
            return 0

        if cmd == "install-resolve":
            from installers import install_resolve
            print(json.dumps(install_resolve(RES_DIR, write_marker())))
            return 0

        if cmd == "install-premiere":
            from installers import install_premiere
            print(json.dumps(install_premiere(RES_DIR, write_marker())))
            return 0

    except Exception as exc:  # noqa: BLE001 — CLI surface, report cleanly
        print("ERROR: " + str(exc))
        return 1

    print("ERROR: unknown command: " + cmd)
    return 1


# ── GUI ───────────────────────────────────────────────────────────────────────
class Api:
    """Bridge exposed to the HTML UI via pywebview (window.pywebview.api.*)."""

    def status(self):
        return {"version": APP_VERSION, "ffmpeg": ffmpeg_ok(),
                "has_key": bool(get_api_key())}

    def save_key(self, key):
        set_api_key(key)
        return {"ok": True}

    def pick_file(self):
        import webview
        res = webview.windows[0].create_file_dialog(webview.OPEN_DIALOG)
        return res[0] if res else None

    def pick_folder(self):
        import webview
        res = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        return res[0] if res else None

    def generate(self, media, out_dir, chars, lines, secs):
        try:
            base = os.path.splitext(os.path.basename(media))[0]
            srt = os.path.join(out_dir, base + ".srt")
            count = t_engine.generate_srt(
                media, srt, int(chars), int(lines), float(secs),
                api_key=get_api_key())
            return {"ok": True, "srt": srt, "cues": count}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def cut_silence(self, media, out_dir, threshold, min_silence, pad,
                    with_subs, chars, lines, secs):
        try:
            base = os.path.splitext(os.path.basename(media))[0]
            ext = os.path.splitext(media)[1] or ".mp4"
            out = os.path.join(out_dir, base + "_nosilence" + ext)
            srt = os.path.join(out_dir, base + "_nosilence.srt")
            words = None
            if with_subs:
                import tempfile
                wj = os.path.join(tempfile.gettempdir(), base + "_words.json")
                t_engine.generate_srt(
                    media, os.path.join(tempfile.gettempdir(), base + "_t.srt"),
                    int(chars), int(lines), float(secs),
                    words_out=wj, api_key=get_api_key())
                words = wj
            result = s_engine.run_silence(
                media, out, srt_out=srt if with_subs else None,
                words_path=words, threshold=str(threshold) + "dB",
                min_silence=min_silence, pad=pad,
                max_chars=chars, max_lines=lines, max_secs=secs)
            return {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def install_resolve(self):
        from installers import install_resolve
        return install_resolve(RES_DIR, write_marker())

    def install_premiere(self):
        from installers import install_premiere
        return install_premiere(RES_DIR, write_marker())


def gui():
    try:
        import webview
    except ImportError:
        sys.stderr.write(
            "GUI needs pywebview. Install it:  pip install pywebview\n"
            "(or run the CLI:  app where | transcribe | silence)\n")
        return 1
    write_marker()
    html = os.path.join(RES_DIR, "ui", "index.html")
    webview.create_window(APP_NAME + " " + APP_VERSION, html,
                          js_api=Api(), width=440, height=720, min_size=(420, 600))
    webview.start()
    return 0


def main():
    args = sys.argv[1:]
    if args:
        sys.exit(cli(args))
    sys.exit(gui())


if __name__ == "__main__":
    main()
