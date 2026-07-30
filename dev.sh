#!/usr/bin/env bash
# macOS / Linux 开发启动脚本（对应 Windows 的 dev.bat）
set -euo pipefail
cd "$(dirname "$0")"
# src-layout 后包在 src/lvjiang 下，仓库根不再是可导入路径，
# 所以显式把源根加进 PYTHONPATH —— 这样裸 python 无需 pip install -e . 也能起。
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m lvjiang -reg yysls
