@echo off
cd /d "%~dp0"
rem With src-layout the package lives under src/lvjiang, so the repo root
rem is not importable. Add the source root to PYTHONPATH explicitly — this
rem allows running bare python without pip install -e .
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
rem Force dev mode: config writes go to config/system (no .git probing)
set "LVJIANG_DEV_MODE=1"

rem Prefer the project's virtual environment interpreter so pinned dependency
rem versions (e.g. rapidocr-onnxruntime==1.4.4) are always used. Running the
rem venv's python.exe resolves its own site-packages via pyvenv.cfg, so it does
rem NOT depend on PATH or activation — this keeps the correct versions even when
rem launched from a fresh/elevated shell where the venv is not activated.
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

rem No plugin is loaded unless asked for: -reg/--register APP (repeatable)
rem passes one through, --all means the whole roster this repo ships —
rem and the host scans the built-in apps/ root plus the default plugins/ root;
rem a plugin anywhere else must be named as -reg NAME@PATH. --admin starts the
rem private admin console checked out at admin/, --stats starts the local
rem telemetry dashboard in ops/stats-client; both are exclusive with plugins
rem and with each other, and trailing args are forwarded to them.
set "EXTRA_APPS="
set "FORWARD_ARGS="
set "USE_ADMIN="
set "USE_STATS="
set "HAS_REG="
:parse_args
if "%~1"=="" goto launch
set "ARG=%~1"
if /I "%ARG%"=="--admin" goto set_admin
if /I "%ARG%"=="--stats" goto set_stats
if /I "%ARG%"=="--all" goto add_all
if /I "%ARG%"=="--all-plugins" goto mark_reg
if /I "%ARG:~0,4%"=="-reg" goto mark_reg
if /I "%ARG:~0,10%"=="--register" goto mark_reg
set "FORWARD_ARGS=%FORWARD_ARGS% %1"
shift
goto parse_args
:add_all
set "EXTRA_APPS=--all-plugins"
set "HAS_REG=1"
shift
goto parse_args
:launch
if defined USE_STATS goto check_stats
if defined USE_ADMIN if defined HAS_REG (
    echo [ERROR] --admin cannot be combined with -reg/--register/--all 1>&2
    exit /b 1
)
if defined USE_ADMIN goto launch_admin
"%PY%" -m lvjiang %EXTRA_APPS% %FORWARD_ARGS%
exit /b %ERRORLEVEL%
:check_stats
if defined USE_ADMIN (
    echo [ERROR] --stats cannot be combined with --admin 1>&2
    exit /b 1
)
if defined HAS_REG (
    echo [ERROR] --stats cannot be combined with -reg/--register/--all 1>&2
    exit /b 1
)
goto launch_stats

:set_admin
set "USE_ADMIN=1"
shift
goto parse_args
:set_stats
set "USE_STATS=1"
shift
goto parse_args
:mark_reg
set "HAS_REG=1"
set "FORWARD_ARGS=%FORWARD_ARGS% %1"
shift
goto parse_args
:launch_admin
if not exist "%~dp0admin\src\lvjiang\admin\__init__.py" (
    echo [ERROR] admin module not installed, --admin requires a private admin checkout: 1>&2
    echo [ERROR]   expected entry point admin\src\lvjiang\admin\__init__.py 1>&2
    exit /b 1
)
set "PYTHONPATH=%~dp0admin\src;%PYTHONPATH%"
"%PY%" -m lvjiang.admin %FORWARD_ARGS%
exit /b %ERRORLEVEL%
:launch_stats
if not exist "%~dp0ops\stats-client\pyproject.toml" (
    echo [ERROR] stats-client not found at ops\stats-client 1>&2
    exit /b 1
)
uv run --directory "%~dp0ops\stats-client" stats-client %FORWARD_ARGS%
exit /b %ERRORLEVEL%
