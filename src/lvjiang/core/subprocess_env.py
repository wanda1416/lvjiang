"""外部子进程的环境清洗——只依赖标准库。

冻结态（PyInstaller）会把包内目录写进 ``LD_LIBRARY_PATH``，外部子进程继承后
会去加载包里的旧动态库。这里的两个函数负责把注入项摘掉，调用方用
:func:`subprocess_clean_env_kwargs` 的返回值直接展开进 ``subprocess.run``。

单独成模块而不是留在 :mod:`lvjiang.core.platforms`：打包主机（``packaging/``
里生成官方预置包的进程）必须能只靠标准库构建预置，不能经由 ``platforms`` 把
``loguru``、``i18n`` 这些运行时依赖拖进来。``platforms`` 仍按原样重新导出这两个
名字，既有 ``platforms.clean_subprocess_env`` 调用点不受影响。
"""
from __future__ import annotations

import os
import posixpath
import sys
from collections.abc import Mapping
from typing import Any

IS_WINDOWS = sys.platform == "win32"


def _strip_meipass_from_ld_library_path(raw: str, meipass: str) -> str:
    """从 POSIX 冒号分隔的 LD_LIBRARY_PATH 中移除 PyInstaller 内部目录。"""
    if not raw or not meipass:
        return raw
    norm_target = posixpath.normpath(meipass)
    real_target = os.path.realpath(meipass) if (not IS_WINDOWS and os.path.exists(meipass)) else None
    kept: list[str] = []
    for entry in raw.split(":"):
        if not entry:
            continue
        if posixpath.normpath(entry) == norm_target:
            continue
        if real_target is not None and os.path.exists(entry) and os.path.realpath(entry) == real_target:
            continue
        kept.append(entry)
    return ":".join(kept)


def clean_subprocess_env(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """清理 PyInstaller 注入的环境变量，防止外部子进程加载包内旧动态库。"""
    env = dict(os.environ if base_env is None else base_env)
    env.pop("_MEIPASS2", None)
    orig_ld = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig_ld:
        env["LD_LIBRARY_PATH"] = orig_ld
    elif orig_ld is not None:
        env.pop("LD_LIBRARY_PATH", None)
    elif getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            cleaned_ld = _strip_meipass_from_ld_library_path(
                env.get("LD_LIBRARY_PATH", ""), str(meipass),
            )
            if cleaned_ld:
                env["LD_LIBRARY_PATH"] = cleaned_ld
            else:
                env.pop("LD_LIBRARY_PATH", None)
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


def subprocess_clean_env_kwargs() -> dict[str, Any]:
    """非 Windows 冻结态或存在 PyInstaller 环境变量时，返回清洗后的 env 字典。"""
    if IS_WINDOWS:
        return {}
    if not (
        getattr(sys, "frozen", False)
        or "LD_LIBRARY_PATH_ORIG" in os.environ
        or "_MEIPASS2" in os.environ
    ):
        return {}
    return {"env": clean_subprocess_env()}
