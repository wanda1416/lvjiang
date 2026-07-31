"""系统布局 × 系统 .wf 的静态引用集成门禁

test_scene_scan.py 用最小假布局覆盖 collect_refs / check_refs 的单元行为，
但「布局漏绑某个 key」「改了区域 key 忘同步脚本」这类错只有拿真实布局比对真实
脚本才能发现 —— 过去要等上机执行到那一行才炸，而实机失败时游戏已经被点到别处
去了（曾因此误开用户的闹钟应用）。

固定只读 config/system 层：随 APK 分发到设备端的就是这一层，且不受本机
config/local 影子文件影响，CI 与开发机结论一致。
"""

import json

import pytest

from lvjiang.config import load_user_config
from lvjiang.constants import SYSTEM_LAYOUTS_DIR, SYSTEM_WORKFLOWS_DIR
from lvjiang.core.scene_registry import Layout
from lvjiang.workflows.engine import WorkflowEngine


def _system_wf_files() -> list:
    """系统 .wf 清单，跳过 `_` 前缀的编辑器临时脚本与录制产物

    _editor_run.wf / _recorded.wf / _testwf/ 的内容随上一次编辑器操作变化，
    不是分发物，不作为门禁对象。
    """
    return sorted(
        p for p in SYSTEM_WORKFLOWS_DIR.rglob("*.wf")
        if not p.name.startswith("_")
        and not any(part.startswith("_") for part in p.relative_to(
            SYSTEM_WORKFLOWS_DIR).parts[:-1])
    )


def _system_layouts() -> list:
    return sorted(SYSTEM_LAYOUTS_DIR.glob("*.json"))


def _validator(layout_path) -> WorkflowEngine:
    """装配「只校验」的引擎：后端全为 None，validate_only 不触碰它们

    delay_params 必须取真实配置 —— 命名等待校验比对的就是它，传空会把
    `wait step_interval` 全判成未定义。
    """
    data = json.loads(layout_path.read_text(encoding="utf-8"))
    user_config = load_user_config()
    return WorkflowEngine(
        capture=None, ocr=None, input_ctrl=None,
        layout=Layout.from_dict(layout_path.stem, data),
        input_sim=user_config.input_sim,
        delay_params=user_config.delay_params,
        window_left=0, window_top=0,
    )


def test_system_layouts_and_workflows_exist():
    """门禁自身的前提：清单非空，否则下面的参数化会静默零用例通过"""
    assert _system_layouts(), "config/system/layouts 下没有布局，门禁形同虚设"
    assert _system_wf_files(), "config/system/workflows 下没有 .wf"


@pytest.mark.parametrize("layout_path", _system_layouts(), ids=lambda p: p.stem)
@pytest.mark.parametrize(
    "wf_path", _system_wf_files(),
    ids=lambda p: p.relative_to(SYSTEM_WORKFLOWS_DIR).as_posix())
def test_wf_refs_all_bound(wf_path, layout_path):
    """每个系统 .wf 引用的场景/区域/坐标点/方向/面板都已在该布局绑定

    失败信息即 format_problems 的清单（含文件名:行号），直接照着补绑即可。
    """
    _validator(layout_path).validate_only(wf_path)
