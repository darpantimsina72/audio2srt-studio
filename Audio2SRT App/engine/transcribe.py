"""Called by audio_to_srt.lua / the Premiere panel: transcribes audio and writes an SRT file.

Usage: python3 transcribe.py <audio_path> <srt_output_path> <max_chars> <max_lines> <max_secs> [source_start_secs] [source_end_secs] [timeline_offset_secs] [--words-out=PATH]

--words-out=PATH  also writes the raw word timings (source-time, pre-offset) as JSON.
                  silence.py reuses this so it never re-bills the ElevenLabs API.
"""

import json
import os
import sys

# Windows consoles/redirects default to a legacy codepage (cp1252): printing a
# Devanagari/emoji filename would crash before the API is even called. Force
# UTF-8 on the standard streams (no-op on mac/Linux and frozen GUI builds).
# line_buffering: windowed builds block-buffer pipes and the shutdown flush can
# fail silently — flush per line so callers always receive our output.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace",
                                line_buffering=True)
        except (AttributeError, OSError):
            pass

# Always resolve relative to this script's own folder — works on any machine
PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_dotenv():
    env_file = os.path.join(PIPELINE_DIR, ".env")
    if not os.path.exists(env_file):
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def fmt_ts(seconds):
    seconds = max(0.0, float(seconds))
    ms = int(round(seconds * 1000))
    h, r = divmod(ms, 3600000)
    m, r = divmod(r, 60000)
    s, ms = divmod(r, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def wrap(text, max_chars, max_lines):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip() if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return "\n".join(lines)


def normalize_words(words, source_start=0.0, source_end=0.0, timeline_offset=0.0):
    normalized = []
    source_end = max(source_end, 0.0)

    for w in words:
        wt = getattr(w, "text", "").strip()
        ws = float(getattr(w, "start", 0) or 0)
        we = float(getattr(w, "end", 0) or 0)
        if not wt:
            continue
        if source_end > source_start and we < source_start:
            continue
        if source_end > source_start and ws > source_end:
            continue

        start = max(ws, source_start) - source_start + timeline_offset
        end = max(we, source_start) - source_start + timeline_offset
        normalized.append({"text": wt, "start": start, "end": max(end, start)})

    return normalized


def build_cues(words, max_chars, max_lines, max_secs):
    cues = []
    idx = 1
    buf = []
    start = None
    end = None
    for w in words:
        wt = w["text"].strip()
        ws = float(w["start"] or 0)
        we = float(w["end"] or 0)
        if not wt:
            continue
        if start is None:
            start = ws
        too_long = len(" ".join(buf + [wt])) > max_chars * max_lines
        too_long_dur = (we - start) > max_secs and bool(buf)
        if too_long or too_long_dur:
            if buf and start is not None:
                cues.append((idx, start, end, wrap(" ".join(buf), max_chars, max_lines)))
                idx += 1
            buf = []
            start = ws
        buf.append(wt)
        end = we
    if buf and start is not None:
        cues.append((idx, start, end, wrap(" ".join(buf), max_chars, max_lines)))
    return cues


def to_srt(cues):
    parts = []
    for i, s, e, t in cues:
        parts.append("%d\n%s --> %s\n%s\n" % (i, fmt_ts(s), fmt_ts(e), t))
    return "\n".join(parts)


_MIME = {
    ".aac": "audio/aac", ".aiff": "audio/aiff", ".alac": "audio/alac",
    ".flac": "audio/flac", ".m4a": "audio/mp4", ".mp3": "audio/mpeg",
    ".mp4": "video/mp4", ".mpeg": "audio/mpeg", ".ogg": "audio/ogg",
    ".wav": "audio/wav", ".wma": "audio/x-ms-wma",
}


_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".m4v", ".webm",
               ".mts", ".m2ts", ".mpg", ".mpeg", ".wmv"}


def extract_audio_for_stt(media_path):
    """For video files, extract a small mono MP3 to upload instead of the whole
    video — much smaller/faster and avoids API file-size limits. Word timings
    are unchanged (the audio stream keeps the same clock). Returns
    (path_to_upload, is_temp); falls back to the original file if ffmpeg is
    unavailable or extraction fails.
    """
    ext = os.path.splitext(media_path)[1].lower()
    if ext not in _VIDEO_EXTS:
        return media_path, False

    try:
        try:
            from silence import tool_path
        except ImportError:
            from engine.silence import tool_path
        ffmpeg = tool_path("ffmpeg")
    except Exception:
        ffmpeg = None
    if not ffmpeg:
        return media_path, False

    import subprocess
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".mp3", prefix="a2srt_audio_")
    os.close(fd)
    res = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-i", media_path, "-vn", "-ac", "1", "-b:a", "96k", tmp],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return media_path, False
    return tmp, True


def fetch_words(audio_path, api_key):
    """Run ElevenLabs Scribe and return the raw word objects (source-time)."""
    from elevenlabs import ElevenLabs
    client = ElevenLabs(api_key=api_key)
    ext = os.path.splitext(audio_path)[1].lower()
    mime = _MIME.get(ext, "application/octet-stream")

    print("Transcribing: " + os.path.basename(audio_path))
    with open(audio_path, "rb") as fh:
        result = client.speech_to_text.convert(
            file=(os.path.basename(audio_path), fh, mime),
            model_id="scribe_v2",
            timestamps_granularity="word",
            tag_audio_events=False,
        )
    if not result or not getattr(result, "words", None):
        return None
    return result.words


def words_to_dicts(raw_words):
    """Flatten ElevenLabs word objects to plain dicts (source-time, pre-offset)."""
    out = []
    for w in raw_words:
        wt = getattr(w, "text", "")
        if not (wt or "").strip():
            continue
        out.append({
            "text": wt,
            "start": float(getattr(w, "start", 0) or 0),
            "end": float(getattr(w, "end", 0) or 0),
        })
    return out


def generate_srt(audio_path, srt_output, max_chars=10, max_lines=1, max_secs=5.0,
                 source_start=0.0, source_end=0.0, timeline_offset=0.0,
                 words_out=None, api_key=None):
    """Transcribe + write an SRT. Returns cue count. Raises RuntimeError on failure.

    Reusable by the CLI (main) and by the standalone app (app.py).
    """
    if not api_key:
        load_dotenv()
        api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not set. Add it in Settings (or the .env file).")

    if not os.path.exists(audio_path):
        raise RuntimeError("Audio file not found: " + audio_path)

    upload_path, is_temp = extract_audio_for_stt(audio_path)
    try:
        raw_words = fetch_words(upload_path, api_key)
    except RuntimeError:
        raise
    except Exception as exc:
        # Turn raw SDK/network tracebacks into a message the NLE dialog can show.
        msg = str(exc) or exc.__class__.__name__
        low = msg.lower()
        if "401" in msg or "unauthorized" in low or "api key" in low or "invalid_api_key" in low:
            raise RuntimeError(
                "ElevenLabs rejected the API key. Check ELEVENLABS_API_KEY in "
                ".env (get a key at https://elevenlabs.io/app/settings/api-keys).") from exc
        if ("getaddrinfo" in low or "connection" in low or "connect" in low
                or "timed out" in low or "timeout" in low or "ssl" in low
                or "network" in low):
            raise RuntimeError(
                "Could not reach ElevenLabs — check your internet connection "
                "and try again. (" + msg[:200] + ")") from exc
        raise RuntimeError("ElevenLabs transcription failed: " + msg[:300]) from exc
    finally:
        if is_temp:
            try:
                os.remove(upload_path)
            except OSError:
                pass
    if raw_words is None:
        raise RuntimeError("ElevenLabs returned no word data.")

    if words_out:
        try:
            with open(words_out, "w", encoding="utf-8") as wf:
                json.dump(words_to_dicts(raw_words), wf)
        except OSError as exc:
            print("WARN: could not write words JSON: " + str(exc))

    words = normalize_words(raw_words, source_start, source_end, timeline_offset)
    if not words:
        raise RuntimeError("No timed words found in the selected clip range.")

    cues = build_cues(words, max_chars, max_lines, max_secs)
    if not cues:
        raise RuntimeError("No subtitle cues generated.")

    # utf-8-sig: the BOM makes DaVinci/Premiere on Windows detect UTF-8 instead
    # of assuming ANSI — without it Devanagari SRTs import as mojibake.
    with open(srt_output, "w", encoding="utf-8-sig") as f:
        f.write(to_srt(cues))
    return len(cues)


def main():
    argv = [a for a in sys.argv[1:]]

    words_out = None
    rest = []
    for a in argv:
        if a.startswith("--words-out="):
            words_out = a.split("=", 1)[1]
        else:
            rest.append(a)

    if len(rest) < 2:
        print("Usage: transcribe.py <audio_path> <srt_output_path> [max_chars] [max_lines] [max_secs] [source_start_secs] [source_end_secs] [timeline_offset_secs] [--words-out=PATH]")
        sys.exit(1)

    try:
        count = generate_srt(
            audio_path=rest[0],
            srt_output=rest[1],
            max_chars=int(rest[2]) if len(rest) > 2 else 10,
            max_lines=int(rest[3]) if len(rest) > 3 else 1,
            max_secs=float(rest[4]) if len(rest) > 4 else 5.0,
            source_start=float(rest[5]) if len(rest) > 5 else 0.0,
            source_end=float(rest[6]) if len(rest) > 6 else 0.0,
            timeline_offset=float(rest[7]) if len(rest) > 7 else 0.0,
            words_out=words_out,
        )
    except RuntimeError as exc:
        print("ERROR: " + str(exc))
        sys.exit(1)

    print("OK: " + str(count) + " cues written to " + rest[1])


if __name__ == "__main__":
    main()
