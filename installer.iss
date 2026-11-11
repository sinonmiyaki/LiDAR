#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z
#endif
#ifndef AppFiles
  #define AppFiles "dist\LiDAR"
#endif
#ifndef ModelFiles
  #define ModelFiles "models"
#endif

[Setup]
AppId={{0E19C5A3-A965-496D-AC19-92666A80AB6D}
AppName=LiDAR
AppVersion={#AppVersion}
AppPublisher=LiDAR
AppPublisherURL=https://github.com/sinonmiyaki/LiDAR
AppUpdatesURL=https://github.com/sinonmiyaki/LiDAR/releases
DefaultDirName={localappdata}\Programs\LiDAR
DefaultGroupName=LiDAR
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=yes
OutputBaseFilename=LiDAR-Setup-{#AppVersion}-x64
SetupIconFile=LiDAR.ico
UninstallDisplayIcon={app}\LiDAR.exe
AppMutex=Local\LiDAR_0E19C5A3_A965_496D_AC19_92666A80AB6D
VersionInfoVersion={#AppVersion}

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#AppFiles}\LiDAR.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#AppFiles}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ModelFiles}\lid.176.ftz"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelFiles}\ocr\*"; DestDir: "{app}\models\ocr"; Flags: ignoreversion recursesubdirs
Source: "LiDAR.vrmanifest"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "brand\lidar-readme.png"; DestDir: "{app}\brand"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\LiDAR"; Filename: "{app}\LiDAR.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\LiDAR.exe"; Description: "LiDAR"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\LiDAR.exe"; Parameters: "--unregister-steamvr"; Flags: runhidden; RunOnceId: "RemoveSteamVRRegistration"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    DeleteFile(ExpandConstant('{app}\_internal\ctranslate2.dll'));
    DeleteFile(ExpandConstant('{app}\_internal\cv2\opencv_videoio_ffmpeg500_64.dll'));
  end;
end;
