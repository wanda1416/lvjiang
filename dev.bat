@echo off
cd /d "%~dp0"
rem With src-layout the package lives under src/lvjiang, so the repo root
rem is not importable. Add the source root to PYTHONPATH explicitly — this
rem allows running bare python without pip install -e .
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
rem Force dev mode: config writes go to config/system (no .git probing)
set "LVJIANG_DEV_MODE=1"
python -m lvjiang -reg yysls
