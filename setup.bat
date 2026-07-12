@echo off
setlocal enabledelayedexpansion
title Audio to SRT -- Setup

:: Always run from this script's own folder
cd /d "%~dp0"

cls
echo ============================================================
echo    Audio to SRT  --  Setup  (Windows)
echo ============================================================
echo.

:: Double-clicking the .bat while it is still INSIDE the ZIP extracts only
:: this one file to a temp folder — every other project file is missing.
if not exist "transcribe.py" (
    echo   ERROR: Project files not found next to setup.bat.
    echo.
    echo   If you downloaded a ZIP, right-click it and choose "Extract All..."
    echo   first, then open the extracted folder and run setup.bat from there.
    echo.
    pause
    exit /b 1
)

:: ── 1. Python check ───────────────────────────────────────────
:: NOTE: "where python" is NOT enough — Windows ships a Microsoft
:: Store alias stub named python.exe that only prints an install
:: hint and fails. We must actually RUN the interpreter and keep
:: its real path (sys.executable).
echo [ 1 / 6 ]  Checking Python...
set "PYTHON_EXE="

:: The python.org "py" launcher is never shadowed by the Store stub
call :try_python py -3
if defined PYTHON_EXE goto python_found

:: "python" on PATH — validated by running it, so the stub is rejected
call :try_python python
if defined PYTHON_EXE goto python_found

:: Common per-user installs (user forgot "Add Python to PATH")
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%d\python.exe" set "PYTHON_EXE=%%d\python.exe"
)
if defined PYTHON_EXE goto python_found
for /d %%d in ("C:\Python3*") do (
    if exist "%%d\python.exe" set "PYTHON_EXE=%%d\python.exe"
)
if defined PYTHON_EXE goto python_found
for /d %%d in ("%ProgramFiles%\Python3*") do (
    if exist "%%d\python.exe" set "PYTHON_EXE=%%d\python.exe"
)
if defined PYTHON_EXE goto python_found

:: Not installed — offer automatic install via winget
echo.
echo   Python 3 is not installed on this computer.
winget --version >nul 2>&1
if errorlevel 1 goto python_manual
choice /c YN /m "  Install Python 3.12 automatically now"
if errorlevel 2 goto python_manual
echo   Installing Python 3.12 via winget (takes 1-2 minutes)...
winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
:: The installer only updates PATH for FUTURE terminals — locate the fresh
:: interpreter directly, wherever winget put it (user or machine scope).
call :try_python py -3
if defined PYTHON_EXE goto python_found
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%d\python.exe" set "PYTHON_EXE=%%d\python.exe"
)
if defined PYTHON_EXE goto python_found
for /d %%d in ("%ProgramFiles%\Python3*") do (
    if exist "%%d\python.exe" set "PYTHON_EXE=%%d\python.exe"
)
if defined PYTHON_EXE goto python_found
echo   The automatic install did not complete.

:python_manual
echo.
echo   Please install Python manually:
echo     1. A browser window will open at  https://www.python.org/downloads/
echo     2. Run the installer and CHECK the box "Add python.exe to PATH"
echo     3. Double-click setup.bat again
echo.
start "" https://www.python.org/downloads/
pause
exit /b 1

:python_found
for /f "tokens=*" %%v in ('"%PYTHON_EXE%" --version 2^>^&1') do echo   OK -- %%v  (%PYTHON_EXE%)

:: ── 2. elevenlabs ─────────────────────────────────────────────
echo.
echo [ 2 / 6 ]  Installing elevenlabs...
:: Some installs ship without pip — bootstrap it first.
"%PYTHON_EXE%" -m pip --version >nul 2>&1 || "%PYTHON_EXE%" -m ensurepip --upgrade >nul 2>&1
"%PYTHON_EXE%" -m pip install --quiet --disable-pip-version-check -r requirements.txt
"%PYTHON_EXE%" -c "import elevenlabs" >nul 2>&1
if errorlevel 1 (
    echo   First attempt failed -- retrying with full output so you can see why...
    "%PYTHON_EXE%" -m pip install --disable-pip-version-check -r requirements.txt
    "%PYTHON_EXE%" -c "import elevenlabs" >nul 2>&1
)
if errorlevel 1 (
    echo   ERROR: pip install failed. See the messages above -- the usual causes
    echo   are no internet, a proxy, or an antivirus blocking Python.
    pause
    exit /b 1
)
echo   OK

:: ── 3. tkinter (bundled with Python on Windows — just verify) ─
echo.
echo [ 3 / 6 ]  Checking tkinter...
"%PYTHON_EXE%" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo   WARNING: tkinter not found.
    echo   Re-install Python and make sure "tcl/tk" option is checked.
) else (
    echo   OK
)

:: ── 4. ffmpeg (optional — needed only for the Silence Cut feature) ─
echo.
echo [ 4 / 6 ]  Checking ffmpeg (optional, used by Silence Cut)...
where ffmpeg >nul 2>&1
if not errorlevel 1 goto ffmpeg_done
if exist "C:\ffmpeg\bin\ffmpeg.exe" goto ffmpeg_done
if exist "C:\Program Files\ffmpeg\bin\ffmpeg.exe" goto ffmpeg_done
winget --version >nul 2>&1
if errorlevel 1 (
    echo   NOTE: ffmpeg not found. Subtitles still work; Silence Cut will not.
    echo         Download from https://ffmpeg.org/download.html and add it to PATH.
    goto ffmpeg_end
)
choice /c YN /m "  ffmpeg not found. Install it automatically now"
if errorlevel 2 (
    echo   Skipped. Subtitles still work; Silence Cut will not.
    goto ffmpeg_end
)
echo   Installing ffmpeg via winget (takes 1-2 minutes)...
winget install -e --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
echo   Done. (If Silence Cut still cannot find ffmpeg, restart DaVinci Resolve.)
goto ffmpeg_end
:ffmpeg_done
echo   OK -- ffmpeg found
:ffmpeg_end

:: ── 5. Save project + Python paths for the Lua script ─────────
echo.
echo [ 5 / 6 ]  Saving project path...
:: Delayed expansion: a folder name containing & or ( ) would break plain %CD%
> "%USERPROFILE%\.audio_to_srt_path" echo(!CD!
> "%USERPROFILE%\.audio_to_srt_python" echo(!PYTHON_EXE!
echo   OK -- paths saved to %%USERPROFILE%%\.audio_to_srt_path

:: ── 6. Install Lua script into Resolve ────────────────────────
echo.
echo [ 6 / 6 ]  Installing audio_to_srt.lua into DaVinci Resolve...
set "USER_RESOLVE_SCRIPTS=%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility"
set "SYSTEM_RESOLVE_SCRIPTS=%ProgramData%\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility"

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
    findstr /r /c:"ELEVENLABS_API_KEY=." .env >nul 2>&1
    if not errorlevel 1 (
        echo   API key already saved in .env  --  skipping.
        goto done
    )
)
echo   ElevenLabs API key setup
echo   Get your key at: https://elevenlabs.io/app/speech-synthesis/api
echo.
set "APIKEY="
set /p APIKEY="  Paste your API key and press Enter: "
if defined APIKEY (
    > .env echo(ELEVENLABS_API_KEY=!APIKEY!
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
exit /b 0

:: ── helper: run "<cmd> -c ..." and keep sys.executable if it works ──
:try_python
for /f "delims=" %%p in ('%1 %2 -c "import sys;print(sys.executable)" 2^>nul') do (
    if exist "%%p" set "PYTHON_EXE=%%p"
)
goto :eof
