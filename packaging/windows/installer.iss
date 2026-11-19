; Inno Setup 6 script for WyldVeil's Media Editor.
; Built by packaging/build.py:  ISCC /DAppVersion=x.y.z /DSourceDir=... /DOutputDir=... installer.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\WyldVeilMediaEditor"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

[Setup]
AppId={{8C1E5A2D-3F4B-4E8A-9C61-2B7D0E9A4F15}
AppName=WyldVeil Media Editor
AppVersion={#AppVersion}
AppPublisher=WyldVeil
AppPublisherURL=https://github.com/WyldVeil/wyldveils-media-editor
AppSupportURL=https://github.com/WyldVeil/wyldveils-media-editor/issues
DefaultDirName={autopf}\WyldVeil Media Editor
DefaultGroupName=WyldVeil Media Editor
DisableProgramGroupPage=yes
; Per-user install by default (no UAC prompt); the user may choose all-users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=WyldVeilMediaEditor-{#AppVersion}-windows-x64-setup
SetupIconFile=..\..\assets\icons\app.ico
UninstallDisplayIcon={app}\WyldVeilMediaEditor.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\..\THIRD_PARTY_NOTICES.md

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\WyldVeil Media Editor"; Filename: "{app}\WyldVeilMediaEditor.exe"
Name: "{group}\Uninstall WyldVeil Media Editor"; Filename: "{uninstallexe}"
Name: "{autodesktop}\WyldVeil Media Editor"; Filename: "{app}\WyldVeilMediaEditor.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\WyldVeilMediaEditor.exe"; Description: "{cm:LaunchProgram,WyldVeil Media Editor}"; Flags: nowait postinstall skipifsilent
