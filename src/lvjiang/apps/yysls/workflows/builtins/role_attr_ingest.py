"""内置函数 - 角色基础属性 OCR 解析与"创建基础属性"面板预填触发"""

from loguru import logger

from lvjiang.workflows.builtins._registry import builtin_func


@builtin_func("to_role_base_attrs")
def _to_role_base_attrs(raw: dict) -> dict:
    """解析角色详情页滚动识别的 OCR 原始数据为基础属性字典

    raw 由 scan_role_base_attr.wf 暂存：{"left_1": ..., "left_2": ...,
    "right_attack": ..., "right_outer_pen": ..., "right_attr_pen": ...}。
    返回字段名对齐 combat_attrs.COMBAT_ATTR_FIELDS 的 flat dict，
    可直接交给 open_base_attr_form 预填"创建基础属性"面板。

    .wf 用法:
        eval $parsed = to_role_base_attrs($data)
        eval open_base_attr_form($parsed)
    """
    if not isinstance(raw, dict) or not raw:
        logger.warning("to_role_base_attrs: 输入为空或非字典")
        return {}

    from ...core.role_attr_parser import get_role_attr_parser

    try:
        return get_role_attr_parser().parse(raw)
    except Exception as e:
        logger.warning(f"to_role_base_attrs: 解析失败: {e}")
        return {}


@builtin_func("open_base_attr_form")
def _open_base_attr_form(_engine, prefill: dict) -> None:
    """触发 UI 弹出"创建基础属性"面板并预填数值，不等待用户确认

    通过 MainWindow.open_play_style_form 信号广播给已打开的"培养→基础
    属性"页签（若页签未打开则静默无效果，与 equipment_changed 通知
    行为一致）。工作流线程发出请求后立即返回，不阻塞等待对话框关闭。

    .wf 用法:
        eval open_base_attr_form($parsed)
    """
    callback = getattr(_engine, "_ui_callback", None)
    if callback is None:
        logger.debug("open_base_attr_form: 无 UI 回调（测试/独立执行端），跳过")
        return
    try:
        callback("open_play_style_form", prefill=prefill or {})
    except Exception as e:
        logger.warning(f"open_base_attr_form: 触发失败: {e}")
