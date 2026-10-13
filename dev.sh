#!/usr/bin/env bash
# macOS / Linux development launch script (counterpart of Windows dev.bat)
set -euo pipefail
cd "$(dirname "$0")"

# ─── Colored output ───
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ─── Python interpreter discovery ───
find_python() {
    # Prefer .venv if present
    if [[ -x ".venv/bin/python" ]]; then
        echo ".venv/bin/python"
        return 0
    fi
    
    # Look for uv
    local uv_cmd=""
    if command -v uv &>/dev/null; then
        uv_cmd="uv"
    elif [[ -x "$HOME/.local/bin/uv" ]]; then
        uv_cmd="$HOME/.local/bin/uv"
    fi
    
    if [[ -n "$uv_cmd" ]]; then
        # uv found — try to set up venv
        info "Found uv: $uv_cmd"
        setup_with_uv "$uv_cmd"
        return $?
    fi
    
    # Fall back to system Python (version check)
    for py in python3.12 python3.11 python3; do
        if command -v "$py" &>/dev/null; then
            local ver
            ver=$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [[ "$major" -eq 3 && "$minor" -ge 11 && "$minor" -lt 13 ]]; then
                warn "No .venv found, using system Python $ver"
                warn "Consider installing uv and running: uv venv && uv pip install -e '.[dev]'"
                echo "$py"
                return 0
            fi
        fi
    done
    
    error "Python 3.11 or 3.12 not found. Please install:"
    error "  1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    error "  2. Create venv: uv venv --python 3.12"
    error "  3. Install deps: uv pip install -e '.[dev]'"
    return 1
}

# ─── Environment setup with uv ───
setup_with_uv() {
    local uv_cmd="$1"
    
    # Create venv if missing
    if [[ ! -d ".venv" ]]; then
        info "Creating virtual environment (.venv)..."
        
        # Try Python 3.12 first (macOS 12 compat), fall back to 3.13
        if ! "$uv_cmd" venv --python 3.12 .venv 2>/dev/null; then
            "$uv_cmd" venv --python 3.13 .venv
        fi
    fi
    
    # Check if key deps are installed
    local py=".venv/bin/python"
    if ! "$py" -c "import PyQt6" 2>/dev/null; then
        info "Installing project dependencies..."
        "$uv_cmd" pip install -e ".[dev]" --python .venv
    fi
    
    echo "$py"
    return 0
}

# ─── Main ───
main() {
    local python_cmd
    local extra_apps=()
    local forwarded=()
    local use_admin=0
    local has_reg=0
    # 默认不加载任何插件：-reg/--register APP 逐个显式指定；--all 表示本仓库
    # 随包提供的全部插件（yysls 后 premium 的固定顺序），不按目录内容推断。
    # --admin 启动 admin/ 下的管理控制台，它在包命名空间里是 lvjiang.admin，
    # 与插件参数互斥——管理控制台不加载客户端插件。
    for arg in "$@"; do
        case "$arg" in
            --admin)
                use_admin=1
                ;;
            --all)
                extra_apps=(-reg yysls -reg premium)
                has_reg=1
                ;;
            -reg*|--register*)
                has_reg=1
                forwarded+=("$arg")
                ;;
            *)
                forwarded+=("$arg")
                ;;
        esac
    done
    if (( use_admin )) && (( has_reg )); then
        error "--admin cannot be combined with -reg/--register/--all"
        exit 1
    fi
    if (( ${#extra_apps[@]} )) && [[ ! -f src/lvjiang/apps/premium/__init__.py ]]; then
        error "--all requires the premium repository at src/lvjiang/apps/premium"
        exit 1
    fi
    if (( use_admin )) && [[ ! -f admin/src/lvjiang/admin/__init__.py ]]; then
        error "admin module not installed, --admin requires a private admin checkout:"
        error "  expected entry point admin/src/lvjiang/admin/__init__.py"
        exit 1
    fi

    python_cmd=$(find_python) || exit 1
    
    info "Using Python: $python_cmd"
    info "Version: $("$python_cmd" --version 2>&1)"
    
    # With src-layout the package lives under src/lvjiang, so the repo root
    # is not importable. Add the source root to PYTHONPATH explicitly — this
    # allows running bare python without pip install -e .
    export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
    # 管理控制台以 lvjiang.admin 子包并入主工程包命名空间：把 admin 源根也放进
    # 搜索路径，由 src/lvjiang/__init__.py 的 extend_path 合并。
    if (( use_admin )); then
        export PYTHONPATH="$PWD/admin/src:$PYTHONPATH"
    fi
    
    # 空数组在 bash 3.2 下直接展开会触发 set -u 的 unbound variable，而 macOS
    # 自带的就是 3.2；按长度逐个追加。args 本身恒非空。
    local args=(-m lvjiang)
    if (( use_admin )); then
        args=(-m lvjiang.admin)
    elif (( ${#extra_apps[@]} )); then
        args+=("${extra_apps[@]}")
    fi
    if (( ${#forwarded[@]} )); then args+=("${forwarded[@]}"); fi
    exec "$python_cmd" "${args[@]}"
}

main "$@"
