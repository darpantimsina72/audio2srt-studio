"""Install the NLE bridges that let Audio2SRT Studio drive the timeline.

Both installers copy a bridge (Resolve Lua script / Premiere CEP panel) into the
host app's folder. The bridges read ~/.audio2srt_app to find this app's binary
and call it in CLI mode — so the user never needs system Python or ffmpeg.
"""

import os
import shutil
import subprocess
import sys


def _home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


# ── DaVinci Resolve ──────────────────────────────────────────────────────────────
def resolve_script_dirs():
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", _home("AppData", "Roaming"))
        progdata = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return [
            os.path.join(appdata, "Blackmagic Design", "DaVinci Resolve",
                         "Support", "Fusion", "Scripts", "Utility"),
            os.path.join(progdata, "Blackmagic Design", "DaVinci Resolve",
                         "Fusion", "Scripts", "Utility"),
        ]
    return [
        _home("Library", "Application Support", "Blackmagic Design",
              "DaVinci Resolve", "Fusion", "Scripts", "Utility"),
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
        "Fusion/Scripts/Utility",
    ]


def install_resolve(res_dir, marker):
    src = os.path.join(res_dir, "bridges", "resolve", "audio_to_srt.lua")
    if not os.path.exists(src):
        return {"ok": False, "error": "bridge not found: " + src}
    installed = []
    for d in resolve_script_dirs():
        try:
            os.makedirs(d, exist_ok=True)
            shutil.copy2(src, os.path.join(d, "audio_to_srt.lua"))
            installed.append(d)
        except (OSError, PermissionError):
            continue  # system dir often not writable — user dir is enough
    if not installed:
        return {"ok": False, "error": "could not write to any Resolve Scripts folder"}
    return {"ok": True, "installed": installed,
            "next": "Restart DaVinci Resolve, then Workspace > Scripts > audio_to_srt"}


# ── Adobe Premiere Pro (CEP) ─────────────────────────────────────────────────────
def cep_extensions_dir():
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", _home("AppData", "Roaming"))
        return os.path.join(appdata, "Adobe", "CEP", "extensions")
    return _home("Library", "Application Support", "Adobe", "CEP", "extensions")


def _enable_cep_debug():
    versions = ["8", "9", "10", "11", "12", "13", "14"]
    if sys.platform == "win32":
        for v in versions:
            subprocess.run(
                ["reg", "add", r"HKCU\Software\Adobe\CSXS." + v,
                 "/v", "PlayerDebugMode", "/t", "REG_SZ", "/d", "1", "/f"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif sys.platform == "darwin":
        for v in versions:
            subprocess.run(
                ["defaults", "write", "com.adobe.CSXS." + v, "PlayerDebugMode", "1"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["killall", "cfprefsd"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def install_premiere(res_dir, marker):
    src = os.path.join(res_dir, "bridges", "premiere", "com.audiotosrt.cep")
    if not os.path.isdir(src):
        return {"ok": False, "error": "bridge not found: " + src}
    dest_root = cep_extensions_dir()
    dest = os.path.join(dest_root, "com.audiotosrt.cep")
    try:
        os.makedirs(dest_root, exist_ok=True)
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
    except (OSError, PermissionError) as exc:
        return {"ok": False, "error": "could not install panel: " + str(exc)}
    _enable_cep_debug()
    return {"ok": True, "installed": [dest],
            "next": "Fully quit and reopen Premiere, then Window > Extensions > Audio to SRT"}
