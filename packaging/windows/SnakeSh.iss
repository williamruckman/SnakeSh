#ifndef MyAppVersion
  #define MyAppVersion "1.6"
#endif

#define MyAppName "SnakeSh"
#define MyAppPublisher "SnakeSh"
#define MyAppExeName "SnakeSh.exe"
#define MySourceDir "..\..\dist\SnakeSh"

[Setup]
AppId={{8B8F444C-EA46-4B03-B236-9AA9BA8B226C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\SnakeSh
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=SnakeSh-{#MyAppVersion}-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\..\src\snakesh\assets\snakesh-icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\SnakeSh"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\SnakeSh"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\.ssx"; ValueType: string; ValueName: ""; ValueData: "SnakeSh.Export"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\SnakeSh.Export"; ValueType: string; ValueName: ""; ValueData: "SnakeSh Export File"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SnakeSh.Export\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"",0"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SnakeSh.Export\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletekey

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\Microsoft\Windows\Start Menu\Programs\SnakeSh Tools"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch SnakeSh"; Flags: nowait postinstall skipifsilent
