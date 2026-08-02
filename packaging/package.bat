@echo off
cd /d "%~dp0.."
rem One-click package: PyInstaller onedir -> dist/lvjiang/ + zip
rem See packaging/lvjiang.spec and packaging/launcher.py for details
rem Can be called from project root: packaging\package.bat

rem --- Version injection ---
rem Read version from pyproject.toml and write to _version.py
python packaging\inject_version.py
if errorlevel 1 (
    echo [package] Version injection failed
    exit /b 1
)

if exist dist\lvjiang rmdir /s /q dist\lvjiang

rem PyInstaller: inject pyinstaller via uv at build time
uv run --with pyinstaller pyinstaller packaging\lvjiang.spec --noconfirm
if errorlevel 1 (
    echo [package] PyInstaller build failed
    exit /b 1
)

rem Copy factory config and adb/scrcpy next to exe
rem Exclude temp files with _ prefix from config/system
robocopy config\system dist\lvjiang\config\system /E /XD _* /XF _* /NJH /NJS /NDL /NFL /NP /NC /NS >nul
if errorlevel 8 exit /b 1
xcopy /e /i /y data\adb dist\lvjiang\data\adb >nul
if errorlevel 1 exit /b 1
xcopy /e /i /y data\scrcpy dist\lvjiang\data\scrcpy >nul
if errorlevel 1 exit /b 1

powershell -NoProfile -Command "Compress-Archive -Path dist/lvjiang -DestinationPath dist/lvjiang-win64.zip -Force"
if errorlevel 1 exit /b 1

echo.
echo [package] Output: dist\lvjiang\lvjiang.exe
echo [package] Output: dist\lvjiang-win64.zip
