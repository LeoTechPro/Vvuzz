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

[Code]
function OpenProcess(Access: LongWord; Inherit: Boolean; PID: LongWord): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(Handle: THandle; Milliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

var
  DataMode, DataPath: String;

function InitializeUninstall(): Boolean;
var
  Choice, PID: Integer;
  Process: THandle;
begin
  Result := False;
  DataMode := ExpandConstant('{param:VVUZZMODE|}');
  DataPath := ExpandConstant('{param:VVUZZDATA|{userappdata}\Vvuzz}');
  if DataMode = '' then begin
    if UninstallSilent then DataMode := 'keep'
    else begin
      Choice := MsgBox('Удалить также локальные списки, историю и настройки Vvuzz?' + #13#10 + #13#10 +
        'Да — удалить приложение и локальные данные.' + #13#10 +
        'Нет — удалить только приложение, сохранить данные.' + #13#10 + #13#10 +
        'Подключённые архивы и скачанные наборы сохранятся в обоих случаях.',
        mbConfirmation, MB_YESNOCANCEL or MB_DEFBUTTON2);
      if Choice = IDCANCEL then Exit;
      if Choice = IDYES then DataMode := 'delete' else DataMode := 'keep';
    end;
  end;
  if (DataMode <> 'keep') and (DataMode <> 'delete') then Exit;
  PID := StrToIntDef(ExpandConstant('{param:VVUZZPID|0}'), 0);
  if PID > 0 then begin
    Process := OpenProcess($00100000, False, PID);
    if Process <> 0 then begin
      if WaitForSingleObject(Process, 15000) <> 0 then begin
        CloseHandle(Process);
        MsgBox('Vvuzz ещё работает. Завершите его через меню трея и повторите удаление.', mbError, MB_OK);
        Exit;
      end;
      CloseHandle(Process);
    end;
  end;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ExitCode: Integer;
  Arguments: String;
begin
  if CurUninstallStep = usUninstall then begin
    if DataMode = 'delete' then Arguments := '--remove-local-data'
    else Arguments := '--check-data-unlocked';
    Arguments := Arguments + ' --data ' + AddQuotes(DataPath);
    if not Exec(ExpandConstant('{app}\vvuzz.exe'), Arguments, '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
      RaiseException('Не удалось проверить локальные данные. Удаление остановлено.');
    if ExitCode <> 0 then
      RaiseException('Vvuzz работает или данные недоступны. Завершите приложение и повторите удаление. Архивы сохранены.');
  end;
end;
