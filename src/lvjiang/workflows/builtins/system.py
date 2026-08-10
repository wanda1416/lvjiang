"""内置函数 - 系统交互（UI、Session、Panel）"""

from loguru import logger

from ...core.platforms import native_confirm, native_notify, native_pause
from ._registry import builtin_func


def _build_text(message, args) -> str:
    """拼接消息文本，null 参数视为空字符串"""
    text = "" if message is None else str(message)
    if args:
        text += " ".join("" if a is None else str(a) for a in args)
    return text


# ─── 用户交互 ───────────────────────────────────────────

@builtin_func("confirm")
def _confirm(_engine=None, message: str = "", *args) -> bool:
    """弹出确认对话框（是/否），返回 bool

    通过 engine._ui_callback 调度到 Qt 主线程显示。
    无回调时回退到平台原生弹窗（Win32 MessageBoxW / macOS osascript）。

    .wf 用法:
        eval $ok = confirm("确认执行？")
        if $ok
            log "用户确认"
        end
    """
    text = _build_text(message, args)
    if _engine is not None and getattr(_engine, '_ui_callback', None) is not None:
        return bool(_engine._ui_callback("confirm", message=text))
    # 回退：平台原生弹窗（无 Qt 环境）
    return native_confirm(text)


@builtin_func("pause")
def _pause(_engine=None, message: str = "", *args) -> str:
    """暂停执行，直到用户点击确定

    通过 engine._ui_callback 调度到 Qt 主线程显示。

    .wf 用法:
        eval pause("请手动处理异常，完成后点击确定")
        eval pause()    # 无消息暂停
    """
    text = _build_text(message or "工作流已暂停，点击确定继续", args)
    if _engine is not None and getattr(_engine, '_ui_callback', None) is not None:
        _engine._ui_callback("pause", message=text)
        return ""
    # 回退：平台原生弹窗
    native_pause(text)
    return ""


@builtin_func("notify")
def _notify(_engine=None, message: str = "", *args) -> str:
    """非阻塞通知（5 秒后自动关闭）+ 写入告警面板

    双重通知：
    1. Windows 在后台守护线程调用 Win32 MessageBoxTimeoutW，超时自动关闭；
       macOS 走系统通知中心（display notification，天然非阻塞）。
    2. 同时写入 session.json 的 alert_info，在 UI 告警面板展示。

    工作流线程立即返回，不被阻塞。

    .wf 用法:
        eval notify("步骤完成")
    """
    text = _build_text(message, args)
    # 1. 弹窗通知（5 秒自动关闭）
    native_notify(text)
    # 2. 写入告警面板（持久化到 session.json）
    if _engine is not None and getattr(_engine, '_ui_callback', None) is not None:
        _engine._ui_callback("notify", message=text)
    return ""


@builtin_func("input")
def _input(_engine=None, prompt: str = "", *args):
    """弹出输入对话框，返回用户输入的字符串

    通过 engine._ui_callback 调度到 Qt 主线程显示。
    无回调时返回 null。
    取消/关闭返回 null。

    .wf 用法:
        eval $name = input("请输入名称:")
        if $name is_empty
            log "用户取消输入"
        end
    """
    text = _build_text(prompt, args)
    if _engine is not None and getattr(_engine, '_ui_callback', None) is not None:
        return _engine._ui_callback("input", prompt=text)
    logger.warning("input(): 无 UI 回调，返回 null")
    return None


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
