"""Audio2SRT single-window UI: form + progress.

Launched by audio_to_srt.lua via pythonw.exe. Renders one Tkinter window
that begins as a track / settings form and transitions in place to a
progress view once the user clicks Generate Subtitles.

Flow:
  1. Read --prompt file (track items + defaults).
  2. Show form. On submit, write --selection file so the Lua wrapper can
     build clip ranges and write the worker args file.
  3. Wait for --args-file to appear, then spawn transcribe.py hidden,
     parse PROGRESS|pct|message lines, and animate the progress UI.
  4. Write the exit code to --done and close.

Args:
  --prompt    JSON  {"items":[...], "defaults":{"settings":"15,1,1","punct":0}}
  --selection JSON  {"chosen":..., "settings":..., "punct": 0|1}
  --args-file path to the worker args file the Lua wrapper writes
  --done      sentinel file: written with exit code when finished
  --log       transcribe.py stdout log file
  --python    interpreter to run the worker
  --script    transcribe.py path
"""

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, font as tkfont


def _enable_dpi_awareness():
    """Tell Windows this process handles its own scaling.

    Without this, an unaware process gets its whole window bitmap-scaled by
    Windows on any display that isn't at 100% scaling (125%/150%/etc. are the
    Windows default on most laptops and 4K monitors). That stretch happens
    AFTER Tk has already laid out and drawn every pixel-measured widget in
    this file, so the true window edge lands to the left of where Tk thinks
    it is — the rightmost content (our right-hand settings column) gets
    silently cut off. This must run before the first Tk() is created; it is
    called once at import time, below. No-op on non-Windows or on failure.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # Per-Monitor v2 (Windows 10 1703+): best fidelity, matches whichever
        # monitor the window is actually on.
        ctypes.windll.shcore.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
        return
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_dpi_awareness()

# Languages offered in the form. Order matters: the first is the default.
# The code (ISO 639-3) is passed to ElevenLabs as language_code, which forces
# the whole transcript into that language's script — for Hindi that means
# Devanagari for every word, including spoken English.
LANGUAGES = ("Hindi", "Bengali", "English", "Tamil", "Telugu", "Marathi",
             "Gujarati", "Kannada", "Malayalam", "Punjabi", "Urdu")
LANGUAGE_CODES = {
    "Hindi": "hin", "Bengali": "ben", "English": "eng", "Tamil": "tam",
    "Telugu": "tel", "Marathi": "mar", "Gujarati": "guj", "Kannada": "kan",
    "Malayalam": "mal", "Punjabi": "pan", "Urdu": "urd",
}

# Caption styles offered in the form. "SRT (colored)" imports subtitles onto a
# subtitle track (reliable, per-speaker colour). Every other entry is an
# animated Fusion Text+ style built from the AutoSubs macro — each maps to
# (caption_style_code, animation_preset). The animation presets are applied in
# audio_to_srt.lua (ANIM_PRESETS). First entry is the default.
CAPTION_STYLES = (
    "SRT (colored)",
    "Plain Text",
    "Karaoke Highlight",
    "Fade",
    "Pop In",
    "Slide Up",
    "Fade + Pop",
    "Fade + Slide",
    "Karaoke + Pop",
    "Fast Pop",
    "Smooth Fade",
    "Slide + Highlight",
)
CAPTION_STYLE_MAP = {
    "SRT (colored)":     ("srt", ""),
    "Plain Text":        ("plain", ""),
    "Karaoke Highlight": ("animated", "karaoke"),
    "Fade":              ("animated", "fade"),
    "Pop In":            ("animated", "pop"),
    "Slide Up":          ("animated", "slide"),
    "Fade + Pop":        ("animated", "fadepop"),
    "Fade + Slide":      ("animated", "fadeslide"),
    "Karaoke + Pop":     ("animated", "karpop"),
    "Fast Pop":          ("animated", "fastpop"),
    "Smooth Fade":       ("animated", "smooth"),
    "Slide + Highlight": ("animated", "slidehi"),
}

# One-click "reel styles": ready-made caption looks. Each bundle sets the same
# StringVar/IntVar keys the form uses, applied by _apply_reel_style(). `swatch`
# / `outline` drive the little thumbnail; the rest configure the real output.
# hl_color (keyword/karaoke highlight) is set when the look calls for it.
REEL_STYLES = (
    {"name": "Bold Yellow", "swatch": "#FFDD00", "outline": "#000000",
     "caption": "Karaoke Highlight",
     "vars": {"color": "#FFDD00", "outline": 1, "shadow": 1,
              "outline_color": "#000000", "outline_thick": "0.120",
              "shadow_color": "#000000", "tp_size": "0.200",
              "tp_posx": "0.500", "tp_posy": "0.280",
              "font_style": "Black", "hl_color": "#00E5FF"}},
    {"name": "Clean White", "swatch": "#FFFFFF", "outline": "#000000",
     "caption": "Fade",
     "vars": {"color": "#FFFFFF", "outline": 1, "shadow": 0,
              "outline_color": "#000000", "outline_thick": "0.060",
              "shadow_color": "#000000", "tp_size": "0.150",
              "tp_posx": "0.500", "tp_posy": "0.280",
              "font_style": "SemiBold", "hl_color": ""}},
    {"name": "Karaoke Pop", "swatch": "#FFFFFF", "outline": "#101828",
     "caption": "Karaoke + Pop",
     "vars": {"color": "#FFFFFF", "outline": 1, "shadow": 1,
              "outline_color": "#101828", "outline_thick": "0.100",
              "shadow_color": "#000000", "tp_size": "0.190",
              "tp_posx": "0.500", "tp_posy": "0.300",
              "font_style": "Bold", "hl_color": "#FFD400"}},
    {"name": "Podcast Lower", "swatch": "#F2F2F2", "outline": "#000000",
     "caption": "Plain Text",
     "vars": {"color": "#F2F2F2", "outline": 0, "shadow": 1,
              "outline_color": "#000000", "outline_thick": "0.000",
              "shadow_color": "#000000", "tp_size": "0.110",
              "tp_posx": "0.500", "tp_posy": "0.140",
              "font_style": "Medium", "hl_color": ""}},
    {"name": "Neon Punch", "swatch": "#00E5FF", "outline": "#001018",
     "caption": "Fast Pop",
     "vars": {"color": "#00E5FF", "outline": 1, "shadow": 1,
              "outline_color": "#001018", "outline_thick": "0.130",
              "shadow_color": "#00131a", "tp_size": "0.210",
              "tp_posx": "0.500", "tp_posy": "0.320",
              "font_style": "Black", "hl_color": "#FF2D95"}},
)

# Mirror of ANIM_PRESETS in audio_to_srt.lua, but only the bits the live
# preview needs to REPLAY the look: which entry effect(s) play, how long the
# intro runs, and whether a per-word karaoke highlight sweeps afterwards.
# Keep the keys and durations in sync with the Lua table so what the preview
# shows matches what actually renders on the timeline.
#   fade  = opacity 0->1      pop = scale up with overshoot
#   slide = rises into place  hi  = karaoke per-word highlight sweep
PREVIEW_ANIM = {
    "karaoke":   {"fade": True,  "pop": False, "slide": False, "len": 0.30, "hi": True},
    "fade":      {"fade": True,  "pop": False, "slide": False, "len": 0.40, "hi": False},
    "pop":       {"fade": False, "pop": True,  "slide": False, "len": 0.30, "hi": False},
    "slide":     {"fade": True,  "pop": False, "slide": True,  "len": 0.40, "hi": False},
    "fadepop":   {"fade": True,  "pop": True,  "slide": False, "len": 0.35, "hi": False},
    "fadeslide": {"fade": True,  "pop": False, "slide": True,  "len": 0.40, "hi": False},
    "karpop":    {"fade": True,  "pop": True,  "slide": False, "len": 0.30, "hi": True},
    "fastpop":   {"fade": False, "pop": True,  "slide": False, "len": 0.18, "hi": False},
    "smooth":    {"fade": True,  "pop": False, "slide": False, "len": 0.60, "hi": False},
    "slidehi":   {"fade": True,  "pop": False, "slide": True,  "len": 0.40, "hi": True},
}

# Fallback caption fonts, used only if system font detection fails. The Font
# picker normally lists every installed font family (tkfont.families()).
# "Auto (by language)" keeps the script-aware behaviour (Devanagari -> Vesper
# Libre, Bengali -> Noto Serif Bengali); any other choice forces that font.
CAPTION_FONTS = (
    "Auto (by language)",
    "Vesper Libre",
    "Noto Serif Bengali",
    "Noto Sans Devanagari",
    "Mukta",
    "Hind",
    "Poppins",
    "Montserrat",
    "Arial",
    "Helvetica Neue Bold",
    "Anton",
)

# Font weight/style applied on top of the family. "Auto" keeps each path's
# default. For animated Text+ this maps to the macro's "Style" input (Fusion's
# Regular/Medium/Bold list); for SRT it drives the bold/italic properties.
FONT_STYLES = ("Auto", "Regular", "Light", "Medium", "SemiBold",
               "Bold", "Black", "Italic", "Bold Italic")

TRANSPARENT_KEY = "#010203"  # sentinel color used for Toplevel transparency

CREATE_NO_WINDOW = 0x08000000

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# UI fonts per platform: "Segoe UI"/"Consolas" only exist on Windows; on
# macOS Tk would silently substitute something with different metrics, which
# shifts every pixel-measured layout in this file.
FONT_UI   = "Segoe UI" if IS_WIN else ("Helvetica Neue" if IS_MAC else "DejaVu Sans")
FONT_MONO = "Consolas" if IS_WIN else ("Menlo" if IS_MAC else "DejaVu Sans Mono")

# Tk's aqua backend cannot reliably give keyboard focus to an
# overrideredirect (frameless) window — entries would be unclickable/untypable.
# On macOS we keep the native window frame instead of the custom title bar.
FRAMELESS = IS_WIN

BG_OUTER = "#0b0d10"        # window background behind floating card
BG       = "#14171c"        # card interior
BG_CARD  = "#14171c"
BG_INPUT = "#0f1217"        # text inputs (darker than card)
BG_HOVER = "#1b2030"
BG_TILE  = "#222936"        # 28×28 icon tile bg
BORDER   = "#1f242d"        # subtle input border
BORDER_TILE = "#2c3340"     # icon tile border
BORDER_CARD = "#23272f"     # outer card border
BORDER_BRIGHT = "#4a5260"
SHADOW   = "#06070a"
FG       = "#e6e9ef"
FG_DIM   = "#9aa1b1"
FG_MUTE  = "#7b8190"
FG_LABEL = "#8a93a4"
ACCENT       = "#3b82f6"
ACCENT_DARK  = "#2563eb"
ACCENT_HOVER = "#4f93ff"
ACCENT_GLOW  = "#1d3b6e"     # solid color stand-in for blue glow shadow
FOCUS_BAR = ACCENT
DIVIDER  = "#23272f"
DIVIDER_MID = "#2a2f38"
TRACK_OFF = "#262b35"
THUMB_OFF = "#9aa2b3"

# Icon names rendered by IconCanvas (Canvas-drawn, font-independent)
ICO_MIC          = "mic"
ICO_VOLUME       = "volume"
ICO_CHEVRON_DOWN = "chevron-down"
ICO_POINT        = "bullet"


def _steal_focus(win):
    if not IS_WIN:
        # macOS/Linux: Tk's own primitives are the only portable option.
        try:
            win.lift()
            win.attributes("-topmost", True)
            win.focus_force()
        except Exception:
            pass
        return
    try:
        import ctypes
        u32 = ctypes.windll.user32
        u32.SystemParametersInfoW(0x2001, 0, 0, 2)
        hwnd = int(win.winfo_id())
        u32.ShowWindow(hwnd, 9)
        u32.BringWindowToTop(hwnd)
        u32.SetForegroundWindow(hwnd)
        # Force HWND_TOPMOST via SetWindowPos — more reliable than tkinter's
        # -topmost attribute when overrideredirect is used on Windows.
        HWND_TOPMOST  = -1
        SWP_NOMOVE    = 0x0002
        SWP_NOSIZE    = 0x0001
        SWP_NOACTIVATE = 0x0010
        u32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:
        pass


def _keep_topmost(win):
    """Re-assert topmost every 500 ms so the window stays above all apps."""
    if IS_WIN:
        try:
            import ctypes
            hwnd = int(win.winfo_id())
            ctypes.windll.user32.SetWindowPos(
                hwnd, -1, 0, 0, 0, 0, 0x0003)  # HWND_TOPMOST | SWP_NOMOVE | SWP_NOSIZE
        except Exception:
            pass
    else:
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
    try:
        win.after(500, lambda: _keep_topmost(win))
    except Exception:
        pass


def _round_rect(canvas, x1, y1, x2, y2, r, **kw):
    """Draw a rounded rectangle on a Canvas using a smoothed polygon."""
    r = min(r, (x2 - x1) // 2, (y2 - y1) // 2)
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1,
        x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2,
        x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


class IconCanvas(tk.Canvas):
    """Monochrome icon drawn directly on a Canvas — no icon-font dependency."""

    def __init__(self, parent, name, size=18, color="#7a85a0", bg_parent=BG):
        super().__init__(parent, width=size, height=size, bg=bg_parent,
                         highlightthickness=0, bd=0)
        self._name = name
        self._size = size
        self._color = color
        self._draw()

    def set_color(self, color):
        self._color = color
        self._draw()

    def configure(self, **kw):
        super().configure(**kw)
        if "bg" in kw or "background" in kw:
            self._draw()

    config = configure

    def _draw(self):
        self.delete("all")
        s = self._size
        c = self._color
        n = self._name
        if n == "mic":
            self._mic(s, c)
        elif n == "volume":
            self._volume(s, c)
        elif n == "chevron-down":
            self._chevron(s, c)
        elif n == "bullet":
            self._bullet(s, c)

    def _mic(self, s, c):
        w = s * 0.42
        x1, x2 = (s - w) / 2, (s + w) / 2
        y1, y2 = s * 0.10, s * 0.62
        _round_rect(self, x1, y1, x2, y2, w / 2, fill=c, outline=c)
        self.create_arc(s * 0.20, s * 0.45, s * 0.80, s * 0.80,
                        start=180, extent=180, style="arc",
                        outline=c, width=max(2, int(s * 0.10)))
        line_w = max(2, int(s * 0.10))
        self.create_line(s / 2, s * 0.78, s / 2, s * 0.92,
                         fill=c, width=line_w, capstyle="round")
        self.create_line(s * 0.34, s * 0.92, s * 0.66, s * 0.92,
                         fill=c, width=line_w, capstyle="round")

    def _volume(self, s, c):
        self.create_rectangle(s * 0.18, s * 0.40, s * 0.32, s * 0.60,
                              fill=c, outline=c)
        pts = [s * 0.32, s * 0.40, s * 0.50, s * 0.22,
               s * 0.50, s * 0.78, s * 0.32, s * 0.60]
        self.create_polygon(pts, fill=c, outline=c)
        wave_w = max(2, int(s * 0.10))
        self.create_arc(s * 0.50, s * 0.30, s * 0.72, s * 0.70,
                        start=-50, extent=100, style="arc",
                        outline=c, width=wave_w)
        self.create_arc(s * 0.58, s * 0.18, s * 0.90, s * 0.82,
                        start=-50, extent=100, style="arc",
                        outline=c, width=wave_w)

    def _chevron(self, s, c):
        line_w = max(2, int(s * 0.14))
        pad = s * 0.25
        mid = s / 2
        self.create_line(pad,     s * 0.40, mid, s * 0.65,
                         fill=c, width=line_w,
                         capstyle="round", joinstyle="round")
        self.create_line(s - pad, s * 0.40, mid, s * 0.65,
                         fill=c, width=line_w,
                         capstyle="round", joinstyle="round")

    def _bullet(self, s, c):
        r = s * 0.18
        cx = cy = s / 2
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=c, outline=c)


class RoundedButton(tk.Canvas):
    """Flex-width rounded button with optional icon and filled/outline variant."""

    def __init__(self, parent, text, command, primary=False,
                 icon_name=None, bg_parent=BG, height=36, radius=6):
        self._font = tkfont.Font(family=FONT_UI, size=10,
                                 weight="bold" if primary else "normal")
        self._text = text
        self._icon = icon_name
        self._icon_size = 14
        self._primary = primary
        self._bg_parent = bg_parent
        self._H = height + 8   # extra space below for primary's glow halo
        self._R = radius
        self._command = command
        self._hover = False
        self._btn_h = height
        min_w = (self._font.measure(text)
                 + (self._icon_size + 8 if icon_name else 0)
                 + 28)
        super().__init__(parent, width=min_w, height=self._H,
                         bg=bg_parent, highlightthickness=0, bd=0,
                         cursor="hand2")
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>",     lambda e: self._set_hover(True))
        self.bind("<Leave>",     lambda e: self._set_hover(False))
        self.bind("<Button-1>",  lambda e: command())

    def _set_hover(self, on):
        self._hover = on
        self._draw()

    def _draw(self):
        self.delete("all")
        w = max(self.winfo_width(), self.winfo_reqwidth())
        h = self._btn_h
        full_h = self._H
        if self._primary:
            fill   = ACCENT_HOVER if self._hover else ACCENT
            border = fill
            tcol   = "#ffffff"
            # Soft glow underneath (concentric blurred rounds)
            for off in range(5, 0, -1):
                col = _mix(self._bg_parent, ACCENT_GLOW, 0.25 - off * 0.03)
                _round_rect(self, off, off + 4, w - off, h + off + 4,
                            self._R + off, fill=col, outline=col)
            _round_rect(self, 0, 0, w, h, self._R, fill=border, outline=border)
            _round_rect(self, 1, 1, w - 1, h - 1, max(1, self._R - 1),
                        fill=fill, outline=fill)
            # Top inner highlight
            self.create_line(self._R, 1, w - self._R, 1,
                             fill=_mix(fill, "#ffffff", 0.30))
        else:
            fill   = BG_HOVER if self._hover else BG
            border = BORDER_BRIGHT if self._hover else BORDER
            tcol   = FG_DIM
            _round_rect(self, 0, 0, w, h, self._R, fill=border, outline=border)
            _round_rect(self, 1, 1, w - 1, h - 1, max(1, self._R - 1),
                        fill=fill, outline=fill)

        cy = h // 2
        if self._icon:
            iw = self._icon_size
            tw = self._font.measure(self._text)
            gap = 8
            total = iw + gap + tw
            ix = (w - total) // 2
            self._draw_inline_icon(self._icon, ix, cy - iw // 2,
                                   iw, tcol, fill)
            self.create_text(ix + iw + gap, cy, anchor="w",
                             text=self._text, fill=tcol, font=self._font)
        else:
            self.create_text(w // 2, cy, text=self._text,
                             fill=tcol, font=self._font)

    def _draw_inline_icon(self, name, x, y, s, color, bg):
        """Render a small IconCanvas-style glyph in-place on the button canvas."""
        if name == "mic":
            w = s * 0.42
            x1, x2 = x + (s - w) / 2, x + (s + w) / 2
            y1, y2 = y + s * 0.10, y + s * 0.62
            _round_rect(self, x1, y1, x2, y2, w / 2, fill=color, outline=color)
            lw = max(2, int(s * 0.10))
            self.create_arc(x + s * 0.20, y + s * 0.45,
                            x + s * 0.80, y + s * 0.80,
                            start=180, extent=180, style="arc",
                            outline=color, width=lw)
            self.create_line(x + s / 2, y + s * 0.78,
                             x + s / 2, y + s * 0.92,
                             fill=color, width=lw, capstyle="round")
            self.create_line(x + s * 0.34, y + s * 0.92,
                             x + s * 0.66, y + s * 0.92,
                             fill=color, width=lw, capstyle="round")


class RoundedField(tk.Canvas):
    """Rounded container holding a single child widget (Entry / Label row).

    Draws a hairline border + filled background and re-lays out on resize.
    The child is positioned via create_window with horizontal padding `padx`
    and centered vertically.
    """

    def __init__(self, parent, height=44, radius=12, padx=16,
                 bg_parent=BG, fill=BG_INPUT, border=BORDER):
        super().__init__(parent, height=height, bg=bg_parent,
                         highlightthickness=0, bd=0)
        self._h = height
        self._r = radius
        self._padx = padx
        self._fill = fill
        self._border = border
        self._child = None
        self._right_child = None  # optional right-side widget (e.g. chevron)
        self._child_win = None
        self._right_win = None
        self.bind("<Configure>", lambda e: self._redraw())

    def set_child(self, child, right_child=None):
        self._child = child
        self._right_child = right_child
        self._redraw()

    def set_border(self, color):
        self._border = color
        self._redraw()

    def set_fill(self, color):
        self._fill = color
        self._redraw()

    def _redraw(self):
        self.delete("bg")
        w = self.winfo_width()
        h = self._h
        # Outer (border) rounded rect
        _round_rect(self, 0, 0, w, h, self._r,
                    fill=self._border, outline=self._border, tags=("bg",))
        # Inner fill
        _round_rect(self, 1, 1, w - 1, h - 1, max(1, self._r - 1),
                    fill=self._fill, outline=self._fill, tags=("bg",))
        # Keep background polygons below the hosted child widgets
        self.tag_lower("bg")

        # Place right child first so we can measure its width
        right_w = 0
        if self._right_child is not None:
            if self._right_win is None:
                self._right_win = self.create_window(
                    w - 6, h // 2, anchor="e", window=self._right_child)
            else:
                self.coords(self._right_win, w - 6, h // 2)
            self.update_idletasks()
            right_w = self._right_child.winfo_reqwidth() + 8

        if self._child is not None:
            child_w = max(10, w - self._padx * 2 - right_w)
            if self._child_win is None:
                self._child_win = self.create_window(
                    self._padx, h // 2, anchor="w",
                    window=self._child, width=child_w)
            else:
                self.coords(self._child_win, self._padx, h // 2)
                self.itemconfigure(self._child_win, width=child_w)


class PillEntry(RoundedField):
    """Rounded text input — Fluent-style bottom-edge accent bar on focus.

    Border stays hairline `BORDER` color regardless of focus state; the
    bottom 2px underline switches from `BORDER` to `FOCUS_BAR` (accent).
    """

    def __init__(self, parent, textvariable):
        super().__init__(parent, height=40, radius=6, padx=12,
                         bg_parent=BG, fill=BG_INPUT, border=BORDER)
        self._focused = False
        self.entry = tk.Entry(
            self, textvariable=textvariable,
            bg=BG_INPUT, fg=FG, insertbackground=ACCENT,
            relief="flat", bd=0, highlightthickness=0,
            font=(FONT_UI, 10),
        )
        self.set_child(self.entry)
        self.entry.bind("<FocusIn>",  lambda e: self._set_focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._set_focus(False))

    def _set_focus(self, on):
        self._focused = on
        self._redraw()

    def _redraw(self):
        super()._redraw()

    def focus(self):
        self.entry.focus()


class SmallInput(RoundedField):
    """Compact numeric input cell — same Fluent-style focus underline."""

    def __init__(self, parent, textvariable):
        super().__init__(parent, height=36, radius=6, padx=10,
                         bg_parent=BG, fill=BG_INPUT, border=BORDER)
        self._focused = False
        self.entry = tk.Entry(
            self, textvariable=textvariable,
            bg=BG_INPUT, fg=FG, insertbackground=ACCENT,
            relief="flat", bd=0, highlightthickness=0,
            font=(FONT_UI, 10, "bold"),
            justify="left",
        )
        self.set_child(self.entry)
        self.entry.bind("<FocusIn>",  lambda e: self._set_focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._set_focus(False))

    def _set_focus(self, on):
        self._focused = on
        self._redraw()

    def _redraw(self):
        super()._redraw()

    def focus(self):
        self.entry.focus()


class IconRow(RoundedField):
    """Static rounded row: left tile + label + right widget (e.g. toggle).

    Tile can be either an icon (via `icon_name`) or a short text label
    (via `tile_text`, e.g. 'Aa')."""

    def __init__(self, parent, icon_name, text, right_widget=None,
                 tile_text=None):
        super().__init__(parent, height=48, radius=12, padx=14,
                         bg_parent=BG, fill=BG_INPUT, border=BORDER)
        body = tk.Frame(self, bg=BG_INPUT)
        if icon_name or tile_text:
            ico = RoundedIconTile(
                body,
                icon_name=None if tile_text else icon_name,
                text=tile_text,
                size=28, radius=8,
                bg_parent=BG_INPUT,
            )
            ico.pack(side="left", padx=(0, 10))
        else:
            ico = None
        lbl = tk.Label(body, text=text, bg=BG_INPUT, fg=FG,
                       font=(FONT_UI, 10))
        lbl.pack(side="left")
        self._body = body
        self._lbl = lbl
        self._ico = ico
        self.set_child(body, right_child=right_widget)


class Dropdown(RoundedField):
    """Rounded dropdown trigger + canvas-rendered rounded menu with pill rows."""

    ROW_H   = 34
    ROW_GAP = 2
    PAD     = 6
    CARD_R  = 8
    PILL_R  = 6

    def __init__(self, parent, values, variable, on_pick=None, icon_name=None):
        super().__init__(parent, height=48, radius=12, padx=10,
                         bg_parent=BG, fill=BG_INPUT, border=BORDER)
        self.values = list(values)
        self.variable = variable
        self.on_pick = on_pick
        self._popup = None
        self._shadow = None
        self._outside_bind = None

        body = tk.Frame(self, bg=BG_INPUT)
        if icon_name:
            self._icon = RoundedIconTile(body, icon_name=icon_name,
                                         size=28, radius=8,
                                         bg_parent=BG_INPUT)
            self._icon.configure(cursor="hand2")
            self._icon.pack(side="left", padx=(0, 10))
        else:
            self._icon = None
        self._label = tk.Label(
            body, textvariable=variable, bg=BG_INPUT, fg=FG,
            font=(FONT_UI, 10), anchor="w", cursor="hand2",
        )
        self._label.pack(side="left", fill="x", expand=True)
        self._body = body

        self._arrow = IconCanvas(self, "chevron-down", size=12, color=FG_MUTE,
                                 bg_parent=BG_INPUT)
        self._arrow.configure(cursor="hand2")
        self.set_child(body, right_child=self._arrow)

        click_targets = [self._label, self._arrow, self, body]
        if self._icon is not None:
            click_targets.append(self._icon)
        for w in click_targets:
            w.bind("<Button-1>", self._toggle)
        for w in (body, self._label, self._arrow):
            w.bind("<Enter>", lambda e: self._hover(True))
            w.bind("<Leave>", lambda e: self._hover(False))

    def _hover(self, on):
        if self._popup:
            return
        c = BG_HOVER if on else BG_INPUT
        self.set_fill(c)
        self._body.configure(bg=c)
        self._label.configure(bg=c)
        self._arrow.configure(bg=c)
        if self._icon is not None:
            self._icon.configure(bg=c)

    def _toggle(self, _e=None):
        if self._popup:
            self._close()
        else:
            self._open()

    def _open(self):
        self.update_idletasks()

        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 4
        w = self.winfo_width()
        n = max(1, len(self.values))
        h = self.PAD * 2 + n * self.ROW_H + (n - 1) * self.ROW_GAP

        # ── Popup toplevel (rounded card, pill rows; no border, no shadow)
        self._popup = tk.Toplevel(self)
        self._popup.overrideredirect(True)
        try:
            self._popup.attributes("-topmost", True)
            self._popup.attributes("-transparentcolor", TRANSPARENT_KEY)
        except Exception:
            pass
        self._popup.configure(bg=TRANSPARENT_KEY)
        self._popup.geometry("%dx%d+%d+%d" % (w, h, x, y))

        c = tk.Canvas(self._popup, bg=TRANSPARENT_KEY,
                      highlightthickness=0, bd=0, width=w, height=h)
        c.pack(fill="both", expand=True)

        # Card backdrop — flat fill, no visible border
        _round_rect(c, 0, 0, w, h, self.CARD_R,
                    fill=BG_INPUT, outline=BG_INPUT)

        current = self.variable.get()
        row_font = (FONT_UI, 10)
        for i, v in enumerate(self.values):
            ry1 = self.PAD + i * (self.ROW_H + self.ROW_GAP)
            ry2 = ry1 + self.ROW_H
            rx1 = self.PAD
            rx2 = w - self.PAD
            is_cur = (v == current)
            fill = BG_HOVER if is_cur else BG_INPUT
            tag = "row%d" % i
            rect = _round_rect(c, rx1, ry1, rx2, ry2, self.PILL_R,
                               fill=fill, outline=fill, tags=(tag,))
            text = c.create_text(rx1 + 16, (ry1 + ry2) // 2, anchor="w",
                                 text=v, fill=FG, font=row_font, tags=(tag,))

            def on_enter(_e, r=rect):
                c.itemconfig(r, fill=BG_HOVER, outline=BG_HOVER)

            def on_leave(_e, r=rect, cur=is_cur):
                color = BG_HOVER if cur else BG_INPUT
                c.itemconfig(r, fill=color, outline=color)

            c.tag_bind(tag, "<Enter>", on_enter)
            c.tag_bind(tag, "<Leave>", on_leave)
            c.tag_bind(tag, "<Button-1>", lambda e, val=v: self._pick(val))

        c.configure(cursor="hand2")
        self._popup.bind("<Escape>", lambda e: self._close())

        # Click-outside-to-close
        root = self.winfo_toplevel()
        self._outside_bind = root.bind("<Button-1>",
                                       self._maybe_close_outside, add="+")
        self._popup.focus_set()

    def _maybe_close_outside(self, event):
        if not self._popup:
            return
        wx = self._popup.winfo_rootx()
        wy = self._popup.winfo_rooty()
        ww = self._popup.winfo_width()
        wh = self._popup.winfo_height()
        if wx <= event.x_root < wx + ww and wy <= event.y_root < wy + wh:
            return
        sx = self.winfo_rootx()
        sy = self.winfo_rooty()
        sw = self.winfo_width()
        sh = self.winfo_height()
        if sx <= event.x_root < sx + sw and sy <= event.y_root < sy + sh:
            return
        self._close()

    def _pick(self, value):
        self.variable.set(value)
        self._close()
        if self.on_pick:
            self.on_pick(value)

    def _close(self):
        if self._popup:
            self._popup.destroy()
            self._popup = None
        if self._shadow:
            self._shadow.destroy()
            self._shadow = None
        if self._outside_bind is not None:
            try:
                self.winfo_toplevel().unbind("<Button-1>", self._outside_bind)
            except Exception:
                pass
            self._outside_bind = None
        self._hover(False)


class ToggleSwitch(tk.Canvas):
    """Compact iOS-style toggle bound to an IntVar."""

    W, H = 38, 20

    def __init__(self, parent, variable):
        super().__init__(parent, width=self.W, height=self.H, bg=BG,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.variable = variable
        self._draw()
        self.bind("<Button-1>", self._toggle)
        try:
            variable.trace_add("write", lambda *a: self._draw())
        except AttributeError:  # Py3.5
            variable.trace("w", lambda *a: self._draw())

    def _rounded(self, x0, y0, x1, y1, r, fill):
        self.create_oval(x0, y0, x0 + 2 * r, y1, fill=fill, outline=fill)
        self.create_oval(x1 - 2 * r, y0, x1, y1, fill=fill, outline=fill)
        self.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, outline=fill)

    def _draw(self):
        self.delete("all")
        on = bool(self.variable.get())
        track = ACCENT if on else TRACK_OFF
        self._rounded(1, 2, self.W - 1, self.H - 2, (self.H - 4) // 2, track)
        kx = self.W - 16 if on else 4
        thumb = "#ffffff" if on else THUMB_OFF
        self.create_oval(kx, 3, kx + 14, self.H - 3,
                         fill=thumb, outline=thumb)

    def _toggle(self, _e=None):
        self.variable.set(0 if self.variable.get() else 1)


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c))) for c in rgb)


def _mix(c1, c2, t):
    """Linear blend c1→c2 at t∈[0,1]."""
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return _rgb_to_hex((r1 + (r2 - r1) * t,
                       g1 + (g2 - g1) * t,
                       b1 + (b2 - b1) * t))


class LogoTile(tk.Canvas):
    """Rounded blue tile with white mic icon — gradient + soft glow."""

    def __init__(self, parent, size=38, radius=10, bg_parent=BG):
        super().__init__(parent, width=size, height=size,
                         bg=bg_parent, highlightthickness=0, bd=0)
        self._size = size
        self._radius = radius
        self._draw()

    def _draw(self):
        s = self._size
        # Vertical gradient: lighter top → darker bottom
        top = ACCENT_HOVER
        bot = ACCENT_DARK
        # Multi-stripe gradient fill clipped to rounded shape
        # First the rounded backdrop in darkest blue
        _round_rect(self, 0, 0, s, s, self._radius,
                    fill=bot, outline="")
        # Draw thin horizontal stripes interpolating top→bot, masked by clipping
        steps = s
        for i in range(steps):
            t = i / max(1, steps - 1)
            col = _mix(top, bot, t)
            # rounded ends: skip pixels outside the rounded shape using x inset
            inset = 0
            if i < self._radius:
                # crude rounded-top fade: shrink width
                dy = self._radius - i
                inset = int(self._radius - (self._radius ** 2 - dy ** 2) ** 0.5)
            elif i > s - self._radius:
                dy = i - (s - self._radius)
                inset = int(self._radius - (self._radius ** 2 - dy ** 2) ** 0.5)
            self.create_line(inset, i, s - inset, i, fill=col)
        # Inner top white-alpha highlight (1px line just inside top edge)
        self.create_line(self._radius, 1, s - self._radius, 1,
                         fill=_mix(top, "#ffffff", 0.35))
        # Mic glyph in white
        cx = s / 2
        cy = s / 2 - 1
        # capsule body
        bw = s * 0.18
        bh = s * 0.22
        _round_rect(self, cx - bw, cy - bh, cx + bw, cy + bh, bw,
                    fill="#ffffff", outline="")
        # stand arc (U shape under capsule)
        ar = s * 0.30
        lw = max(2, int(s * 0.07))
        self.create_arc(cx - ar, cy - ar * 0.30, cx + ar, cy + ar * 1.40,
                        start=200, extent=140, style="arc",
                        outline="#ffffff", width=lw)
        # stem
        self.create_line(cx, cy + ar * 1.05, cx, cy + ar * 1.35,
                         fill="#ffffff", width=lw, capstyle="round")
        # base
        self.create_line(cx - ar * 0.50, cy + ar * 1.35,
                         cx + ar * 0.50, cy + ar * 1.35,
                         fill="#ffffff", width=lw, capstyle="round")


class RoundedIconTile(tk.Canvas):
    """28×28 rounded tile (bg + 1px border) hosting either a canvas icon
    or a short text label (e.g. 'Aa')."""

    def __init__(self, parent, icon_name=None, text=None,
                 size=28, radius=8, bg_parent=BG_INPUT,
                 fill=BG_TILE, border=BORDER_TILE,
                 icon_color=FG_DIM, text_color=FG_DIM,
                 text_font=(FONT_UI, 9, "bold")):
        super().__init__(parent, width=size, height=size, bg=bg_parent,
                         highlightthickness=0, bd=0)
        self._size = size
        self._radius = radius
        self._fill = fill
        self._border = border
        self._icon_name = icon_name
        self._text = text
        self._icon_color = icon_color
        self._text_color = text_color
        self._text_font = text_font
        self._draw()

    def set_parent_bg(self, color):
        self.configure(bg=color)

    def _draw(self):
        self.delete("all")
        s = self._size
        # Border
        _round_rect(self, 0, 0, s, s, self._radius,
                    fill=self._border, outline=self._border)
        # Inner fill
        _round_rect(self, 1, 1, s - 1, s - 1, max(1, self._radius - 1),
                    fill=self._fill, outline=self._fill)
        # Glyph
        if self._text:
            self.create_text(s / 2, s / 2 + 1, text=self._text,
                             fill=self._text_color, font=self._text_font)
        elif self._icon_name:
            self._draw_icon(self._icon_name)

    def _draw_icon(self, name):
        s = self._size
        c = self._icon_color
        # Scale down icon to fit ~14px area centered
        pad = s * 0.28
        gx = s / 2
        gy = s / 2
        if name == "volume":
            # speaker body
            self.create_rectangle(s * 0.30, s * 0.42,
                                  s * 0.40, s * 0.58,
                                  fill=c, outline=c)
            pts = [s * 0.40, s * 0.42, s * 0.52, s * 0.28,
                   s * 0.52, s * 0.72, s * 0.40, s * 0.58]
            self.create_polygon(pts, fill=c, outline=c)
            wave_w = max(1, int(s * 0.07))
            self.create_arc(s * 0.52, s * 0.34, s * 0.70, s * 0.66,
                            start=-50, extent=100, style="arc",
                            outline=c, width=wave_w)
            self.create_arc(s * 0.58, s * 0.26, s * 0.80, s * 0.74,
                            start=-50, extent=100, style="arc",
                            outline=c, width=wave_w)
        elif name == "mic":
            bw = s * 0.13
            bh = s * 0.18
            _round_rect(self, gx - bw, gy - bh, gx + bw, gy + bh, bw,
                        fill=c, outline=c)
            ar = s * 0.23
            lw = max(1, int(s * 0.07))
            self.create_arc(gx - ar, gy - ar * 0.30, gx + ar, gy + ar * 1.40,
                            start=200, extent=140, style="arc",
                            outline=c, width=lw)
            self.create_line(gx, gy + ar * 1.05, gx, gy + ar * 1.35,
                             fill=c, width=lw, capstyle="round")
            self.create_line(gx - ar * 0.50, gy + ar * 1.35,
                             gx + ar * 0.50, gy + ar * 1.35,
                             fill=c, width=lw, capstyle="round")


class GradientDivider(tk.Canvas):
    """1px horizontal line with fade-in/out at the edges."""

    def __init__(self, parent, height=1, bg_parent=BG,
                 mid_color=DIVIDER_MID, edge_color=BG):
        super().__init__(parent, height=height, bg=bg_parent,
                         highlightthickness=0, bd=0)
        self._h = height
        self._mid = mid_color
        self._edge = edge_color
        self.bind("<Configure>", lambda e: self._draw())

    def _draw(self):
        self.delete("all")
        w = max(self.winfo_width(), 2)
        steps = max(20, w)
        mid = w / 2
        for x in range(steps):
            xx = int(x * w / steps)
            t = 1.0 - abs(xx - mid) / mid
            t = max(0.0, min(1.0, t))
            col = _mix(self._edge, self._mid, t * t)
            self.create_line(xx, 0, xx + 1, 0, fill=col, width=self._h)


class CardBackground(tk.Canvas):
    """Outer canvas that draws the floating card backdrop:
    soft drop shadow → rounded card border → card fill, with a faint
    radial-ish blue glow at the top-left and indigo at the bottom-right
    of the outer area."""

    def __init__(self, parent, width, height,
                 card_margin=14, card_radius=18,
                 outer_bg=BG_OUTER, card_fill=BG_CARD,
                 card_border=BORDER_CARD):
        super().__init__(parent, width=width, height=height,
                         bg=outer_bg, highlightthickness=0, bd=0)
        self._cw = width
        self._ch = height
        self._m = card_margin
        self._r = card_radius
        self._outer = outer_bg
        self._fill = card_fill
        self._border = card_border
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h, m, r = self._cw, self._ch, self._m, self._r

        # Faint background glow stripes (cheap radial fake)
        # Top-left blue glow
        for i in range(12):
            t = i / 11
            col = _mix(self._outer, "#1a2940", 0.18 * (1 - t))
            size = 30 + i * 14
            self.create_oval(-size + 40, -size + 30, size + 40, size + 30,
                             fill=col, outline=col)
        # Bottom-right indigo glow
        for i in range(12):
            t = i / 11
            col = _mix(self._outer, "#1c1a3a", 0.14 * (1 - t))
            size = 30 + i * 14
            self.create_oval(w - size - 40, h - size - 30,
                             w + size - 40, h + size - 30,
                             fill=col, outline=col)

        # Drop shadow under card — concentric blurred rounded rects
        for off in range(8, 0, -1):
            alpha_col = _mix(self._outer, SHADOW, 0.45 - off * 0.04)
            _round_rect(self, m - off, m + 2, w - m + off, h - m + off + 2,
                        r + off, fill=alpha_col, outline=alpha_col)

        # Card border + fill
        _round_rect(self, m, m, w - m, h - m, r,
                    fill=self._border, outline=self._border)
        _round_rect(self, m + 1, m + 1, w - m - 1, h - m - 1, r - 1,
                    fill=self._fill, outline=self._fill)


class TitleBarControls(tk.Frame):
    """Minimize / maximize / close buttons for the frameless window."""
    BTN_W, BTN_H = 46, 32

    def __init__(self, parent, on_minimize, on_maximize, on_close):
        super().__init__(parent, bg=BG, bd=0, highlightthickness=0)
        self._max_c = None
        for kind, cmd, is_close in [
            ("min",   on_minimize, False),
            ("max",   on_maximize, False),
            ("close", on_close,    True),
        ]:
            c = self._make_btn(kind, cmd, is_close)
            c.pack(side="left")
            if kind == "max":
                self._max_c = c

    def _make_btn(self, kind, cmd, is_close):
        hover_bg = "#c42b1c" if is_close else BG_HOVER
        c = tk.Canvas(self, width=self.BTN_W, height=self.BTN_H,
                      bg=BG, highlightthickness=0, bd=0, cursor="hand2")
        c._kind = kind
        c._maximized = False

        def draw(hover=False):
            c.delete("all")
            bg = hover_bg if hover else BG
            c.create_rectangle(0, 0, self.BTN_W, self.BTN_H, fill=bg, outline=bg)
            fg = "#ffffff" if hover else FG_DIM
            cx, cy = self.BTN_W // 2, self.BTN_H // 2
            if c._kind == "min":
                c.create_line(cx - 5, cy, cx + 5, cy,
                              fill=fg, width=1)
            elif c._kind == "max":
                if c._maximized:
                    # Restore: back square, then front square offset
                    c.create_rectangle(cx - 2, cy - 5, cx + 5, cy + 2,
                                       outline=fg, fill=bg, width=1)
                    c.create_rectangle(cx - 5, cy - 2, cx + 2, cy + 5,
                                       outline=fg, fill=bg, width=1)
                else:
                    c.create_rectangle(cx - 5, cy - 5, cx + 5, cy + 5,
                                       outline=fg, fill="", width=1)
            elif c._kind == "close":
                c.create_line(cx - 5, cy - 5, cx + 5, cy + 5,
                              fill=fg, width=1, capstyle="round")
                c.create_line(cx + 5, cy - 5, cx - 5, cy + 5,
                              fill=fg, width=1, capstyle="round")

        draw()
        c.bind("<Enter>",    lambda e: draw(True))
        c.bind("<Leave>",    lambda e: draw(False))
        c.bind("<Button-1>", lambda e: cmd())
        c._draw = draw
        return c

    def update_max_icon(self, maximized):
        if self._max_c:
            self._max_c._maximized = maximized
            self._max_c._draw()


class SlimScrollbar(tk.Canvas):
    """Thin rounded dark scrollbar that matches the UI (replaces the chunky
    native tk.Scrollbar). Drives a Canvas through its yview; the scrolled
    widget calls .set(first, last) via its yscrollcommand."""

    def __init__(self, parent, command, width=6, bg_parent=BG):
        super().__init__(parent, width=width, bg=bg_parent,
                         highlightthickness=0, bd=0)
        self._command = command          # canvas.yview
        self._first, self._last = 0.0, 1.0
        self._barw = width               # NOT self._w — Tk uses that internally
        self._drag_dy = None
        self._hover = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<Enter>", lambda e: self._draw(True))
        self.bind("<Leave>", lambda e: self._draw(False))

    def set(self, first, last):
        self._first, self._last = float(first), float(last)
        self._draw()

    def _thumb_bounds(self):
        h = max(self.winfo_height(), 1)
        y0, y1 = self._first * h, self._last * h
        if y1 - y0 < 24:                 # keep the thumb grabbable
            mid = (y0 + y1) / 2
            y0, y1 = mid - 12, mid + 12
            y0 = max(0, min(y0, h - 24)); y1 = y0 + 24
        return y0, y1, h

    def _draw(self, hover=None):
        if hover is not None:
            self._hover = hover
        self.delete("all")
        w = self._barw
        y0, y1, _ = self._thumb_bounds()
        # faint track
        _round_rect(self, 2, 1, w - 2, self.winfo_height() - 1, (w - 4) / 2,
                    fill=BG_INPUT, outline=BG_INPUT)
        col = ACCENT if self._hover else BORDER_BRIGHT
        _round_rect(self, 1, y0, w - 1, y1, (w - 2) / 2, fill=col, outline=col)

    def _on_press(self, e):
        y0, y1, h = self._thumb_bounds()
        if y0 <= e.y <= y1:
            self._drag_dy = e.y - y0
        else:
            self._command("moveto", max(0.0, min(1.0, e.y / max(h, 1))))
            self._drag_dy = None

    def _on_drag(self, e):
        h = max(self.winfo_height(), 1)
        base = e.y if self._drag_dy is None else (e.y - self._drag_dy)
        self._command("moveto", max(0.0, min(1.0, base / h)))


class Slider(tk.Canvas):
    """Rounded dark horizontal slider bound to a StringVar holding a number in
    [lo, hi]. Dragging updates the var (formatted with ``fmt``); an external
    change to the var (e.g. typing in a linked number box) redraws the thumb —
    two-way binding through the shared StringVar."""

    def __init__(self, parent, variable, lo, hi, fmt="%.3f", step=None,
                 height=30, bg_parent=BG):
        super().__init__(parent, height=height, bg=bg_parent,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self.lo, self.hi = float(lo), float(hi)
        self.fmt = fmt
        self.step = step
        self._th = 6          # track thickness
        self._r = 8           # thumb radius
        self._guard = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._set_from_x)
        self.bind("<B1-Motion>", self._set_from_x)
        try:
            variable.trace_add("write", lambda *a: self._draw())
        except AttributeError:
            variable.trace("w", lambda *a: self._draw())

    def _value(self):
        try:
            return max(self.lo, min(self.hi, float(self.var.get())))
        except (ValueError, TypeError):
            return self.lo

    def _frac(self):
        span = self.hi - self.lo
        return (self._value() - self.lo) / span if span else 0.0

    def _bounds(self):
        w = max(self.winfo_width(), 1)
        pad = self._r + 2
        return pad, w - pad

    def _draw(self):
        # The bound StringVar outlives this widget (it lives on App); once the
        # form is rebuilt the trace can still fire on a destroyed canvas.
        if self._guard or not self.winfo_exists():
            return
        self.delete("all")
        h = self.winfo_height()
        cy = h // 2
        x0, x1 = self._bounds()
        _round_rect(self, x0, cy - self._th // 2, x1, cy + self._th // 2,
                    self._th // 2, fill=BG_INPUT, outline=BG_INPUT)
        tx = x0 + self._frac() * (x1 - x0)
        if tx > x0 + 1:
            _round_rect(self, x0, cy - self._th // 2, tx, cy + self._th // 2,
                        self._th // 2, fill=ACCENT, outline=ACCENT)
        self.create_oval(tx - self._r, cy - self._r, tx + self._r, cy + self._r,
                         fill="#ffffff", outline=ACCENT, width=2)

    def _set_from_x(self, e):
        x0, x1 = self._bounds()
        f = max(0.0, min(1.0, (e.x - x0) / max(1, x1 - x0)))
        v = self.lo + f * (self.hi - self.lo)
        if self.step:
            v = round(v / self.step) * self.step
            v = max(self.lo, min(self.hi, v))
        # Avoid the trace bouncing back into a redraw mid-set.
        self._guard = True
        self.var.set(self.fmt % v)
        self._guard = False
        self._draw()


class ColorSwatch(tk.Canvas):
    """Clickable colour swatch bound to a StringVar. Empty string means "auto"
    (per-speaker / language colour). Left-click opens the colour chooser;
    right-click clears back to auto."""

    def __init__(self, parent, variable, size=30, bg_parent=BG):
        super().__init__(parent, width=size, height=size, bg=bg_parent,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self._size = size
        self.bind("<Button-1>", self._pick)
        self.bind("<Button-3>", lambda e: self.var.set(""))
        try:
            variable.trace_add("write", lambda *a: self._draw())
        except AttributeError:
            variable.trace("w", lambda *a: self._draw())
        self._draw()

    def _draw(self):
        if not self.winfo_exists():   # var can outlive the swatch after rebuild
            return
        self.delete("all")
        s = self._size
        c = (self.var.get() or "").strip()
        if c:
            _round_rect(self, 1, 1, s - 1, s - 1, 6, fill=c, outline=BORDER_BRIGHT)
        else:
            _round_rect(self, 1, 1, s - 1, s - 1, 6, fill=BG_INPUT, outline=BORDER)
            self.create_text(s / 2, s / 2 + 1, text="A",
                             fill=FG_MUTE, font=(FONT_UI, 9, "bold"))

    def _pick(self, _e=None):
        try:
            from tkinter import colorchooser
            init = (self.var.get() or "").strip() or "#FFDD00"
            _rgb, hx = colorchooser.askcolor(color=init, title="Caption colour")
            if hx:
                self.var.set(hx.upper())
        except Exception:
            pass


class ReelPreview(tk.Canvas):
    """Live 9:16 reel/short preview showing where the animated caption will
    land on the frame before it is exported to the timeline.

    Bound to the Position X / Position Y / Text+ size StringVars (and,
    optionally, colour + outline/shadow toggles). The preview uses the SAME
    coordinate convention as the Fusion Text+ ``TextPosition`` input the Lua
    macro writes: a bottom-left origin where X=0 is the left edge, X=1 the
    right edge, Y=0 the bottom and Y=1 the top. So what you see here is where
    the caption actually renders on the reel.

    It is also interactive: click or drag inside the frame to set X/Y."""

    SAMPLE = "Subtitle preview"

    def __init__(self, parent, posx_var, posy_var, size_var,
                 color_var=None, outline_var=None, shadow_var=None,
                 outline_color_var=None, outline_thick_var=None,
                 shadow_color_var=None, srt_size_var=None,
                 caption_style_var=None, safe_zone_var=None,
                 width=170, bg_parent=BG):
        self._pw = int(width)
        self._ph = int(round(self._pw * 16 / 9))   # 9:16 portrait
        super().__init__(parent, width=self._pw, height=self._ph, bg=bg_parent,
                         highlightthickness=0, bd=0, cursor="crosshair")
        self.posx_var = posx_var
        self.posy_var = posy_var
        self.size_var = size_var
        self.color_var = color_var
        self.outline_var = outline_var
        self.shadow_var = shadow_var
        self.outline_color_var = outline_color_var
        self.outline_thick_var = outline_thick_var
        self.shadow_color_var = shadow_color_var
        self.srt_size_var = srt_size_var
        self.caption_style_var = caption_style_var
        self.safe_zone_var = safe_zone_var
        self._guard = False
        self._pad = 7            # phone bezel thickness

        # Live-animation state consumed by _draw(). Defaults = fully-shown
        # static caption, so a non-animated style renders exactly as before.
        self._a_alpha = 1.0      # 0..1 opacity (fade)
        self._a_scale = 1.0      # font scale multiplier (pop)
        self._a_dy = 0.0         # vertical offset in px, +down (slide)
        self._a_hl = -1          # karaoke: highlighted word index, -1 = none
        self._a_words = False    # render word-by-word (karaoke layout)
        self._anim_job = None    # pending after() id, or None
        self._anim_start = 0.0

        for v in (posx_var, posy_var, size_var, color_var,
                  outline_var, shadow_var, outline_color_var,
                  outline_thick_var, shadow_color_var, srt_size_var,
                  safe_zone_var):
            if v is None:
                continue
            try:
                v.trace_add("write", lambda *a: self._draw())
            except AttributeError:
                v.trace("w", lambda *a: self._draw())

        # Changing the caption style (re)starts or stops the preview animation.
        if caption_style_var is not None:
            try:
                caption_style_var.trace_add("write", lambda *a: self._restart_anim())
            except AttributeError:
                caption_style_var.trace("w", lambda *a: self._restart_anim())

        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._set_from_xy)
        self.bind("<B1-Motion>", self._set_from_xy)
        self.bind("<Destroy>", self._on_destroy)
        self._restart_anim()

    # ── geometry: the inner "screen" rectangle inside the phone bezel ──────
    def _screen(self):
        p = self._pad
        return p, p, self._pw - p, self._ph - p

    def _fnum(self, var, default):
        try:
            return float(var.get())
        except (ValueError, TypeError, AttributeError):
            return default

    def _set_from_xy(self, e):
        fx0, fy0, fx1, fy1 = self._screen()
        px = (e.x - fx0) / max(1, fx1 - fx0)
        py = (fy1 - e.y) / max(1, fy1 - fy0)     # flip: bottom-left origin
        px = max(0.0, min(1.0, px))
        py = max(0.0, min(1.0, py))
        self._guard = True
        self.posx_var.set("%.3f" % px)
        self.posy_var.set("%.3f" % py)
        self._guard = False
        self._draw()

    # ── live preview animation ─────────────────────────────────────────────
    def _on_destroy(self, _e=None):
        # Stop the after() loop when the canvas goes away (form rebuilds), so
        # no callback fires on a dead widget.
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None

    def _preset_key(self):
        """Animation preset name for the current style, or None if the style
        is static (SRT / Plain Text / unknown)."""
        if self.caption_style_var is None:
            return None
        style, anim = CAPTION_STYLE_MAP.get(self.caption_style_var.get(),
                                            ("srt", ""))
        if style != "animated":
            return None
        return anim if anim in PREVIEW_ANIM else None

    def _reset_anim_state(self):
        self._a_alpha, self._a_scale = 1.0, 1.0
        self._a_dy, self._a_hl, self._a_words = 0.0, -1, False

    def _restart_anim(self):
        """Called on style change (and once at build). Starts the loop for an
        animated style, or freezes the caption fully-shown for a static one."""
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None
        self._reset_anim_state()
        if self._preset_key() is None:
            self._draw()
            return
        self._anim_start = time.monotonic()
        self._tick()

    @staticmethod
    def _ease_out_cubic(u):
        return 1.0 - (1.0 - u) ** 3

    @staticmethod
    def _ease_out_back(u):
        c1 = 1.70158
        c3 = c1 + 1.0
        return 1.0 + c3 * (u - 1.0) ** 3 + c1 * (u - 1.0) ** 2

    def _tick(self):
        """One animation frame: compute the intro / hold / karaoke-sweep phase
        for the elapsed time, update _a_* state, redraw, and reschedule. Loops
        forever until the style changes or the widget is destroyed."""
        self._anim_job = None
        if not self.winfo_exists():
            return
        key = self._preset_key()
        if key is None:
            self._reset_anim_state()
            self._draw()
            return
        p = PREVIEW_ANIM[key]
        nwords = max(1, len(self.SAMPLE.split(" ")))
        intro = max(0.05, p["len"])
        hold = 0.7
        sweep = (nwords * 0.32) if p["hi"] else 0.0
        tail = 1.0
        period = intro + hold + sweep + tail

        t = time.monotonic() - self._anim_start
        if t >= period:                     # loop
            self._anim_start = time.monotonic()
            t = 0.0

        self._reset_anim_state()
        self._a_words = bool(p["hi"])
        if t < intro:
            u = max(0.0, min(1.0, t / intro))
            e = self._ease_out_cubic(u)
            if p["fade"]:
                self._a_alpha = e
            if p["slide"]:
                # rise from ~7% of screen height below its resting spot
                self._a_dy = (1.0 - e) * (self._ph * 0.07)
                if not p["fade"]:
                    self._a_alpha = e
            if p["pop"]:
                self._a_scale = max(0.05, self._ease_out_back(u))
                if not p["fade"]:
                    self._a_alpha = min(1.0, u * 2.0)
        else:
            tt = t - intro
            if p["hi"] and hold <= tt < hold + sweep:
                self._a_hl = int((tt - hold) / 0.32)

        self._draw()
        self._anim_job = self.after(33, self._tick)   # ~30 fps

    @staticmethod
    def _blend(fg, bg, a):
        """Blend hex colour fg over bg by alpha a (a=1 -> fg, a=0 -> bg).
        Used to fake opacity, which tk Canvas text does not support."""
        try:
            a = max(0.0, min(1.0, a))
            fr, fgn, fb = int(fg[1:3], 16), int(fg[3:5], 16), int(fg[5:7], 16)
            br, bgn, bb = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
            r = int(fr * a + br * (1 - a))
            g = int(fgn * a + bgn * (1 - a))
            b = int(fb * a + bb * (1 - a))
            return "#%02x%02x%02x" % (r, g, b)
        except Exception:
            return fg

    def _draw(self):
        # The bound vars live on App and outlive this canvas across rebuilds.
        if not self.winfo_exists():
            return
        self.delete("all")
        w, h = self._pw, self._ph
        fx0, fy0, fx1, fy1 = self._screen()

        # phone body + screen
        _round_rect(self, 1, 1, w - 1, h - 1, 16,
                    fill=BG_TILE, outline=BORDER_BRIGHT)
        _round_rect(self, fx0, fy0, fx1, fy1, 11,
                    fill="#0a0c10", outline=BORDER)

        sw, sh = fx1 - fx0, fy1 - fy0

        # rule-of-thirds guides
        for i in (1, 2):
            gx = fx0 + sw * i / 3
            self.create_line(gx, fy0 + 4, gx, fy1 - 4, fill="#1c212b")
            gy = fy0 + sh * i / 3
            self.create_line(fx0 + 4, gy, fx1 - 4, gy, fill="#1c212b")
        # safe-area box (5% inset, dashed)
        mx, my = sw * 0.05, sh * 0.05
        self.create_rectangle(fx0 + mx, fy0 + my, fx1 - mx, fy1 - my,
                              outline="#2a3341", dash=(2, 3))

        # platform-UI "safe zone" overlay: mimics where Reels/Shorts/TikTok put
        # their own controls, so you can judge whether the caption collides with
        # them. Right column = like/comment/share; bottom band = caption/handle.
        if self.safe_zone_var is None or bool(int(self._fnum(self.safe_zone_var, 1))):
            ui = "#3a4453"
            cxr = fx1 - sw * 0.11            # right-side action column
            r = max(3, sw * 0.045)
            for j, fy in enumerate((0.42, 0.55, 0.68, 0.80)):
                yc = fy1 - sh * fy
                self.create_oval(cxr - r, yc - r, cxr + r, yc + r,
                                 outline=ui, width=1)
            # bottom caption/handle band (kept clear on a pro layout)
            by = fy1 - sh * 0.16
            self.create_line(fx0 + mx, by, fx1 - sw * 0.20, by, fill=ui, dash=(1, 2))
            self.create_rectangle(fx0 + mx, by + 3, fx0 + sw * 0.44, by + 8,
                                  outline=ui, width=1)

        # caption position (bottom-left origin, matches Fusion TextPosition)
        px = max(0.0, min(1.0, self._fnum(self.posx_var, 0.5)))
        py = max(0.0, min(1.0, self._fnum(self.posy_var, 0.15)))
        cx_t = fx0 + px * sw
        cy_t = fy1 - py * sh

        # font size from Text+ size (relative to frame height, clamped so the
        # sample text stays readable inside the little preview)
        ts = self._fnum(self.size_var, 0.18)
        fontpx = int(round(ts * sh))
        fontpx = max(8, min(int(sh * 0.28), fontpx))
        col = (self.color_var.get().strip() if self.color_var else "") or "#ffffff"

        # pop animation scales the whole caption up into place
        disp_px = max(6, int(round(fontpx * self._a_scale)))
        try:
            font = tkfont.Font(family=FONT_UI, size=-disp_px, weight="bold")
        except Exception:
            font = (FONT_UI, 9, "bold")

        # measure, then shrink the font if the sample would overflow the frame
        # width (real reel captions wrap/scale to fit) so nothing is clipped
        try:
            tw = font.measure(self.SAMPLE)
            th = font.metrics("linespace")
            avail = sw * 0.90
            if tw > avail and tw > 0:
                disp_px = max(7, int(disp_px * avail / tw))
                font = tkfont.Font(family=FONT_UI, size=-disp_px,
                                   weight="bold")
                tw = font.measure(self.SAMPLE)
                th = font.metrics("linespace")
        except Exception:
            tw, th = disp_px * 8, disp_px
        half_w, half_h = tw / 2 + 5, th / 2 + 3
        # clamp resting spot inside the frame, then apply the slide offset
        cx = max(fx0 + half_w, min(fx1 - half_w, cx_t))
        cy = max(fy0 + half_h, min(fy1 - half_h, cy_t)) + self._a_dy

        # colours (alpha-blended over the pill bg to fake opacity for fades)
        pill = "#11151c"
        a = self._a_alpha
        col_a = self._blend(col, pill, a)
        outline_on = (not self.outline_var) or bool(int(self._fnum(self.outline_var, 1)))
        shadow_on = self.shadow_var and bool(int(self._fnum(self.shadow_var, 1)))
        ocol = ((self.outline_color_var.get().strip()
                 if self.outline_color_var else "") or "#000000")
        scol = ((self.shadow_color_var.get().strip()
                 if self.shadow_color_var else "") or "#000000")
        ocol_a = self._blend(ocol, pill, a)
        scol_a = self._blend(scol, pill, a)
        acc_a = self._blend(ACCENT, pill, a)
        # Outline thickness (macro scale, default 0.08) -> preview pixels
        othick = self._fnum(self.outline_thick_var, 0.08)
        opx = max(1, int(round(othick / 0.08))) if othick > 0 else 0

        # translucent-ish backing pill for legibility (fades in with the text)
        _round_rect(self, cx - half_w, cy - half_h, cx + half_w, cy + half_h,
                    5, fill=self._blend(pill, "#0a0c10", 0.35 + 0.65 * a),
                    outline="")

        # draw one text token with its shadow + outline halo underneath
        def _token(tx, ty, text, fill):
            if shadow_on:
                self.create_text(tx + 2 + opx, ty + 2 + opx, text=text,
                                 font=font, fill=scol_a)
            if outline_on and opx:
                for dx in range(-opx, opx + 1):
                    for dy in range(-opx, opx + 1):
                        if dx or dy:
                            self.create_text(tx + dx, ty + dy, text=text,
                                             font=font, fill=ocol_a)
            self.create_text(tx, ty, text=text, font=font, fill=fill)

        if self._a_words:
            # karaoke layout: lay the words out centred and light up the
            # currently-spoken one in the accent colour as the sweep advances
            words = self.SAMPLE.split(" ")
            try:
                spw = font.measure(" ")
                wws = [font.measure(wd) for wd in words]
            except Exception:
                spw, wws = disp_px, [disp_px * len(wd) for wd in words]
            total = sum(wws) + spw * (len(words) - 1)
            x = cx - total / 2.0
            for i, wd in enumerate(words):
                wx = x + wws[i] / 2.0
                _token(wx, cy, wd, acc_a if i == self._a_hl else col_a)
                x += wws[i] + spw
        else:
            _token(cx, cy, self.SAMPLE, col_a)

        # anchor dot + live X/Y readout
        self.create_oval(cx - 2, cy - 2, cx + 2, cy + 2,
                         fill=ACCENT, outline="")
        self.create_text(fx0 + 5, fy1 - 7,
                         text="X %.2f   Y %.2f" % (px, py),
                         anchor="w", fill=FG_MUTE, font=(FONT_UI, 7))
        # original SRT text size, shown top-left so it is visible before export
        if self.srt_size_var is not None:
            srt = (self.srt_size_var.get() or "").strip()
            if srt:
                self.create_text(fx0 + 5, fy0 + 7,
                                 text="SRT size " + srt,
                                 anchor="w", fill=FG_MUTE,
                                 font=(FONT_UI, 7))


class StyleThumb(tk.Canvas):
    """Tiny 9:16 thumbnail for a reel-style preset: a mini phone frame with a
    sample word drawn in the style's fill colour and outline halo. Purely
    decorative — clicking is handled by the parent row."""

    def __init__(self, parent, fill, outline, width=40, bg_parent=BG):
        self._pw = int(width)
        self._ph = int(round(self._pw * 16 / 9))
        super().__init__(parent, width=self._pw, height=self._ph,
                         bg=bg_parent, highlightthickness=0, bd=0,
                         cursor="hand2")
        self._fill, self._outline = fill, outline
        self.bind("<Configure>", lambda e: self._draw())
        self._draw()

    def _draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        w, h = self._pw, self._ph
        _round_rect(self, 1, 1, w - 1, h - 1, 7, fill=BG_TILE,
                    outline=BORDER_BRIGHT)
        _round_rect(self, 4, 4, w - 4, h - 4, 5, fill="#0a0c10",
                    outline=BORDER)
        cx, cy = w / 2, h * 0.6
        fpx = max(9, int(h * 0.26))
        try:
            font = tkfont.Font(family=FONT_UI, size=-fpx, weight="bold")
        except Exception:
            font = (FONT_UI, 10, "bold")
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            self.create_text(cx + dx, cy + dy, text="Aa", font=font,
                             fill=self._outline)
        self.create_text(cx, cy, text="Aa", font=font, fill=self._fill)


class SearchDropdown(RoundedField):
    """Dropdown for long lists (system fonts): rounded trigger that opens a
    popup with a search box and a scrollable list. ``values_provider`` is a
    callable evaluated at open time so the list is always current."""

    LIST_ROWS = 10

    def __init__(self, parent, values_provider, variable):
        super().__init__(parent, height=48, radius=12, padx=14,
                         bg_parent=BG, fill=BG_INPUT, border=BORDER)
        self.values_provider = values_provider
        self.variable = variable
        self._popup = None
        self._outside_bind = None
        body = tk.Frame(self, bg=BG_INPUT)
        self._label = tk.Label(body, textvariable=variable, bg=BG_INPUT,
                               fg=FG, font=(FONT_UI, 10), anchor="w",
                               cursor="hand2")
        self._label.pack(side="left", fill="x", expand=True)
        self._body = body
        self._arrow = IconCanvas(self, ICO_CHEVRON_DOWN, size=12,
                                 color=FG_MUTE, bg_parent=BG_INPUT)
        self._arrow.configure(cursor="hand2")
        self.set_child(body, right_child=self._arrow)
        for w in (self, body, self._label, self._arrow):
            w.bind("<Button-1>", self._toggle)

    def _toggle(self, _e=None):
        if self._popup:
            self._close()
        else:
            self._open()

    def _open(self):
        self.update_idletasks()
        values = list(self.values_provider())
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 4
        w = max(self.winfo_width(), 220)

        self._popup = tk.Toplevel(self)
        self._popup.overrideredirect(True)
        try:
            self._popup.attributes("-topmost", True)
        except Exception:
            pass
        self._popup.configure(bg=BORDER_CARD)
        inner = tk.Frame(self._popup, bg=BG_INPUT)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        self._search_var = tk.StringVar()
        search = tk.Entry(inner, textvariable=self._search_var,
                          bg=BG_TILE, fg=FG, insertbackground=ACCENT,
                          relief="flat", bd=0, highlightthickness=1,
                          highlightbackground=BORDER, highlightcolor=ACCENT,
                          font=(FONT_UI, 10))
        search.pack(fill="x", padx=8, pady=(8, 6), ipady=5)

        lwrap = tk.Frame(inner, bg=BG_INPUT)
        lwrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        lb = tk.Listbox(lwrap, bg=BG_INPUT, fg=FG, relief="flat", bd=0,
                        highlightthickness=0, activestyle="none",
                        selectbackground=BG_HOVER, selectforeground=FG,
                        font=(FONT_UI, 10), height=self.LIST_ROWS)
        sb = SlimScrollbar(lwrap, lb.yview, width=6, bg_parent=BG_INPUT)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def _fill(*_a):
            ft = self._search_var.get().lower()
            lb.delete(0, "end")
            for v in values:
                if ft in v.lower():
                    lb.insert("end", v)
        _fill()
        try:
            self._search_var.trace_add("write", _fill)
        except AttributeError:
            self._search_var.trace("w", _fill)

        def _pick(_e=None):
            sel = lb.curselection()
            if sel:
                self.variable.set(lb.get(sel[0]))
            self._close()
        lb.bind("<ButtonRelease-1>", _pick)
        lb.bind("<Return>", _pick)

        def _pick_first(_e=None):
            if lb.size() > 0:
                self.variable.set(lb.get(0))
                self._close()
        search.bind("<Return>", _pick_first)
        self._popup.bind("<Escape>", lambda e: self._close())

        self._popup.update_idletasks()
        h = inner.winfo_reqheight() + 2
        self._popup.geometry("%dx%d+%d+%d" % (w, h, x, y))
        search.focus_set()

        root = self.winfo_toplevel()
        self._outside_bind = root.bind("<Button-1>",
                                       self._maybe_close_outside, add="+")

    def _maybe_close_outside(self, event):
        if not self._popup:
            return
        px, py = self._popup.winfo_rootx(), self._popup.winfo_rooty()
        pw, ph = self._popup.winfo_width(), self._popup.winfo_height()
        if px <= event.x_root < px + pw and py <= event.y_root < py + ph:
            return
        sx, sy = self.winfo_rootx(), self.winfo_rooty()
        sw2, sh2 = self.winfo_width(), self.winfo_height()
        if sx <= event.x_root < sx + sw2 and sy <= event.y_root < sy + sh2:
            return
        self._close()

    def _close(self):
        if self._popup:
            self._popup.destroy()
            self._popup = None
        if self._outside_bind is not None:
            try:
                self.winfo_toplevel().unbind("<Button-1>", self._outside_bind)
            except Exception:
                pass
            self._outside_bind = None


class App:
    def __init__(self, root, prompt_data, selection_path, args_path,
                 done_path, log_path, python_exe, script_path,
                 result_path, ack_path):
        self.root = root
        self.prompt_data = prompt_data
        self.selection_path = selection_path
        self.args_path = args_path
        self.done_path = done_path
        self.log_path = log_path
        self.python_exe = python_exe
        self.script_path = script_path
        self.result_path = result_path
        self.ack_path = ack_path

        self.cancelled = False
        self.exit_code = None
        self.q = queue.Queue()
        self._anim_target = 0.0
        self._anim_value = 0.0

        root.title("Audio2SRT")
        root.configure(bg=BG_OUTER)
        if FRAMELESS:
            root.resizable(False, False)
            try:
                root.overrideredirect(True)
            except Exception:
                pass
        else:
            # Native frame on macOS/Linux: real title bar, resize border,
            # working keyboard focus (see FRAMELESS note at the top).
            root.resizable(True, True)
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass

        # Single flat card window — no outer shadow/glow ring. Landscape
        # two-column layout: main controls on the left, settings on the right.
        # The field area scrolls if needed and the window is clamped to the
        # screen so the bottom button bar is always visible; resizable grip.
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        W = min(1000, sw - 140)
        H = min(965, sh - 70)
        self._min_w, self._min_h = 780, 480
        root.geometry("%dx%d+%d+%d" % (W, H, (sw - W) // 2, max(20, (sh - H) // 2)))
        root.configure(bg=BG)
        self._maximized = False
        self._normal_geo = "%dx%d+%d+%d" % (W, H, (sw - W) // 2, max(20, (sh - H) // 2))

        self._setup_styles()

        self.subtitle_var = tk.StringVar(value="Subtitle generator")

        # Body fills the whole window with internal padding
        self.body = tk.Frame(root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=22, pady=22)

        # Drag support: clicking anywhere on body bg (not on widgets)
        # moves the frameless window.
        self._drag_off = (0, 0)
        self.body.bind("<Button-1>", self._drag_start)
        self.body.bind("<B1-Motion>", self._drag_move)

        self._build_form()
        self._controls = None
        if FRAMELESS:
            self._controls = TitleBarControls(root,
                on_minimize=self._minimize,
                on_maximize=self._toggle_maximize,
                on_close=self._on_cancel)
            self._controls.place(relx=1.0, y=0, anchor="ne")

            # Resize grip (frameless windows have no native resize border). A
            # small diagonal-hatch handle in the bottom-right corner resizes.
            self._grip = tk.Canvas(root, width=16, height=16, bg=BG,
                                   highlightthickness=0, bd=0, cursor="sizing")
            for d in (5, 9, 13):
                self._grip.create_line(16 - d, 15, 15, 16 - d, fill=BORDER_BRIGHT)
            self._grip.place(relx=1.0, rely=1.0, x=-3, y=-3, anchor="se")
            self._grip.bind("<Button-1>", self._resize_start)
            self._grip.bind("<B1-Motion>", self._resize_move)

        root.after(60, lambda: _steal_focus(root))
        root.after(600, lambda: _keep_topmost(root))
        root.protocol("WM_DELETE_WINDOW", self._on_cancel)

    # ── window drag (frameless only — native frames drag themselves) ─────
    def _drag_start(self, e):
        if not FRAMELESS:
            return
        self._drag_off = (e.x_root - self.root.winfo_x(),
                          e.y_root - self.root.winfo_y())

    def _drag_move(self, e):
        if not FRAMELESS:
            return
        x = e.x_root - self._drag_off[0]
        y = e.y_root - self._drag_off[1]
        self.root.geometry("+%d+%d" % (x, y))

    # ── window resize (frameless corner grip) ────────────────────────────
    def _resize_start(self, e):
        self._resize_ref = (e.x_root, e.y_root,
                            self.root.winfo_width(), self.root.winfo_height())

    def _resize_move(self, e):
        sx, sy, sw, sh = self._resize_ref
        nw = max(self._min_w, sw + (e.x_root - sx))
        nh = max(self._min_h, sh + (e.y_root - sy))
        self.root.geometry("%dx%d" % (nw, nh))

    def _minimize(self):
        if not FRAMELESS:
            self.root.iconify()
            return
        self.root.overrideredirect(False)
        self.root.iconify()
        def _check():
            try:
                if self.root.state() == "normal":
                    self.root.overrideredirect(True)
                    _steal_focus(self.root)
                else:
                    self.root.after(200, _check)
            except Exception:
                pass
        self.root.after(300, _check)

    def _toggle_maximize(self):
        if self._maximized:
            self.root.geometry(self._normal_geo)
            self._maximized = False
        else:
            self._normal_geo = "%dx%d+%d+%d" % (
                self.root.winfo_width(), self.root.winfo_height(),
                self.root.winfo_x(), self.root.winfo_y())
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            self.root.geometry("%dx%d+0+0" % (sw, sh))
            self._maximized = True
        self._controls.update_max_icon(self._maximized)

    def _make_draggable(self, widget):
        widget.bind("<Button-1>", self._drag_start)
        widget.bind("<B1-Motion>", self._drag_move)

    def _setup_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        # Only ttk widget still in use: progress bar.
        style.configure(
            "Audio2SRT.Horizontal.TProgressbar",
            troughcolor=BG_INPUT,
            background=ACCENT,
            bordercolor=BG,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            thickness=8,
        )

    # ── helpers ───────────────────────────────────────────────────────────
    def _clear_body(self):
        for w in self.body.winfo_children():
            w.destroy()

    def _make_button(self, parent, text, command, primary=False):
        return RoundedButton(parent, text, command, primary=primary, bg_parent=BG)

    # ── Phase 1: form ─────────────────────────────────────────────────────
    def _build_form(self):
        self._clear_body()
        items    = self.prompt_data.get("items", [])
        defaults = self.prompt_data.get("defaults", {})

        # ── Header: title + subtitle (also drag handle, no icons) ────────
        header = tk.Frame(self.body, bg=BG)
        header.pack(fill="x", pady=(0, 14))
        title_lbl = tk.Label(header, text="Audio2SRT", bg=BG, fg=FG,
                             font=(FONT_UI, 16, "bold"))
        title_lbl.pack(anchor="w")
        subtitle_lbl = tk.Label(header, text="Subtitle generator",
                                bg=BG, fg=FG_MUTE,
                                font=(FONT_UI, 9))
        subtitle_lbl.pack(anchor="w")
        for w in (header, title_lbl, subtitle_lbl):
            self._make_draggable(w)

        # Gradient divider under header
        div = GradientDivider(self.body, height=1, bg_parent=BG)
        div.pack(fill="x", pady=(0, 12))

        # ── Button bar pinned to the BOTTOM (packed before the scroll area
        #    so it always reserves its space and can never be clipped). ────
        btn_frm = tk.Frame(self.body, bg=BG)
        btn_frm.pack(fill="x", side="bottom", pady=(12, 0))
        btn_frm.grid_columnconfigure(0, weight=1, uniform="btn")
        btn_frm.grid_columnconfigure(1, weight=2, uniform="btn")
        # Restyles the Text+ caption clips already on the timeline (size,
        # position, outline/shadow) without re-transcribing anything.
        update_btn = RoundedButton(btn_frm, "Update Captions",
                                   lambda: self._on_submit("update"),
                                   primary=False, bg_parent=BG, height=34)
        update_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5), pady=(0, 8))
        # Removes the caption track created by the most recent generation.
        undo_btn = RoundedButton(btn_frm, "Undo Last Captions",
                                 lambda: self._on_submit("undo"),
                                 primary=False, bg_parent=BG, height=34)
        undo_btn.grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=(0, 8))
        cancel = RoundedButton(btn_frm, "Cancel", self._on_cancel,
                               primary=False, bg_parent=BG, height=40)
        cancel.grid(row=1, column=0, sticky="ew", padx=(0, 5))
        generate = RoundedButton(btn_frm, "Generate SRT",
                                 self._on_submit, primary=True,
                                 bg_parent=BG, height=40)
        generate.grid(row=1, column=1, sticky="ew", padx=(5, 0))

        # ── Fixed preview panel (docked right, NEVER scrolls) ────────────
        # Packed BEFORE the scroll area so side="right" reserves its column;
        # the reel preview and all colour/outline controls live here so they
        # stay put on screen while the rest of the form scrolls.
        # Fixed width + pack_propagate(False) so the Canvas-based sliders/
        # toggles inside (which have a large natural width) fill the column
        # instead of dictating it; fill="y" gives it the full body height.
        self._fixed_panel = tk.Frame(self.body, bg=BG, width=280)
        self._fixed_panel.pack(side="right", fill="y", padx=(14, 2))
        self._fixed_panel.pack_propagate(False)

        # ── Scrollable field area (fills the space between header/buttons) ─
        scroll_wrap = tk.Frame(self.body, bg=BG)
        scroll_wrap.pack(side="left", fill="both", expand=True)
        canvas = tk.Canvas(scroll_wrap, bg=BG, highlightthickness=0, bd=0)
        vsb = SlimScrollbar(scroll_wrap, canvas.yview, width=6)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(0, 6))
        # The scrollbar's track always keeps its space reserved (never
        # pack_forget'd) so the canvas width — and therefore every widget's
        # allocated width inside it — never shifts when content grows past the
        # viewport. Toggling that space in and out was exactly what made
        # right-column widgets render flush against (or under) the scrollbar.
        # When nothing needs scrolling the thumb simply fills the whole track,
        # which reads as a normal, harmless "you're seeing everything" cue.
        vsb.pack(side="right", fill="y", padx=(6, 2))
        self._scroll_canvas = canvas

        form = tk.Frame(canvas, bg=BG)
        form_id = canvas.create_window((0, 0), window=form, anchor="nw")

        def _sync_scrollregion(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        form.bind("<Configure>", _sync_scrollregion)
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(form_id, width=e.width))

        def _on_wheel(e):
            if not canvas.winfo_exists():
                return
            try:
                # Ignore wheel events happening inside a popup (font list,
                # dropdown) — those scroll their own list, not the form.
                if e.widget.winfo_toplevel() is not self.root:
                    return
            except Exception:
                return
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_wheel)

        # ── Landscape two-column layout: main controls | settings ────────
        cols = tk.Frame(form, bg=BG)
        cols.pack(fill="both", expand=True)
        # minsize guarantees each column keeps a sane width even if the window
        # is shrunk toward its minimum — without it, a widget with a fixed
        # natural width (e.g. a button's measured text) could push its column
        # wider than its share and get clipped at the window's right edge.
        cols.grid_columnconfigure(0, weight=1, uniform="cols", minsize=210)
        cols.grid_columnconfigure(2, weight=1, uniform="cols", minsize=210)
        left = tk.Frame(cols, bg=BG)
        left.grid(row=0, column=0, sticky="new", padx=(0, 16))
        tk.Frame(cols, bg=DIVIDER, width=1).grid(row=0, column=1, sticky="ns")
        right = tk.Frame(cols, bg=BG)
        right.grid(row=0, column=2, sticky="new", padx=(16, 6))

        # ── SUBTITLE SETTINGS (3-column grid) ────────────────────────────
        tk.Label(left, text="S U B T I T L E   S E T T I N G S",
                 bg=BG, fg=FG_LABEL,
                 font=(FONT_UI, 8, "bold")).pack(anchor="w")

        default_settings = defaults.get("settings", "25,1,2")
        parts = [p.strip() for p in default_settings.split(",")]
        while len(parts) < 3:
            parts.append("")

        grid = tk.Frame(left, bg=BG)
        grid.pack(fill="x", pady=(10, 20))
        for i in range(3):
            grid.grid_columnconfigure(i, weight=1, uniform="settings_g")

        # Row 1: the classic chars/lines/secs. Row 2: readability controls
        # ported from AutoSubs — min on-screen time, a reading-speed cap
        # (characters per second; 0 disables) and the subtitle text size.
        self.min_var      = tk.StringVar(value=str(defaults.get("min_secs", "0.4")))
        self.cps_var      = tk.StringVar(value=str(defaults.get("cps", "25")))
        self.textsize_var = tk.StringVar(value=str(defaults.get("text_size", "55")))
        rows = (
            (("Max chars", None), ("Max lines", None), ("Max sec", None)),
            (("Min sec", self.min_var), ("CPS limit", self.cps_var),
             ("Text size", self.textsize_var)),
        )
        self.settings_vars = []
        first_input = None
        for rno, row in enumerate(rows):
            for i, cell in enumerate(row):
                if cell is None:
                    continue
                lbl_text, var = cell
                col = tk.Frame(grid, bg=BG)
                pad_l = 0 if i == 0 else 4
                pad_r = 0 if i == 2 else 4
                col.grid(row=rno, column=i, sticky="ew", padx=(pad_l, pad_r),
                         pady=(0 if rno == 0 else 10, 0))
                tk.Label(col, text=lbl_text, bg=BG, fg=FG_DIM,
                         font=(FONT_UI, 9)).pack(anchor="w", pady=(0, 4))
                if var is None:
                    var = tk.StringVar(value=parts[i])
                    self.settings_vars.append(var)
                inp = SmallInput(col, var)
                inp.pack(fill="x")
                if first_input is None:
                    first_input = inp

        # ── LANGUAGE row ─────────────────────────────────────────────────
        # Selecting Hindi pins the ElevenLabs transcription to Devanagari, so
        # even code-switched English words come back in Devanagari script.
        tk.Label(left, text="L A N G U A G E",
                 bg=BG, fg=FG_LABEL,
                 font=(FONT_UI, 8, "bold")).pack(anchor="w")
        self.lang_var = tk.StringVar(
            value=defaults.get("lang", LANGUAGES[0]))
        if self.lang_var.get() not in LANGUAGES:
            self.lang_var.set(LANGUAGES[0])
        Dropdown(left, LANGUAGES, self.lang_var).pack(fill="x", pady=(10, 14))

        # ── CAPTION STYLE row ────────────────────────────────────────────
        # SRT = subtitle track (per-speaker colour). Animated = Fusion Text+
        # template clips with per-word highlighting (needs the AutoSubs macro).
        tk.Label(left, text="C A P T I O N   S T Y L E",
                 bg=BG, fg=FG_LABEL,
                 font=(FONT_UI, 8, "bold")).pack(anchor="w")
        self.caption_var = tk.StringVar(
            value=defaults.get("caption_style", CAPTION_STYLES[0]))
        if self.caption_var.get() not in CAPTION_STYLES:
            self.caption_var.set(CAPTION_STYLES[0])
        Dropdown(left, CAPTION_STYLES, self.caption_var).pack(
            fill="x", pady=(10, 14))

        # ── AUDIO TRACK row ──────────────────────────────────────────────
        tk.Label(left, text="A U D I O   T R A C K",
                 bg=BG, fg=FG_LABEL,
                 font=(FONT_UI, 8, "bold")).pack(anchor="w")
        self.combo_var = tk.StringVar(value=items[0] if items else "")
        Dropdown(left, items, self.combo_var).pack(fill="x", pady=(10, 14))

        # ── VIDEO TRACK row (where animated captions are placed) ─────────
        # Populated with the timeline's existing video tracks plus "New track".
        # Only used by the animated caption styles (SRT ignores it).
        video_items = self.prompt_data.get("video_tracks", ["New track"])
        tk.Label(left, text="V I D E O   T R A C K   ( A N I M A T E D )",
                 bg=BG, fg=FG_LABEL,
                 font=(FONT_UI, 8, "bold")).pack(anchor="w")
        self.vtrack_var = tk.StringVar(
            value=video_items[0] if video_items else "New track")
        Dropdown(left, video_items, self.vtrack_var).pack(fill="x", pady=(10, 14))

        # ── Toggle rows helper (parent-aware) ────────────────────────────
        def _toggle_row(parent, label, var, pady):
            row = IconRow(parent, None, label)
            switch = ToggleSwitch(row, var)
            switch.configure(bg=BG_INPUT)
            row.set_child(row._body, right_child=switch)
            row._lbl.configure(cursor="hand2", fg=FG)
            row._lbl.bind("<Button-1>", lambda e: switch._toggle())
            if row._ico is not None:
                row._ico.configure(cursor="hand2")
                row._ico.bind("<Button-1>", lambda e: switch._toggle())
            row.pack(fill="x", pady=pady)

        def _section(parent, label):
            tk.Label(parent, text=label, bg=BG, fg=FG_LABEL,
                     font=(FONT_UI, 8, "bold")).pack(anchor="w", pady=(4, 6))

        # All secondary options live behind this Settings button so the main
        # form stays clean. Clicking expands/collapses the panel in place.
        self.punct_var   = tk.IntVar(value=int(defaults.get("punct", 0)))
        self.diarize_var = tk.IntVar(value=int(defaults.get("diarize", 0)))
        self.censor_var  = tk.IntVar(value=int(defaults.get("censor", 0)))
        self.outline_var = tk.IntVar(value=int(defaults.get("outline", 1)))
        self.shadow_var  = tk.IntVar(value=int(defaults.get("shadow", 1)))
        # Text+ size is the macro's native relative scale (Fusion inspector
        # "Size", e.g. 0.18) — applied directly, no conversion.
        self.tp_size_var = tk.StringVar(value=str(defaults.get("tp_size", "0.18")))
        # Professional reel/short default: centred horizontally, lower third
        # (clears the platform UI at the very bottom while staying readable).
        self.tp_posx_var = tk.StringVar(value=str(defaults.get("tp_posx", "0.5")))
        self.tp_posy_var = tk.StringVar(value=str(defaults.get("tp_posy", "0.28")))
        self.color_var   = tk.StringVar(value=str(defaults.get("color", "")))
        # Text+ outline / shadow styling (empty colour = keep macro default)
        self.outline_color_var = tk.StringVar(
            value=str(defaults.get("outline_color", "")))
        self.outline_thick_var = tk.StringVar(
            value=str(defaults.get("outline_thick", "0.08")))
        self.shadow_color_var = tk.StringVar(
            value=str(defaults.get("shadow_color", "")))
        # Max words per caption clip (0 = no limit; animated captions only).
        self.words_per_var = tk.StringVar(
            value=str(defaults.get("words_per", "0")))
        # Keyword highlight: words (space/comma separated) shown in hl_color.
        self.hl_words_var = tk.StringVar(value=str(defaults.get("hl_words", "")))
        self.hl_color_var = tk.StringVar(value=str(defaults.get("hl_color", "")))
        # Draw a platform-UI "safe zone" overlay in the preview.
        self.safe_zone_var = tk.IntVar(value=int(defaults.get("safe_zone", 1)))
        # Stop for a caption review/edit pass before applying to the timeline.
        self.review_var = tk.IntVar(value=int(defaults.get("review", 1)))
        # Silence cut: render a tightened "silence removed" copy of the source
        # clip (plus a re-timed SRT) into the Media Pool after captioning.
        self.silence_var = tk.IntVar(value=int(defaults.get("silence", 0)))
        self.sil_thr_var = tk.StringVar(value=str(defaults.get("sil_thr", "-30")))
        self.sil_gap_var = tk.StringVar(value=str(defaults.get("sil_gap", "0.5")))
        self.sil_pad_var = tk.StringVar(value=str(defaults.get("sil_pad", "0.05")))
        self.font_var    = tk.StringVar(value=str(defaults.get("font", CAPTION_FONTS[0])))
        self.font_style_var = tk.StringVar(
            value=str(defaults.get("font_style", "Auto")))
        if self.font_style_var.get() not in FONT_STYLES:
            self.font_style_var.set("Auto")

        # In the landscape layout the settings live in the always-visible
        # right column — no collapsible button needed anymore.
        adv_panel = right

        # ── PRESETS (inside Settings) ─────────────────────────────────────
        # Named bundles of all tunable settings (not the timeline track
        # choices), saved as JSON next to the script in a presets/ folder.
        _section(adv_panel, "P R E S E T S")
        presets_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "presets")
        preset_map = {
            "min_secs": self.min_var, "cps": self.cps_var,
            "text_size": self.textsize_var, "lang": self.lang_var,
            "caption": self.caption_var, "punct": self.punct_var,
            "diarize": self.diarize_var, "censor": self.censor_var,
            "outline": self.outline_var, "shadow": self.shadow_var,
            "tp_size": self.tp_size_var, "tp_posx": self.tp_posx_var,
            "tp_posy": self.tp_posy_var, "color": self.color_var,
            "outline_color": self.outline_color_var,
            "outline_thick": self.outline_thick_var,
            "shadow_color": self.shadow_color_var,
            "words_per": self.words_per_var,
            "hl_words": self.hl_words_var, "hl_color": self.hl_color_var,
            "safe_zone": self.safe_zone_var, "review": self.review_var,
            "font": self.font_var, "font_style": self.font_style_var,
            "silence": self.silence_var, "sil_thr": self.sil_thr_var,
            "sil_gap": self.sil_gap_var, "sil_pad": self.sil_pad_var,
        }

        def _list_presets():
            try:
                return sorted(f[:-5] for f in os.listdir(presets_dir)
                              if f.endswith(".json"))
            except Exception:
                return []

        self.preset_pick_var = tk.StringVar(value="Select a preset")
        self.preset_name_var = tk.StringVar(value="")
        preset_dd = Dropdown(adv_panel, _list_presets() or ["(none saved)"],
                             self.preset_pick_var)
        preset_dd.pack(fill="x", pady=(0, 6))
        PillEntry(adv_panel, self.preset_name_var).pack(fill="x", pady=(0, 6))
        tk.Label(adv_panel, text="Type a name and Save, or pick one to load.",
                 bg=BG, fg=FG_MUTE, font=(FONT_UI, 8)).pack(anchor="w",
                                                               pady=(0, 4))

        def _apply_preset(name):
            if not name or name in ("Select a preset", "(none saved)"):
                return
            try:
                with open(os.path.join(presets_dir, name + ".json"),
                          encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                return
            settings = data.pop("_settings", None)
            for k, v in data.items():
                if k in preset_map:
                    try:
                        preset_map[k].set(v)
                    except Exception:
                        pass
            if settings and len(self.settings_vars) == len(settings):
                for var, val in zip(self.settings_vars, settings):
                    var.set(val)
            self.root.after(0, _sync_scrollregion)

        preset_dd.on_pick = _apply_preset

        def _save_preset():
            name = (self.preset_name_var.get().strip()
                    or self.preset_pick_var.get().strip())
            if not name or name in ("Select a preset", "(none saved)"):
                return
            try:
                os.makedirs(presets_dir, exist_ok=True)
                data = {k: var.get() for k, var in preset_map.items()}
                data["_settings"] = [v.get() for v in self.settings_vars]
                with open(os.path.join(presets_dir, name + ".json"),
                          "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=1)
                preset_dd.values = _list_presets()
                self.preset_pick_var.set(name)
                self.preset_name_var.set("")
            except Exception as e:
                messagebox.showerror("Audio2SRT", "Cannot save preset: %s" % e)

        def _remove_preset():
            name = self.preset_pick_var.get().strip()
            try:
                p = os.path.join(presets_dir, name + ".json")
                if os.path.exists(p):
                    os.remove(p)
                preset_dd.values = _list_presets() or ["(none saved)"]
                self.preset_pick_var.set("Select a preset")
            except Exception:
                pass

        prow = tk.Frame(adv_panel, bg=BG)
        prow.pack(fill="x", pady=(0, 4))
        prow.grid_columnconfigure(0, weight=1, uniform="preset")
        prow.grid_columnconfigure(1, weight=1, uniform="preset")
        RoundedButton(prow, "Save", _save_preset, primary=False,
                      bg_parent=BG, height=32).grid(row=0, column=0,
                                                    sticky="ew", padx=(0, 4))
        RoundedButton(prow, "Remove", _remove_preset, primary=False,
                      bg_parent=BG, height=32).grid(row=0, column=1,
                                                    sticky="ew", padx=(4, 0))

        # ── OPTIONS (left column, under the track pickers) ───────────────
        _section(left, "O P T I O N S")
        _toggle_row(left, "Punctuation",       self.punct_var,   (0, 8))
        _toggle_row(left, "Speaker detection", self.diarize_var, (0, 8))
        _toggle_row(left, "Censor words",      self.censor_var,  (0, 8))

        # ── FONT (system fonts, searchable) + weight, side by side ────────
        # Combined into one row (font gets more width via a 2:1 split) so this
        # section takes half the vertical space of stacking them.
        _section(adv_panel, "F O N T")

        def _caption_fonts():
            """Every installed font family, detected live from the system."""
            try:
                fams = sorted({f for f in tkfont.families()
                               if f and not f.startswith("@")},
                              key=str.lower)
            except Exception:
                fams = []
            return ["Auto (by language)"] + (fams or list(CAPTION_FONTS[1:]))

        frow = tk.Frame(adv_panel, bg=BG)
        frow.pack(fill="x", pady=(0, 10))
        frow.grid_columnconfigure(0, weight=2, uniform="fontrow")
        frow.grid_columnconfigure(1, weight=1, uniform="fontrow")
        fcol = tk.Frame(frow, bg=BG)
        fcol.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        tk.Label(fcol, text="Family", bg=BG, fg=FG_DIM,
                 font=(FONT_UI, 9)).pack(anchor="w", pady=(0, 4))
        SearchDropdown(fcol, _caption_fonts, self.font_var).pack(fill="x")
        wcol = tk.Frame(frow, bg=BG)
        wcol.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        tk.Label(wcol, text="Weight", bg=BG, fg=FG_DIM,
                 font=(FONT_UI, 9)).pack(anchor="w", pady=(0, 4))
        Dropdown(wcol, FONT_STYLES, self.font_style_var).pack(fill="x")

        # ── REEL STYLES (one-click looks with thumbnails) ────────────────
        _section(adv_panel, "R E E L   S T Y L E S")
        styles_wrap = tk.Frame(adv_panel, bg=BG)
        styles_wrap.pack(fill="x", pady=(0, 12))
        for style in REEL_STYLES:
            srow_ = tk.Frame(styles_wrap, bg=BG_INPUT, cursor="hand2")
            srow_.pack(fill="x", pady=2)
            thumb = StyleThumb(srow_, style["swatch"], style["outline"],
                               width=34, bg_parent=BG_INPUT)
            thumb.pack(side="left", padx=8, pady=6)
            txt = tk.Frame(srow_, bg=BG_INPUT)
            txt.pack(side="left", fill="x", expand=True)
            tk.Label(txt, text=style["name"], bg=BG_INPUT, fg=FG,
                     font=(FONT_UI, 10, "bold")).pack(anchor="w")
            tk.Label(txt, text=style["caption"], bg=BG_INPUT, fg=FG_MUTE,
                     font=(FONT_UI, 8)).pack(anchor="w")
            for w in (srow_, thumb, txt, *txt.winfo_children()):
                w.bind("<Button-1>",
                       lambda e, s=style: self._apply_reel_style(s))

        # ── ADVANCED (max words, keyword highlight, review) ──────────────
        _section(adv_panel, "A D V A N C E D")
        mw = tk.Frame(adv_panel, bg=BG)
        mw.pack(fill="x", pady=(0, 8))
        tk.Label(mw, text="Max words / caption (0 = off)", bg=BG, fg=FG_DIM,
                 font=(FONT_UI, 9)).pack(side="left")
        mwbox = SmallInput(mw, self.words_per_var)
        mwbox.configure(width=52)
        mwbox.pack(side="right")

        hlrow = tk.Frame(adv_panel, bg=BG)
        hlrow.pack(fill="x", pady=(0, 4))
        tk.Label(hlrow, text="Highlight words", bg=BG, fg=FG_DIM,
                 font=(FONT_UI, 9)).pack(side="left")
        ColorSwatch(hlrow, self.hl_color_var, size=24).pack(side="right")
        PillEntry(adv_panel, self.hl_words_var).pack(fill="x", pady=(0, 2))
        tk.Label(adv_panel,
                 text="Space/comma separated. Pick a colour (right-click = off). "
                      "Colours matching words in animated captions.",
                 bg=BG, fg=FG_MUTE, font=(FONT_UI, 8), wraplength=300,
                 justify="left").pack(anchor="w", pady=(0, 8))

        _toggle_row(adv_panel, "Review captions before applying",
                    self.review_var, (0, 12))

        # ── SILENCE CUT (tightened copy + re-timed SRT, needs ffmpeg) ────
        _section(adv_panel, "S I L E N C E   C U T")
        _toggle_row(adv_panel, "Cut silence (tightened copy)",
                    self.silence_var, (0, 8))
        silrow = tk.Frame(adv_panel, bg=BG)
        silrow.pack(fill="x", pady=(0, 2))
        for i in range(3):
            silrow.grid_columnconfigure(i, weight=1, uniform="sil_g")
        for i, (lbl, var) in enumerate((("Threshold dB", self.sil_thr_var),
                                        ("Min gap s", self.sil_gap_var),
                                        ("Pad s", self.sil_pad_var))):
            col = tk.Frame(silrow, bg=BG)
            col.grid(row=0, column=i, sticky="ew",
                     padx=(0 if i == 0 else 4, 0 if i == 2 else 4))
            tk.Label(col, text=lbl, bg=BG, fg=FG_DIM,
                     font=(FONT_UI, 9)).pack(anchor="w", pady=(0, 4))
            SmallInput(col, var).pack(fill="x")
        tk.Label(adv_panel,
                 text="Renders a \"silence removed\" copy of the source clip + "
                      "matching SRT into the Media Pool. The timeline is never "
                      "touched. Needs ffmpeg.",
                 bg=BG, fg=FG_MUTE, font=(FONT_UI, 8), wraplength=300,
                 justify="left").pack(anchor="w", pady=(4, 12))

        # ── CENSOR WORDS editor ──────────────────────────────────────────
        _section(adv_panel, "C E N S O R   W O R D S")
        censor_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "censor_words.txt")
        cbox = tk.Text(adv_panel, height=4, bg=BG_INPUT, fg=FG,
                       insertbackground=ACCENT, relief="flat", bd=0,
                       font=(FONT_MONO, 9), wrap="word",
                       highlightthickness=1, highlightbackground=BORDER,
                       highlightcolor=ACCENT)
        cbox.pack(fill="x", pady=(0, 4))
        try:
            with open(censor_path, encoding="utf-8") as f:
                cbox.insert("1.0", f.read())
        except Exception:
            pass

        def _save_censor():
            try:
                with open(censor_path, "w", encoding="utf-8") as f:
                    f.write(cbox.get("1.0", "end-1c"))
                messagebox.showinfo("Audio2SRT", "Censor list saved.")
            except Exception as e:
                messagebox.showerror("Audio2SRT",
                                     "Cannot save censor list: %s" % e)
        RoundedButton(adv_panel, "Save censor list", _save_censor,
                      primary=False, bg_parent=BG, height=30).pack(fill="x")
        tk.Label(adv_panel,
                 text="One word per line · # = comment · used when Censor is on.",
                 bg=BG, fg=FG_MUTE, font=(FONT_UI, 8), wraplength=300,
                 justify="left").pack(anchor="w", pady=(4, 0))

        # The Text+ colour / outline / position controls used to live here in
        # the scrollable column; they now sit in the fixed preview panel
        # (built below) so they stay beside the live preview at all times.
        self._build_preview_panel(defaults)

        if first_input is not None:
            first_input.focus()
        self.root.after(0, _sync_scrollregion)
        self.root.bind("<Return>", lambda e: self._on_submit())
        self.root.bind("<Escape>", lambda e: self._on_cancel())

    def _apply_reel_style(self, style):
        """Apply a one-click reel-style bundle to all the look vars."""
        v = style["vars"]
        setters = {
            "color": self.color_var, "outline_color": self.outline_color_var,
            "outline_thick": self.outline_thick_var,
            "shadow_color": self.shadow_color_var, "tp_size": self.tp_size_var,
            "tp_posx": self.tp_posx_var, "tp_posy": self.tp_posy_var,
            "font_style": self.font_style_var, "hl_color": self.hl_color_var,
            "outline": self.outline_var, "shadow": self.shadow_var,
        }
        for k, var in setters.items():
            if k in v:
                var.set(v[k])
        if style.get("caption"):
            self.caption_var.set(style["caption"])

    # ── Fixed preview panel: reel preview + all colour/outline controls ────
    def _build_preview_panel(self, defaults):
        """Everything that shapes the caption's look lives here, docked to the
        right and NOT inside the scroll area, so the live preview is always
        visible while you tune colour / outline / position."""
        p = self._fixed_panel

        tk.Label(p, text="P R E V I E W   ( R E E L  /  S H O R T )",
                 bg=BG, fg=FG_LABEL,
                 font=(FONT_UI, 8, "bold")).pack(anchor="w", pady=(0, 6))

        # The reel preview itself — pinned at the top of the fixed panel.
        prev_wrap = tk.Frame(p, bg=BG)
        prev_wrap.pack(fill="x", pady=(0, 3))
        ReelPreview(prev_wrap, self.tp_posx_var, self.tp_posy_var,
                    self.tp_size_var, color_var=self.color_var,
                    outline_var=self.outline_var, shadow_var=self.shadow_var,
                    outline_color_var=self.outline_color_var,
                    outline_thick_var=self.outline_thick_var,
                    shadow_color_var=self.shadow_color_var,
                    srt_size_var=self.textsize_var,
                    caption_style_var=self.caption_var,
                    safe_zone_var=self.safe_zone_var, width=128).pack()

        # Position snap buttons — one-click placement (X always centred).
        def _snap(py):
            self.tp_posx_var.set("0.500")
            self.tp_posy_var.set("%.3f" % py)
        snap = tk.Frame(p, bg=BG)
        snap.pack(fill="x", pady=(3, 4))
        for i, (lbl, py) in enumerate((("Top", 0.82), ("Middle", 0.50),
                                       ("Lower", 0.28), ("Bottom", 0.10))):
            snap.grid_columnconfigure(i, weight=1, uniform="snap")
            RoundedButton(snap, lbl, (lambda v=py: _snap(v)), primary=False,
                          bg_parent=BG, height=26).grid(
                row=0, column=i, sticky="ew",
                padx=(0 if i == 0 else 2, 0 if i == 3 else 2))

        # Safe-zone overlay toggle (shows where the platform UI sits)
        szrow = IconRow(p, None, "Show platform safe zone")
        szsw = ToggleSwitch(szrow, self.safe_zone_var)
        szsw.configure(bg=BG_INPUT)
        szrow.set_child(szrow._body, right_child=szsw)
        szrow._lbl.configure(cursor="hand2", fg=FG_DIM, font=(FONT_UI, 8))
        szrow._lbl.bind("<Button-1>", lambda e: szsw._toggle())
        szrow.pack(fill="x", pady=(0, 5))

        # Outline / Shadow on-off toggles
        srow = tk.Frame(p, bg=BG)
        srow.pack(fill="x", pady=(0, 6))
        srow.grid_columnconfigure(0, weight=1, uniform="stylerow")
        srow.grid_columnconfigure(1, weight=1, uniform="stylerow")
        for i, (lbl, var) in enumerate((("Outline", self.outline_var),
                                        ("Shadow", self.shadow_var))):
            cell = tk.Frame(srow, bg=BG)
            cell.grid(row=0, column=i, sticky="ew",
                      padx=(0 if i == 0 else 4, 4 if i == 0 else 0))
            r = IconRow(cell, None, lbl)
            sw_ = ToggleSwitch(r, var)
            sw_.configure(bg=BG_INPUT)
            r.set_child(r._body, right_child=sw_)
            r._lbl.configure(cursor="hand2", fg=FG)
            r._lbl.bind("<Button-1>", lambda e, s=sw_: s._toggle())
            r.pack(fill="x")

        # Colour swatches: fill / outline / shadow (empty = auto/default)
        def _color_row(label, var):
            row = tk.Frame(p, bg=BG)
            row.pack(fill="x", pady=(0, 4))
            tk.Label(row, text=label, bg=BG, fg=FG_DIM,
                     font=(FONT_UI, 9)).pack(side="left")
            ColorSwatch(row, var, size=24).pack(side="right")

        _color_row("Text colour", self.color_var)
        _color_row("Outline colour", self.outline_color_var)
        _color_row("Shadow colour", self.shadow_color_var)
        tk.Label(p, text="Click swatch = pick · right-click = auto.",
                 bg=BG, fg=FG_MUTE, font=(FONT_UI, 8),
                 wraplength=272, justify="left").pack(anchor="w", pady=(0, 3))

        # Size / thickness / position sliders, each with a synced number box
        def _slider_row(label, var, lo, hi, fmt, step):
            row = tk.Frame(p, bg=BG)
            row.pack(fill="x", pady=(0, 3))
            top = tk.Frame(row, bg=BG)
            top.pack(fill="x")
            tk.Label(top, text=label, bg=BG, fg=FG_DIM,
                     font=(FONT_UI, 9)).pack(side="left")
            box = SmallInput(top, var)
            box.configure(width=60)
            box.pack(side="right")
            Slider(row, var, lo, hi, fmt=fmt, step=step, height=20).pack(
                fill="x", pady=(1, 0))

        _slider_row("Text+ size", self.tp_size_var, 0.02, 0.50, "%.3f", 0.005)
        _slider_row("Outline thickness", self.outline_thick_var,
                    0.0, 0.30, "%.3f", 0.005)
        _slider_row("Position X", self.tp_posx_var, 0.0, 1.0, "%.3f", 0.005)
        _slider_row("Position Y", self.tp_posy_var, 0.0, 1.0, "%.3f", 0.005)

    def _on_submit(self, action="generate"):
        chosen = self.combo_var.get()
        if action == "generate" and not chosen:
            return
        settings = ",".join(v.get().strip() for v in self.settings_vars)
        lang = self.lang_var.get()
        cstyle, animation = CAPTION_STYLE_MAP.get(self.caption_var.get(), ("srt", ""))
        sel = {
            "action":   action,
            "chosen":   chosen,
            "settings": settings,
            "punct":    int(self.punct_var.get()),
            "lang":     lang,
            "lang_code": LANGUAGE_CODES.get(lang, "hin"),
            "diarize":  int(self.diarize_var.get()),
            "censor":   int(self.censor_var.get()),
            "min_secs": self.min_var.get().strip() or "0",
            "cps":      self.cps_var.get().strip() or "0",
            "caption_style": cstyle,
            "animation": animation,
            "video_track": self.vtrack_var.get(),
            "text_size": self.textsize_var.get().strip() or "55",
            "outline":  int(self.outline_var.get()),
            "shadow":   int(self.shadow_var.get()),
            "tp_size":  self.tp_size_var.get().strip(),
            "tp_posx":  self.tp_posx_var.get().strip(),
            "tp_posy":  self.tp_posy_var.get().strip(),
            "color":    self.color_var.get().strip(),
            "outline_color": self.outline_color_var.get().strip(),
            "outline_thick": self.outline_thick_var.get().strip(),
            "shadow_color":  self.shadow_color_var.get().strip(),
            "words_per": self.words_per_var.get().strip() or "0",
            "hl_words":  self.hl_words_var.get().strip(),
            "hl_color":  self.hl_color_var.get().strip(),
            "review":    int(self.review_var.get()),
            "font":     self.font_var.get().strip(),
            "font_style": self.font_style_var.get().strip() or "Auto",
            "silence":  int(self.silence_var.get()),
            "sil_thr":  self.sil_thr_var.get().strip() or "-30",
            "sil_gap":  self.sil_gap_var.get().strip() or "0.5",
            "sil_pad":  self.sil_pad_var.get().strip() or "0.05",
        }
        try:
            with open(self.selection_path, "w", encoding="utf-8") as f:
                json.dump(sel, f)
        except Exception as e:
            messagebox.showerror("Audio2SRT",
                                 "Cannot write selection file: %s" % e)
            return
        self.root.unbind("<Return>")
        self.root.unbind("<Escape>")
        try:
            self.root.unbind_all("<MouseWheel>")
        except Exception:
            pass
        if action in ("update", "undo"):
            # No transcription: the Lua script acts on existing timeline clips
            # (restyle for update, remove the last track for undo) and writes
            # the result file when done.
            title = "Undoing" if action == "undo" else "Updating captions"
            busy = ("Removing the last caption track…" if action == "undo"
                    else "Updating captions on the timeline…")
            self._build_progress(title)
            self._set_progress(60, busy)
            self.root.after(30, self._animate_bar)
            self.root.after(300, self._poll_result_update)
            return
        self._build_progress()
        threading.Thread(target=self._wait_for_args_and_run,
                         daemon=True).start()
        self.root.after(50, self._pump)
        self.root.after(30, self._animate_bar)

    def _poll_result_update(self):
        if self.cancelled:
            return
        if os.path.exists(self.result_path):
            self.exit_code = 0
            try:
                with open(self.result_path, "r", encoding="utf-8") as f:
                    msg = f.read().strip()
            except Exception:
                msg = "Done."
            self._build_done(msg)
            return
        self.root.after(250, self._poll_result_update)

    def _on_cancel(self):
        self.cancelled = True
        try:
            with open(self.done_path, "w", encoding="utf-8") as f:
                f.write("130")
        except Exception:
            pass
        try:
            with open(self.ack_path, "w", encoding="utf-8") as f:
                f.write("cancel")
        except Exception:
            pass
        self.root.destroy()

    # ── Phase 2: progress ─────────────────────────────────────────────────
    def _build_progress(self, title="Transcribing audio"):
        self._clear_body()
        self.subtitle_var.set(title)

        header = tk.Frame(self.body, bg=BG)
        header.pack(fill="x")
        title_lbl = tk.Label(header, text="Audio2SRT", bg=BG, fg=FG,
                             font=(FONT_UI, 16, "bold"))
        title_lbl.pack(anchor="w")
        sub_lbl = tk.Label(header, text=title, bg=BG, fg=FG_MUTE,
                           font=(FONT_UI, 9))
        sub_lbl.pack(anchor="w")
        for w in (header, title_lbl, sub_lbl):
            self._make_draggable(w)

        wrap = tk.Frame(self.body, bg=BG)
        wrap.pack(expand=True, fill="both", pady=(18, 0))

        # Giant percentage
        self.pct_var = tk.StringVar(value="0%")
        tk.Label(wrap, textvariable=self.pct_var,
                 bg=BG, fg=ACCENT,
                 font=(FONT_UI, 44, "bold")).pack(pady=(14, 0))

        # Status line
        self.status_var = tk.StringVar(value="Preparing…")
        tk.Label(wrap, textvariable=self.status_var,
                 bg=BG, fg=FG_DIM,
                 font=(FONT_UI, 10)).pack(pady=(2, 18))

        # Bar
        self.pct = tk.DoubleVar(value=0.0)
        bar = ttk.Progressbar(
            wrap, orient="horizontal", mode="determinate",
            maximum=100, variable=self.pct,
            style="Audio2SRT.Horizontal.TProgressbar",
        )
        bar.pack(fill="x")

        self.detail_var = tk.StringVar(value="This may take a minute")
        tk.Label(wrap, textvariable=self.detail_var,
                 bg=BG, fg=FG_MUTE,
                 font=(FONT_UI, 8)).pack(pady=(10, 0))

    def _set_progress(self, pct, msg):
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            return
        pct = max(0.0, min(100.0, pct))
        self._anim_target = pct
        self.pct_var.set("%d%%" % int(round(pct)))
        if msg:
            self.status_var.set(msg)

    def _animate_bar(self):
        # Smoothly interpolate the visible bar toward the target percentage.
        if self._anim_value < self._anim_target:
            self._anim_value += max(0.4, (self._anim_target - self._anim_value) * 0.18)
            if self._anim_value > self._anim_target:
                self._anim_value = self._anim_target
            self.pct.set(self._anim_value)
        elif self._anim_value > self._anim_target:
            self._anim_value = self._anim_target
            self.pct.set(self._anim_value)
        if self.exit_code is None:
            self.root.after(30, self._animate_bar)

    # ── Phase 3: done ─────────────────────────────────────────────────────
    def _poll_result(self):
        if self.cancelled:
            return
        if os.path.exists(self.result_path):
            try:
                with open(self.result_path, "r", encoding="utf-8") as f:
                    msg = f.read().strip()
            except Exception:
                msg = "Done."
            self._build_done(msg)
            return
        self.root.after(200, self._poll_result)

    def _build_done(self, message):
        self._clear_body()

        header = tk.Frame(self.body, bg=BG)
        header.pack(fill="x", pady=(0, 14))
        title_lbl = tk.Label(header, text="Audio2SRT", bg=BG, fg=FG,
                             font=(FONT_UI, 16, "bold"))
        title_lbl.pack(anchor="w")
        sub_lbl = tk.Label(header, text="Done", bg=BG, fg=FG_MUTE,
                           font=(FONT_UI, 9))
        sub_lbl.pack(anchor="w")
        for w in (header, title_lbl, sub_lbl):
            self._make_draggable(w)

        div = GradientDivider(self.body, height=1, bg_parent=BG)
        div.pack(fill="x", pady=(0, 16))

        tk.Label(self.body, text=message, bg=BG, fg=FG,
                 wraplength=340, justify="left",
                 font=(FONT_UI, 10)).pack(anchor="w", pady=(0, 22))

        btn_frm = tk.Frame(self.body, bg=BG)
        btn_frm.pack(fill="x", side="bottom")
        ok_btn = RoundedButton(btn_frm, "OK", self._on_ok_done,
                               primary=True, bg_parent=BG, height=40)
        ok_btn.pack(fill="x")
        self.root.bind("<Return>", lambda e: self._on_ok_done())
        self.root.bind("<Escape>", lambda e: self._on_ok_done())

    def _on_ok_done(self):
        try:
            with open(self.ack_path, "w", encoding="utf-8") as f:
                f.write("ok")
        except Exception:
            pass
        self.root.destroy()

    # ── Worker plumbing ───────────────────────────────────────────────────
    def _wait_for_args_and_run(self):
        # Lua writes the args file after reading our selection. 10 min cap.
        deadline = time.time() + 600
        while time.time() < deadline:
            if self.cancelled:
                return
            if os.path.exists(self.args_path):
                break
            time.sleep(0.2)
        else:
            self.q.put(("error", "Timed out waiting for args."))
            self.q.put(("exit", 1))
            return
        self._run_transcribe()

    def _run_transcribe(self):
        try:
            log_dir = os.path.dirname(self.log_path)
            if log_dir and not os.path.isdir(log_dir):
                os.makedirs(log_dir, exist_ok=True)
            log_f = open(self.log_path, "w", encoding="utf-8", errors="replace")
        except Exception as e:
            self.q.put(("error", "Cannot open log file: %s" % e))
            self.q.put(("exit", 1))
            return

        creationflags = 0
        startupinfo = None
        if os.name == "nt":
            creationflags = CREATE_NO_WINDOW
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE

        cmd = [self.python_exe, self.script_path,
               "--args-file", self.args_path]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                bufsize=1,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
        except Exception as e:
            log_f.write("ERROR: failed to launch worker: %s\n" % e)
            log_f.close()
            self.q.put(("error", "Failed to launch worker: %s" % e))
            self.q.put(("exit", 1))
            return

        for line in proc.stdout:
            log_f.write(line)
            log_f.flush()
            s = line.strip()
            if s.startswith("PROGRESS|"):
                parts = s.split("|", 2)
                if len(parts) >= 2:
                    pct = parts[1]
                    msg = parts[2] if len(parts) >= 3 else ""
                    self.q.put(("progress", pct, msg))

        proc.wait()
        log_f.close()
        self.q.put(("exit", proc.returncode))

    def _pump(self):
        try:
            while True:
                evt = self.q.get_nowait()
                kind = evt[0]
                if kind == "progress":
                    self._set_progress(evt[1], evt[2])
                elif kind == "error":
                    self._set_progress(100, evt[1])
                elif kind == "exit":
                    self.exit_code = evt[1]
                    # Optional review/edit pass before the Lua applies captions.
                    if (evt[1] == 0 and int(self.review_var.get())
                            and self._review_available()):
                        self._begin_review()
                    else:
                        self._finish_after_transcribe(evt[1])
                    return
        except queue.Empty:
            pass
        self.root.after(50, self._pump)

    def _finish_after_transcribe(self, code):
        """Signal the waiting Lua (via done_path) and either poll for the
        result (success) or close (failure/cancel)."""
        try:
            with open(self.done_path, "w", encoding="utf-8") as f:
                f.write(str(code))
        except Exception:
            pass
        if code == 0:
            self._set_progress(100, "Importing subtitles…")
            self.detail_var.set("")
            self.root.after(200, self._poll_result)
        else:
            self.root.after(450, self.root.destroy)

    # ── Caption review / edit ─────────────────────────────────────────────
    def _transcribe_paths(self):
        """(srt_path, cap_path) read from the args file the Lua wrote, or
        (None, None) if unreadable."""
        try:
            with open(self.args_path, encoding="utf-8") as f:
                lines = [ln.rstrip("\r\n") for ln in f]
            srt = lines[1]
            return srt, srt + ".cap"
        except Exception:
            return None, None

    def _review_available(self):
        srt, _ = self._transcribe_paths()
        return bool(srt) and os.path.exists(srt)

    @staticmethod
    def _fmt_ts(sec):
        if sec < 0:
            sec = 0.0
        ms = int(round(sec * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return "%02d:%02d:%02d,%03d" % (h, m, s, ms)

    def _parse_cap(self, cap_path):
        """Parse the .cap sidecar into (header_lines, segments). Each segment:
        {s, e, spk, words:[(ws, we, text)], text}."""
        header, segs, cur = [], [], None
        try:
            with open(cap_path, encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if line.startswith("FPS ") or line.startswith("SPK "):
                        header.append(line)
                    elif line.startswith("SEG "):
                        parts = line.split(None, 3)
                        s = float(parts[1]); e = float(parts[2])
                        spk = int(parts[3]) if len(parts) > 3 else 0
                        cur = {"s": s, "e": e, "spk": spk, "words": []}
                        segs.append(cur)
                    elif line.startswith("WRD ") and cur is not None:
                        parts = line.split(None, 3)
                        ws = float(parts[1]); we = float(parts[2])
                        txt = parts[3] if len(parts) > 3 else ""
                        cur["words"].append((ws, we, txt))
        except Exception:
            return [], []
        for seg in segs:
            seg["text"] = " ".join(w[2] for w in seg["words"])
        return header, segs

    def _write_srt_cap(self, srt_path, cap_path, header, segs):
        """Rewrite the SRT + .cap from edited segments. When a segment's text
        changed, its per-word timing is redistributed evenly across the
        segment so animated (karaoke) captions still line up."""
        # SRT (dummy t=0 entry keeps Resolve anchored to timeline start).
        srt = ["1\n00:00:00,000 --> 00:00:00,001\n \n"]
        for i, seg in enumerate(segs, start=1):
            srt.append("%d\n%s --> %s\n%s\n" % (
                i + 1, self._fmt_ts(seg["s"]), self._fmt_ts(seg["e"]),
                seg["text"]))
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(srt))

        cap = list(header)
        for seg in segs:
            cap.append("SEG %.3f %.3f %d" % (seg["s"], seg["e"], seg["spk"]))
            orig = " ".join(w[2] for w in seg["words"])
            new_words = seg["text"].split()
            if seg["text"].strip() == orig.strip() and seg["words"]:
                for ws, we, txt in seg["words"]:
                    cap.append("WRD %.3f %.3f %s" % (ws, we, txt))
            elif new_words:
                # redistribute timing evenly across the segment duration
                n = len(new_words)
                span = max(0.001, seg["e"] - seg["s"])
                step = span / n
                for j, wtok in enumerate(new_words):
                    ws = seg["s"] + j * step
                    we = seg["s"] + (j + 1) * step
                    cap.append("WRD %.3f %.3f %s" % (ws, we, wtok))
        with open(cap_path, "w", encoding="utf-8") as f:
            f.write("\n".join(cap) + "\n")

    def _begin_review(self):
        srt_path, cap_path = self._transcribe_paths()
        header, segs = self._parse_cap(cap_path)
        if not segs:
            # No sidecar (shouldn't happen) — skip review, apply as-is.
            self._finish_after_transcribe(0)
            return
        self._build_review(srt_path, cap_path, header, segs)

    def _build_review(self, srt_path, cap_path, header, segs):
        self._clear_body()
        self.root.unbind("<Return>")

        head = tk.Frame(self.body, bg=BG)
        head.pack(fill="x", pady=(0, 10))
        tk.Label(head, text="Review captions", bg=BG, fg=FG,
                 font=(FONT_UI, 16, "bold")).pack(anchor="w")
        tk.Label(head, text="Fix any wrong words, or delete a line, then apply.",
                 bg=BG, fg=FG_MUTE, font=(FONT_UI, 9)).pack(anchor="w")
        for w in (head, *head.winfo_children()):
            self._make_draggable(w)
        GradientDivider(self.body, height=1, bg_parent=BG).pack(
            fill="x", pady=(0, 10))

        # bottom action bar (packed first so it can never be clipped)
        bar = tk.Frame(self.body, bg=BG)
        bar.pack(fill="x", side="bottom", pady=(10, 0))
        bar.grid_columnconfigure(0, weight=1, uniform="rv")
        bar.grid_columnconfigure(1, weight=2, uniform="rv")
        RoundedButton(bar, "Cancel", self._on_cancel, primary=False,
                      bg_parent=BG, height=40).grid(row=0, column=0,
                                                    sticky="ew", padx=(0, 5))
        RoundedButton(bar, "Apply captions",
                      lambda: self._apply_review(srt_path, cap_path, header),
                      primary=True, bg_parent=BG, height=40).grid(
            row=0, column=1, sticky="ew", padx=(5, 0))

        # scrollable list of editable segment rows
        wrap = tk.Frame(self.body, bg=BG)
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        vsb = SlimScrollbar(wrap, canvas.yview, width=6)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(0, 6))
        vsb.pack(side="right", fill="y", padx=(6, 2))
        lst = tk.Frame(canvas, bg=BG)
        lst_id = canvas.create_window((0, 0), window=lst, anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(lst_id, width=e.width))

        def _sync(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
        lst.bind("<Configure>", _sync)

        def _wheel(e):
            if canvas.winfo_exists():
                canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _wheel)

        self._review_rows = []

        def _add_row(seg):
            row = tk.Frame(lst, bg=BG_INPUT)
            row.pack(fill="x", pady=2)
            top = tk.Frame(row, bg=BG_INPUT)
            top.pack(fill="x", padx=8, pady=(5, 0))
            tk.Label(top, text="%s → %s" % (self._fmt_ts(seg["s"]),
                                            self._fmt_ts(seg["e"])),
                     bg=BG_INPUT, fg=FG_MUTE,
                     font=(FONT_UI, 8)).pack(side="left")
            dele = tk.Label(top, text="✕  delete", bg=BG_INPUT, fg=FG_MUTE,
                            font=(FONT_UI, 8), cursor="hand2")
            dele.pack(side="right")
            var = tk.StringVar(value=seg["text"])
            ent = tk.Entry(row, textvariable=var, bg=BG_INPUT, fg=FG,
                           insertbackground=ACCENT, relief="flat", bd=0,
                           font=(FONT_UI, 11),
                           highlightthickness=0)
            ent.pack(fill="x", padx=8, pady=(2, 6))
            desc = {"seg": seg, "var": var, "row": row}
            self._review_rows.append(desc)

            def _del(_e=None):
                if desc in self._review_rows:
                    self._review_rows.remove(desc)
                row.destroy()
                self.root.after(0, _sync)
            dele.bind("<Button-1>", _del)

        for seg in segs:
            _add_row(seg)
        self.root.after(0, _sync)

    def _apply_review(self, srt_path, cap_path, header):
        final = []
        for desc in self._review_rows:
            seg = desc["seg"]
            seg["text"] = desc["var"].get().strip()
            if seg["text"]:
                final.append(seg)
        try:
            self._write_srt_cap(srt_path, cap_path, header, final)
        except Exception as e:
            messagebox.showerror("Audio2SRT",
                                 "Cannot write edited captions: %s" % e)
            return
        try:
            self.root.unbind_all("<MouseWheel>")
        except Exception:
            pass
        self._build_progress("Applying captions")
        self._set_progress(80, "Placing captions on the timeline…")
        self.root.after(30, self._animate_bar)
        self._finish_after_transcribe(0)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prompt",    required=True)
    p.add_argument("--selection", required=True)
    p.add_argument("--args-file", required=True)
    p.add_argument("--done",      required=True)
    p.add_argument("--log",       required=True)
    p.add_argument("--python",    required=True)
    p.add_argument("--script",    required=True)
    p.add_argument("--result",    required=True)
    p.add_argument("--ack",       required=True)
    a = p.parse_args()

    with open(a.prompt, "r", encoding="utf-8") as f:
        prompt = json.load(f)

    root = tk.Tk()
    app = App(root, prompt, a.selection, a.args_file, a.done, a.log,
              a.python, a.script, a.result, a.ack)
    root.mainloop()

    code = app.exit_code if app.exit_code is not None else (
        130 if app.cancelled else 1
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
