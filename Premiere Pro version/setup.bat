@echo off
REM ============================================================
REM   Audio to SRT  --  Premiere Pro Setup  (Windows)
REM   Double-click this file to run.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "PROJ=%cd%"
set "EXT_ID=com.audiotosrt.cep"

cls
echo ============================================================
echo    Audio to SRT  --  Premiere Pro Setup  (Windows)
echo ============================================================
echo.

REM -- 1. Python --------------------------------------------------
echo [ 1 / 6 ]  Checking Python...
set "PYTHON="
where python >nul 2>nul && set "PYTHON=python"
if not defined PYTHON ( where py >nul 2>nul && set "PYTHON=py -3" )
if not defined PYTHON (
    echo   ERROR: Python 3 not found. Install from https://www.python.org/downloads/
    echo   Be sure to check "Add Python to PATH" during install.
    pause & exit /b 1
)
echo   OK -- %PYTHON%

REM -- 2. elevenlabs ---------------------------------------------
echo.
echo [ 2 / 6 ]  Installing elevenlabs...
%PYTHON% -c "import elevenlabs" >nul 2>nul
if %errorlevel%==0 (
    echo   OK -- already installed
) else (
    %PYTHON% -m pip install elevenlabs
    %PYTHON% -c "import elevenlabs" >nul 2>nul
    if !errorlevel!==0 ( echo   OK -- installed ) else (
        echo   ERROR: could not install elevenlabs. Try: pip install elevenlabs
        pause & exit /b 1
    )
)

REM -- 3. ffmpeg -------------------------------------------------
echo.
echo [ 3 / 6 ]  Checking ffmpeg (needed for the Silence Cut feature)...
where ffmpeg >nul 2>nul
if %errorlevel%==0 (
    echo   OK -- ffmpeg found
) else (
    echo   NOTE: ffmpeg not found. Subtitles still work; Silence Cut will not.
    echo         Download from https://ffmpeg.org/download.html and add it to PATH.
)

REM -- 4. Save project path --------------------------------------
echo.
echo [ 4 / 6 ]  Saving project path...
> "%USERPROFILE%\.audio_to_srt_premiere_path" echo %PROJ%
echo   OK -- path saved to %USERPROFILE%\.audio_to_srt_premiere_path

REM -- 5. Enable CEP debug mode + install the panel -------------
echo.
echo [ 5 / 6 ]  Enabling Premiere extensions + installing the panel...
for %%v in (8 9 10 11 12) do (
    reg add "HKCU\Software\Adobe\CSXS.%%v" /v PlayerDebugMode /t REG_SZ /d 1 /f >nul 2>nul
)
set "CEP_DIR=%APPDATA%\Adobe\CEP\extensions"
if not exist "%CEP_DIR%" mkdir "%CEP_DIR%"
if exist "%CEP_DIR%\%EXT_ID%" rmdir /s /q "%CEP_DIR%\%EXT_ID%"
xcopy /e /i /q /y "%PROJ%\%EXT_ID%" "%CEP_DIR%\%EXT_ID%" >nul
echo   OK -- panel installed to:
echo        %CEP_DIR%\%EXT_ID%

REM -- 6. API key -----------------------------------------------
echo.
echo [ 6 / 6 ]  ElevenLabs API key
set "HAVEKEY="
if exist ".env" ( findstr /b /c:"ELEVENLABS_API_KEY=" .env >nul 2>nul && set "HAVEKEY=1" )
if defined HAVEKEY (
    echo   API key already saved in .env  --  skipping.
) else (
    echo   Get your key at: https://elevenlabs.io/app/speech-synthesis/api
    set /p APIKEY="  Paste your API key and press Enter: "
    if defined APIKEY (
        > .env echo ELEVENLABS_API_KEY=!APIKEY!
        echo   OK -- saved to .env
    ) else (
        echo   Skipped. Add it later to %PROJ%\.env
    )
)

echo.
echo ============================================================
echo    Setup complete!
echo ============================================================
echo.
echo    1. FULLY QUIT Premiere Pro and reopen it
echo    2. Open a project + a sequence with an audio clip
echo    3. Menu:  Window  ^>  Extensions  ^>  Audio to SRT
echo.
pause
