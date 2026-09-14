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

"%PY%" -m lvjiang -reg yysls %*
