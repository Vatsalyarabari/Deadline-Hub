; Windows installer for Deadline Hub (Inno Setup 6).
; One screen: click Install. No admin rights needed (installs for the current user).
; Build:  iscc /DAppVersion=0.5.0 packaging\installer.iss   ->  dist\DeadlineHub-Setup.exe

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{194315B1-A781-4BB1-B63F-3C34D466326E}
AppName=Deadline Hub
AppVersion={#AppVersion}
AppPublisher=Deadline Hub
AppPublisherURL=https://github.com/Vatsalyarabari/Deadline-Hub
DefaultDirName={localappdata}\Programs\Deadline Hub
DefaultGroupName=Deadline Hub
PrivilegesRequired=lowest
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
CloseApplications=yes
OutputDir=..\dist
OutputBaseFilename=DeadlineHub-Setup
SetupIconFile=icons\icon.ico
UninstallDisplayIcon={app}\DeadlineHub.exe
UninstallDisplayName=Deadline Hub
WizardStyle=modern
Compression=lzma2
SolidCompression=yes

[Files]
Source: "..\dist\DeadlineHub.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Deadline Hub"; Filename: "{app}\DeadlineHub.exe"
Name: "{autodesktop}\Deadline Hub"; Filename: "{app}\DeadlineHub.exe"

[Registry]
; Open with the computer (icon by the clock, no window) so syncing keeps happening.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DeadlineHub"; ValueData: """{app}\DeadlineHub.exe"" ""--no-browser"""; Flags: uninsdeletevalue
; Receive "Sign in with Moodle" links (unless the Moodle Desktop app already does).
Root: HKCU; Subkey: "Software\Classes\moodlemobile"; ValueType: string; ValueData: "URL:Moodle sign-in (Deadline Hub)"; Flags: uninsdeletekey; Check: not MoodleSchemeTaken
Root: HKCU; Subkey: "Software\Classes\moodlemobile"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Check: not MoodleSchemeTaken
Root: HKCU; Subkey: "Software\Classes\moodlemobile\shell\open\command"; ValueType: string; ValueData: """{app}\DeadlineHub.exe"" ""%1"""; Check: not MoodleSchemeTaken

[Run]
Filename: "{app}\DeadlineHub.exe"; Description: "Open Deadline Hub"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM DeadlineHub.exe /F"; Flags: runhidden; RunOnceId: "StopApp"
Filename: "{cmd}"; Parameters: "/C schtasks /Delete /F /TN DeadlineHub"; Flags: runhidden; RunOnceId: "RemoveTask"

[Code]
function MoodleSchemeTaken(): Boolean;
var
  Cmd: String;
begin
  Result := False;
  if RegQueryStringValue(HKCU, 'Software\Classes\moodlemobile\shell\open\command', '', Cmd) then
    Result := Pos('deadlinehub', Lowercase(Cmd)) = 0;
end;
