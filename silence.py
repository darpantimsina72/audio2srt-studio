"""Silence cutter — shared by the DaVinci and Premiere versions.

Detects silent stretches in an audio/video file and produces a tightened copy
with the silence removed (the "auto cut" behaviour). If a words JSON dump from
transcribe.py is supplied, it also writes a re-timed SRT that matches the
tightened clip — so subtitles stay in sync after the cut.

Detection uses ffmpeg's `silencedetect`. If ffmpeg is missing but a words JSON
is supplied, it falls back to word-gap detection (no extra tools needed) — but
rendering the tightened media always needs ffmpeg.

Usage:
  python3 silence.py <media_path> --out <tightened_path>
        [--srt-out <srt_path>] [--words <words.json>]
        [--threshold -30dB] [--min-silence 0.5] [--pad 0.05]

Prints a one-line JSON summary to stdout on success.
"""

import json
import os
import re
import shutil
import subprocess
import sys

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))

# Reuse the cue/SRT builders so the re-timed SRT matches transcribe.py exactly.
# Works standalone (same folder) and when bundled as the engine package.
try:
    from transcribe import build_cues, to_srt  # noqa: E402
except ImportError:  # pragma: no cover
    from engine.transcribe import build_cues, to_srt  # noqa: E402


# GUI hosts (the Premiere panel, Resolve) often launch us with a stripped PATH
# that omits /opt/homebrew/bin etc., so resolve tools against common locations too.
_COMMON_BINS = [
    "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
    "C:\\ffmpeg\\bin", "C:\\Program Files\\ffmpeg\\bin",
]
_TOOL_CACHE = {}


def tool_path(name):
    if name in _TOOL_CACHE:
        return _TOOL_CACHE[name]
    exe = name + (".exe" if os.name == "nt" else "")
    found = None
    # The packaged app sets this to its bundled ffmpeg/ffprobe folder.
    bundled = os.environ.get("AUDIO2SRT_FFMPEG_DIR")
    if bundled:
        cand = os.path.join(bundled, exe)
        if os.path.exists(cand):
            found = cand
    if not found:
        found = shutil.which(name)
    if not found:
        for d in _COMMON_BINS:
            cand = os.path.join(d, exe)
            if os.path.exists(cand):
                found = cand
                break
    _TOOL_CACHE[name] = found
    return found


def _run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


def have(tool):
    return tool_path(tool) is not None


def media_duration(path):
    """Return duration in seconds via ffprobe, or None."""
    ffprobe = tool_path("ffprobe")
    if not ffprobe:
        return None
    res = _run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", path])
    try:
        return float(res.stdout.strip())
    except (ValueError, AttributeError):
        return None


def has_video(path):
    ffprobe = tool_path("ffprobe")
    if not ffprobe:
        return False
    res = _run([ffprobe, "-v", "error", "-select_streams", "v",
                "-show_entries", "stream=codec_type", "-of", "csv=p=0", path])
    return "video" in (res.stdout or "")


def detect_silences_ffmpeg(path, threshold, min_silence):
    """Return list of (start, end) silent intervals using ffmpeg silencedetect."""
    res = _run([tool_path("ffmpeg"), "-hide_banner", "-i", path, "-af",
                "silencedetect=noise=%s:d=%s" % (threshold, min_silence),
                "-f", "null", "-"])
    log = res.stderr or ""
    silences = []
    start = None
    for line in log.splitlines():
        m = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m:
            start = float(m.group(1))
            continue
        m = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m and start is not None:
            silences.append((start, float(m.group(1))))
            start = None
    return silences


def detect_silences_wordgap(words, min_silence):
    """Fallback: silence = gap between consecutive words longer than min_silence."""
    silences = []
    prev_end = 0.0
    for w in words:
        ws = float(w.get("start", 0) or 0)
        we = float(w.get("end", 0) or 0)
        if ws - prev_end >= min_silence:
            silences.append((prev_end, ws))
        prev_end = max(prev_end, we)
    return silences


def cuts_from_silences(silences, pad):
    """Shrink each silence by `pad` on both sides (keep a little breath)."""
    cuts = []
    for s, e in silences:
        cs, ce = s + pad, e - pad
        if ce - cs > 0.05:
            cuts.append((cs, ce))
    return cuts


def keep_segments(cuts, duration):
    """Complement of the cut intervals within [0, duration]."""
    segs = []
    cursor = 0.0
    for cs, ce in sorted(cuts):
        if cs > cursor:
            segs.append((cursor, min(cs, duration)))
        cursor = max(cursor, ce)
    if cursor < duration:
        segs.append((cursor, duration))
    return [(s, e) for s, e in segs if e - s > 0.01]


def _between(segs):
    return "+".join("between(t,%.4f,%.4f)" % (s, e) for s, e in segs)


def render_tightened(src, dst, segs, with_video):
    expr = _between(segs)
    cmd = [tool_path("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-i", src]
    if with_video:
        cmd += ["-vf", "select='%s',setpts=N/FRAME_RATE/TB" % expr,
                "-af", "aselect='%s',asetpts=N/SR/TB" % expr]
    else:
        cmd += ["-af", "aselect='%s',asetpts=N/SR/TB" % expr]
    cmd += [dst]
    res = _run(cmd)
    return res.returncode == 0, (res.stderr or "")


def removed_before(t, cuts):
    """Total removed seconds occurring before source time t."""
    total = 0.0
    for cs, ce in cuts:
        if ce <= t:
            total += ce - cs
        elif cs < t < ce:
            total += t - cs
    return total


def in_any_cut(t, cuts):
    return any(cs <= t < ce for cs, ce in cuts)


def retime_words(words, cuts):
    """Drop words inside cuts, shift the rest onto the tightened timeline."""
    out = []
    for w in words:
        ws = float(w.get("start", 0) or 0)
        we = float(w.get("end", 0) or 0)
        mid = (ws + we) / 2.0
        if in_any_cut(mid, cuts):
            continue
        ns = ws - removed_before(ws, cuts)
        ne = we - removed_before(we, cuts)
        out.append({"text": w.get("text", ""), "start": max(0.0, ns),
                    "end": max(0.0, ne)})
    return out


def detect_cuts(media, threshold="-30dB", min_silence=0.5, pad=0.05, words=None):
    """Detect silence and return the cut intervals (source-time).

    Returns dict: {cuts, duration, method, silences}. Raises RuntimeError on
    hard failures. `cuts` are the [start, end] ranges that should be removed.
    The NLE bridges read these to ripple-cut / mark the live timeline.
    """
    if not os.path.exists(media):
        raise RuntimeError("media not found: " + media)

    if have("ffmpeg"):
        silences = detect_silences_ffmpeg(media, threshold, float(min_silence))
        method = "ffmpeg"
    elif words is not None:
        silences = detect_silences_wordgap(words, float(min_silence))
        method = "wordgap"
    else:
        raise RuntimeError("ffmpeg not found and no word data given. "
                           "Install ffmpeg: https://ffmpeg.org/download.html")

    duration = media_duration(media)
    if not duration:
        raise RuntimeError("could not read media duration (ffprobe).")

    cuts = cuts_from_silences(silences, float(pad))
    return {"cuts": cuts, "duration": duration, "method": method,
            "silences": len(silences)}


def run_silence(media, out, srt_out=None, words_path=None, words=None,
                threshold="-30dB", min_silence=0.5, pad=0.05,
                max_chars=10, max_lines=1, max_secs=5.0):
    """Detect silence, render a tightened clip, optionally write a re-timed SRT.

    Returns a summary dict. Raises RuntimeError on hard failures. Reusable by the
    CLI (main) and the standalone app (app.py).
    """
    if not out:
        raise RuntimeError("output path is required")

    if words is None and words_path and os.path.exists(words_path):
        with open(words_path, encoding="utf-8") as f:
            words = json.load(f)

    info = detect_cuts(media, threshold, min_silence, pad, words)
    cuts, duration = info["cuts"], info["duration"]

    if not cuts:
        return {"status": "nothing_to_cut", "method": info["method"],
                "silences": info["silences"], "removed_secs": 0.0}

    if not have("ffmpeg"):
        raise RuntimeError("ffmpeg is required to render the tightened clip. "
                           "Install it: https://ffmpeg.org/download.html")

    segs = keep_segments(cuts, duration)
    if not segs:
        raise RuntimeError("every segment was classified as silence — loosen "
                           "--threshold or raise --min-silence.")

    removed = sum(ce - cs for cs, ce in cuts)

    ok, err = render_tightened(media, out, segs, has_video(media))
    if not ok:
        raise RuntimeError("ffmpeg failed to render tightened clip:\n" + err[:600])

    cues_written = 0
    if srt_out and words is not None:
        retimed = retime_words(words, cuts)
        cues = build_cues(retimed, int(max_chars), int(max_lines), float(max_secs))
        with open(srt_out, "w", encoding="utf-8") as f:
            f.write(to_srt(cues))
        cues_written = len(cues)

    return {
        "status": "ok",
        "method": info["method"],
        "silences": info["silences"],
        "cuts": len(cuts),
        "removed_secs": round(removed, 2),
        "kept_secs": round(duration - removed, 2),
        "out": out,
        "srt_out": srt_out if cues_written else None,
        "cues": cues_written,
    }


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    media = args[0]
    opts = {"--out": None, "--srt-out": None, "--words": None,
            "--threshold": "-30dB", "--min-silence": "0.5", "--pad": "0.05",
            "--max-chars": "10", "--max-lines": "1", "--max-secs": "5"}
    i = 1
    while i < len(args):
        if args[i] in opts and i + 1 < len(args):
            opts[args[i]] = args[i + 1]
            i += 2
        else:
            i += 1

    try:
        result = run_silence(
            media, opts["--out"], srt_out=opts["--srt-out"],
            words_path=opts["--words"], threshold=opts["--threshold"],
            min_silence=opts["--min-silence"], pad=opts["--pad"],
            max_chars=opts["--max-chars"], max_lines=opts["--max-lines"],
            max_secs=opts["--max-secs"],
        )
    except RuntimeError as exc:
        print("ERROR: " + str(exc))
        sys.exit(1)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
