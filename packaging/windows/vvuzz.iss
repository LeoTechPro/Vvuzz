#define AppVersion "0.0.1"
#ifndef SourceDir
  #define SourceDir "..\..\dist"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif
[Setup]
AppId={{74A875A2-FDA2-4FC5-ADE8-36E9265AE066}
AppName=Vvuzz
AppVersion={#AppVersion}
AppPublisher=intData
AppPublisherURL=https://intdata.pro/vvuzz/
AppSupportURL=https://github.com/LeoTechPro/Vvuzz
DefaultDirName={localappdata}\Programs\Vvuzz
DefaultGroupName=Vvuzz
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=vvuzz-0.0.1-windows-amd64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\vvuzz.exe
CloseApplications=no
RestartApplications=no
SetupLogging=no
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
[Tasks]
Name: "autostart"; Description: "Запускать Vvuzz при входе в Windows"; Flags: unchecked
[Files]
Source: "{#SourceDir}\vvuzz.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"
Source: "..\licenses\*"; DestDir: "{app}\licenses"; Flags: recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Vvuzz"; Filename: "{app}\vvuzz.exe"; Comment: "Локальное наблюдение за поступлением"
Name: "{group}\Открыть интерфейс Vvuzz"; Filename: "http://127.0.0.1:9177"
Name: "{userstartup}\Vvuzz"; Filename: "{app}\vvuzz.exe"; Tasks: autostart
[Run]
Filename: "{app}\vvuzz.exe"; Description: "Запустить Vvuzz в трее"; Flags: nowait postinstall skipifsilent unchecked
