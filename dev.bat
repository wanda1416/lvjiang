@echo off
cd /d "%~dp0"
rem src-layout 后包在 src/lvjiang 下，仓库根不再是可导入路径，
rem 所以显式把源根加进 PYTHONPATH —— 这样裸 python 无需 pip install -e . 也能起。
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
rem dev 脚本强制开发模式：配置写向 config/system（不依赖 .git 探测）
set "LVJIANG_DEV_MODE=1"
python -m lvjiang -reg yysls
