"""PyInstaller 打包专用入口。

职责（均只在 frozen 环境生效，开发环境请用 dev.bat / python -m lvjiang）：
1. 锚定工作目录到 exe 旁 —— loguru 的 "logs/..." 与 crash_handler 的
   "logs/crashes" 均为相对路径，快捷方式启动时 CWD 可能在别处；
2. windowed（无控制台）模式下 sys.stdout/stderr 为 None，而
   _configure_logging 会 logger.add(sys.stderr)，必须先兜底成 devnull；
3. freeze_support —— loguru enqueue=True 与 scrcpy/OCR 均涉及
   multiprocessing，Windows frozen 环境下子进程会重入本入口，必须拦截；
4. 默认注入 -reg yysls，最终用户双击即用，免命令行参数；
5. 日志系统建立之前的早期异常落盘 logs/crashes/launcher_error.log
   （windowed 模式无处看 traceback，不落盘就是静默闪退）。
"""
import multiprocessing
import os
import sys
from pathlib import Path

multiprocessing.freeze_support()

if getattr(sys, "frozen", False):
    os.chdir(Path(sys.executable).parent)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if not any(a in ("-reg", "--register") for a in sys.argv[1:]):
    sys.argv += ["-reg", "yysls"]

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
