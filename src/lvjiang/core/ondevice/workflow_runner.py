"""设备端工作流引擎装配 — 把各硬件后端 + 布局 + DSL 引擎串起来

设备端没有 UI 层，需要自己把各组件装配起来供 DSL 引擎使用：
- A11yCapture（截图）
- A11yInput（输入）
- OCREngine（OCR，需要先 patch RapidOCR）
- Layout（从 JSON 文件加载）
- WorkflowEngine（DSL 执行）

典型用法：
    from .workflow_runner import run_workflow
    result = run_workflow("scan_equipped.wf")
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from loguru import logger

from ...core.capture_base import CaptureBackend
from ...core.config import load_user_config
from ...core.config.resolver import get_resolver
from ...core.config.session import get_session_store
from ...core.input_base import InputBackend
from ...core.layout_models import Layout
from ...core.ocr import OCREngine
from ...i18n import tr
from ...workflows.engine import WorkflowEngine
from .capture import A11yCapture
from .input import A11yInput


def _create_capture() -> CaptureBackend:
    """创建设备端截图后端"""
    return A11yCapture()


def _create_input(input_sim=None) -> InputBackend:
    """创建设备端输入后端"""
    return A11yInput(input_sim=input_sim)


def _create_ocr() -> OCREngine:
    """创建设备端 OCR 引擎（已 patch RapidOCR）"""
    from .onnx_session import install
    install()
    return OCREngine()


def _default_layout_name() -> str:
    """默认布局名：session 的 active_layout → layouts.yaml 名册第一个

    Raises:
        RuntimeError: 无任何可用布局时
    """
    name = get_session_store().get_node("active_layout", "")
    if name:
        return name
    merged = get_resolver().load_merged("layouts.yaml")
    names = sorted(merged.get("layouts", {}).keys())
    if names:
        return names[0]
    raise RuntimeError(tr("没有可用布局：session 未指定 active_layout 且 layouts.yaml 名册为空"))


def _load_layout(name: str) -> Layout:
    """加载布局（目录化结构：layouts.yaml + layouts/{name}/{scene}.json）

    Args:
        name: 布局名称

    Returns:
        Layout 实例
    """
    from ..layout_manager import load_layout_by_name
    layout = load_layout_by_name(name)
    if layout is None:
        logger.warning(f"布局不存在: {name}，使用空布局")
        return Layout(name=name)
    return layout


def create_engine(
    layout_name: str | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> WorkflowEngine:
    """创建设备端工作流引擎

    Args:
        layout_name: 布局名称，None 则用 session 活动布局/名册首个布局
        stop_check: 停止判据，引擎每条语句前轮询一次；None 表示不可停止

    Returns:
        装配好的 WorkflowEngine 实例
    """
    # 先加插件：.wf 里的游戏专属内置函数（to_equipment 等）靠插件导入时注册，
    # 未加载则 DSL 调用直接报未知函数（见 plugins 模块说明）。
    from .plugins import ensure_loaded

    ensure_loaded()

    capture = _create_capture()
    ocr = _create_ocr()
    layout = _load_layout(layout_name or _default_layout_name())

    # 输入模拟/延迟参数从 app.yaml（system ← local）加载，设备端与 PC 端同源
    user_config = load_user_config()
    input_ctrl = _create_input(user_config.input_sim)

    engine = WorkflowEngine(
        capture=capture,
        ocr=ocr,
        input_ctrl=input_ctrl,
        layout=layout,
        input_sim=user_config.input_sim,
        delay_params=user_config.delay_params,
        window_left=0,
        window_top=0,
        stop_check=stop_check,
    )
    return engine


def run_workflow(
    wf_name: str,
    layout_name: str | None = None,
    initial_variables: dict | None = None,
) -> dict:
    """运行指定工作流

    Args:
        wf_name: 工作流文件名（如 "scan_equipped.wf"）或完整路径
        layout_name: 布局名称，None 则用 session 活动布局/名册首个布局
        initial_variables: 初始变量

    Returns:
        collect 累积的结果字典
    """
    # 解析工作流路径（跨层：local 影子优先 → system）
    wf_path: Path | None
    if Path(wf_name).is_absolute():
        wf_path = Path(wf_name)
        if not wf_path.exists():
            raise FileNotFoundError(f"工作流文件不存在: {wf_path}")
    else:
        wf_path = get_resolver().resolve_read(f"workflows/{wf_name}")
        if wf_path is None:
            raise FileNotFoundError(f"工作流文件不存在: {wf_name}")

    engine = create_engine(layout_name)
    logger.info(f"开始执行工作流: {wf_path.name}")
    result = engine.execute(wf_path, initial_variables=initial_variables)
    logger.info(f"工作流执行完成，收集到 {len(result)} 项数据")
    return result
