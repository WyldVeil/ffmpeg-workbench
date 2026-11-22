; Inno Setup 6 script for FFmpeg Workbench.
; Built by packaging/build.py:  ISCC /DAppVersion=x.y.z /DSourceDir=... /DOutputDir=... installer.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\FFmpegWorkbench"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

#ifndef RepoUrl
  #define RepoUrl "https://github.com"
#endif

[Setup]
AppId={{8C1E5A2D-3F4B-4E8A-9C61-2B7D0E9A4F15}
AppName=FFmpeg Workbench
AppVersion={#AppVersion}
AppPublisher=FFmpeg Workbench
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
DefaultDirName={autopf}\FFmpeg Workbench
DefaultGroupName=FFmpeg Workbench
DisableProgramGroupPage=yes
; Per-user install by default (no UAC prompt); the user may choose all-users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=FFmpegWorkbench-{#AppVersion}-windows-x64-setup
SetupIconFile=..\..\assets\icons\app.ico
UninstallDisplayIcon={app}\FFmpegWorkbench.exe
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
Name: "{group}\FFmpeg Workbench"; Filename: "{app}\FFmpegWorkbench.exe"
Name: "{group}\Uninstall FFmpeg Workbench"; Filename: "{uninstallexe}"
Name: "{autodesktop}\FFmpeg Workbench"; Filename: "{app}\FFmpegWorkbench.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\FFmpegWorkbench.exe"; Description: "{cm:LaunchProgram,FFmpeg Workbench}"; Flags: nowait postinstall skipifsilent
