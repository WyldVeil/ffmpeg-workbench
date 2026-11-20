; WyldVeil's Media Editor - Windows installer (Inno Setup 6.3+ / 7)
;
; Built by packaging/release.py, which stages an embeddable Python (with
; tkinter) in StageDir\python, the app with its bundled tools in StageDir\app,
; and the launchers next to them:
;
;   ISCC /DAppVersion=1.2.0 /DStageDir=...\build\release-windows release.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef StageDir
  #error StageDir must be defined
#endif
#define AppName "WyldVeil's Media Editor"

[Setup]
; Same AppId as earlier releases, so installing this upgrades them in place.
AppId={{B2F4C8E1-7A3D-4E9F-A1B6-2D5E8F3C7A9B}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=WyldVeil
AppPublisherURL=https://github.com/WyldVeil/wyldveils-media-editor
AppSupportURL=https://github.com/WyldVeil/wyldveils-media-editor/issues
DefaultDirName={autopf}\WyldVeil Media Editor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputBaseFilename=WyldVeils-Media-Editor_{#AppVersion}_Windows_Setup
SetupIconFile={#StageDir}\app\assets\icons\app.ico
UninstallDisplayIcon={app}\app\assets\icons\app.ico
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Left over from releases before 1.2.0: Python add-ons now live in app\libs
; (an old copy here would shadow them) and the tests aren't shipped any more.
Type: filesandordirs; Name: "{app}\libs"
Type: filesandordirs; Name: "{app}\app\tests"
Type: filesandordirs; Name: "{app}\app\bin"
; Python is replaced as a whole, so nothing from an older one (such as the Tk 8.6
; files before 1.2.1) is left behind.
Type: filesandordirs; Name: "{app}\python"

[Files]
Source: "{#StageDir}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\Launch.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\wme-cli.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\main.py"""; WorkingDir: "{app}"; IconFilename: "{app}\app\assets\icons\app.ico"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\main.py"""; WorkingDir: "{app}"; IconFilename: "{app}\app\assets\icons\app.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\main.py"""; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\python"
