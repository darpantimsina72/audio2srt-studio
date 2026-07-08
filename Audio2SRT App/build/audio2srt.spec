# PyInstaller spec for Audio2SRT Studio.
#   macOS:    pyinstaller build/audio2srt.spec   ->  dist/Audio2SRT Studio.app
#   Windows:  pyinstaller build\audio2srt.spec   ->  dist\Audio2SRT Studio\...exe
# Run from the "Audio2SRT App" folder.

import os
import sys
from PyInstaller.utils.hooks import collect_all

# SPECPATH is the directory containing this spec file (not the file path).
APP_BUILD_DIR = os.path.abspath(SPECPATH)                    # .../Audio2SRT App/build
ROOT = os.path.dirname(APP_BUILD_DIR)                        # .../Audio2SRT App

datas = [
    (os.path.join(ROOT, "ui"), "ui"),
    (os.path.join(ROOT, "bridges"), "bridges"),
]
# Bundle ffmpeg/ffprobe if present in bin/ (build scripts put them there).
bin_dir = os.path.join(ROOT, "bin")
if os.path.isdir(bin_dir) and os.listdir(bin_dir):
    datas.append((bin_dir, "bin"))

binaries = []
hiddenimports = [
    "engine", "engine.transcribe", "engine.silence",
    "transcribe", "silence", "dialog", "installers",
    "webview", "elevenlabs",
    "tkinter", "tkinter.ttk",   # dialog.py — Resolve bridge track picker / alerts
]

# pydantic / pydantic_core / httpx use dynamic imports and data files — pull
# them wholesale. elevenlabs itself is deliberately NOT collect_all'd: the SDK
# is fern-generated with filenames so long that the full package blows past
# Windows' 260-char MAX_PATH inside the installer; its imports are static, so
# PyInstaller's normal analysis (via the hiddenimport above) bundles what the
# speech-to-text client actually uses.
for pkg in ("pydantic", "pydantic_core", "certifi", "httpx", "httpcore"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass  # package may be absent on the build machine; ignore

a = Analysis(
    [os.path.join(ROOT, "app.py")],
    pathex=[ROOT, os.path.join(ROOT, "engine")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["numpy", "PIL", "pandas"],   # keep the bundle lean
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Audio2SRT Studio",
    console=False,          # GUI app; CLI still works when launched with args
    disable_windowed_traceback=False,
    argv_emulation=False,   # would add Apple-event wait to every CLI call from the bridges
    target_arch=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Audio2SRT Studio")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Audio2SRT Studio.app",
        icon=None,
        bundle_identifier="com.audiotosrt.studio",
        info_plist={
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.video",
            "CFBundleShortVersionString": "1.0.0",
        },
    )
