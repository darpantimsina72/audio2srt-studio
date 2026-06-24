@echo off
REM Build Audio2SRT Studio on Windows.  Run from "Audio2SRT App":  build\build_win.bat
setlocal
cd /d "%~dp0\.."
echo == Audio2SRT Studio -- Windows build ==

set "PY=python"
where %PY% >nul 2>nul || set "PY=py -3"

echo [1/4] Python deps...
%PY% -m pip install --quiet --upgrade pyinstaller pywebview elevenlabs
if errorlevel 1 ( echo   ERROR installing deps & pause & exit /b 1 )

echo [2/4] Static ffmpeg / ffprobe into bin\ ...
if not exist bin mkdir bin
if not exist "bin\ffmpeg.exe" (
  echo   downloading static ffmpeg from gyan.dev ...
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Stop';" ^
    "$u='https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip';" ^
    "$z=Join-Path $env:TEMP 'a2srt_ffmpeg.zip';" ^
    "Invoke-WebRequest -Uri $u -OutFile $z;" ^
    "$d=Join-Path $env:TEMP 'a2srt_ffmpeg';" ^
    "if(Test-Path $d){Remove-Item -Recurse -Force $d};" ^
    "Expand-Archive -Path $z -DestinationPath $d -Force;" ^
    "$ff=Get-ChildItem -Path $d -Recurse -Filter ffmpeg.exe | Select-Object -First 1;" ^
    "$fp=Get-ChildItem -Path $d -Recurse -Filter ffprobe.exe | Select-Object -First 1;" ^
    "Copy-Item $ff.FullName 'bin\ffmpeg.exe' -Force;" ^
    "Copy-Item $fp.FullName 'bin\ffprobe.exe' -Force;" ^
    "Write-Host '   ok - static ffmpeg in bin\\'"
  if errorlevel 1 (
    echo   WARNING: auto-download failed. Manually put static ffmpeg.exe + ffprobe.exe
    echo            in bin\ from https://www.gyan.dev/ffmpeg/builds/
  )
)

echo [3/4] PyInstaller...
%PY% -m PyInstaller --noconfirm --clean build\audio2srt.spec
if errorlevel 1 ( echo   ERROR: build failed. If it mentions elevenlabs/pydantic, see README. & pause & exit /b 1 )

echo [4/4] Optional one-click installer (Inno Setup)...
where iscc >nul 2>nul
if %errorlevel%==0 (
  iscc build\installer.iss && echo   installer -> dist\Audio2SRT-Studio-Setup.exe
) else (
  echo   Inno Setup ^(iscc^) not found - skipping. Ship dist\Audio2SRT Studio\ as a zip,
  echo   or install Inno Setup ^(https://jrsoftware.org/isinfo.php^) to make a single .exe.
)

echo.
echo Done. App: dist\Audio2SRT Studio\Audio2SRT Studio.exe
echo Unsigned: first run shows SmartScreen -> "More info" -> "Run anyway".
pause
