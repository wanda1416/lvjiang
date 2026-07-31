"""设备端工作流引擎装配 — 把各硬件后端 + 布局 + DSL 引擎串起来

设备端没有 UI 层，需要自己把各组件装配起来供 DSL 引擎使用：
- A11yCapture（截图）
- A11yInput（输入）
- OCREngine（OCR，需要先 patch RapidOCR）
- Layout（从 JSON 文件加载）
- WorkflowEngine（DSL 执行）

典型用法：
    from .workflow_runner import run_workflow
    result = run_workflow("equip_analysis.wf")
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from loguru import logger

from ...config import DelayConfig
from ...core.capture_base import CaptureBackend
from ...core.config_resolver import get_resolver
from ...core.input_base import InputBackend
from ...core.ocr import OCREngine
from ...core.scene_registry import Layout
from ...workflows.engine import WorkflowEngine
from .capture import A11yCapture
from .input import A11yInput


def _create_capture() -> CaptureBackend:
    """创建设备端截图后端"""
    return A11yCapture()


def _create_input() -> InputBackend:
    """创建设备端输入后端"""
    return A11yInput()


def _create_ocr() -> OCREngine:
    """创建设备端 OCR 引擎（已 patch RapidOCR）"""
    from .onnx_session import install
    install()
    return OCREngine()


def _default_layout_name() -> str:
    """默认布局名：session 的 active_layout → 枚举到的第一个布局

    Raises:
        RuntimeError: 无任何可用布局时
    """
    from ...constants import SESSION_PATH
    if SESSION_PATH.exists():
        try:
            name = json.loads(
                SESSION_PATH.read_text(encoding="utf-8")).get("active_layout", "")
            if name:
                return name
        except Exception as e:  # noqa: BLE001 session 损坏不应阻断回退枚举
            logger.warning(f"读取 session.json 失败: {e}")
    names = get_resolver().enumerate_entities("layouts", "*.json")
    if names:
        return Path(names[0]).stem
    raise RuntimeError("没有可用布局：session 未指定 active_layout 且 layouts/ 下无 *.json")


def _load_layout(name: str) -> Layout:
    """从 JSON 文件加载布局（local 影子优先 → system）

    Args:
        name: 布局文件名（不含 .json 后缀）

    Returns:
        Layout 实例
    """
    layout_path = get_resolver().resolve_read(f"layouts/{name}.json")
    if layout_path is None:
        logger.warning(f"布局文件不存在: {name}，使用空布局")
        return Layout(name=name)

    data = json.loads(layout_path.read_text(encoding="utf-8"))
    return Layout.from_dict(name, data)


def create_engine(
    layout_name: str | None = None,
    delay_config: DelayConfig | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> WorkflowEngine:
    """创建设备端工作流引擎

    Args:
        layout_name: 布局文件名（不含 .json），None 则用 session 活动布局/首个布局
        delay_config: 延迟配置，None 则用默认值
        stop_check: 停止判据，引擎每条语句前轮询一次；None 表示不可停止

    Returns:
        装配好的 WorkflowEngine 实例
    """
    capture = _create_capture()
    input_ctrl = _create_input()
    ocr = _create_ocr()
    layout = _load_layout(layout_name or _default_layout_name())

    engine = WorkflowEngine(
        capture=capture,
        ocr=ocr,
        input_ctrl=input_ctrl,
        layout=layout,
        delay_config=delay_config or DelayConfig(),
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
        wf_name: 工作流文件名（如 "equip_analysis.wf"）或完整路径
        layout_name: 布局文件名（不含 .json），None 则用 session 活动布局/首个布局
        initial_variables: 初始变量

    Returns:
        collect 累积的结果字典
    """
    # 解析工作流路径（跨层：local 影子优先 → system）
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
