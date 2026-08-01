@echo off
chcp 65001 >nul
cd /d "%~dp0.."
rem 一键打包：PyInstaller onedir → dist\lvjiang\（绿色免安装目录）+ zip
rem 产物布局与资源收集说明见 packaging\lvjiang.spec / packaging\launcher.py
rem 支持从项目根目录调用：packaging\package.bat

rem ─── 版本注入 ───
rem 从 pyproject.toml 读取版本号，写入 _version.py
python packaging\inject_version.py
if errorlevel 1 (
    echo [package] 版本注入失败
    exit /b 1
)

if exist dist\lvjiang rmdir /s /q dist\lvjiang

rem pyinstaller 不进项目依赖，构建时由 uv 临时注入
uv run --with pyinstaller pyinstaller packaging\lvjiang.spec --noconfirm
if errorlevel 1 (
    echo [package] PyInstaller 构建失败
    exit /b 1
)

rem 出厂配置与 adb/scrcpy 放 exe 旁（用户可见，由 config/local 覆盖机制管理）
rem config/system 排除 _ 前缀的编辑器临时文件/目录（_editor_run.wf / _testwf/ 等）
robocopy config\system dist\lvjiang\config\system /E /XD _* /XF _* /NJH /NJS /NDL /NFL /NP /NC /NS >nul
if errorlevel 8 exit /b 1
xcopy /e /i /y data\adb dist\lvjiang\data\adb >nul
if errorlevel 1 exit /b 1
xcopy /e /i /y data\scrcpy dist\lvjiang\data\scrcpy >nul
if errorlevel 1 exit /b 1

powershell -NoProfile -Command "Compress-Archive -Path dist/lvjiang -DestinationPath dist/lvjiang-win64.zip -Force"
if errorlevel 1 exit /b 1

echo.
echo [package] : dist\lvjiang\lvjiang.exe
echo [package] : dist\lvjiang-win64.zip
