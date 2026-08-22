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
    for py in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$py" &>/dev/null; then
            local ver
            ver=$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [[ "$major" -ge 3 && "$minor" -ge 10 ]]; then
                warn "No .venv found, using system Python $ver"
                warn "Consider installing uv and running: uv venv && uv pip install -e '.[dev]'"
                echo "$py"
                return 0
            fi
        fi
    done
    
    error "Python 3.10+ not found. Please install:"
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
    
    python_cmd=$(find_python) || exit 1
    
    info "Using Python: $python_cmd"
    info "Version: $("$python_cmd" --version 2>&1)"
    
    # With src-layout the package lives under src/lvjiang, so the repo root
    # is not importable. Add the source root to PYTHONPATH explicitly — this
    # allows running bare python without pip install -e .
    export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
    
    exec "$python_cmd" -m lvjiang -reg yysls "$@"
}

main "$@"
