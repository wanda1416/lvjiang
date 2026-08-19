@echo off
cd /d "%~dp0.."
rem One-click package: PyInstaller onedir -> dist/lvjiang/ + zip + installer
rem See packaging/lvjiang.spec and packaging/launcher.py for details
rem Can be called from project root: packaging\package.bat
rem Requires: Inno Setup 6+ (iscc.exe in PATH or default install location)

rem --- Version injection ---
rem Read version from pyproject.toml and write to _version.py
python packaging\inject_version.py
if errorlevel 1 (
    echo [package] Version injection failed
    exit /b 1
)

rem Extract version from pyproject.toml for Inno Setup
for /f "tokens=*" %%i in ('python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"') do set APP_VERSION=%%i
if not defined APP_VERSION (
    echo [package] Failed to read version from pyproject.toml
    exit /b 1
)
echo [package] Version: %APP_VERSION%

if exist dist\lvjiang rmdir /s /q dist\lvjiang

rem PyInstaller: inject pyinstaller via uv at build time
uv run --with pyinstaller pyinstaller packaging\lvjiang.spec --noconfirm
if errorlevel 1 (
    echo [package] PyInstaller build failed
    exit /b 1
)

rem Copy factory config and adb/scrcpy next to exe
rem PyInstaller creates dist/lvjiang/ with lvjiang.exe at the root
rem Exclude temp files with _ prefix from config/system
robocopy config\system dist\lvjiang\config\system /E /XD _* /XF _* /NJH /NJS /NDL /NFL /NP /NC /NS >nul
if errorlevel 8 exit /b 1
xcopy /e /i /y data\adb dist\lvjiang\data\adb >nul
if errorlevel 1 exit /b 1
xcopy /e /i /y data\scrcpy dist\lvjiang\data\scrcpy >nul
if errorlevel 1 exit /b 1
xcopy /e /i /y data\image dist\lvjiang\data\image >nul
if errorlevel 1 exit /b 1

powershell -NoProfile -Command "Compress-Archive -Path dist/lvjiang -DestinationPath dist/lvjiang-v%APP_VERSION%-win64.zip -Force"
if errorlevel 1 exit /b 1

echo.
echo [package] Output: dist\lvjiang\lvjiang.exe
echo [package] Output: dist\lvjiang-v%APP_VERSION%-win64.zip

rem --- Inno Setup installer ---
rem Try PATH first, then default install locations
where iscc >nul 2>&1
if %errorlevel%==0 (
    set ISCC=iscc
) else if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
) else if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
    set ISCC="C:\Program Files\Inno Setup 6\ISCC.exe"
) else if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" (
    set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
) else (
    echo [package] Inno Setup not found, skipping installer build
    echo [package] Install from: https://jrsoftware.org/isdl.php
    exit /b 0
)

%ISCC% /DAppVersion=%APP_VERSION% packaging\installer.iss
if errorlevel 1 (
    echo [package] Inno Setup build failed
    exit /b 1
)

echo [package] Output: dist\lvjiang-v%APP_VERSION%-win64-setup.exe
