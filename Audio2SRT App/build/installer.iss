; Inno Setup script for Audio2SRT Studio (Windows).
; Build the app first (build\build_win.bat creates dist\Audio2SRT Studio\),
; then compile this with Inno Setup:  iscc build\installer.iss
; Output: dist\Audio2SRT-Studio-Setup.exe  (a single double-click installer)

#define AppName "Audio2SRT Studio"
#define AppVersion "1.0.3"
#define AppExe "Audio2SRT Studio.exe"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Audio2SRT
DefaultDirName={localappdata}\Programs\Audio2SRT Studio
DefaultGroupName=Audio2SRT Studio
DisableProgramGroupPage=yes
; Per-user install -> no admin prompt (UAC) for non-technical users.
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Audio2SRT-Studio-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
; Ship the whole PyInstaller onedir output.
Source: "..\dist\Audio2SRT Studio\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch Audio2SRT Studio"; Flags: nowait postinstall skipifsilent
