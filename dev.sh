#!/usr/bin/env bash
# macOS / Linux 开发启动脚本（对应 Windows 的 dev.bat）
set -euo pipefail
cd "$(dirname "$0")"

# ─── 颜色输出 ───
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ─── Python 解释器查找 ───
find_python() {
    # 优先使用 .venv
    if [[ -x ".venv/bin/python" ]]; then
        echo ".venv/bin/python"
        return 0
    fi
    
    # 查找 uv
    local uv_cmd=""
    if command -v uv &>/dev/null; then
        uv_cmd="uv"
    elif [[ -x "$HOME/.local/bin/uv" ]]; then
        uv_cmd="$HOME/.local/bin/uv"
    fi
    
    if [[ -n "$uv_cmd" ]]; then
        # uv 存在，尝试创建 venv
        info "检测到 uv: $uv_cmd"
        setup_with_uv "$uv_cmd"
        return $?
    fi
    
    # 回退到系统 Python（检查版本）
    for py in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$py" &>/dev/null; then
            local ver
            ver=$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [[ "$major" -ge 3 && "$minor" -ge 10 ]]; then
                warn "未找到 .venv，使用系统 Python $ver"
                warn "建议安装 uv 并运行: uv venv && uv pip install -e '.[dev]'"
                echo "$py"
                return 0
            fi
        fi
    done
    
    error "未找到 Python 3.10+，请先安装："
    error "  1. 安装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    error "  2. 创建环境: uv venv --python 3.12"
    error "  3. 安装依赖: uv pip install -e '.[dev]'"
    return 1
}

# ─── 使用 uv 搭建环境 ───
setup_with_uv() {
    local uv_cmd="$1"
    
    # 检查是否已有 venv
    if [[ ! -d ".venv" ]]; then
        info "创建虚拟环境 (.venv)..."
        
        # 尝试 Python 3.12（macOS 12 兼容），失败则用 3.13
        if ! "$uv_cmd" venv --python 3.12 .venv 2>/dev/null; then
            "$uv_cmd" venv --python 3.13 .venv
        fi
    fi
    
    # 检查关键依赖是否已安装
    local py=".venv/bin/python"
    if ! "$py" -c "import PyQt6" 2>/dev/null; then
        info "安装项目依赖..."
        "$uv_cmd" pip install -e ".[dev]" --python .venv
    fi
    
    echo "$py"
    return 0
}

# ─── 主流程 ───
main() {
    local python_cmd
    
    python_cmd=$(find_python) || exit 1
    
    info "使用 Python: $python_cmd"
    info "版本: $("$python_cmd" --version 2>&1)"
    
    # src-layout 后包在 src/lvjiang 下，仓库根不再是可导入路径，
    # 所以显式把源根加进 PYTHONPATH —— 这样裸 python 无需 pip install -e . 也能起。
    export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
    
    exec "$python_cmd" -m lvjiang -reg yysls "$@"
}

main "$@"
