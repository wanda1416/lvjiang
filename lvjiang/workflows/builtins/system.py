"""内置函数 - 系统交互（UI、Session、Panel）"""

from loguru import logger

from ._registry import builtin_func


# ─── UI ─────────────────────────────────────────────────

@builtin_func("messagebox")
def _messagebox(message: str, *args) -> str:
    """弹出 Windows 消息框，阻塞直到用户点击确定

    使用 Win32 MessageBoxW API，可在工作流子线程中安全调用。

    .wf 用法:
        eval messagebox("请在初始界面开始执行")
        eval messagebox(concat("错误: ", $reason))
    """
    import ctypes
    text = str(message)
    if args:
        text += " ".join(str(a) for a in args)
    ctypes.windll.user32.MessageBoxW(0, text, "工作流提示", 0x40)  # MB_ICONINFORMATION
    return text


# ─── Session 持久化 ─────────────────────────────────────

@builtin_func("save")
def _save(_engine=None, *args) -> str:
    """强制保存 session 到磁盘

    通过 engine._save_callback 触发 SessionManager.save()。

    .wf 用法:
        eval save()
    """
    if _engine is not None and _engine._save_callback is not None:
        _engine._save_callback()
        logger.info("session 已手动保存")
    else:
        logger.warning("save(): 无保存回调，跳过")
    return ""


# ─── Panel 查询 ─────────────────────────────────────────

@builtin_func("panel_rows")
def _panel_rows(_engine, scene_key: str = "", panel_key: str = "") -> int:
    """返回 panel 实际检测到的行数

    .wf 用法:
        eval $rows = panel_rows("bag_equip_detail", "bag_grid")
    """
    cal = _engine._panel_alignments.get((scene_key, panel_key))
    return cal.n_rows if cal else 0


@builtin_func("panel_cols")
def _panel_cols(_engine, scene_key: str = "", panel_key: str = "") -> int:
    """返回 panel 实际检测到的列数

    .wf 用法:
        eval $cols = panel_cols("bag_equip_detail", "bag_grid")
    """
    cal = _engine._panel_alignments.get((scene_key, panel_key))
    return cal.n_cols if cal else 0
