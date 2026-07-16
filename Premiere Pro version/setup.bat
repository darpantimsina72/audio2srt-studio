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

REM Double-clicking the .bat while it is still INSIDE the ZIP extracts only
REM this one file to a temp folder — every other project file is missing.
if not exist "%EXT_ID%\" (
    echo   ERROR: Project files not found next to setup.bat.
    echo.
    echo   If you downloaded a ZIP, right-click it and choose "Extract All..."
    echo   first, then open the extracted folder and run setup.bat from there.
    echo.
    pause
    exit /b 1
)

REM -- 1. Python --------------------------------------------------
REM NOTE: "where python" is NOT enough — Windows ships a Microsoft Store
REM alias stub named python.exe that only prints an install hint and fails.
REM We must actually RUN the interpreter and keep its real path.
echo [ 1 / 6 ]  Checking Python...
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
winget --version >nul 2>nul
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
pause & exit /b 1

:python_found
for /f "tokens=*" %%v in ('"%PYTHON_EXE%" --version 2^>^&1') do echo   OK -- %%v  (%PYTHON_EXE%)

REM -- 2. elevenlabs ---------------------------------------------
echo.
echo [ 2 / 6 ]  Installing elevenlabs...
"%PYTHON_EXE%" -c "import elevenlabs" >nul 2>nul
if %errorlevel%==0 (
    echo   OK -- already installed
) else (
    REM Some installs ship without pip — bootstrap it first.
    "%PYTHON_EXE%" -m pip --version >nul 2>nul || "%PYTHON_EXE%" -m ensurepip --upgrade >nul 2>nul
    "%PYTHON_EXE%" -m pip install elevenlabs
    "%PYTHON_EXE%" -c "import elevenlabs" >nul 2>nul
    if !errorlevel!==0 ( echo   OK -- installed ) else (
        echo   ERROR: could not install elevenlabs. See the messages above --
        echo   the usual causes are no internet, a proxy, or an antivirus blocking Python.
        pause & exit /b 1
    )
)
REM Best-effort extra: truststore lets Python use Windows' certificate store,
REM so a company proxy / antivirus that inspects HTTPS doesn't break
REM transcription. The tools still work without it.
"%PYTHON_EXE%" -c "import truststore" >nul 2>nul
if errorlevel 1 "%PYTHON_EXE%" -m pip install --quiet --disable-pip-version-check truststore >nul 2>nul

REM -- 3. ffmpeg -------------------------------------------------
echo.
echo [ 3 / 6 ]  Checking ffmpeg (needed for the Silence Cut feature)...
where ffmpeg >nul 2>nul
if %errorlevel%==0 goto ffmpeg_done
if exist "C:\ffmpeg\bin\ffmpeg.exe" goto ffmpeg_done
if exist "C:\Program Files\ffmpeg\bin\ffmpeg.exe" goto ffmpeg_done
winget --version >nul 2>nul
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
echo   Done. (If Silence Cut still cannot find ffmpeg, restart Premiere.)
goto ffmpeg_end
:ffmpeg_done
echo   OK -- ffmpeg found
:ffmpeg_end

REM -- 4. Save project + Python paths ----------------------------
echo.
echo [ 4 / 6 ]  Saving project path...
REM Delayed expansion: a folder name containing & or ( ) would break plain %PROJ%
> "%USERPROFILE%\.audio_to_srt_premiere_path" echo(!PROJ!
> "%USERPROFILE%\.audio_to_srt_python" echo(!PYTHON_EXE!
echo   OK -- path saved to %USERPROFILE%\.audio_to_srt_premiere_path

REM -- 5. Enable CEP debug mode + install the panel -------------
echo.
echo [ 5 / 6 ]  Enabling Premiere extensions + installing the panel...
for %%v in (8 9 10 11 12) do (
    reg add "HKCU\Software\Adobe\CSXS.%%v" /v PlayerDebugMode /t REG_SZ /d 1 /f >nul 2>nul
)
set "CEP_DIR=%APPDATA%\Adobe\CEP\extensions"
if not exist "%CEP_DIR%" mkdir "%CEP_DIR%"
if exist "%CEP_DIR%\%EXT_ID%" rmdir /s /q "%CEP_DIR%\%EXT_ID%" 2>nul
xcopy /e /i /q /y "%PROJ%\%EXT_ID%" "%CEP_DIR%\%EXT_ID%" >nul
if errorlevel 1 (
    echo   ERROR: Could not copy the panel. If Premiere Pro is open with the
    echo   panel loaded, the files are locked -- quit Premiere and run
    echo   setup.bat again.
    pause & exit /b 1
)
echo   OK -- panel installed to:
echo        %CEP_DIR%\%EXT_ID%

REM -- 6. API key -----------------------------------------------
echo.
echo [ 6 / 6 ]  ElevenLabs API key
set "HAVEKEY="
if exist ".env" ( findstr /r /b /c:"ELEVENLABS_API_KEY=." .env >nul 2>nul && set "HAVEKEY=1" )
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
exit /b 0

REM -- helper: run "<cmd> -c ..." and keep sys.executable if it works --
:try_python
for /f "delims=" %%p in ('%1 %2 -c "import sys;print(sys.executable)" 2^>nul') do (
    if exist "%%p" set "PYTHON_EXE=%%p"
)
goto :eof
