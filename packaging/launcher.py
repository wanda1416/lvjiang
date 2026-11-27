"""PyInstaller 打包专用入口。

职责（均只在 frozen 环境生效，开发环境请用 dev.bat / python -m lvjiang）：
1. 锚定工作目录到 exe 旁 —— 本模块在日志系统建立前写 logs/crashes/launcher_error.log
   用的是相对路径，快捷方式启动时 CWD 可能在别处（应用自身的 logs/ 路径由
   constants.PROJECT_ROOT 锚定，frozen 下即 exe 目录）；
2. windowed（无控制台）模式下 sys.stdout/stderr 为 None，而
   _configure_logging 会 logger.add(sys.stderr)，必须先兜底成 devnull；
3. freeze_support —— loguru enqueue=True 与 scrcpy/OCR 均涉及
   multiprocessing，Windows frozen 环境下子进程会重入本入口，必须拦截；
4. 默认注入 --all-plugins，最终用户双击即用：加载内置插件与 plugins/ 默认
   目录（公开包没有 plugins/，即只有内置插件）；
5. 日志系统建立之前的早期异常落盘 logs/crashes/launcher_error.log
   （windowed 模式无处看 traceback，不落盘就是静默闪退）。
"""
import multiprocessing
import os
import shutil
import sys
from pathlib import Path

_BUNDLE_SYNC_DIRS = ("config/system", "config/i18n", "data/adb", "docs", "plugins")
_BUNDLE_SYNC_FILES = ("LICENSE.txt",)


def _resolve_frozen_root(exe_dir: Path) -> Path:
    override = os.environ.get("LVJIANG_ROOT")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return exe_dir
    if sys.platform == "darwin":
        if (exe_dir.name == "MacOS" and exe_dir.parent.name == "Contents") or not os.access(exe_dir, os.W_OK):
            return Path.home() / "Library" / "Application Support" / "lvjiang"
        return exe_dir
    if not os.access(exe_dir, os.W_OK):
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) if xdg else (Path.home() / ".local" / "share")
        return base / "lvjiang"
    return exe_dir


def _sync_bundle_assets(bundle_root: Path, user_root: Path) -> None:
    """将只读 Bundle 中的出厂只读资产增量同步到用户数据目录，绝不触碰 local/session/logs。"""
    marker_src = bundle_root / ".bundle_version"
    marker_dst = user_root / ".bundle_version"
    src_ver = marker_src.read_text(encoding="utf-8").strip() if marker_src.is_file() else ""
    dst_ver = marker_dst.read_text(encoding="utf-8").strip() if marker_dst.is_file() else ""
    if src_ver and src_ver == dst_ver and (user_root / "config/system").is_dir():
        return
    user_root.mkdir(parents=True, exist_ok=True)
    for rel in _BUNDLE_SYNC_DIRS:
        src = bundle_root / rel
        if not src.is_dir():
            continue
        dst = user_root / rel
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
    for rel in _BUNDLE_SYNC_FILES:
        src = bundle_root / rel
        if src.is_file():
            shutil.copy2(src, user_root / rel)
    if src_ver:
        marker_dst.write_text(f"{src_ver}\n", encoding="utf-8")


multiprocessing.freeze_support()

if getattr(sys, "frozen", False):
    _exe_dir = Path(sys.executable).parent
    _work_dir = _resolve_frozen_root(_exe_dir)
    if _work_dir != _exe_dir:
        _sync_bundle_assets(_exe_dir, _work_dir)
    _work_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(_work_dir)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
_CLI_ACTIONS = {"--exec", "--list"}
_IS_CLI = any(a in _CLI_ACTIONS for a in sys.argv[1:])
# 命令行动作自己决定插件范围；在动作后追加 --all-plugins 会被 argparse 拒绝。
if not _IS_CLI and not any(
        a in ("-reg", "--register", "--all-plugins", "--all") for a in sys.argv[1:]):
    sys.argv += ["--all-plugins"]

try:
    from lvjiang.__main__ import main  # noqa: E402 — 须在 chdir/argv 处理后导入
    code = main()
except Exception:
    import traceback
    crash_dir = Path("logs/crashes")
    crash_dir.mkdir(parents=True, exist_ok=True)
    (crash_dir / "launcher_error.log").write_text(
        traceback.format_exc(), encoding="utf-8")
    raise
sys.exit(code)
