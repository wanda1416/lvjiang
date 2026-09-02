@echo off
rem Launch the stats-client local dashboard (ops/stats-client).
rem First run: uv sync --extra dev inside ops/stats-client to create the venv.
uv run --directory "%~dp0ops\stats-client" stats-client %*
