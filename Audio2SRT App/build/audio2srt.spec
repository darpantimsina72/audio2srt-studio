# PyInstaller spec for Audio2SRT Studio.
#   macOS:    pyinstaller build/audio2srt.spec   ->  dist/Audio2SRT Studio.app
#   Windows:  pyinstaller build\audio2srt.spec   ->  dist\Audio2SRT Studio\...exe
# Run from the "Audio2SRT App" folder.

import os
import sys
from PyInstaller.utils.hooks import collect_all

APP_BUILD_DIR = os.path.dirname(os.path.abspath(SPECPATH))   # .../Audio2SRT App/build
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
]

# elevenlabs drags in pydantic / pydantic_core / httpx — pull everything so the
# untested frozen build doesn't crash on a missing submodule or data file.
for pkg in ("elevenlabs", "pydantic", "pydantic_core", "certifi", "httpx", "httpcore"):
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
    argv_emulation=True,    # macOS: receive file-open args
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
