; Inno Setup 脚本 — 律匠 Windows 安装包
; 构建: iscc packaging\installer.iss /DAppVersion=X.Y.Z
; 前提: package.bat 已完成 PyInstaller onedir 构建 + 依赖拷贝

#define MyAppName "律匠"
#define MyAppExeName "lvjiang.exe"
#define MyAppPublisher "lvjiang"
#define MyAppURL "https://github.com/wanda1416/lvjiang"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
#ifndef AppVersion
  #error AppVersion must be defined (pass /DAppVersion=X.Y.Z)
#endif
AppVersion={#AppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; 默认安装到用户可选目录（不强制 Program Files，因应用需运行时写入 config/local）
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes

; 输出
OutputDir=..\dist
OutputBaseFilename=lvjiang-v{#AppVersion}-win64-setup
Compression=lzma2/ultra64
SolidCompression=yes

; UI
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; 卸载
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; PyInstaller onedir 产物 + 运行时依赖（config/system, data/adb, data/scrcpy）
; PyInstaller 创建 dist/lvjiang/ 结构，lvjiang.exe 位于该目录根部
Source: "..\dist\lvjiang\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
