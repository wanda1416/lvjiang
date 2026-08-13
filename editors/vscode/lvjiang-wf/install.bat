@echo off
rem Install the LvJiang WF VS Code extension in development mode.
rem Supports VS Code, Qoder, and other VS Code-based editors.
rem
rem Usage: install.bat [editor]
rem   editor: vscode (default) | qoder | cursor
cd /d "%~dp0"

rem Parse command line argument
set "EDITOR=%~1"
if "%EDITOR%"=="" set "EDITOR=vscode"

rem Determine extensions directory based on editor
if /i "%EDITOR%"=="vscode" (
    set "EXTENSIONS_DIR=%USERPROFILE%\.vscode\extensions"
) else if /i "%EDITOR%"=="qoder" (
    set "EXTENSIONS_DIR=%USERPROFILE%\.qoder\extensions"
) else if /i "%EDITOR%"=="cursor" (
    set "EXTENSIONS_DIR=%USERPROFILE%\.cursor\extensions"
) else (
    echo Unknown editor: %EDITOR%
    echo Supported editors: vscode, qoder, cursor
    exit /b 1
)

set "LINK_PATH=%EXTENSIONS_DIR%\lvjiang-wf"

echo Installing extension for %EDITOR%...
echo Extensions directory: %EXTENSIONS_DIR%

rem Check if npm dependencies are installed
if not exist "node_modules" (
    echo Installing npm dependencies...
    call npm install
    if errorlevel 1 (
        echo npm install failed. Make sure Node.js is installed.
        exit /b 1
    )
)

rem Compile TypeScript
echo Compiling TypeScript...
call npm run compile
if errorlevel 1 (
    echo TypeScript compilation failed.
    exit /b 1
)

rem Create symlink to extensions directory
if exist "%LINK_PATH%" (
    echo Extension link already exists at %LINK_PATH%
    echo Remove it first if you want to reinstall.
) else (
    if not exist "%EXTENSIONS_DIR%" mkdir "%EXTENSIONS_DIR%"
    mklink /J "%LINK_PATH%" "%cd%"
    if errorlevel 1 (
        echo Failed to create junction. Check that the path is correct.
        exit /b 1
    )
)

echo.
echo Extension installed successfully for %EDITOR%.
echo Reload the editor or press Ctrl+Shift+P ^> "Developer: Reload Window" to activate.
