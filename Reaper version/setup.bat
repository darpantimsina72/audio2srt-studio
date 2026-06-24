@echo off
setlocal
title Audio to SRT for REAPER -- Setup

cd /d "%~dp0"
set "PYTHON_CMD="

cls
echo ============================================================
echo    Audio to SRT for REAPER  --  Setup  (Windows)
echo ============================================================
echo.

echo [ 1 / 4 ]  Checking Python...
where python >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"
if "%PYTHON_CMD%"=="" (
    where py >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)
if "%PYTHON_CMD%"=="" (
    echo   ERROR: Python is not installed or not on PATH.
    echo   Download from: https://www.python.org/downloads/
    echo   During install, enable "Add Python to PATH".
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('%PYTHON_CMD% --version 2^>^&1') do echo   OK -- %%v

echo.
echo [ 2 / 4 ]  Installing Python dependencies...
%PYTHON_CMD% -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo   ERROR: pip install failed. Check your connection or Python permissions.
    pause
    exit /b 1
)
%PYTHON_CMD% -c "import elevenlabs" >nul 2>&1
if errorlevel 1 (
    echo   ERROR: elevenlabs did not install correctly.
    pause
    exit /b 1
)
echo   OK

echo.
echo [ 3 / 4 ]  Saving project path...
echo %CD%> "%USERPROFILE%\.audio_to_srt_reaper_path"
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
    findstr /C:"ELEVENLABS_API_KEY=" .env >nul 2>&1
    if not errorlevel 1 (
        echo   API key already saved in .env -- skipping.
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
echo    In REAPER:
echo    1. Open Actions
echo    2. Click ReaScript: Load
echo    3. Choose %REAPER_SCRIPTS%\audio_to_srt_reaper.lua
echo    4. Run the script with one media item selected
echo    5. Enter the output .srt file path when prompted
echo.
pause
