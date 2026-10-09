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

[InstallDelete]
; 覆盖安装前清空随包分发的两个目录，Inno 只覆盖同名文件、不会删除新包里已经
; 消失的文件。不清理的话上游删除或移动过的内容会永远留在用户机器上：
; `.wf` 换个目录就变成两份同 id 脚本（一份生效一份幽灵），被删掉的场景、布局、
; 参照图继续以旧内容加载，换依赖后的旧 .pyd/.dll 也会残留在 _internal 里。
;
; config/local 是用户自己的覆盖，config/session 与 data/capture 是运行数据，
; 都不能删除。data/scrcpy 是旧版本随包目录，JAR 已迁入 data/adb，可安全清理。
Type: filesandordirs; Name: "{app}\config\system"
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\agent"
Type: filesandordirs; Name: "{app}\data\scrcpy"

[Files]
; PyInstaller onedir 产物 + 运行时依赖（config/system, data/adb）
; PyInstaller 创建 dist/lvjiang/ 结构，lvjiang.exe 位于该目录根部
Source: "..\dist\lvjiang\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
