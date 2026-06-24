@echo off
setlocal
title Audio to SRT — Setup

:: Always run from this script's own folder
cd /d "%~dp0"

cls
echo ============================================================
echo    Audio to SRT  --  Setup  (Windows)
echo ============================================================
echo.

:: ── 1. Python check ───────────────────────────────────────────
echo [ 1 / 5 ]  Checking Python...
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ERROR: Python is not installed or not on PATH.
    echo.
    echo   Download from:  https://www.python.org/downloads/
    echo.
    echo   IMPORTANT: During install, check the box:
    echo   "Add Python to PATH"
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo   OK -- %%v

:: ── 2. elevenlabs ─────────────────────────────────────────────
echo.
echo [ 2 / 5 ]  Installing elevenlabs...
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo   ERROR: pip install failed. Check your internet connection or Python permissions.
    pause
    exit /b 1
)
echo   OK

:: ── 3. tkinter (bundled with Python on Windows — just verify) ─
echo.
echo [ 3 / 5 ]  Checking tkinter...
python -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo   WARNING: tkinter not found.
    echo   Re-install Python and make sure "tcl/tk" option is checked.
) else (
    echo   OK
)

:: ── 4. Save project path for the Lua script ───────────────────
echo.
echo [ 4 / 5 ]  Saving project path...
echo %CD%> "%USERPROFILE%\.audio_to_srt_path"
echo   OK -- path saved to %%USERPROFILE%%\.audio_to_srt_path

:: ── 5. Install Lua script into Resolve ────────────────────────
echo.
echo [ 5 / 5 ]  Installing audio_to_srt.lua into DaVinci Resolve...
set USER_RESOLVE_SCRIPTS=%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility
set SYSTEM_RESOLVE_SCRIPTS=%ProgramData%\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility

if not exist "%USER_RESOLVE_SCRIPTS%" mkdir "%USER_RESOLVE_SCRIPTS%" >nul 2>&1
copy /Y audio_to_srt.lua "%USER_RESOLVE_SCRIPTS%\audio_to_srt.lua" >nul
if errorlevel 1 (
    echo   WARNING: Could not install to user scripts folder:
    echo   %USER_RESOLVE_SCRIPTS%
) else (
    echo   OK -- installed for current user:
    echo   %USER_RESOLVE_SCRIPTS%
)

if exist "%SYSTEM_RESOLVE_SCRIPTS%" (
    copy /Y audio_to_srt.lua "%SYSTEM_RESOLVE_SCRIPTS%\audio_to_srt.lua" >nul
    if errorlevel 1 (
        echo   Note -- shared scripts folder exists but was not writable:
        echo   %SYSTEM_RESOLVE_SCRIPTS%
    ) else (
        echo   OK -- updated shared install:
        echo   %SYSTEM_RESOLVE_SCRIPTS%
    )
 ) else (
    echo   Note -- shared scripts folder not found, skipped:
    echo   %SYSTEM_RESOLVE_SCRIPTS%
)

:: ── API key ────────────────────────────────────────────────────
echo.
echo ------------------------------------------------------------
if exist .env (
    findstr /C:"ELEVENLABS_API_KEY=" .env >nul 2>&1
    if not errorlevel 1 (
        echo   API key already saved in .env  --  skipping.
        goto done
    )
)
echo   ElevenLabs API key setup
echo   Get your key at: https://elevenlabs.io/app/speech-synthesis/api
echo.
set /p APIKEY="  Paste your API key and press Enter: "
if not "%APIKEY%"=="" (
    echo ELEVENLABS_API_KEY=%APIKEY%> .env
    echo   OK -- saved to .env
) else (
    echo   Skipped. Add it later by editing .env in this folder.
)

:done
echo.
echo ============================================================
echo    Setup complete!
echo ============================================================
echo.
echo    Open DaVinci Resolve
echo    Go to:  Workspace  -^>  Scripts  -^>  audio_to_srt
echo.
pause
