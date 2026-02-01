[Setup]
AppName=Jarvis Assistant
AppVersion=1.0
AppPublisher=Jarvis Team
DefaultDirName={pf}\JarvisAssistant
DefaultGroupName=Jarvis Assistant
OutputDir=output
OutputBaseFilename=JarvisInstaller
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\Jarvis.exe
DisableProgramGroupPage=no


[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"


[Files]
; Copy FULL PyInstaller folder
Source: "..\dist\Jarvis\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion


[Icons]
; Start Menu shortcut
Name: "{group}\Jarvis Assistant"; Filename: "{app}\Jarvis.exe"

; Desktop shortcut
Name: "{commondesktop}\Jarvis Assistant"; Filename: "{app}\Jarvis.exe"


[Run]
; Launch after install
Filename: "{app}\Jarvis.exe"; Description: "Launch Jarvis Assistant"; Flags: nowait postinstall skipifsilent
