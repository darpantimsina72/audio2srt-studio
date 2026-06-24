"""Transcribe audio and write an SRT file for the REAPER workflow.

Usage:
    python3 transcribe.py <audio_path> <srt_output_path> <max_chars> <max_lines>
                          <max_secs> [source_start_secs] [source_end_secs]
                          [timeline_offset_secs]
"""

import os
import sys

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
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def wrap(text, max_chars, max_lines):
    words = text.split()
    lines = []
    current = ""

    for word in words:
        candidate = f"{current} {word}".strip() if current else word
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

    for word in words:
        text = getattr(word, "text", "").strip()
        start = float(getattr(word, "start", 0) or 0)
        end = float(getattr(word, "end", 0) or 0)

        if not text:
            continue
        if source_end > source_start and end < source_start:
            continue
        if source_end > source_start and start > source_end:
            continue

        cue_start = max(start, source_start) - source_start + timeline_offset
        cue_end = max(end, source_start) - source_start + timeline_offset
        normalized.append(
            {
                "text": text,
                "start": cue_start,
                "end": max(cue_end, cue_start),
            }
        )

    return normalized


def build_cues(words, max_chars, max_lines, max_secs):
    cues = []
    idx = 1
    buffer_words = []
    start = None
    end = None

    for word in words:
        text = word["text"].strip()
        word_start = float(word["start"] or 0)
        word_end = float(word["end"] or 0)
        if not text:
            continue

        if start is None:
            start = word_start

        too_long = len(" ".join(buffer_words + [text])) > max_chars * max_lines
        too_long_duration = (word_end - start) > max_secs and bool(buffer_words)

        if too_long or too_long_duration:
            if buffer_words and start is not None:
                cues.append(
                    (
                        idx,
                        start,
                        end,
                        wrap(" ".join(buffer_words), max_chars, max_lines),
                    )
                )
                idx += 1
            buffer_words = []
            start = word_start

        buffer_words.append(text)
        end = word_end

    if buffer_words and start is not None:
        cues.append((idx, start, end, wrap(" ".join(buffer_words), max_chars, max_lines)))

    return cues


def to_srt(cues):
    parts = []
    for idx, start, end, text in cues:
        parts.append(f"{idx}\n{fmt_ts(start)} --> {fmt_ts(end)}\n{text}\n")
    return "\n".join(parts)


_MIME = {
    ".aac": "audio/aac",
    ".aiff": "audio/aiff",
    ".alac": "audio/alac",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".mpeg": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".wma": "audio/x-ms-wma",
}


def main():
    if len(sys.argv) < 3:
        print(
            "Usage: transcribe.py <audio_path> <srt_output_path> [max_chars] "
            "[max_lines] [max_secs] [source_start_secs] [source_end_secs] "
            "[timeline_offset_secs]"
        )
        sys.exit(1)

    audio_path = sys.argv[1]
    srt_output = sys.argv[2]
    max_chars = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    max_lines = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    max_secs = float(sys.argv[5]) if len(sys.argv) > 5 else 5.0
    source_start = float(sys.argv[6]) if len(sys.argv) > 6 else 0.0
    source_end = float(sys.argv[7]) if len(sys.argv) > 7 else 0.0
    timeline_offset = float(sys.argv[8]) if len(sys.argv) > 8 else 0.0

    load_dotenv()

    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ELEVENLABS_API_KEY not set. Add it to " + os.path.join(PIPELINE_DIR, ".env"))
        sys.exit(1)

    if not os.path.exists(audio_path):
        print("ERROR: Audio file not found: " + audio_path)
        sys.exit(1)

    output_dir = os.path.dirname(os.path.abspath(srt_output))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

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
        print("ERROR: ElevenLabs returned no word data.")
        sys.exit(1)

    words = normalize_words(result.words, source_start, source_end, timeline_offset)
    if not words:
        print("ERROR: No timed words found in the selected item range.")
        sys.exit(1)

    cues = build_cues(words, max_chars, max_lines, max_secs)
    if not cues:
        print("ERROR: No subtitle cues generated.")
        sys.exit(1)

    with open(srt_output, "w", encoding="utf-8") as f:
        f.write(to_srt(cues))

    print(f"OK: {len(cues)} cues written to {srt_output}")


if __name__ == "__main__":
    main()
