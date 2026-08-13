@echo off
cd /d "%~dp0"
rem 一键打包：PyInstaller onedir → dist\lvjiang\（绿色免安装目录）+ zip
rem 产物布局与资源收集说明见 lvjiang.spec / packaging\launcher.py

if exist dist\lvjiang rmdir /s /q dist\lvjiang

rem pyinstaller 不进项目依赖，构建时由 uv 临时注入
uv run --with pyinstaller pyinstaller lvjiang.spec --noconfirm
if errorlevel 1 (
    echo [package] PyInstaller 构建失败
    exit /b 1
)

rem 出厂配置与 scrcpy 放 exe 旁（用户可见，由 config/local 覆盖机制管理）
xcopy /e /i /y config\system dist\lvjiang\config\system >nul
if errorlevel 1 exit /b 1
xcopy /e /i /y data\scrcpy dist\lvjiang\data\scrcpy >nul
if errorlevel 1 exit /b 1

powershell -NoProfile -Command "Compress-Archive -Path dist/lvjiang -DestinationPath dist/lvjiang-win64.zip -Force"
if errorlevel 1 exit /b 1

echo.
echo [package] 完成: dist\lvjiang\lvjiang.exe
echo [package] 分发: dist\lvjiang-win64.zip（解压到用户可写目录，勿放 Program Files）
