"""内置函数 - 系统交互（UI、Session、Panel）"""

from loguru import logger

from ...core.input_base import InputBackendKind
from ...core.platforms import native_confirm, native_notify, native_pause
from ...i18n import tr
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


def pause_user(_engine=None, message: str = "", *args) -> str:
    """暂停执行，直到用户点击确定。"""
    text = _build_text(message or tr("工作流已暂停，点击确定继续"), args)
    if _engine is not None and getattr(_engine, '_ui_callback', None) is not None:
        _engine._ui_callback("pause", message=text)
        return ""
    native_pause(text)
    return ""


@builtin_func("pause")
def _pause(_engine=None, message: str = "", *args) -> str:
    """暂停 DSL 执行，直到用户点击确定。

    通过 engine._ui_callback 调度到 Qt 主线程显示。

    .wf 用法:
        eval pause("请手动处理异常，完成后点击确定")
        eval pause()    # 无消息暂停
    """
    return pause_user(_engine, message, *args)


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


# ─── 工作环境查询 ─────────────────────────────────────────

@builtin_func("env")
def _env(_engine=None, name: str | None = None) -> str | bool:
    """查询当前工作环境

    - env()       → 返回环境名称（"desktop" / "android"）
    - env("xxx")  → 返回 bool，判断当前环境是否匹配

    环境配置持久化在 session.json 的 settings.env 节点，
    通过 UI 下拉框切换。用于工作流中区分 PC 游戏与手游导航策略：

    .wf 用法:
        # 获取环境名称
        eval $current_env = env()

        # 条件判断
        if env("desktop")
            press "esc"
        end

        if env("android")
            click [main_page].[menu_button]
        end
    """
    current = str(getattr(_engine, "run_env", ""))
    if name is None:
        return current
    return current == str(name)


@builtin_func("check_env")
def _check_env(_engine=None, envs=None) -> bool:
    """检查当前工作环境是否在允许列表中内，否则报错中断

    参数为环境名称列表，当前环境不在列表中时抛 WorkflowUserError。
    用于工作流开头快速校验环境，避免后续执行到不兼容的步骤才失败。

    返回值：
    -  True：当前环境在列表中，可继续执行

    .wf 用法:
        eval check_env(["android"])
        # 当前环境不是 android 时直接报错，后续语句不会执行

        eval check_env(["android", "desktop"])
        # 只允许这两种环境；取到空串等其他值一律中止
    """
    from ..engine.signals import WorkflowUserError

    current = str(getattr(_engine, "run_env", ""))
    if not isinstance(envs, list):
        envs = [envs]
    env_strs = [str(e) for e in envs]
    if current not in env_strs:
        raise WorkflowUserError(
            f"check_env: 当前环境 {current!r} 不在允许列表 {env_strs} 中，工作流中止")
    return True



def _backend_kind(engine) -> InputBackendKind:
    """取当前输入后端的 kind，容错成 UNKNOWN。

    InputBackendKind 继承 str，所以测试替身或旧扩展把 kind 写成普通字符串
    （"send"）时，``kind is InputBackendKind.SEND`` 会是 False、``kind.is_device``
    更会直接 AttributeError 打断整条工作流。这里统一收敛成枚举。
    """
    raw = getattr(getattr(engine, "_input", None), "kind", None)
    if isinstance(raw, InputBackendKind):
        return raw
    try:
        return InputBackendKind(raw)
    except ValueError:
        return InputBackendKind.UNKNOWN


@builtin_func("is_send")
def _is_send(_engine=None) -> bool:
    """判断当前是否为 SendInput 模式

    .wf 用法:
        if is_send()
            log info "当前为 SendInput 模式"
        end
    """
    if _engine is None:
        return False
    return _backend_kind(_engine) is InputBackendKind.SEND


@builtin_func("is_post")
def _is_post(_engine=None) -> bool:
    """判断当前是否为 PostMessage 模式

    .wf 用法:
        if is_post()
            log info "当前为 PostMessage 模式"
        end
    """
    if _engine is None:
        return False
    return _backend_kind(_engine) is InputBackendKind.POST


@builtin_func("is_device")
def _is_device(_engine=None) -> bool:
    """判断当前是否由设备端后端执行（ADB / Agent / 无障碍 / Shell）

    与另外两组判断的区别：

    - ``env()`` 问的是**配置的工作环境**（desktop / android），由 UI 下拉框
      决定，回答「这个流程该按哪套导航策略走」。
    - ``is_send()`` / ``is_post()`` 问的是**窗口模式下用哪种注入方式**，
      两者在设备端都为假。
    - ``is_device()`` 问的是**指令实际打给谁**：设备端后端（手机 ADB、
      Agent、机上无障碍/Shell）为真，PC 窗口注入为假。

    典型用途是区分「只有窗口模式才成立」的前提，例如按键、光标位置、
    前台焦点这类概念在设备端并不存在。

    .wf 用法:
        if is_device()
            log info "设备端执行，跳过键盘快捷键分支"
        end
    """
    if _engine is None:
        return False
    return _backend_kind(_engine).is_device


# ─── Android 应用生命周期 ─────────────────────────────────

def _android_apps(_engine):
    """懒创建控制器；DSL 与 Python 工作流共享公共 Android 实现。"""
    from ...core.device_app import AndroidAppController
    from ..engine.signals import WorkflowUserError

    if _engine is None or getattr(_engine, "_android_device", None) is None:
        raise WorkflowUserError("安卓应用控制仅支持已连接 ADB 设备的 PC 运行模式")
    controller = getattr(_engine, "_android_app_controller", None)
    if controller is None:
        controller = AndroidAppController(
            _engine._android_device,
            getattr(_engine, "_android_apps", {}),
            capture=getattr(_engine, "_capture", None),
            stop_check=getattr(_engine, "_stop_check", None),
        )
        _engine._android_app_controller = controller
    return controller


def _android_call(_engine, operation):
    from ...core.device_app import AndroidAppError
    from ..engine.signals import WorkflowUserError

    try:
        return operation(_android_apps(_engine))
    except AndroidAppError as exc:
        raise WorkflowUserError(str(exc)) from exc


@builtin_func("android_app_running")
def _android_app_running(_engine, name: str = "") -> bool:
    """返回已注册安卓应用的进程是否存在。"""
    return _android_call(_engine, lambda controller: controller.is_running(name))


@builtin_func("android_app_stop")
def _android_app_stop(_engine, name: str = "", timeout: float = 15) -> bool:
    """强制停止已注册安卓应用，并等待其进程消失。"""
    return _android_call(
        _engine, lambda controller: controller.stop(name, float(timeout)))


@builtin_func("android_app_start")
def _android_app_start(_engine, name: str = "", timeout: float = 30) -> bool:
    """启动已注册安卓应用，并等待其进程出现。"""
    return _android_call(
        _engine, lambda controller: controller.start(name, float(timeout)))


@builtin_func("android_wait_stable_frame")
def _android_wait_stable_frame(
    _engine, name: str = "", timeout: float = 60, stable_duration: float = 1,
) -> bool:
    """等待应用期望方向的截图连续稳定。"""
    return _android_call(
        _engine,
        lambda controller: controller.wait_stable_frame(
            name, float(timeout), float(stable_duration)),
    )
