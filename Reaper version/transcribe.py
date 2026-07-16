"""Called by audio_to_srt.lua / loader.pyw / the Premiere panel: transcribes
audio and writes an SRT file (plus an optional .cap caption sidecar).

Two calling conventions:

  Legacy positional (Premiere panel, Reaper, standalone app, silence flow):
    python3 transcribe.py <audio_path> <srt_output_path> [max_chars] [max_lines]
        [max_secs] [source_start_secs] [source_end_secs] [timeline_offset_secs]
        [--words-out=PATH]

  Args-file (DaVinci Resolve GUI flow — survives cmd.exe codepage mangling):
    python3 transcribe.py --args-file <path>
    One UTF-8 value per line:
        audio_path, srt_output, max_chars, max_lines, max_secs, ranges_path,
        include_punct, lang_code, diarize, censor, cps, min_dur, max_words,
        words_out (optional)

--words-out=PATH  also writes the raw word timings (source-time, pre-offset) as
                  JSON. silence.py reuses this so it never re-bills the API.
"""

import json
import os
import re
import sys
import time
import unicodedata

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


def _install_ssl_trust():
    """Make HTTPS verification use certificates the machine actually trusts.

    Python ships its own CA list (certifi) and ignores the operating system's
    certificate store. Corporate networks, VPNs and some antivirus products
    re-sign TLS traffic with their own root certificate; that root lives in
    the *OS* store, so the browser works but our API calls die with
    "[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate". truststore
    (Python 3.10+) points Python's ssl at the OS store on Windows/macOS/Linux,
    fixing that whole class of failure.

    Overrides, checked first:
      AUDIO2SRT_CA_BUNDLE=<file.pem>  verify against a specific CA bundle
                                      (for proxies whose root isn't installed
                                      machine-wide).
    Returns a short tag naming the active trust source (for logs/tests).
    """
    bundle = os.environ.get("AUDIO2SRT_CA_BUNDLE", "").strip()
    if bundle and os.path.isfile(bundle):
        # httpx (the ElevenLabs SDK), requests and urllib all honor these.
        os.environ["SSL_CERT_FILE"] = bundle
        os.environ["REQUESTS_CA_BUNDLE"] = bundle
        return "ca-bundle"
    try:
        import truststore
        truststore.inject_into_ssl()
        return "os-truststore"
    except Exception:
        pass
    # Fallback: at least make sure certifi's CAs are found (frozen builds can
    # lose the default path).
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except Exception:
        pass
    return "certifi"


_SSL_TRUST = _install_ssl_trust()


def _insecure_ssl_requested():
    """AUDIO2SRT_NO_SSL_VERIFY=1 disables certificate checks entirely — a
    last-resort escape hatch for networks whose proxy certificate cannot be
    installed. Off by default; we warn loudly when it's used."""
    return (os.environ.get("AUDIO2SRT_NO_SSL_VERIFY", "").strip().lower()
            in ("1", "true", "yes"))

# Language pinned on the ElevenLabs Scribe API when the user picks Hindi so the
# transcript is Devanagari — code-switched English words included. Overridable
# via the ELEVENLABS_LANGUAGE env var (ISO 639-1 "hi" or 639-3 "hin").
HINDI_LANG_CODE = "hin"

# On Windows some installs put elevenlabs into C:\el to dodge the MAX_PATH
# limit (the fern-generated package has very long filenames).
if sys.platform == "win32" and os.path.isdir(r"C:\el") and r"C:\el" not in sys.path:
    sys.path.insert(0, r"C:\el")


def _progress(pct, msg):
    """Emit a progress marker the loader.pyw GUI parses. Harmless when
    transcribe.py runs standalone (just prints a line)."""
    try:
        print("PROGRESS|%d|%s" % (int(pct), msg), flush=True)
    except Exception:
        pass


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


def _wval(w, key, default=None):
    """Word-field accessor that works for both plain dicts (silence.py,
    normalize_words output) and ElevenLabs SDK objects (attribute access)."""
    if isinstance(w, dict):
        return w.get(key, default)
    return getattr(w, key, default)


def wrap(text, max_chars, max_lines):
    """Greedy word-wrap into at most ``max_lines`` lines of ``max_chars``.

    Never drops words: once the line budget is exhausted, remaining words
    overflow onto the final line. A slightly long line is always better than
    silently losing text."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip() if current else word
        if not current or len(candidate) <= max_chars:
            current = candidate
        elif len(lines) + 1 < max_lines:
            lines.append(current)      # room for another line
            current = word
        else:
            current = candidate        # budget spent → overflow, never drop
    if current:
        lines.append(current)
    return "\n".join(lines)


# ── Script detection (Hindi/Devanagari vs Bengali) ──────────────────────────
# A Resolve subtitle item can carry only ONE font, so cues must never mix
# scripts: Devanagari cues get the Devanagari font, Bengali cues keep the
# Bengali font. Detection is per-codepoint so no Hindi word slips through.
#
# Devanagari blocks: U+0900–U+097F (core), U+A8E0–U+A8FF (Extended),
# U+11B00–U+11B09 (Extended-A), U+1CD0–U+1CFF (Vedic Extensions).
# IMPORTANT: danda ।(U+0964) / double danda ॥(U+0965) live in the Devanagari
# block but are shared by Bengali and most Indic scripts — they must NOT count
# as Hindi. Same for the cross-script Vedic tone marks U+0951–U+0952.
def _char_script(cp):
    if 0x0964 <= cp <= 0x0965 or 0x0951 <= cp <= 0x0952:
        return None  # pan-Indic punctuation/marks — script-neutral
    if (0x0900 <= cp <= 0x097F or 0xA8E0 <= cp <= 0xA8FF
            or 0x11B00 <= cp <= 0x11B09 or 0x1CD0 <= cp <= 0x1CFF):
        return "dev"
    if 0x0980 <= cp <= 0x09FF:
        return "beng"
    return None


def _word_script(token):
    """Script of a token: 'dev' if it contains ANY Devanagari char (so no
    Hindi word is ever missed), else 'beng' if it contains Bengali, else
    None (digits, Latin, punctuation — script-neutral)."""
    found_beng = False
    for ch in token:
        s = _char_script(ord(ch))
        if s == "dev":
            return "dev"
        if s == "beng":
            found_beng = True
    return "beng" if found_beng else None


def _buf_script(buf):
    """Dominant script of the words currently buffered in a cue."""
    for entry in buf:
        s = _word_script(entry[0])
        if s:
            return s
    return None


# Bengali/Latin sentence-end punctuation. A word ending in one of these is a
# "strong" boundary — never carry past it into the same cue when avoidable.
_STRONG_END = ("।", ".", "?", "!", "？", "！")
# Clause-level boundary — weaker, used as fallback when no strong boundary fits.
_WEAK_END   = (",", ";", ":", "—", "–")


def _ends_with(token, suffixes):
    t = token.rstrip("”’\")]")
    return any(t.endswith(s) for s in suffixes)


# ── Latin → Devanagari safety net (Hindi mode) ──────────────────────────────
# With language_code="hin" ElevenLabs writes English words in Devanagari
# (fast food → फास्ट-फूड), but the API occasionally still emits a Latin token.
# Hindi mode guarantees 100% Devanagari output: any Latin word that slips
# through is transliterated here — dictionary first (exact, natural spellings),
# then a rule-based phonetic fallback for unknown words. No external library.
_EN2DEV = {
    # words seen leaking in real runs of this pipeline
    "that": "दैट", "are": "आर", "always": "ऑलवेज़", "you": "यू",
    "may": "मे", "have": "हैव", "to": "टू", "and": "एंड",
    "vastness": "वास्टनेस", "of": "ऑफ", "the": "द", "sky": "स्काई",
    "because": "बिकॉज़", "quality": "क्वालिटी", "so": "सो", "if": "इफ",
    "i": "आई", "but": "बट", "this": "दिस", "is": "इज़", "not": "नॉट",
    "about": "अबाउट", "there": "देयर", "substantial": "सब्स्टैंशियल",
    "medical": "मेडिकल", "only": "ओनली", "people": "पीपल", "will": "विल",
    "superhuman": "सुपरह्यूमन", "super": "सुपर", "your": "योर",
    "experience": "एक्सपीरियंस", "life": "लाइफ", "in": "इन", "be": "बी",
    "love": "लव",
    # common code-switch vocabulary in these talks
    "fast": "फास्ट", "food": "फूड", "supermarket": "सुपरमार्केट",
    "ok": "ओके", "okay": "ओके", "yes": "यस", "no": "नो", "very": "वेरी",
    "good": "गुड", "time": "टाइम", "world": "वर्ल्ड", "human": "ह्यूमन",
    "mind": "माइंड", "body": "बॉडी", "energy": "एनर्जी", "yoga": "योग",
    "system": "सिस्टम", "nature": "नेचर", "possibility": "पॉसिबिलिटी",
    "technology": "टेक्नोलॉजी", "school": "स्कूल", "college": "कॉलेज",
    "office": "ऑफिस", "doctor": "डॉक्टर", "phone": "फोन", "money": "मनी",
    "business": "बिज़नेस", "family": "फैमिली", "children": "चिल्ड्रन",
    "training": "ट्रेनिंग", "program": "प्रोग्राम", "process": "प्रोसेस",
    "project": "प्रोजेक्ट", "problem": "प्रॉब्लम", "question": "क्वेश्चन",
    "answer": "आंसर", "simple": "सिंपल", "special": "स्पेशल",
    "computer": "कंप्यूटर", "internet": "इंटरनेट", "mobile": "मोबाइल",
    "manager": "मैनेजर", "festival": "फेस्टिवल", "market": "मार्केट",
    "hospital": "हॉस्पिटल", "engineer": "इंजीनियर", "science": "साइंस",
    "student": "स्टूडेंट", "teacher": "टीचर", "music": "म्यूज़िक",
    "video": "वीडियो", "camera": "कैमरा", "machine": "मशीन",
    "hotel": "होटल", "station": "स्टेशन", "ticket": "टिकट",
    "minute": "मिनट", "second": "सेकंड", "hour": "आवर", "day": "डे",
    "management": "मैनेजमेंट", "meditation": "मेडिटेशन",
    "inner": "इनर", "engineering": "इंजीनियरिंग",
}

# Rule-based fallback: approximate phonetic mapping, guarantees Devanagari.
_DEV_DIGRAPH_C = {
    "sh": "श", "ch": "च", "ck": "क", "ph": "फ", "th": "थ", "wh": "व",
    "gh": "घ", "kh": "ख", "bh": "भ", "dh": "ध", "qu": "क्व",
}
_DEV_C = {
    "b": "ब", "c": "क", "d": "ड", "f": "फ", "g": "ग", "h": "ह", "j": "ज",
    "k": "क", "l": "ल", "m": "म", "n": "न", "p": "प", "q": "क", "r": "र",
    "s": "स", "t": "ट", "v": "व", "w": "व", "x": "क्स", "y": "य", "z": "ज़",
}
_DEV_DIGRAPH_V = {  # (matra-after-consonant, independent) forms
    "ee": ("ी", "ई"), "oo": ("ू", "ऊ"), "ea": ("ी", "ई"), "ai": ("ै", "ऐ"),
    "ay": ("े", "ए"), "au": ("ौ", "औ"), "oa": ("ो", "ओ"), "ey": ("े", "ए"),
    "ie": ("ी", "ई"), "ue": ("ू", "ऊ"), "ew": ("ू", "ऊ"), "ou": ("ाउ", "आउ"),
}
_DEV_V = {
    "a": ("ा", "अ"), "e": ("े", "ए"), "i": ("ि", "इ"),
    "o": ("ो", "ओ"), "u": ("ु", "उ"),
}
_VOWELS = "aeiou"


def _rule_translit(word):
    """Phonetic Latin→Devanagari for a lowercase a-z word. Approximate by
    nature (English spelling is irregular) but always fully Devanagari —
    fast→फास्ट, food→फूड, market→मार्केट."""
    out = []
    prev_cons = False
    i, n = 0, len(word)
    while i < n:
        two = word[i:i + 2]
        # 'er' with no vowel after = schwa+r (super→सुपर, market→मार्केट)
        if two == "er" and prev_cons and (i + 2 >= n or word[i + 2] not in _VOWELS):
            out.append("र")
            prev_cons = True
            i += 2
            continue
        if two in _DEV_DIGRAPH_V:
            m, ind = _DEV_DIGRAPH_V[two]
            out.append(m if prev_cons else ind)
            prev_cons = False
            i += 2
            continue
        if two in _DEV_DIGRAPH_C:
            if prev_cons:
                out.append("्")           # virama joins the cluster
            out.append(_DEV_DIGRAPH_C[two])
            prev_cons = True
            i += 2
            continue
        ch = word[i]
        if ch in _DEV_V:
            m, ind = _DEV_V[ch]
            # silent final 'e' (love, table) — drop it
            if ch == "e" and i == n - 1 and prev_cons and n > 2:
                i += 1
                continue
            out.append(m if prev_cons else ind)
            prev_cons = False
        elif ch in _DEV_C:
            if prev_cons:
                out.append("्")           # consonant cluster
            out.append(_DEV_C[ch])
            prev_cons = True
        else:
            out.append(ch)                      # digits/symbols pass through
            prev_cons = False
        i += 1
    return "".join(out)


_LATIN_RUN = re.compile(r"[A-Za-z]+")


def _devanagarize(text):
    """Replace every Latin-letter run in ``text`` with Devanagari. Dictionary
    lookup first, phonetic rules otherwise. Non-Latin content is untouched."""
    def _one(m):
        w = m.group(0)
        return _EN2DEV.get(w.lower()) or _rule_translit(w.lower())
    return _LATIN_RUN.sub(_one, text)


# ── Word censoring ───────────────────────────────────────────────────────────
# Words listed in censor_words.txt (one per line, any script, # = comment)
# are masked in the output: first + last char kept, middle starred
# (word → w**d); words of ≤3 chars become all stars. Matching is
# case-insensitive and ignores surrounding punctuation.
_PUNCT_STRIP = "।॥.?!,;:—–-\"“”‘’'()[]{}"


def load_censor_words():
    path = os.path.join(PIPELINE_DIR, "censor_words.txt")
    words = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    words.add(unicodedata.normalize("NFC", line.lower()))
    return words


_DEFAULT_SPEAKER_COLORS = [
    "#FFFFFF", "#FFD400", "#00E5FF", "#7CFC00",
    "#FF6EC7", "#FFA500", "#B388FF", "#FF5252",
]


def load_speaker_colors():
    """Palette assigned to diarization speakers in order of appearance. Read
    from speaker_colors.json (a JSON array of hex strings) if present, else a
    sensible high-contrast default."""
    path = os.path.join(PIPELINE_DIR, "speaker_colors.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            colors = [c for c in data if isinstance(c, str) and c.strip()]
            if colors:
                return colors
        except Exception:
            pass
    return list(_DEFAULT_SPEAKER_COLORS)


def censor_token(token, censor_words):
    core = token.strip(_PUNCT_STRIP)
    if not core:
        return token
    key = unicodedata.normalize("NFC", core.lower())
    if key not in censor_words:
        return token
    if len(core) <= 3:
        mask = "*" * len(core)
    else:
        mask = core[0] + "*" * (len(core) - 2) + core[-1]
    return token.replace(core, mask, 1)


# ── Hindi "glue" words — auxiliaries, copulas, aspect/modal helpers ──────────
# These complete the phrase that precedes them (होना forms, रहा/सका/पड़ता …).
# When the char/duration budget forces a split, a short trailing word like
# "है" can land alone in its own cue — grammatically wrong and jarring to read
# (e.g. "लेन-देन नहीं" | "है"). Such a lone cue is reattached to the previous
# cue so the whole phrase stays together ("लेन-देन नहीं है").
_HI_GLUE = {unicodedata.normalize("NFC", w) for w in (
    "है", "हैं", "हूँ", "हूं", "हो", "था", "थी", "थे", "थीं",
    "होगा", "होगी", "होंगे", "होगे", "होता", "होती", "होते", "होना", "होने",
    "हुआ", "हुई", "हुए", "रहा", "रही", "रहे", "रहीं",
    "गया", "गई", "गए", "गयी", "गये",
    "चुका", "चुकी", "चुके", "सकता", "सकती", "सकते", "सका", "सके",
    "पड़ता", "पड़ती", "पड़ते", "पड़ा", "पड़ी", "पड़े", "पड़ेगा", "पड़ेगी",
    "चाहिए", "चाहिये", "लगा", "लगी", "लगे", "लगता", "लगती", "लगते",
    # Postpositions & clitic particles — these attach to the word BEFORE them
    # (राम का, घर में, उस से…), so a cue must never start with one either.
    "का", "की", "के", "को", "में", "से", "पर", "ने", "तक",
    "भी", "तो", "ही", "वाला", "वाली", "वाले",
)}

# Max gap (seconds) between the previous cue's end and a lone glue word's start
# for the two to be considered the same phrase. Sentence-final auxiliaries
# follow their head almost immediately; a large gap means a real pause, so we
# leave the word alone rather than glue across sentences.
_GLUE_MAX_GAP = 1.2

# ── Pause-aware splitting ────────────────────────────────────────────────────
# A silence between two words is where the speaker actually breathed/paused —
# the most natural place for a subtitle boundary. Two thresholds:
#   _PAUSE_SPLIT: a gap this long is a hard boundary — the cue closes at the
#     pause so subtitles appear exactly when speech resumes, never early.
#   _PAUSE_MIN: when the char/duration budget forces a mid-phrase cut and no
#     punctuation is available, prefer the widest in-buffer gap of at least
#     this size over an arbitrary word split.
_PAUSE_SPLIT = 0.35
_PAUSE_MIN   = 0.12


def _widest_pause(items, incoming_start):
    """Best cut index in ``items`` by audio silence.

    Returns j so the cut is items[:j] | items[j:], chosen at the widest
    inter-word gap ≥ _PAUSE_MIN; the gap before the incoming word competes
    too (returning len(items)). Ties go to the later gap (fuller first cue).
    Returns 0 when no gap qualifies."""
    best_j, best_gap = 0, _PAUSE_MIN
    for j in range(1, len(items)):
        gap = items[j][1] - items[j - 1][2]
        if gap >= best_gap:
            best_gap, best_j = gap, j
    if incoming_start - items[-1][2] >= best_gap:
        best_j = len(items)
    return best_j


def _is_glue(token):
    """True if token is a Hindi auxiliary/copula that must not stand alone."""
    t = unicodedata.normalize("NFC", token.strip().strip("।.?!,;:—–\"”’)]"))
    return t in _HI_GLUE


def _append_word(cue_text, word, max_chars, max_lines):
    """Append ``word`` to an already-wrapped cue without violating the
    char/line budget.

    Adds to the last line when it still fits, else starts a new line while one
    is available, else returns None — there is nowhere left to put the word
    within budget, so the caller must not force the merge (unlike wrap(),
    which is building a single cue from scratch and has no "leave it out"
    option, here the word already has a perfectly good home: staying in its
    own cue)."""
    lines = cue_text.split("\n")
    last = lines[-1]
    if not last:
        lines[-1] = word
    elif len(last) + 1 + len(word) <= max_chars:
        lines[-1] = last + " " + word
    elif len(lines) < max_lines:
        lines.append(word)
    else:
        return None
    return "\n".join(lines)


def _merge_orphans(cues, max_chars, max_lines, max_words=0):
    """Reattach lone Hindi glue-word cues to the preceding cue.

    A cue that is a single glue word (है/हैं/हूं/रहा…) is folded back into the
    previous cue when they share the same script and follow close in time, so
    phrases such as "लेन-देन नहीं है" are never split across cues — but only
    when it still fits the char/line budget. Without that check, a run of
    several short, correctly pause-split cues could get glued back into one
    over-long, over-duration cue (defeating max_chars/max_lines/max_secs and
    spanning pauses that should have stayed separate cues). When it doesn't
    fit, the glue word is left as its own cue instead of forcing the merge.
    Cues are re-numbered 1-based afterwards so downstream SRT numbering stays
    correct."""
    out = []
    for cue in cues:
        _, s, e, text, words = cue
        tok = text.strip()
        if (out and "\n" not in text and " " not in tok and _is_glue(tok)):
            pidx, ps, pe, ptext, pwords = out[-1]
            gap = s - pe
            # Same script (one font per cue) and close in time → same phrase.
            # Respect a word cap: never merge past max_words words.
            if (_word_script(tok) == _word_script(ptext)
                    and -0.1 <= gap <= _GLUE_MAX_GAP
                    and not (max_words > 0 and len(pwords) >= max_words)):
                merged = _append_word(ptext, tok, max_chars, max_lines)
                if merged is not None:
                    out[-1] = (pidx, ps, e, merged, pwords + words)
                    continue
        out.append(cue)
    return [(i + 1, s, e, t, w) for i, (_, s, e, t, w) in enumerate(out)]


def build_cues(words, max_chars, max_lines, max_secs, include_punct="1",
               cps=0.0, max_words=0):
    """Group word-timed transcription tokens into SRT cues.

    ``words`` may be plain dicts (text/start/end[/speaker]) or objects with
    those attributes — both front-ends and silence.py feed this.

    Chunking rules (language-agnostic):
      1. Prefer splitting at natural boundaries (। ? !) over mid-sentence cuts.
      2. Never break in the middle of a phrase if a clause boundary (, ;) is
         available within the same buffer.
      3. Do not merge unrelated sentences — once a sentence terminator is
         emitted, close the cue at that point rather than carrying the next
         sentence into the same chunk.
      4. cps > 0 caps reading speed (characters per second, AutoSubs-style):
         a cue that would exceed it is split early at the best boundary.
      5. A cue never mixes speakers: a diarization speaker change is a hard
         boundary, same as a script change.
    """
    cues = []
    idx = 1
    buf = []       # list of (text, start, end, speaker) for words in the cue
    start = None
    end = None
    cur_spk = None  # diarization speaker of the words currently buffered
    cap = max_chars * max_lines

    def _flush_at(cut):
        """Emit a cue for buf[:cut]; return remaining buf[cut:].

        Each cue is (idx, start, end, wrapped_text, words) where words is the
        list of (word_text, word_start, word_end, speaker) it was built from —
        retained so the caller can emit per-word timing for animated captions."""
        nonlocal idx
        if cut <= 0 or not buf:
            return buf
        head = buf[:cut]
        text = " ".join(entry[0] for entry in head)
        cue_text = wrap(text, max_chars, max_lines)
        cues.append((idx, head[0][1], head[-1][2], cue_text, list(head)))
        idx += 1
        return buf[cut:]

    def _last_boundary(items, suffixes):
        """Index AFTER the last token in items that ends with one of suffixes."""
        for j in range(len(items) - 1, -1, -1):
            if _ends_with(items[j][0], suffixes):
                return j + 1
        return 0

    for w in words:
        wt = unicodedata.normalize("NFC", (_wval(w, "text", "") or "").strip())
        if include_punct == "0":
            wt = re.sub(r'[.,?!:;।]', '', wt).strip()
        ws = float(_wval(w, "start", 0) or 0)
        we = float(_wval(w, "end", 0) or 0)
        if not wt:
            continue

        if start is None:
            start = ws

        # Speaker boundary (diarization): a cue must never mix speakers.
        w_spk = _wval(w, "speaker", None)
        if buf and w_spk is not None and cur_spk is not None and w_spk != cur_spk:
            buf = _flush_at(len(buf))
            start = ws
            end = we
        if w_spk is not None:
            cur_spk = w_spk

        # Script boundary (Bengali ↔ Devanagari): a cue must be single-script
        # so the Lua importer can assign exactly one font per cue. Flush the
        # whole buffer the moment the incoming word switches script.
        w_script = _word_script(wt)
        if buf and w_script:
            b_script = _buf_script(buf)
            if b_script and w_script != b_script:
                buf = _flush_at(len(buf))
                start = ws
                end = we

        # Pause boundary: real silence in the audio closes the cue, so the
        # subtitle disappears with the speech and the next one appears exactly
        # when the speaker resumes — cue timing tracks the audio, not the
        # character budget. A glue word (auxiliary) never starts a cue, so a
        # short pause before one does not split — unless the gap is so long
        # (> _GLUE_MAX_GAP) it is clearly a new utterance.
        if buf and end is not None:
            gap = ws - end
            if gap >= _PAUSE_SPLIT and (not _is_glue(wt) or gap > _GLUE_MAX_GAP):
                buf = _flush_at(len(buf))
                start = ws
                end = we

        prospective_text = " ".join(
            [entry[0] for entry in buf] + [wt])
        too_long = len(prospective_text) > cap
        too_long_dur = (we - start) > max_secs and bool(buf)
        # Reading-speed cap: past a settling window of 0.5s (so one quick word
        # at cue start does not trip it), split when chars/sec would exceed cps.
        dur = we - start
        too_fast = (cps > 0 and bool(buf) and dur >= 0.5
                    and len(prospective_text) / dur > cps)
        # Hard word cap (animated "one hook per clip" style): once the buffer
        # already holds max_words words, the incoming word starts a new cue.
        too_many_words = (max_words > 0 and len(buf) >= max_words)

        # Only split when there is a buffer to split. A single word longer than
        # the whole character budget (e.g. a long Devanagari compound) has
        # nothing to cut — it just becomes its own cue rather than crashing the
        # boundary search on an empty buffer.
        if (too_long or too_long_dur or too_fast or too_many_words) and buf:
            # Find the best place to cut the *current* buffer (without the new
            # word). Strong boundary preferred; then weak; then the widest
            # audio pause inside the buffer; finally right before the new word.
            cut = _last_boundary(buf, _STRONG_END)
            if cut == 0:
                cut = _last_boundary(buf, _WEAK_END)
            if cut == 0:
                cut = _widest_pause(buf, ws)
            if cut == 0:
                cut = len(buf)  # forced mid-clause break
            # A cue must never START with a Hindi auxiliary/copula — that word
            # completes the phrase before it (verb + helper: "करते हैं",
            # "होता है", "नहीं है"). Walk the cut back until the word that would
            # begin the next cue is a content word, so the verb group is never
            # split across cues. The word starting the next cue is buf[cut]
            # (remaining buffer) or, when the whole buffer is flushed, the
            # incoming word wt.
            while cut > 0 and _is_glue(buf[cut][0] if cut < len(buf) else wt):
                cut -= 1
            buf = _flush_at(cut)
            start = buf[0][1] if buf else ws
            end   = buf[-1][2] if buf else we

        buf.append((wt, ws, we, w_spk))
        end = we

        # Rule 3 — a sentence terminator closes the cue right here, so the next
        # sentence does not get merged with this one.
        if _ends_with(wt, _STRONG_END):
            buf = _flush_at(len(buf))
            start = None
            end = None

    if buf:
        _flush_at(len(buf))
    return _merge_orphans(cues, max_chars, max_lines, max_words)


def to_srt(cues, anchor=False):
    """Serialize cues to SRT text.

    ``anchor=True`` prepends a dummy cue at t=0: it forces Resolve to anchor
    the subtitle clip at the timeline start frame. Without it, Resolve places
    the clip at the first real cue's time and uses clip-relative offsets,
    shifting every subtitle forward by first_cue_time (typically ~0.1-0.5s).
    Other NLEs (Premiere, Reaper) don't need it, so it stays opt-in."""
    parts = []
    n = 1
    if anchor:
        parts.append("1\n00:00:00,000 --> 00:00:00,001\n \n")
        n = 2
    for cue in cues:
        s, e, t = cue[1], cue[2], cue[3]
        parts.append("%d\n%s --> %s\n%s\n" % (n, fmt_ts(s), fmt_ts(e), t))
        n += 1
    return "\n".join(parts)


def _cue_speaker(words):
    """The diarization speaker of a cue: first non-empty speaker among its
    words (cues are single-speaker, so the first is representative)."""
    for entry in words:
        spk = entry[3] if len(entry) > 3 else None
        if spk is not None and spk != "":
            return spk
    return None


def to_caption_sidecar(cues, fps, speaker_colors):
    """Serialize cues (with per-word timing + speaker) to a simple line-based
    format the Lua importer parses without needing a JSON library. Consumed by
    both caption styles: SRT (per-speaker colour) and Animated (Text+ macro).

        FPS <rate>
        SPK <index> <#hexcolor> <style>          # 1-based, one per speaker
        SEG <start_s> <end_s> <speaker_index>    # speaker_index 0 = none
        WRD <start_s> <end_s> <word text...>     # words follow their SEG

    Segment text is the words joined by single spaces, so the macro's
    character-index word timing lines up with the Text input exactly."""
    # Map raw diarization ids (e.g. "speaker_0") → 1-based speaker index in
    # first-appearance order, and assign each a colour from the palette.
    order = []
    seen = {}
    for cue in cues:
        spk = _cue_speaker(cue[4] if len(cue) > 4 else [])
        if spk is not None and spk not in seen:
            seen[spk] = len(order) + 1
            order.append(spk)

    lines = ["FPS %g" % fps]
    for idx, spk in enumerate(order, start=1):
        color = speaker_colors[(idx - 1) % len(speaker_colors)] if speaker_colors else "#FFFFFF"
        lines.append("SPK %d %s Fill" % (idx, color))

    for cue in cues:
        s, e = cue[1], cue[2]
        words = cue[4] if len(cue) > 4 else []
        spk = _cue_speaker(words)
        spk_idx = seen.get(spk, 0)
        lines.append("SEG %.3f %.3f %d" % (s, e, spk_idx))
        if words:
            for wt, ws, we, _ in words:
                clean = wt.replace("\n", " ").replace("\r", " ")
                lines.append("WRD %.3f %.3f %s" % (ws, we, clean))
        else:
            # No word-level data (shouldn't happen with Scribe) — fall back to
            # the whole cue text as one "word" spanning the cue.
            lines.append("WRD %.3f %.3f %s" % (s, e, cue[3].replace("\n", " ")))
    return "\n".join(lines) + "\n"


def read_clip_ranges(path):
    """Each line: src_start_frames src_end_frames tl_start_frames fps anchor_frame [src_path]

    The optional 6th field is the source media file for that clip (added so
    correction clips on the same track from a different file get transcribed
    too). Legacy 5-field rows are still accepted with src_path=None.
    """
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            # split with maxsplit=5 so the path (which may contain spaces) is
            # captured intact as the final field.
            parts = line.split(None, 5)
            if len(parts) < 5:
                continue
            src_start, src_end, tl_start, fps, anchor = parts[:5]
            src_path = parts[5].strip() if len(parts) == 6 else None
            out.append({
                "src_start_s": float(src_start) / float(fps),
                "src_end_s":   float(src_end)   / float(fps),
                "tl_start_s":  float(tl_start)  / float(fps),
                "anchor_s":    float(anchor)    / float(fps),
                "src_path":    src_path,
                "fps":         float(fps),
            })
    # Sort by timeline position so earlier-in-timeline clips get priority
    out.sort(key=lambda r: r["tl_start_s"])
    return out


def _sanitize_cues(cues, min_dur=0.04, read_dur=0.0):
    """Make any cue list a valid SRT: sorted, non-overlapping, positive
    durations, 1-based contiguous numbering.

    With multiple clips (retakes) on one track, cues from different clips can
    collide at the cut points. Overlaps are resolved by truncating the earlier
    cue at the later one's start when possible; when that would erase the
    earlier cue, the later cue is nudged forward instead — text is never
    dropped, and every cue keeps at least ``min_dur`` seconds on screen."""
    cleaned = []
    for cue in sorted(cues, key=lambda c: (c[1], c[2])):
        s, e, text, words = cue[1], cue[2], cue[3], (cue[4] if len(cue) > 4 else [])
        if not text.strip():
            continue
        if e <= s:
            e = s + min_dur
        if cleaned:
            ps, pe = cleaned[-1][0], cleaned[-1][1]
            if s < pe:
                if s - ps >= min_dur:
                    cleaned[-1] = (ps, s, cleaned[-1][2], cleaned[-1][3])  # truncate prev
                else:
                    s = pe                                   # nudge this cue
                    if e < s + min_dur:
                        e = s + min_dur
        cleaned.append((s, e, text, words))

    # Readability pass (AutoSubs-style): a cue shorter than ``read_dur`` is
    # extended into the following silence so it stays on screen long enough
    # to read, stopping 1 ms before the next cue starts.
    if read_dur > min_dur:
        for i, (s, e, text, words) in enumerate(cleaned):
            if e - s < read_dur:
                limit = cleaned[i + 1][0] - 0.001 if i + 1 < len(cleaned) else s + read_dur
                cleaned[i] = (s, max(e, min(s + read_dur, limit)), text, words)

    return [(i + 1, s, e, t, w) for i, (s, e, t, w) in enumerate(cleaned)]


def build_and_remap_cues(words_by_source, max_chars, max_lines, max_secs, ranges,
                         include_punct="1", cps=0.0, min_dur=0.0, max_words=0):
    """Assign words to their clip by (source file, source-start time), build cues
    within each clip independently, then remap timestamps to the timeline.

    ``words_by_source`` maps source file path → list of words (the transcription
    result for that file). For legacy single-source callers, pass
    ``{None: words}`` and ranges without a ``src_path``."""
    if not ranges:
        # No ranges → fall back to the first (and typically only) word list.
        flat = next(iter(words_by_source.values()), [])
        return _sanitize_cues(
            build_cues(flat, max_chars, max_lines, max_secs, include_punct,
                       cps, max_words),
            read_dur=min_dur)

    # For each range, pull words from its source file's word list that fall in
    # the range's source-time window, by containment only. A word may belong
    # to more than one range: when the editor uses the SAME source region
    # twice on the timeline (duplicated take), both clips must get subtitles.
    # Timeline-side collisions are resolved later by clamping each clip's cues
    # to its own timeline window plus a global overlap pass.
    range_words = {i: [] for i in range(len(ranges))}
    for i, r in enumerate(ranges):
        src = r.get("src_path")
        if src in words_by_source:
            candidates = words_by_source[src]
        else:
            # Legacy / unknown source: fall back to the single bucket if there
            # is exactly one, otherwise skip (no transcription for this clip).
            candidates = next(iter(words_by_source.values())) if len(words_by_source) == 1 else []
        for w in candidates:
            ws = float(_wval(w, "start", 0) or 0)
            if r["src_start_s"] <= ws < r["src_end_s"]:
                range_words[i].append(w)

    all_cues = []
    for i, r in enumerate(ranges):
        clip_words = range_words[i]
        if not clip_words:
            continue
        cues = build_cues(clip_words, max_chars, max_lines, max_secs,
                          include_punct, cps, max_words)
        shift = r["tl_start_s"] - r["src_start_s"] - r["anchor_s"]
        # The clip occupies this window on the timeline. Transcription word
        # end-times often run past the spoken word into silence; when the
        # editor cut the clip right there (retake trims), an unclamped cue
        # would spill into the NEXT clip and overlap its first cue. Clamp
        # every cue to its own clip's window so cuts stay clean.
        win_start = r["tl_start_s"] - r["anchor_s"]
        win_end   = win_start + (r["src_end_s"] - r["src_start_s"])
        for _, s, e, text, words in cues:
            ns = max(0.0, win_start, s + shift)
            ne = min(e + shift, win_end)
            if ne > ns:
                # Shift each word's timing onto the timeline too, so animated
                # captions highlight at the right moment; clamp to the cue.
                shifted = [(wt, min(max(ws + shift, ns), ne),
                                min(max(we + shift, ns), ne), spk)
                           for wt, ws, we, spk in words]
                all_cues.append((0, ns, ne, text, shifted))

    return _sanitize_cues(all_cues, read_dur=min_dur)


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


def _classify_api_error(exc):
    """Map an SDK/network exception to (human_message, is_transient)."""
    msg = str(exc) or exc.__class__.__name__
    low = msg.lower()
    if "401" in msg or "unauthorized" in low or "api key" in low or "invalid_api_key" in low:
        return ("ElevenLabs rejected the API key. Check ELEVENLABS_API_KEY in "
                ".env (get a key at https://elevenlabs.io/app/settings/api-keys).",
                False)
    if "429" in msg or "rate" in low or "too many requests" in low:
        return ("ElevenLabs rate limit hit: " + msg[:200], True)
    if any(code in msg for code in ("500", "502", "503", "504")):
        return ("ElevenLabs server error: " + msg[:200], True)
    if ("certificate_verify_failed" in low or "certificate verify failed" in low
            or "certificate required" in low or "self-signed" in low
            or "self signed" in low or "sslcertverification" in low):
        # Retrying cannot fix a certificate mismatch, so mark non-transient.
        return ("Secure connection to ElevenLabs was blocked (SSL certificate "
                "check failed). This usually means a company network, VPN or "
                "antivirus is inspecting HTTPS traffic. Try: 1) another "
                "network, e.g. a phone hotspot, to confirm; 2) ask IT to "
                "install their root certificate on this computer (the app "
                "trusts the system certificate store); 3) advanced: set "
                "AUDIO2SRT_CA_BUNDLE to your proxy's root-CA .pem file, or "
                "AUDIO2SRT_NO_SSL_VERIFY=1 to skip the check (unsafe on "
                "public networks). Details: " + msg[:160], False)
    if ("getaddrinfo" in low or "connection" in low or "connect" in low
            or "timed out" in low or "timeout" in low or "ssl" in low
            or "network" in low):
        return ("Could not reach ElevenLabs - check your internet connection "
                "and try again. (" + msg[:200] + ")", True)
    return ("ElevenLabs transcription failed: " + msg[:300], False)


def fetch_words(audio_path, api_key, language=None, diarize=False,
                attempts=3):
    """Run ElevenLabs Scribe and return the raw word objects (source-time).

    Retries transient failures (network, 429, 5xx) with backoff — the
    teammate's original pipeline had no retry and one hiccup failed the whole
    run. Raises RuntimeError with a human-readable message on failure."""
    from elevenlabs import ElevenLabs
    client_kwargs = {"api_key": api_key}
    if _insecure_ssl_requested():
        import httpx
        print("WARN: AUDIO2SRT_NO_SSL_VERIFY=1 - SSL certificate checks are "
              "OFF for this run. Only use this on a network you trust.")
        client_kwargs["httpx_client"] = httpx.Client(
            verify=False, timeout=httpx.Timeout(300.0, connect=30.0))
    client = ElevenLabs(**client_kwargs)
    ext = os.path.splitext(audio_path)[1].lower()
    mime = _MIME.get(ext, "application/octet-stream")

    kwargs = {}
    if language:
        # language_code pins the transcript's script: Hindi ("hin") makes
        # ElevenLabs write everything in Devanagari, including code-switched
        # English (e.g. "sky" → "स्काई"). Auto-detect used to return mixed
        # Latin/Devanagari.
        kwargs["language_code"] = language
    if diarize:
        kwargs["diarize"] = True

    print("Transcribing: " + os.path.basename(audio_path))
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            with open(audio_path, "rb") as fh:
                result = client.speech_to_text.convert(
                    file=(os.path.basename(audio_path), fh, mime),
                    model_id="scribe_v2",
                    timestamps_granularity="word",
                    tag_audio_events=False,
                    **kwargs
                )
            break
        except Exception as exc:
            human, transient = _classify_api_error(exc)
            last_err = RuntimeError(human)
            last_err.__cause__ = exc
            if not transient or attempt == attempts:
                raise last_err
            wait = 2 * attempt
            print("WARN: %s - retrying in %ds (%d/%d)"
                  % (human, wait, attempt, attempts - 1))
            _progress(40, "Retrying transcription (%d/%d)..." % (attempt, attempts - 1))
            time.sleep(wait)
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


def normalize_words(words, source_start=0.0, source_end=0.0, timeline_offset=0.0,
                    hindi_mode=False, censor_words=None, diarize=False):
    """Trim to the used source range, shift onto the timeline, and apply the
    text transforms (Devanagari safety net, censoring). Returns plain dicts."""
    normalized = []
    source_end = max(source_end, 0.0)

    for w in words:
        wt = (getattr(w, "text", "") or "").strip()
        ws = float(getattr(w, "start", 0) or 0)
        we = float(getattr(w, "end", 0) or 0)
        if not wt:
            continue
        if source_end > source_start and we < source_start:
            continue
        if source_end > source_start and ws > source_end:
            continue

        # Censor BEFORE transliterating: an English censor entry ("damn") must
        # match the raw token — Hindi mode would rewrite it to Devanagari first
        # and the mask would never apply. A masked token is left as-is (its
        # stars must survive), everything else goes through the safety net.
        if censor_words:
            masked = censor_token(wt, censor_words)
            if masked != wt:
                normalized_token = masked
            else:
                normalized_token = _devanagarize(wt) if hindi_mode else wt
        elif hindi_mode:
            normalized_token = _devanagarize(wt)
        else:
            normalized_token = wt
        wt = normalized_token

        start = max(ws, source_start) - source_start + timeline_offset
        end = max(we, source_start) - source_start + timeline_offset
        normalized.append({
            "text": wt, "start": start, "end": max(end, start),
            "speaker": getattr(w, "speaker_id", None) if diarize else None,
        })

    return normalized


def _resolve_language(lang_code):
    """Form/CLI language + ELEVENLABS_LANGUAGE env escape hatch. Returns
    (language_or_None, hindi_mode)."""
    resolved = (os.environ.get("ELEVENLABS_LANGUAGE", "").strip()
                or (lang_code or "").strip())
    if not resolved:
        return None, False
    return resolved, resolved.lower() in ("hi", "hin")


def generate_srt(audio_path, srt_output, max_chars=10, max_lines=1, max_secs=5.0,
                 source_start=0.0, source_end=0.0, timeline_offset=0.0,
                 words_out=None, api_key=None, language=None, diarize=False,
                 censor=False, include_punct="1", cps=0.0, min_dur=0.0,
                 max_words=0, cap_out=None, cap_fps=24.0, anchor=False):
    """Transcribe + write an SRT. Returns cue count. Raises RuntimeError on failure.

    Reusable by the CLI (main) and by the standalone app (app.py). The extra
    keyword options default to the legacy behaviour so existing callers
    (Premiere panel, Reaper, app) are unchanged.
    """
    if not api_key:
        load_dotenv()
        api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not set. Add it in Settings (or the .env file).")

    if not os.path.exists(audio_path):
        raise RuntimeError("Audio file not found: " + audio_path)

    resolved_lang, hindi_mode = _resolve_language(language)
    censor_words = load_censor_words() if censor else set()

    upload_path, is_temp = extract_audio_for_stt(audio_path)
    try:
        raw_words = fetch_words(upload_path, api_key, language=resolved_lang,
                                diarize=diarize)
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

    words = normalize_words(raw_words, source_start, source_end, timeline_offset,
                            hindi_mode=hindi_mode, censor_words=censor_words,
                            diarize=diarize)
    if not words:
        raise RuntimeError("No timed words found in the selected clip range.")

    cues = build_cues(words, max_chars, max_lines, max_secs, include_punct,
                      cps, max_words)
    if not cues:
        raise RuntimeError("No subtitle cues generated.")
    cues = _sanitize_cues(cues, read_dur=min_dur)

    # utf-8-sig: the BOM makes DaVinci/Premiere on Windows detect UTF-8 instead
    # of assuming ANSI — without it Devanagari SRTs import as mojibake.
    with open(srt_output, "w", encoding="utf-8-sig") as f:
        f.write(to_srt(cues, anchor=anchor))

    if cap_out:
        try:
            with open(cap_out, "w", encoding="utf-8") as f:
                f.write(to_caption_sidecar(cues, cap_fps, load_speaker_colors()))
        except Exception as exc:
            print("WARNING: could not write caption sidecar: %s" % exc)
    return len(cues)


def _run_args_file(args):
    """Full Resolve pipeline: multi-clip ranges, diarization, censoring,
    caption sidecar, progress markers for the loader GUI."""
    if len(args) < 2:
        print("ERROR: args file must contain at least audio_path and srt_output")
        sys.exit(1)
    audio_path = args[0]
    srt_output = args[1]
    max_chars = int(args[2]) if len(args) > 2 and args[2] else 20
    max_lines = int(args[3]) if len(args) > 3 and args[3] else 1
    max_secs = float(args[4]) if len(args) > 4 and args[4] else 2.0
    ranges_path = args[5] if len(args) > 5 and args[5] else None
    include_punct = args[6] if len(args) > 6 and args[6] else "1"
    lang_code = args[7] if len(args) > 7 and args[7] else HINDI_LANG_CODE
    diarize = (args[8] if len(args) > 8 and args[8] else "0") == "1"
    censor  = (args[9] if len(args) > 9 and args[9] else "0") == "1"
    cps     = float(args[10]) if len(args) > 10 and args[10] else 20.0
    min_dur = float(args[11]) if len(args) > 11 and args[11] else 0.4
    max_words = int(args[12]) if len(args) > 12 and args[12] else 0
    words_out = args[13] if len(args) > 13 and args[13] else None

    _progress(15, "Loading configuration...")
    load_dotenv()

    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ELEVENLABS_API_KEY not set. Add it to " + os.path.join(PIPELINE_DIR, ".env"))
        sys.exit(1)

    if not os.path.exists(audio_path):
        print("ERROR: Audio file not found: " + audio_path)
        sys.exit(1)

    _progress(25, "Loading modules...")

    resolved_lang, hindi_mode = _resolve_language(lang_code)
    censor_words = load_censor_words() if censor else set()
    print("Language: %s (hindi_mode=%s, diarize=%s, censor=%d words, cps=%g, min_dur=%g)"
          % (resolved_lang, hindi_mode, diarize, len(censor_words), cps, min_dur))

    def _normalize(raw_words):
        return normalize_words(raw_words, hindi_mode=hindi_mode,
                               censor_words=censor_words, diarize=diarize)

    def _transcribe_file(path):
        upload_path, is_temp = extract_audio_for_stt(path)
        try:
            raw = fetch_words(upload_path, api_key, language=resolved_lang,
                              diarize=diarize)
        finally:
            if is_temp:
                try:
                    os.remove(upload_path)
                except OSError:
                    pass
        if raw is None:
            print("ERROR: ElevenLabs returned no word data for " + path)
            return None
        return raw

    ranges = read_clip_ranges(ranges_path)

    # Collect unique source files from ranges, preserving timeline order so
    # progress reporting is stable. Falls back to audio_path when ranges have
    # no src_path (legacy 5-field format) or no ranges file exists.
    unique_sources = []
    seen = set()
    for r in ranges:
        sp = r.get("src_path")
        if sp and sp not in seen:
            seen.add(sp)
            unique_sources.append(sp)

    words_by_source = {}
    raw_by_source = {}
    try:
        if unique_sources:
            n = len(unique_sources)
            for i, sp in enumerate(unique_sources):
                _progress(40 + int(40 * i / n), "Transcribing %d/%d..." % (i + 1, n))
                if not os.path.exists(sp):
                    print("WARNING: source file missing, skipping: " + sp)
                    words_by_source[sp] = []
                    continue
                raw = _transcribe_file(sp)
                raw_by_source[sp] = raw or []
                words_by_source[sp] = _normalize(raw) if raw else []
        else:
            # Legacy single-audio path: no ranges or no source paths in ranges.
            _progress(40, "Transcribing audio...")
            raw = _transcribe_file(audio_path)
            raw_by_source[audio_path] = raw or []
            words_by_source[None] = _normalize(raw) if raw else []
    except RuntimeError as exc:
        print("ERROR: " + str(exc))
        sys.exit(1)

    _progress(80, "Building subtitles...")

    if not any(words_by_source.values()):
        print("ERROR: no word data from any source.")
        sys.exit(1)

    # Raw word timings of the PRIMARY source (source-time, pre-offset) for the
    # optional silence-cut pass — silence.py reuses them so it never re-bills
    # the API. Best-effort.
    if words_out:
        primary = raw_by_source.get(audio_path) or next(
            (v for v in raw_by_source.values() if v), [])
        try:
            with open(words_out, "w", encoding="utf-8") as wf:
                json.dump(words_to_dicts(primary), wf)
        except OSError as exc:
            print("WARN: could not write words JSON: " + str(exc))

    cues = build_and_remap_cues(words_by_source, max_chars, max_lines, max_secs,
                                ranges, include_punct, cps, min_dur, max_words)
    if not cues:
        print("ERROR: No subtitle cues generated.")
        sys.exit(1)

    _progress(95, "Finalizing...")
    # utf-8-sig BOM: Windows NLEs detect UTF-8 instead of assuming ANSI.
    with open(srt_output, "w", encoding="utf-8-sig") as f:
        f.write(to_srt(cues, anchor=True))

    # Caption sidecar (per-word timing + per-speaker colour) next to the SRT.
    # The Lua importer reads it for per-speaker colouring (SRT style) and for
    # the animated Text+ macro (per-word highlighting). Best-effort: a failure
    # here must never break the core SRT output.
    try:
        fps = ranges[0]["fps"] if ranges else 24.0
        speaker_colors = load_speaker_colors()
        with open(srt_output + ".cap", "w", encoding="utf-8") as f:
            f.write(to_caption_sidecar(cues, fps, speaker_colors))
    except Exception as e:
        print("WARNING: could not write caption sidecar: %s" % e)

    print("OK: " + str(len(cues)) + " cues written to " + srt_output)
    _progress(100, "Done")


def main():
    _progress(5, "Initializing...")

    # Prefer --args-file: on Windows, cmd.exe mangles non-ASCII argv
    # (e.g. curly apostrophe U+2019) by re-encoding through the system
    # codepage. Reading args from a UTF-8 file avoids that entirely.
    if len(sys.argv) >= 3 and sys.argv[1] == "--args-file":
        with open(sys.argv[2], encoding="utf-8") as af:
            args = [line.rstrip("\r\n") for line in af]
        _run_args_file(args)
        return

    # Legacy positional contract (Premiere panel, Reaper, standalone app).
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
        print("   or: transcribe.py --args-file <path>")
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
