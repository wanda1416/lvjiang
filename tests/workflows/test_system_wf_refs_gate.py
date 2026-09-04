"""系统布局 × 系统 .wf 的静态引用集成门禁

test_workflow_references.py 用最小假布局覆盖 collect_refs / check_refs 的单元行为，
但「布局漏绑某个 key」「改了区域 key 忘同步脚本」这类错只有拿真实布局比对真实
脚本才能发现 —— 过去要等上机执行到那一行才炸，而实机失败时游戏已经被点到别处
去了（曾因此误开用户的闹钟应用）。

固定只读 config/system 层：随 APK 分发到设备端的就是这一层，且不受本机
config/local 影子文件影响，CI 与开发机结论一致。
"""


from lvjiang.core.config import load_user_config
from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR
from lvjiang.core.key_validation import validate_layout_activation_keys
from lvjiang.workflows.engine import WorkflowEngine
from tests.case_matrix import case_matrix


def _system_wf_files() -> list:
    """系统 .wf 清单，跳过 `_` 前缀的编辑器临时脚本与录制产物

    _editor_run.wf / _recorded.wf / _testwf/ 的内容随上一次编辑器操作变化，
    不是分发物，不作为门禁对象。
    """
    workflows_dir = SYSTEM_CONFIG_DIR / "workflows"
    return sorted(
        p for p in workflows_dir.rglob("*.wf")
        if not p.name.startswith("_")
        and not any(part.startswith("_") for part in p.relative_to(
            workflows_dir).parts[:-1])
    )


def _system_layouts() -> list[str]:
    """系统布局名册（目录化结构：layouts/{name}/ 目录）"""
    layouts_dir = SYSTEM_CONFIG_DIR / "layouts"
    if not layouts_dir.is_dir():
        return []
    return sorted(
        p.name for p in layouts_dir.iterdir()
        if p.is_dir() and not p.name.startswith("_")
    )


_VALIDATOR_CACHE: dict[str, WorkflowEngine] = {}


def _validator(layout_name: str) -> WorkflowEngine:
    """装配「只校验」的引擎：后端全为 None，validate_only 不触碰它们

    delay_params 必须取真实配置 —— 命名等待校验比对的就是它，传空会把
    `wait @step_interval` 全判成未定义。
    同一布局的引擎实例缓存复用，避免每个参数化用例重复加载布局+配置。
    """
    if layout_name in _VALIDATOR_CACHE:
        return _VALIDATOR_CACHE[layout_name]
    from lvjiang.core.layout_manager import load_layout_by_name
    layout = load_layout_by_name(layout_name)
    assert layout is not None, f"布局加载失败: {layout_name}"
    user_config = load_user_config()
    engine = WorkflowEngine(
        capture=None, ocr=None, input_ctrl=None,
        layout=layout,
        input_sim=user_config.input_sim,
        delay_params=user_config.delay_params,
        window_left=0, window_top=0,
    )
    _VALIDATOR_CACHE[layout_name] = engine
    return engine


def test_system_layouts_and_workflows_exist():
    """门禁自身的前提：清单非空，否则下面的参数化会静默零用例通过"""
    assert _system_layouts(), "config/system/layouts 下没有布局，门禁形同虚设"
    assert _system_wf_files(), "config/system/workflows 下没有 .wf"


def test_desktop_layout_has_required_activation_bindings():
    """统一 DSL 后，桌面布局必须把等价实体准确绑定到对应按键。"""
    layout = _validator("桌面布局")._layout
    expected = {
        ("general_control", "confirm"): "SPACE",
        ("equip_tune_detail", "reset_tune"): "R",
        ("equip_tune_detail", "reset_confirm"): "SPACE",
        ("general_action", "camera_shot"): "SPACE",
        ("general_action", "back"): "ESC",
        ("training_xinfa", "purchase"): "SPACE",
        ("training_xinfa", "confirm"): "SPACE",
        ("baiye_main", "goto_baiye"): "SPACE",
        ("game_main_page", "menu"): "ESC",
        ("bag_detail", "back"): "ESC",
        ("game_menu_page", "back"): "ESC",
    }
    for (scene_key, entity_key), activation_key in expected.items():
        entity = next(
            item for item in layout.get_scene_regions(scene_key)
            if item.key == entity_key
        )
        assert entity.activation_key == activation_key, (
            f"{scene_key}.{entity_key} 应绑定 {activation_key}，"
            f"实际为 {entity.activation_key!r}"
        )


def test_desktop_layout_has_no_activation_key_conflicts():
    """桌面布局同一场景视图内，一个按键只能表示一个动作。"""
    validate_layout_activation_keys(_validator("桌面布局")._layout)


@case_matrix("layout_name", _system_layouts())
@case_matrix(
    "wf_path", _system_wf_files(),
    ids=lambda p: p.relative_to(SYSTEM_CONFIG_DIR / "workflows").as_posix())
def test_wf_refs_all_bound(wf_path, layout_name):
    """每个系统 .wf 引用的场景/区域/坐标点/方向/面板都已在该布局绑定

    失败信息即 format_problems 的清单（含文件名:行号），直接照着补绑即可。
    """
    _validator(layout_name).validate_only(wf_path)
