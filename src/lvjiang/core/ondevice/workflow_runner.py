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

from loguru import logger

from ...config import DelayConfig
from ...constants import PROJECT_ROOT
from ...core.capture_base import CaptureBackend
from ...core.input_base import InputBackend
from ...core.ocr import OCREngine
from ...core.scene_registry import Layout
from ...workflows.engine import WorkflowEngine

from .capture import A11yCapture
from .input import A11yInput
from .rapidocr_adapter import patch_all as patch_rapidocr_all


def _create_capture() -> CaptureBackend:
    """创建设备端截图后端"""
    return A11yCapture()


def _create_input() -> InputBackend:
    """创建设备端输入后端"""
    return A11yInput()


def _create_ocr() -> OCREngine:
    """创建设备端 OCR 引擎（已 patch RapidOCR）"""
    from .onnx_bridge import create_session_factory
    patch_rapidocr_all(create_session_factory)
    return OCREngine()


def _load_layout(name: str = "手机直控") -> Layout:
    """从 JSON 文件加载布局

    Args:
        name: 布局文件名（不含 .json 后缀）

    Returns:
        Layout 实例
    """
    layout_path = PROJECT_ROOT / "config" / "local" / "layouts" / f"{name}.json"
    if not layout_path.exists():
        logger.warning(f"布局文件不存在: {layout_path}，使用空布局")
        return Layout(name=name)

    data = json.loads(layout_path.read_text(encoding="utf-8"))
    return Layout.from_dict(name, data)


def create_engine(
    layout_name: str = "手机直控",
    delay_config: DelayConfig | None = None,
) -> WorkflowEngine:
    """创建设备端工作流引擎

    Args:
        layout_name: 布局文件名（不含 .json）
        delay_config: 延迟配置，None 则用默认值

    Returns:
        装配好的 WorkflowEngine 实例
    """
    capture = _create_capture()
    input_ctrl = _create_input()
    ocr = _create_ocr()
    layout = _load_layout(layout_name)

    engine = WorkflowEngine(
        capture=capture,
        ocr=ocr,
        input_ctrl=input_ctrl,
        layout=layout,
        delay_config=delay_config or DelayConfig(),
        window_left=0,
        window_top=0,
    )
    return engine


def run_workflow(
    wf_name: str,
    layout_name: str = "手机直控",
    initial_variables: dict | None = None,
) -> dict:
    """运行指定工作流

    Args:
        wf_name: 工作流文件名（如 "equip_analysis.wf"）或完整路径
        layout_name: 布局文件名（不含 .json）
        initial_variables: 初始变量

    Returns:
        collect 累积的结果字典
    """
    # 解析工作流路径
    if Path(wf_name).is_absolute():
        wf_path = Path(wf_name)
    else:
        wf_path = PROJECT_ROOT / "config" / "system" / "workflows" / wf_name

    if not wf_path.exists():
        raise FileNotFoundError(f"工作流文件不存在: {wf_path}")

    engine = create_engine(layout_name)
    logger.info(f"开始执行工作流: {wf_path.name}")
    result = engine.execute(wf_path, initial_variables=initial_variables)
    logger.info(f"工作流执行完成，收集到 {len(result)} 项数据")
    return result
