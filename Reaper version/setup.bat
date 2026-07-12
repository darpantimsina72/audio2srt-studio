@echo off
setlocal enabledelayedexpansion
title Audio to SRT for REAPER -- Setup

cd /d "%~dp0"

cls
echo ============================================================
echo    Audio to SRT for REAPER  --  Setup  (Windows)
echo ============================================================
echo.

REM Double-clicking the .bat while it is still INSIDE the ZIP extracts only
REM this one file to a temp folder — every other project file is missing.
if not exist "transcribe.py" (
    echo   ERROR: Project files not found next to setup.bat.
    echo.
    echo   If you downloaded a ZIP, right-click it and choose "Extract All..."
    echo   first, then open the extracted folder and run setup.bat from there.
    echo.
    pause
    exit /b 1
)

REM NOTE: "where python" is NOT enough — Windows ships a Microsoft Store
REM alias stub named python.exe that only prints an install hint and fails.
REM We must actually RUN the interpreter and keep its real path.
echo [ 1 / 4 ]  Checking Python...
set "PYTHON_EXE="
call :try_python py -3
if not defined PYTHON_EXE call :try_python python
if not defined PYTHON_EXE (
    for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if exist "%%d\python.exe" set "PYTHON_EXE=%%d\python.exe"
    )
)
if not defined PYTHON_EXE (
    for /d %%d in ("C:\Python3*") do (
        if exist "%%d\python.exe" set "PYTHON_EXE=%%d\python.exe"
    )
)
if not defined PYTHON_EXE (
    for /d %%d in ("%ProgramFiles%\Python3*") do (
        if exist "%%d\python.exe" set "PYTHON_EXE=%%d\python.exe"
    )
)
if defined PYTHON_EXE goto python_found

echo.
echo   Python 3 is not installed on this computer.
winget --version >nul 2>&1
if errorlevel 1 goto python_manual
choice /c YN /m "  Install Python 3.12 automatically now"
if errorlevel 2 goto python_manual
echo   Installing Python 3.12 via winget (takes 1-2 minutes)...
winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
REM The installer only updates PATH for FUTURE terminals — locate the fresh
REM interpreter directly, wherever winget put it (user or machine scope).
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

echo.
echo [ 2 / 4 ]  Installing Python dependencies...
REM Some installs ship without pip — bootstrap it first.
"%PYTHON_EXE%" -m pip --version >nul 2>&1 || "%PYTHON_EXE%" -m ensurepip --upgrade >nul 2>&1
"%PYTHON_EXE%" -m pip install --quiet --disable-pip-version-check -r requirements.txt
"%PYTHON_EXE%" -c "import elevenlabs" >nul 2>&1
if errorlevel 1 (
    echo   First attempt failed -- retrying with full output so you can see why...
    "%PYTHON_EXE%" -m pip install --disable-pip-version-check -r requirements.txt
    "%PYTHON_EXE%" -c "import elevenlabs" >nul 2>&1
)
if errorlevel 1 (
    echo   ERROR: elevenlabs did not install correctly. See the messages above --
    echo   the usual causes are no internet, a proxy, or an antivirus blocking Python.
    pause
    exit /b 1
)
echo   OK

echo.
echo [ 3 / 4 ]  Saving project path...
REM Delayed expansion: a folder name containing & or ( ) would break plain %CD%
> "%USERPROFILE%\.audio_to_srt_reaper_path" echo(!CD!
> "%USERPROFILE%\.audio_to_srt_python" echo(!PYTHON_EXE!
echo   OK -- path saved to %%USERPROFILE%%\.audio_to_srt_reaper_path

echo.
echo [ 4 / 4 ]  Installing script into REAPER...
set REAPER_SCRIPTS=%APPDATA%\REAPER\Scripts
if not exist "%REAPER_SCRIPTS%" mkdir "%REAPER_SCRIPTS%" >nul 2>&1
copy /Y audio_to_srt_reaper.lua "%REAPER_SCRIPTS%\audio_to_srt_reaper.lua" >nul
if errorlevel 1 (
    echo   ERROR: Could not copy script into:
    echo   %REAPER_SCRIPTS%
    pause
    exit /b 1
)
echo   OK -- copied to:
echo   %REAPER_SCRIPTS%\audio_to_srt_reaper.lua

echo.
echo ------------------------------------------------------------
if exist .env (
    findstr /r /c:"ELEVENLABS_API_KEY=." .env >nul 2>&1
    if not errorlevel 1 (
        echo   API key already saved in .env -- skipping.
        goto done
    )
)
echo   ElevenLabs API key setup
echo   Get your key at: https://elevenlabs.io/app/speech-synthesis/api
echo.
set "APIKEY="
set /p APIKEY="  Paste your API key and press Enter: "
if defined APIKEY (
    REM Redirect-first: "echo ...%APIKEY%> .env" would eat a trailing digit
    REM of the key as a file-handle number (cmd redirection quirk).
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
echo    In REAPER:
echo    1. Open Actions
echo    2. Click ReaScript: Load
echo    3. Choose %REAPER_SCRIPTS%\audio_to_srt_reaper.lua
echo    4. Run the script with one media item selected
echo    5. Enter the output .srt file path when prompted
echo.
pause
exit /b 0

REM -- helper: run "<cmd> -c ..." and keep sys.executable if it works --
:try_python
for /f "delims=" %%p in ('%1 %2 -c "import sys;print(sys.executable)" 2^>nul') do (
    if exist "%%p" set "PYTHON_EXE=%%p"
)
goto :eof
