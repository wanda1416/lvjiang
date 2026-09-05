"""auto_tuning 端到端链路的分支覆盖测试

用 FakeWF 覆写场景交互原语（click_region/ocr_scene/ocr_scene_by/
recognize_references_by/wait_delay）并 spy 空接口，monkeypatch 模块级
判定函数 judge_equipment_potential（结构化结果，预期评级由真实的
summarize_potential/_expect_key 归纳），驱动 _process_equipment 的各分支：
already_full / 未达进入门槛 / no_tune_entry / tuned（含材料不足提前
结束），不依赖真实规则与真实 OCR。行为处置（扫描处理/结束处理）
由注入 run_ctx.base_group 的基础规则组（TuningGroup）驱动，
钩子委派真实实现。
"""

from unittest.mock import MagicMock

import pytest

from lvjiang.apps.yysls.config import LevelConfig
from lvjiang.apps.yysls.core.equip_parser import EquipmentData
from lvjiang.apps.yysls.core.tuning_rules import (
    BehaviorRule,
    FoodRule,
    MaterialSettings,
    ScanBehavior,
    TuneBehavior,
    TuningGroup,
)
from lvjiang.apps.yysls.workflows.implementations import auto_tuning
from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
    AutoTuningWorkflow,
)
from lvjiang.apps.yysls.workflows.implementations.bag_traversal import (
    PositionalTraversal,
    ScrollState,
)
from lvjiang.apps.yysls.workflows.implementations.tuning import TuningRecorder
from lvjiang.apps.yysls.workflows.implementations.tuning import (
    executor as tuning_executor,
)
from lvjiang.apps.yysls.workflows.implementations.tuning import (
    judge as tuning_judge,
)
from lvjiang.apps.yysls.workflows.implementations.tuning.navigator import (
    TuningNavigator,
)
from lvjiang.apps.yysls.workflows.implementations.tuning.route_strategy import (
    AndroidTuningRouteStrategy,
    DesktopTuningRouteStrategy,
)
from lvjiang.apps.yysls.workflows.implementations.tuning.stone_stock import (
    CachedStoneStock,
)
from lvjiang.apps.yysls.workflows.tuning_context import TuningRunContext
from lvjiang.core.config import load_user_config
from lvjiang.core.layout_manager import load_layout_by_name
from lvjiang.workflows.engine import WorkflowEngine

WEAPON_DETAIL = AutoTuningWorkflow.WEAPON_DETAIL
ARMOR_DETAIL = AutoTuningWorkflow.ARMOR_DETAIL
EQUIP_DETAIL = AutoTuningWorkflow.EQUIP_DETAIL
CONTROL_SCENE = AutoTuningWorkflow.CONTROL_SCENE
# 调律页与调律结果弹窗已合并为同一场景（结果在 result 视图），
# 故 _ocr_map 里两者字段共用一个场景条目
TUNE_SCENE = AutoTuningWorkflow.TUNE_SCENE

# judge_equipment_potential 的结构化结果样本：命中顶级 / 判为垃圾
_WORTHY = {"s": {"name": "血河", "rating": "顶级", "skipped": False,
                 "not_applicable": False, "reasons": ["词条匹配"]}}
_JUNK = {"s": {"name": "血河", "rating": "垃圾", "skipped": False,
               "not_applicable": False, "reasons": ["词条不符"]}}

# ─── subcall 桥：FakeWF 接线引擎 ─────────────────────────
# 导航改走 DSL subcall 后，FakeWF 需要一个真引擎：真布局 + 真等待
# 参数供 load_subcalls 静态校验，运行时 DSL 动作经
# engine._workflow = wf 委派到 FakeWF 覆写的原语。

_LAYOUT_CACHE = None
_DELAY_CACHE = None


def _system_layout():
    global _LAYOUT_CACHE
    if _LAYOUT_CACHE is None:
        _LAYOUT_CACHE = load_layout_by_name("默认布局")
    assert _LAYOUT_CACHE is not None, "默认布局加载失败"
    return _LAYOUT_CACHE


def _delay_params():
    global _DELAY_CACHE
    if _DELAY_CACHE is None:
        _DELAY_CACHE = load_user_config().delay_params
    return _DELAY_CACHE


def _make_subcall_engine(wf, run_env: str = "android") -> WorkflowEngine:
    """装配最小引擎：后端全 mock，DSL 动作委派到 wf 的覆写原语"""
    capture = MagicMock()
    capture.get_capture_size.return_value = (1920, 1080)
    engine = WorkflowEngine(
        capture=capture, ocr=MagicMock(), input_ctrl=MagicMock(),
        layout=_system_layout(), input_sim=MagicMock(),
        delay_params=_delay_params(),
        run_env=run_env,
    )
    engine._workflow = wf
    return engine


class FakeWF(AutoTuningWorkflow):
    """不走 BaseWorkflow.__init__ 的测试替身，记录调用并脚本化识别响应"""

    def __init__(self, run_env: str = "android"):
        self.output = {}
        # 默认注入空基础规则组（行为/材料/等级门槛全默认值），
        # 测试可覆写 run_ctx.base_group 注入定制配置
        self.run_ctx = TuningRunContext(judge_configs={}, judge_rule_keys=[],
                                        base_group=TuningGroup(),
                                        use_stone_cache=False)
        self._stopped = False
        # subcall 桥：真布局供 click_any 解析区域，引擎执行 DSL 导航
        self._layout = _system_layout()
        self._engine = _make_subcall_engine(self, run_env)
        # 显式加载导航 subcall（生产路径在 run() 中加载，测试路径在此加载）
        self.navigator.load_dependencies()
        self.clicks: list[tuple[str, str]] = []
        self.ocr_calls: list[tuple[str, list[str] | None]] = []
        self.scan_reject_calls: list = []
        self.full_calls: list = []
        self._ocr_map: dict[str, dict] = {}
        self._material_result: dict[str, str] = {}
        self._material_infos: dict[str, object] = {}
        self.material_info_calls = 0
        self._nav_tune_ok = True

    @property
    def is_stopped(self) -> bool:
        # 与真实属性对齐：材料耗尽也视为停止（阻断不触发回收）
        return (
            self._stopped
            or self.executor.materials_exhausted
            or self.run_state.end_requested
        )

    def wait_delay(self, name: str):
        pass

    def wait_stable(self, timeout: float | str = 8.0, threshold: float = 0.02,
                    interval: float = 0.3, stable_duration: float = 0.5,
                    least: float = 0.5):
        pass

    def wait_seconds(self, seconds: float):
        pass

    def click_region(self, scene_key, field_key, jitter: bool = True, **kw):
        self.clicks.append((scene_key, field_key))

    def ocr_scene(self, scene_key, field_keys=None, min_confidence=None):
        self.ocr_calls.append((scene_key, field_keys))
        data = dict(self._ocr_map.get(scene_key, {}))
        # 默认值：标准确认弹窗包含「确认」（除非测试显式覆盖）。
        if scene_key == CONTROL_SCENE and "confirm" not in data:
            data["confirm"] = "确认"
        if scene_key == TUNE_SCENE and "reset_confirm" not in data:
            data["reset_confirm"] = "确认"
        # 导航预检：菜单页检查
        if scene_key == "game_menu_page":
            data.setdefault("baoguo", "包裹")
        # 导航预检：主界面检查（is_in_main_page 用）
        if scene_key == "game_main_page":
            data.setdefault("menu", "菜单")
        # 导航预检：包裹页 tab 检查
        if scene_key == "bag_detail":
            data.setdefault("sub_baoguo", "培养")
        if field_keys:
            return {k: v for k, v in data.items() if k in field_keys}
        return data

    def ocr_scene_by(self, scene_key, field_keys, target_value, mode, min_confidence=None):
        # page_action.scan_and_confirm 通过 by contains_any 扫描通用确认区。
        if scene_key == CONTROL_SCENE and "confirm" in field_keys:
            targets = target_value if isinstance(target_value, list) else [target_value]
            configured = self._ocr_map.get(scene_key, {})
            for key in field_keys:
                text = configured.get(key, "确认" if key == "confirm" else "")
                if any(target in text for target in targets):
                    return key
            return ""
        # 导航预检：菜单页检查（is_in_menu_page 用）
        if scene_key == "game_menu_page" and ("baoguo" in field_keys or "peiyang" in field_keys or "wulinlu" in field_keys):
            return "baoguo" if "baoguo" in field_keys else "wulinlu"
        # 导航预检：主页多区域检查
        if scene_key == "game_main_page":
            return "菜单"  # 模拟在主页
        # 导航预检：包裹页 tab 检查
        if scene_key == "bag_detail" and "sub_baoguo" in field_keys:
            return "培养"  # 模拟在培养 tab
        # 导航预检：装备列表检查（recycle + sub_equip）
        if scene_key == "bag_equip_detail" and ("recycle" in field_keys or "sub_equip" in field_keys):
            if "recycle" in field_keys:
                return "回收"
            if "sub_equip" in field_keys:
                return "装备"
        # 调律按钮查找
        if scene_key == "equip_detail":
            return "sub_func_1" if self._nav_tune_ok else ""
        # 调律页校验
        if scene_key == "equip_tune_detail" and "tune_btn" in field_keys:
            return "词库预览"
        return ""

    def recognize_references_by(self, scene_key, field_keys, target_value,
                               mode, group=None, min_confidence=None):
        return self._material_result.get(target_value, "")

    def recognize_references_info_panel(self, scene_key, panel_key, group=None):
        self.material_info_calls += 1
        return dict(self._material_infos)

    def click_panel(self, scene_key, panel_key, row, col, **kw):
        self.clicks.append((scene_key, panel_key, row, col))

    def _on_scan_reject(self, equip_data, potential, detail_scene=None,
                        already_full=False):
        self.scan_reject_calls.append(equip_data)
        if already_full:
            self.full_calls.append((equip_data, potential))
        return super()._on_scan_reject(equip_data, potential, detail_scene,
                                       already_full=already_full)


def _equip(affix_count: int, quality: str = "gold", cap_pct: int = 50,
           name: str = "测试剑") -> dict:
    """构造装备 dict：affix_count 决定词条数，quality/cap_pct 影响狗粮策略"""
    d: dict = {
        "type": "剑", "name": name, "level": 110, "quality": quality,
        "_extra": {"affix_count": affix_count},
    }
    d["affix_1"] = {"name": "最大外功攻击", "value": 100, "cap_pct": cap_pct}
    for i in range(2, affix_count + 1):
        d[f"affix_{i}"] = {"name": "劲", "value": 10}
    return d


def _wf_with(base: TuningGroup) -> FakeWF:
    """注入基础规则组的 FakeWF（替代旧模块级补丁）"""
    wf = FakeWF()
    wf.run_ctx.base_group = base
    return wf


@pytest.fixture(autouse=True)
def _patch_core(monkeypatch):
    """tuning 包拆分后模块级引用需同步 patch（默认值，个别测试可再覆盖）"""
    monkeypatch.setattr(tuning_judge, "judge_equipment_potential",
                        lambda *a, **k: dict(_WORTHY))
    monkeypatch.setattr(TuningNavigator, "collect_new_affix",
                        lambda self, ed, text: None)


@pytest.fixture
def patch_worth(monkeypatch):
    """默认：值得调律（血河 顶级）；终局判定返回同一结构"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_WORTHY))


def test_android_route_restores_detail_menu():
    wf = FakeWF("android")

    assert isinstance(wf.navigator.routes, AndroidTuningRouteStrategy)
    wf.navigator.leave_tune()

    assert wf.clicks == [
        (TUNE_SCENE, "back"),
        (EQUIP_DETAIL, "more_func"),
    ]


def test_desktop_route_leaves_detail_without_menu_click():
    wf = FakeWF("desktop")

    assert isinstance(wf.navigator.routes, DesktopTuningRouteStrategy)
    wf.navigator.leave_tune()

    assert wf.clicks == [(TUNE_SCENE, "back")]


def test_blue_equipment_is_ignored_and_invalidates_stone_cache():
    wf = FakeWF()
    wf.run_ctx.use_stone_cache = True
    wf._stone_stock_strategy = None
    wf.stone_stock.accept_scan(1000)

    result = wf._process_equipment(
        "蓝色测试剑", _equip(1, quality="blue"), WEAPON_DETAIL)

    assert result
    assert wf.stone_stock.cache_invalid
    assert not wf.scan_reject_calls
    assert (TUNE_SCENE, "tune_btn") not in wf.clicks


def test_workflow_shares_route_strategy_between_components():
    """导航、回收与重置必须共享同一策略实例，不能各自判断环境。"""
    wf = FakeWF()

    assert wf.navigator.routes is wf.route_strategy
    assert wf.recycler._routes is wf.route_strategy
    assert wf.resetter._routes is wf.route_strategy


def test_shared_confirm_subcall_accepts_cancel_when_confirm_is_unreadable():
    """公共确认以 confirm/cancel 联合判定，动作仍固定点击 confirm。"""
    wf = FakeWF()
    wf._ocr_map[CONTROL_SCENE] = {"confirm": "", "cancel": "取消"}

    result = wf.engine.call_subcall(
        "scan_and_confirm", ["测试确认", 1])

    assert result == 1
    assert wf.clicks == [(CONTROL_SCENE, "confirm")]


def test_grid_click_uses_aligned_slot_center():
    """详情由 ESC 收起后，所有格子恢复使用正常的对齐中心。"""
    wf = MagicMock()
    wf.GRID_SCENE = AutoTuningWorkflow.GRID_SCENE
    wf.GRID_PANEL = AutoTuningWorkflow.GRID_PANEL
    wf.click_panel.return_value = True

    result = AutoTuningWorkflow._click_grid(wf, row=1, col=1)

    assert result is True
    wf.click_panel.assert_called_once_with(
        AutoTuningWorkflow.GRID_SCENE, AutoTuningWorkflow.GRID_PANEL, 1, 1)


class DesktopDetailFakeWF(FakeWF):
    """记录端游详情收尾按键，不触碰真实输入后端。"""

    def __init__(self):
        super().__init__("desktop")
        self.presses: list[str] = []

    def press(self, key, wait="step_interval"):
        self.presses.append(key)


def _script_equipment_read(monkeypatch, wf, equip):
    wf._ocr_map[WEAPON_DETAIL] = {
        "equip_level": "OCR", "equip_type": "OCR", "base_attr": "OCR",
    }
    monkeypatch.setattr(
        wf, "call_function", lambda name, args, engine=None: dict(equip))


def test_equipment_scan_rescans_shifted_fields_when_gong_contains_cooldown():
    wf = FakeWF()
    wf._ocr_map[WEAPON_DETAIL] = {
        "equip_type": "剑·流星·Lv110·金色",
        "affix_gong": "冷却期：1小时后可重置",
        "affix_shang": "错位商",
        "cooldown_affix_gong": "会意率 5%",
        "cooldown_affix_shang": "最大外功攻击 90%",
        "cooldown_affix_jue": "最小外功攻击 86%",
        "cooldown_affix_zhi": "最大鸣金攻击 97%",
        "cooldown_affix_yu": "垃圾词条 59%",
        "cooldown_dingyin": "定音属性",
    }

    raw = wf._scan_equipment_detail(WEAPON_DETAIL)

    assert raw["affix_gong"] == "会意率 5%"
    assert raw["affix_shang"] == "最大外功攻击 90%"
    assert raw["affix_jue"] == "最小外功攻击 86%"
    assert raw["affix_zhi"] == "最大鸣金攻击 97%"
    assert raw["affix_yu"] == "垃圾词条 59%"
    assert raw["dingyin"] == "定音属性"
    assert raw["cooldown_text"] == "冷却期：1小时后可重置"
    detail_calls = [call for call in wf.ocr_calls if call[0] == WEAPON_DETAIL]
    assert len(detail_calls) == 2
    assert "cooldown_affix_gong" not in detail_calls[0][1]
    assert detail_calls[1][1] == [
        "cooldown_affix_gong", "cooldown_affix_shang",
        "cooldown_affix_jue", "cooldown_affix_zhi",
        "cooldown_affix_yu", "cooldown_dingyin",
    ]


def test_desktop_empty_slot_does_not_press_escape(monkeypatch):
    wf = DesktopDetailFakeWF()
    _script_equipment_read(monkeypatch, wf, {"level": 0, "type": None})

    _, fp, equip = wf._read_row(WEAPON_DETAIL, 1)

    assert fp == ""
    assert wf._equipment_read_state(equip) == "empty"
    assert wf.presses == []


def test_desktop_incomplete_equipment_logs_skip_and_closes(monkeypatch):
    wf = DesktopDetailFakeWF()
    _script_equipment_read(monkeypatch, wf, {
        "name": "无法识别类型", "level": 110, "type": None,
    })

    _, fp, equip = wf._read_row(WEAPON_DETAIL, 1)

    assert fp.startswith("invalid:")
    assert wf._equipment_read_state(equip) == "invalid"
    assert wf.presses == ["ESC"]


def test_desktop_retained_equipment_closes_after_processing(monkeypatch):
    wf = DesktopDetailFakeWF()
    equip = _equip(2)
    _script_equipment_read(monkeypatch, wf, equip)
    name, _, parsed = wf._read_row(WEAPON_DETAIL, 1)
    monkeypatch.setattr(
        wf, "_process_equipment_once", lambda *a, **k: ("fp_after", None))

    assert wf.presses == []  # OCR 后仍需在详情中处理，不能提前关闭
    assert wf._process_equipment(name, parsed, WEAPON_DETAIL) == "fp_after"
    assert wf.presses == ["ESC"]


def test_desktop_recycled_equipment_does_not_press_escape(monkeypatch):
    wf = DesktopDetailFakeWF()
    equip = _equip(2)
    _script_equipment_read(monkeypatch, wf, equip)
    name, _, parsed = wf._read_row(WEAPON_DETAIL, 1)
    monkeypatch.setattr(
        wf, "_process_equipment_once",
        lambda *a, **k: ("", auto_tuning.RecycleOutcome.RECYCLED))

    assert wf._process_equipment(name, parsed, WEAPON_DETAIL) == ""
    assert wf.presses == []


def test_desktop_locked_recycle_does_not_press_escape(monkeypatch):
    wf = DesktopDetailFakeWF()
    equip = _equip(5)
    _script_equipment_read(monkeypatch, wf, equip)
    name, fp, parsed = wf._read_row(WEAPON_DETAIL, 1)
    monkeypatch.setattr(
        wf, "_process_equipment_once",
        lambda *a, **k: (fp, auto_tuning.RecycleOutcome.LOCKED))

    assert wf._process_equipment(name, parsed, WEAPON_DETAIL) == fp
    assert wf.presses == []


def test_manual_recycle_end_stops_run_without_recording_locked():
    wf = FakeWF()
    wf._recycler = MagicMock()
    wf._recycler.recycle_current.return_value = (
        auto_tuning.RecycleOutcome.STOPPED)
    equip = EquipmentData(
        type="剑", name="待处理剑", level=110, quality="gold")

    outcome = wf._recycle_current(
        equip, WEAPON_DETAIL, "scan", "手动处理")

    assert outcome is auto_tuning.RecycleOutcome.STOPPED
    assert wf.run_state.end_requested is True
    assert wf.is_stopped is True
    assert wf.output["stop_reason"] == "用户选择结束任务"
    assert not wf.run_state.locked_fingerprints


def test_desktop_equipped_slot_closes_before_grid_alignment(monkeypatch):
    wf = DesktopDetailFakeWF()
    _script_equipment_read(monkeypatch, wf, _equip(2))

    equipped = wf._read_equipped("main_weapon")

    assert equipped is not None
    assert wf.presses == ["ESC"]


def test_already_full(monkeypatch):
    """词条满 → 不进调律，走扫描处理，未处理过不收集 report"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: {"s": {"name": "x", "rating": "顶级",
                                               "skipped": False,
                                               "not_applicable": False,
                                               "reasons": []}})
    wf = FakeWF()
    wf._process_equipment("满词条剑", _equip(5), WEAPON_DETAIL)

    assert not wf.output.get("tuning_reports")
    assert len(wf.full_calls) == 1
    assert len(wf.scan_reject_calls) == 1  # 已满装备走扫描处理路径
    # 未进入调律导航；scan 处置表默认无规则 → 保留不回收
    assert (EQUIP_DETAIL, "more_func") not in wf.clicks
    assert "recycled_items" not in wf.output


def test_already_full_emits_legal_potential_as_final_rating(monkeypatch):
    """满词条最终评级必须沿用合法转律模拟结论，不能退回静态评级。"""
    from types import SimpleNamespace

    potential = {"s": {"name": "通用会意", "rating": "优秀",
                       "skipped": False, "not_applicable": False,
                       "reasons": ["最小外功攻击 转律为 势"]}}
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: potential)
    monkeypatch.setattr(tuning_judge, "judge_equipment_potential",
                        lambda *a, **k: potential)
    seen: list[dict] = []
    wf = FakeWF()
    wf._engine = SimpleNamespace(_progress_hub=SimpleNamespace(
        equipment_finished=SimpleNamespace(emit=seen.append)))

    wf._process_equipment("满词条冠胄", _equip(5), WEAPON_DETAIL)

    assert seen[-1]["final_rating"] == "excellent"


def test_below_entry_not_tuned(monkeypatch):
    """预期未达进入门槛 → 调 _on_scan_reject，不进调律，不收集 report"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    # 使用默认配置（无扫描处置规则）→ 保留，不碰回收链
    wf = FakeWF()
    wf._process_equipment("垃圾胚子", _equip(2), WEAPON_DETAIL)

    assert not wf.output.get("tuning_reports")
    assert len(wf.scan_reject_calls) == 1
    assert not wf.full_calls
    # 处置表无规则（默认）→ 保留，不碰回收链
    assert (EQUIP_DETAIL, "more_func") not in wf.clicks


def test_no_tune_entry(patch_worth):
    """值得但未找到调律入口 → no_tune_entry，不回报完成"""
    wf = FakeWF()
    wf._nav_tune_ok = False
    wf._process_equipment("无入口剑", _equip(2), WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "no_tune_entry"
    assert not wf.full_calls
    assert (TUNE_SCENE, "back") not in wf.clicks
    # 「更多」弹窗已开却无调律按钮：再点一次收起 → more_func 共 2 次
    assert wf.clicks.count((EQUIP_DETAIL, "more_func")) == 2


def test_worth_tuned_to_full(patch_worth, monkeypatch):
    """值得 → 调律循环到 5 条 → tuned + 返回 back。

    结束处理默认关 → 每轮走默认「继续调律」，词条满走默认「跳过该装备」。
    石头检查等材料配置注入代码默认值，不读真实 yaml（开关变更不应破测）。
    """
    wf = FakeWF()
    # gold + cap_pct 50 → 不加狗粮，_tune_once 走无材料路径
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100", "tune_tip": ""}
    wf._process_equipment("待调剑", _equip(2, quality="gold", cap_pct=50),
                          WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "tuned"
    assert reports[0]["rounds"] == 3          # 2 → 5，共 3 轮
    assert reports[0]["final_affix_count"] == 5
    assert (TUNE_SCENE, "back") in wf.clicks   # 单次 back 返回背包页
    # back 回背包后再点一次「更多」收起弹窗 → more_func 共 2 次（展开 + 收起）
    assert wf.clicks.count((EQUIP_DETAIL, "more_func")) == 2
    # 词条满 → 结束处理默认「跳过该装备」，不回收
    assert "跳过该装备" in reports[0]["stop_reason"]
    assert "recycled_items" not in wf.output
    # 每轮调律结果挂在本件 report 下，与装备一一对应（不再全局平铺）
    assert len(reports[0]["tune_results"]) == 3
    assert "tune_results" not in wf.output


def test_food_skip_rule_stops_equipment(patch_worth, monkeypatch):
    """狗粮规则命中但库存不足且 on_insufficient=skip → 跳过该装备（rounds=0）"""
    base = TuningGroup(materials=MaterialSettings(food_rules=[
        FoodRule(food="紫狗粮", on_insufficient="skip")]))
    wf = _wf_with(base)
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律"}
    wf._material_infos = {}   # 材料区读不到紫狗粮 → 不足
    wf._process_equipment("缺料剑", _equip(2, quality="purple", cap_pct=50),
                          WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "tuned"
    assert reports[0]["rounds"] == 0
    assert "跳过" in reports[0]["stop_reason"]
    assert (TUNE_SCENE, "back") in wf.clicks
    assert wf.clicks.count((EQUIP_DETAIL, "more_func")) == 2
    assert not wf.full_calls
    assert not wf.executor.materials_exhausted   # 只跳过该装备，遍历继续


def test_food_rule_feeds_each_round(patch_worth, monkeypatch):
    """规则命中且库存充足 → 每轮先点狗粮槽位再一键添加"""
    from lvjiang.core.recognizers import ReferenceInfo

    base = TuningGroup(materials=MaterialSettings(food_rules=[
        FoodRule(pct=90, min_expect="excellent", food="金狗粮")]))
    wf = _wf_with(base)
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100", "tune_tip": ""}
    wf._material_infos = {
        (1, 3): _reference("金狗粮", count=42),
        # 真实识别边界在未匹配时不会附带 OCR 字段；不得拖垮整次调律。
        (1, 7): ReferenceInfo(label="", confidence=0.1),
    }
    wf._process_equipment("高分剑", _equip(2, quality="gold", cap_pct=95),
                          WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "tuned"
    assert reports[0]["rounds"] == 3
    # 材料识别只在进入调律页时调用一次（缓存优化）
    assert wf.material_info_calls == 1
    # 每轮：点狗粮槽位（缓存扣减，不重 OCR）
    assert wf.clicks.count((TUNE_SCENE, "materials", 1, 3)) == 3


def test_no_recognition_when_stone_off_and_no_rules(patch_worth, monkeypatch):
    """石头检查关闭且无狗粮规则 → 全程不识别材料区"""
    base = TuningGroup(materials=MaterialSettings(food_rules=[]))
    wf = _wf_with(base)
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100", "tune_tip": ""}
    wf._process_equipment("待调剑", _equip(2), WEAPON_DETAIL)

    assert wf.material_info_calls == 0
    assert wf.output["tuning_reports"][0]["rounds"] == 3


def test_ghost_duplicate_slot_not_mask_stock(patch_worth, monkeypatch):
    """低置信度误匹配的同名幽灵槽（数量 None）不得覆盖真槽库存，
    且狗粮点击定位到数量有效的真槽（复刻 20260730 雁南飞甲现场）"""
    base = TuningGroup(materials=MaterialSettings(food_rules=[
        FoodRule(pct=90, min_expect="excellent", food="紫狗粮")]))
    wf = _wf_with(base)
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100", "tune_tip": ""}
    wf._material_infos = {
        (1, 1): _reference("紫狗粮"),                     # 前置幽灵槽
        (1, 2): _reference("紫狗粮", count=103, devoted=0),  # 真槽
        (1, 6): _reference("紫狗粮"),                     # 后置幽灵槽
    }
    wf._process_equipment("紫胸甲", _equip(2, quality="purple", cap_pct=95),
                          WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "tuned"
    assert reports[0]["rounds"] == 3
    # 库存取真槽 count=103 → 每轮都喂；点击落在真槽而非幽灵槽
    assert wf.clicks.count((TUNE_SCENE, "materials", 1, 2)) == 3
    assert (TUNE_SCENE, "materials", 1, 1) not in wf.clicks


# ─── 大律准石数量检查 ─────────────────────────

class _Stone:
    """已完成 yysls 解析的调律材料替身。"""

    def __init__(self, label="大律准石", count=None, devoted=None):
        self.label = label
        self.count = count
        self.devoted = devoted


def _reference(label="大律准石", count=None, devoted=None):
    """构造进入通用识别边界时的原始 ReferenceInfo。"""
    from lvjiang.core.recognizers import ReferenceInfo
    count_text = "" if count is None else str(count)
    if devoted is not None and count is not None:
        count_text = f"{devoted}/{count}"
    return ReferenceInfo(
        label=label, confidence=0.9,
        # 系统 schema 始终产出两个 key；无识别结果用空文本表示。
        ocr_texts={"level_text": "", "count_text": count_text},
    )


@pytest.fixture
def stone_check_on():
    """打开石头检查开关（基准 100），狗粮规则保持默认"""
    base = TuningGroup(materials=MaterialSettings(
        stone_check_enabled=True, stone_min_count=100))
    return base


# _check_stone_stock 单测直接传 settings + infos（识别已上提到 _tune_once）
_STONE_ON = MaterialSettings(stone_check_enabled=True, stone_min_count=100)


def test_stone_check_disabled_passes():
    """开关关闭（内置配置默认）→ 不看 infos 直接放行"""
    wf = FakeWF()
    assert wf.executor._check_stone_stock(MaterialSettings(), None) is True
    assert not wf.executor.materials_exhausted


def test_initial_skip_does_not_waive_hard_stone_limit():
    """跳过的只是首次额外校验，默认 80 安全线仍然阻断一键添加。"""
    base = TuningGroup(materials=MaterialSettings(
        stone_check_enabled=True, stone_min_count=80,
        stone_insufficient_action="skip"))
    wf = _wf_with(base)
    wf.run_ctx.use_stone_cache = True
    wf.run_ctx.initial_stone_check_enabled = True
    wf.run_ctx.initial_stone_min_count = 100
    wf._stone_stock_strategy = None
    wf.stone_stock.accept_scan(700)
    wf.executor._choose_initial_stock = lambda _message: "skip"

    wf.executor._handle_initial_stock_check(700)

    assert not wf.stone_stock.needs_initial_check
    assert wf.executor._check_stone_stock(base.materials, None) is False
    assert "安全线 80" in wf.executor.abort_reason


def test_initial_manual_stock_is_rechecked():
    base = TuningGroup(materials=MaterialSettings(
        stone_check_enabled=True, stone_min_count=80,
        stone_insufficient_action="skip"))
    wf = _wf_with(base)
    wf.run_ctx.use_stone_cache = True
    wf.run_ctx.initial_stone_check_enabled = True
    wf.run_ctx.initial_stone_min_count = 100
    wf._stone_stock_strategy = None
    wf.stone_stock.accept_scan(700)
    wf.executor._choose_initial_stock = lambda _message: "manual"
    wf.executor._input_stock_units = lambda: 1200

    wf.executor._handle_initial_stock_check(700)

    assert wf.stone_stock.stock_units == 1200
    assert not wf.stone_stock.needs_initial_check
    assert wf.executor._check_stone_stock(base.materials, None) is True


def test_initial_threshold_applies_without_stone_cache():
    """实时扫描策略也必须执行首次额外门槛。"""
    base = TuningGroup(materials=MaterialSettings())
    wf = _wf_with(base)
    wf.run_ctx.use_stone_cache = False
    wf.run_ctx.initial_stone_check_enabled = True
    wf.run_ctx.initial_stone_min_count = 100
    wf._stone_stock_strategy = None
    choices = []
    wf.executor._choose_initial_stock = lambda message: choices.append(message) or "skip"

    wf.executor._handle_initial_stock_check(700)

    assert choices
    assert "初始检查大律准石数量大于: 100" in choices[0]
    assert wf.executor._initial_stock_check_done


def test_runtime_cache_validation_runs_on_every_fifth_entry(monkeypatch):
    wf = FakeWF()
    wf.run_ctx.use_stone_cache = True
    wf.run_ctx.validate_stone_cache = True
    calls = []
    monkeypatch.setattr(
        wf.executor,
        "cache_materials",
        lambda **kwargs: calls.append(kwargs["validate_stone_cache"]),
    )

    for _ in range(10):
        wf.executor.cache_equipment_materials()

    assert calls == [False, False, False, False, True] * 2


def test_runtime_cache_validation_logs_only_difference_over_one(monkeypatch):
    stock = CachedStoneStock()
    stock.accept_scan(800)
    errors = []
    monkeypatch.setattr(tuning_executor.logger, "error", errors.append)

    tuning_executor.TuningExecutor._validate_cached_stone_stock(stock, 790)
    tuning_executor.TuningExecutor._validate_cached_stone_stock(stock, 0)
    assert errors == []

    tuning_executor.TuningExecutor._validate_cached_stone_stock(stock, 789)

    assert len(errors) == 1
    assert "缓存=80" in errors[0]
    assert "识别=78.9" in errors[0]
    assert "请检查并调整等级配置" in errors[0]


def test_stone_check_enough_passes():
    """库存 ≥ 基准 → 放行；无斜杠样式取 count（×1253）"""
    wf = FakeWF()
    infos = {(1, 2): _Stone(count=1253)}
    assert wf.executor._check_stone_stock(_STONE_ON, infos) is True
    assert not wf.executor.materials_exhausted


def test_stone_check_count_as_stock():
    """count 即持有量：count=117 ≥ 100 → 放行"""
    wf = FakeWF()
    infos = {(1, 2): _Stone(count=117, devoted=7)}
    assert wf.executor._check_stone_stock(_STONE_ON, infos) is True
    assert not wf.executor.materials_exhausted


def test_stone_check_ocr_fail_passes():
    """找到大律准石但数量 OCR 失败 → 视为装备而非调律石，按材料不足处理"""
    wf = FakeWF()
    infos = {(1, 2): _Stone(count=None, devoted=None)}
    assert wf.executor._check_stone_stock(_STONE_ON, infos) is False
    assert wf.executor.materials_exhausted


def test_stone_check_low_stops_all():
    """库存 < 基准 → 置标志全退，记 stop_reason，触发不足钩子"""
    wf = FakeWF()
    infos = {
        (1, 1): _Stone(label="小律准石", count=8),
        (1, 2): _Stone(count=50),
    }
    hook_calls = []
    wf.executor._on_materials_insufficient = \
        lambda stock, baseline: hook_calls.append((stock, baseline))
    assert wf.executor._check_stone_stock(_STONE_ON, infos) is False
    assert wf.executor.materials_exhausted
    assert "大律准石 50" in wf.output["stop_reason"]
    assert "材料不足" in wf.executor.abort_reason
    assert hook_calls == [(50, 100)]


def test_stone_check_missing_slot_stops():
    """材料区没有大律准石 → 视为已耗尽（stock=0）全退"""
    wf = FakeWF()
    infos = {(1, 1): _Stone(label="小律准石", count=8)}
    assert wf.executor._check_stone_stock(_STONE_ON, infos) is False
    assert wf.executor.materials_exhausted
    assert "大律准石 0" in wf.output["stop_reason"]


def test_stone_check_skip_action():
    """不足处理 skip → 本件终止但不全退，不足钩子仍触发"""
    wf = FakeWF()
    settings = MaterialSettings(stone_check_enabled=True, stone_min_count=100,
                                stone_insufficient_action="skip")
    hook_calls = []
    wf.executor._on_materials_insufficient = \
        lambda stock, baseline: hook_calls.append((stock, baseline))
    assert wf.executor._check_stone_stock(
        settings, {(1, 2): _Stone(count=50)}) is False
    assert not wf.executor.materials_exhausted
    assert "stop_reason" not in wf.output
    assert "跳过该装备" in wf.executor.abort_reason
    assert hook_calls == [(50, 100)]


def test_stone_check_ask_manual_value_is_rechecked():
    """硬性安全线不可豁免；人工修正后重新检查。"""
    wf = FakeWF()
    settings = MaterialSettings(stone_check_enabled=True, stone_min_count=100,
                                stone_insufficient_action="ask")
    asked = []
    wf.executor._confirm_hard_stone_failure = \
        lambda msg: asked.append(msg) or "manual"
    wf.executor._input_stock_units = lambda: 1200
    infos = {(1, 2): _Stone(count=50)}
    assert wf.executor._check_stone_stock(settings, infos) is True
    assert not wf.executor.materials_exhausted
    assert len(asked) == 1 and "大律准石 50" in asked[0]


def test_stone_check_ask_decline():
    """不足处理 ask + 用户拒绝 → 同 abort 全退"""
    wf = FakeWF()
    settings = MaterialSettings(stone_check_enabled=True, stone_min_count=100,
                                stone_insufficient_action="ask")
    wf.executor._confirm_hard_stone_failure = lambda msg: "end"
    assert wf.executor._check_stone_stock(
        settings, {(1, 2): _Stone(count=50)}) is False
    assert wf.executor.materials_exhausted
    assert "材料不足" in wf.output["stop_reason"]


def test_stone_low_aborts_tuning_flow(patch_worth, stone_check_on):
    """集成：调律循环内石头不足 → rounds=0，仍正常 back 退出调律页"""
    wf = _wf_with(stone_check_on)
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律"}
    wf._material_infos = {(1, 2): _reference(count=3)}
    wf._process_equipment("缺石剑", _equip(2, quality="gold", cap_pct=50),
                          WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["rounds"] == 0
    assert wf.executor.materials_exhausted
    assert wf.output["stop_reason"]
    # 本用例关闭律准石缓存：进页扫一次，首个检查点再扫一次。
    assert wf.material_info_calls == 2
    # 退出路径仍收束：调律页 back 正常点击
    assert (TUNE_SCENE, "back") in wf.clicks


def test_stone_low_skip_continues_flow(patch_worth, monkeypatch):
    """集成：不足处理 skip → 本件 rounds=0 结束，不置全退标志，
    遍历可继续（is_stopped 仍为假）"""
    base = TuningGroup(materials=MaterialSettings(
        stone_check_enabled=True, stone_min_count=100,
        stone_insufficient_action="skip"))
    wf = _wf_with(base)
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律"}
    wf._material_infos = {(1, 2): _reference(count=3)}
    wf._process_equipment("缺石剑", _equip(2, quality="gold", cap_pct=50),
                          WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["rounds"] == 0
    assert "跳过该装备" in reports[0]["stop_reason"]
    assert not wf.executor.materials_exhausted
    assert not wf.is_stopped
    assert "stop_reason" not in wf.output
    assert (TUNE_SCENE, "back") in wf.clicks


# ─── 一键添加后「调律」按钮就绪检查 ─────────────────


def test_tune_btn_not_ready_default_skips():
    """按钮未就绪 + 石头检查未启用 → 兜底 skip：本件终止不全退"""
    wf = FakeWF()   # _ocr_map 无 tune_btn → OCR 为空 → 重扫后仍未就绪
    assert wf.executor._ensure_tune_ready(MaterialSettings()) is False
    assert not wf.executor.materials_exhausted
    assert "stop_reason" not in wf.output
    assert "未就绪" in wf.executor.abort_reason
    assert "结束本件调律" in wf.executor.abort_reason


def test_tune_btn_not_ready_abort():
    """按钮未就绪 + 不足处理 abort → 置标志全退，记 stop_reason"""
    wf = FakeWF()
    settings = MaterialSettings(stone_check_enabled=True,
                                stone_insufficient_action="abort")
    assert wf.executor._ensure_tune_ready(settings) is False
    assert wf.executor.materials_exhausted
    assert "未就绪" in wf.output["stop_reason"]


def test_tune_btn_not_ready_ask_continue():
    """按钮未就绪 + ask 确认 → 按 skip 跳过本件，本次运行不再询问"""
    wf = FakeWF()
    settings = MaterialSettings(stone_check_enabled=True,
                                stone_insufficient_action="ask")
    asked = []
    wf.executor._confirm_material_insufficient = lambda msg: asked.append(msg) or "continue"
    assert wf.executor._ensure_tune_ready(settings) is False
    assert not wf.executor.materials_exhausted
    assert wf.executor._tune_ready_waived
    assert len(asked) == 1 and "未就绪" in asked[0]
    # 后续装备再次未就绪：直接按跳过处理，不再弹窗
    assert wf.executor._ensure_tune_ready(settings) is False
    assert len(asked) == 1
    assert not wf.executor.materials_exhausted


def test_tune_btn_not_ready_ask_decline():
    """按钮未就绪 + ask 拒绝 → 同 abort 全退"""
    wf = FakeWF()
    settings = MaterialSettings(stone_check_enabled=True,
                                stone_insufficient_action="ask")
    wf.executor._confirm_material_insufficient = lambda msg: "end"
    assert wf.executor._ensure_tune_ready(settings) is False
    assert wf.executor.materials_exhausted
    assert not wf.executor._tune_ready_waived
    assert "未就绪" in wf.output["stop_reason"]


def test_tune_btn_retry_recovers():
    """首扫未就绪、重扫变「调律」 → 放行（防 UI 刷新慢/OCR 波动误杀）"""
    wf = FakeWF()
    scans = [{"tune_btn": ""}, {"tune_btn": "调律"}]
    wf.ocr_scene = lambda scene, keys=None: scans.pop(0)
    assert wf.executor._ensure_tune_ready(MaterialSettings()) is True
    assert not scans          # 恰好扫了两次
    assert not wf.executor.materials_exhausted


def test_tune_btn_not_ready_flow(patch_worth, monkeypatch):
    """集成：一键添加后按钮没变「调律」→ 本件 rounds=0 结束，
    未启用石头检查也兜底：不盲点调律、不全退，仍正常 back 退出"""
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "",
                               "tune_btn": "一键添加"}   # 添加失败，文字未变
    wf._process_equipment("无料剑", _equip(2, quality="gold", cap_pct=50),
                          WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["rounds"] == 0
    assert "未就绪" in reports[0]["stop_reason"]
    assert not wf.executor.materials_exhausted
    assert not wf.is_stopped
    # 点了一键添加，但没有盲点调律按钮
    assert (TUNE_SCENE, "auto_add") in wf.clicks
    assert (TUNE_SCENE, "tune_btn") not in wf.clicks
    assert (TUNE_SCENE, "back") in wf.clicks


def test_ensure_judge_config_keeps_injected():
    """已注入 judge_configs 时 _ensure_judge_config 不覆盖"""
    wf = FakeWF()
    wf.run_ctx = TuningRunContext(
        judge_configs={"huiyi": {"enabled": True}}, judge_rule_keys=["huiyi"])
    wf._ensure_judge_config()
    assert wf.ctx.judge_rule_keys == ["huiyi"]


def test_skip_tuning_switch(patch_worth):
    """跳过实际调律开关：值得调律的装备才真实进出调律页但不调律"""
    wf = FakeWF()
    wf.ctx.skip_tuning = True
    wf._process_equipment("测试剑", _equip(2), WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "skip_tuning"
    assert reports[0]["worthiness"] == ["血河: 顶级（词条匹配）"]   # 潜力判定正常执行
    # 装备未被改动：不走扫描处置/已满处理
    assert not wf.scan_reject_calls
    assert not wf.full_calls
    # 真实进出调律页：展开「更多」+ 调律入口 + back + 收起「更多」
    assert (EQUIP_DETAIL, "sub_func_1") in wf.clicks
    assert (TUNE_SCENE, "back") in wf.clicks
    assert wf.clicks.count((EQUIP_DETAIL, "more_func")) == 2
    # 未执行任何调律
    assert "tune_results" not in reports[0]
    assert "tune_results" not in wf.output


def test_skip_tuning_full_affix_not_entered(monkeypatch):
    """开关开启但词条已满 → 仍走 already_full，不进调律页也不收集 report"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: {})
    wf = FakeWF()
    wf.ctx.skip_tuning = True
    wf._process_equipment("满词条剑", _equip(5), WEAPON_DETAIL)

    assert not wf.output.get("tuning_reports")
    assert len(wf.full_calls) == 1
    assert (EQUIP_DETAIL, "more_func") not in wf.clicks
    assert (TUNE_SCENE, "back") not in wf.clicks


def test_skip_tuning_junk_not_entered(monkeypatch):
    """开关开启但预期未达门槛 → 仍走扫描处理，不进调律页也不收集 report"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    # 使用默认配置（无扫描处置规则）→ 保留，不碰回收链
    wf = FakeWF()
    wf.ctx.skip_tuning = True
    wf._process_equipment("垃圾胚子", _equip(2), WEAPON_DETAIL)

    assert not wf.output.get("tuning_reports")
    assert len(wf.scan_reject_calls) == 1
    assert (EQUIP_DETAIL, "more_func") not in wf.clicks
    assert (TUNE_SCENE, "back") not in wf.clicks


def test_skip_tuning_no_entry(patch_worth):
    """开关开启且值得但无调律入口 → 仍走 no_tune_entry，不点 back"""
    wf = FakeWF()
    wf.ctx.skip_tuning = True
    wf._nav_tune_ok = False
    wf._process_equipment("无入口剑", _equip(2), WEAPON_DETAIL)

    assert wf.output["tuning_reports"][0]["status"] == "no_tune_entry"
    assert not wf.scan_reject_calls
    assert (TUNE_SCENE, "back") not in wf.clicks
    # _nav_to_tune 失败分支自行收起弹窗 → more_func 共 2 次
    assert wf.clicks.count((EQUIP_DETAIL, "more_func")) == 2


# ─── 等级门槛 + 品阶异常前置拦截 ───────────────────


def test_below_min_level_ends_current_slot(monkeypatch):
    """有效装备等级低于门槛 → 标记当前部位结束，不走行为判定。"""
    from lvjiang.apps.yysls.core.tuning_rules import ScanBehavior
    base = TuningGroup(scan=ScanBehavior(min_level=120))
    wf = _wf_with(base)
    fp, outcome = wf._process_equipment_once(
        "低级剑", _equip(2, name="低级剑"), WEAPON_DETAIL)

    assert fp   # 保留装备，返回指纹
    assert outcome is None
    assert wf.slot_level_exhausted
    assert not wf.output.get("tuning_reports")   # 不进调律
    assert not wf.scan_reject_calls   # 不走扫描处置
    assert not wf.full_calls   # 不走已满处理
    assert (EQUIP_DETAIL, "more_func") not in wf.clicks   # 不触发回收


def test_missing_level_does_not_end_current_slot():
    """等级 OCR 缺失是异常数据，不得据此推断后续装备都低等级。"""
    wf = FakeWF()
    equip = _equip(2, name="等级异常剑")
    equip["level"] = None
    equip["quality"] = None

    wf._process_equipment_once("等级异常剑", equip, WEAPON_DETAIL)

    assert not wf.slot_level_exhausted


def test_quality_unrecognized_skips(monkeypatch):
    """品阶识别失败（quality 为空）→ 视为异常直接跳过"""
    wf = FakeWF()
    equip = _equip(2, name="异常剑")
    equip["quality"] = None   # 模拟品阶识别失败
    fp, outcome = wf._process_equipment_once("异常剑", equip, WEAPON_DETAIL)

    assert fp   # 保留装备，返回指纹
    assert outcome is None
    assert not wf.output.get("tuning_reports")
    assert not wf.scan_reject_calls
    assert not wf.full_calls
    assert (EQUIP_DETAIL, "more_func") not in wf.clicks


def test_non_weapon_wuku_bottom_does_not_depend_on_equipment_parse(monkeypatch):
    """非武器 status 识别到武库后，即使装备字段全空也立即见底。"""
    wf = FakeWF()
    wf._current_slot = "head"
    wf._ocr_map[ARMOR_DETAIL] = {"status": "武库中"}
    monkeypatch.setattr(wf, "call_function", lambda *args, **kwargs: {})

    name, fp, equip = wf._read_row(ARMOR_DETAIL, 1)

    assert name == ""
    assert fp == ""
    assert equip == {}
    assert wf.slot_level_exhausted


def test_non_weapon_wuku_never_enters_equipment_processing():
    """旁路调用也不得对非武器武库装备评级、调律或回收。"""
    wf = FakeWF()
    wf._current_slot = "head"
    equip = _equip(2, name="武库冠")
    equip["is_wuku"] = True

    fp, outcome = wf._process_equipment_once(
        "武库冠", equip, ARMOR_DETAIL)

    assert fp
    assert outcome is None
    assert wf.slot_level_exhausted
    assert not wf.output.get("tuning_reports")
    assert not wf.scan_reject_calls
    assert not wf.full_calls
    assert not wf.clicks


def test_min_level_default_100_passes(monkeypatch):
    """默认门槛 100，等级 110 装备正常通过"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_WORTHY))
    wf = FakeWF()   # base_group 默认 TuningGroup()，min_level 默认 100
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "",
                               "tune_btn": "调律", "tune_affix": "",
                               "tune_tip": ""}
    fp, outcome = wf._process_equipment_once("满级剑", _equip(2), WEAPON_DETAIL)

    assert fp   # 正常处理，未被等级门槛拦截
    assert outcome is None
    assert wf.output.get("tuning_reports")   # 进了调律


# ─── 行为处置（behavior 扫描处理 / 结束处理）──────────────


def _behavior_base(scan=None, tune=None) -> TuningGroup:
    """构造带行为配置的基础规则组（狗粮规则清空，免材料识别）"""
    return TuningGroup(
        materials=MaterialSettings(food_rules=[]),
        scan=scan or ScanBehavior(),
        tune=tune or TuneBehavior())


def _mock_game_config(level_configs=None):
    """创建带等级配置的 GameConfigManager mock"""
    mock_mgr = MagicMock()
    configs = level_configs or []
    mock_mgr.get_level_configs.return_value = configs
    mock_mgr.level_config_for = lambda level: next(
        (c for c in configs if c.level == level), None)
    return mock_mgr


_RECYCLE_ALL = [BehaviorRule(action="recycle")]   # 无条件 → 全部回收


def test_scan_recycles_junk(monkeypatch):
    """扫描处置回收：未达门槛 + 处置规则命中 → 更多→回收→确认"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    base = _behavior_base(scan=ScanBehavior(enabled=True,
                                            rules=_RECYCLE_ALL))
    wf = _wf_with(base)
    fp = wf._process_equipment("垃圾适子", _equip(2), WEAPON_DETAIL)

    assert fp == ""   # row=None → 空指纹由上层按空 slot 处理
    assert len(wf.scan_reject_calls) == 1
    # 回收链：展开「更多」→ 子菜单「回收」→ 确认弹窗
    assert wf.clicks.count((EQUIP_DETAIL, "more_func")) == 1
    assert (EQUIP_DETAIL, "sub_func_1") in wf.clicks
    assert (CONTROL_SCENE, "confirm") in wf.clicks
    items = wf.output["recycled_items"]
    assert len(items) == 1 and items[0]["stage"] == "scan"
    assert not wf.output.get("tuning_reports")


def _first_affix_judge(equip, *a, **k):
    """仅注入首词条（affixes 只剩 1 条）→ 顶级；全词条 → 垃圾"""
    return dict(_WORTHY) if len(equip.affixes) <= 1 else dict(_JUNK)


def test_scan_first_affix_only_spares_resettable(monkeypatch):
    """仅首词条（逐规则声明）：本条规则取评级只注入首词条 →
    非首词条已成垃圾但首词条优质的装备不被回收（可等冷却
    重置调律）"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        _first_affix_judge)
    base = _behavior_base(scan=ScanBehavior(
        enabled=True,
        rules=[BehaviorRule(ratings=["junk"], first_affix_only=True,
                            action="recycle")]))
    wf = _wf_with(base)
    fp = wf._process_equipment("待重置金装", _equip(3), WEAPON_DETAIL)

    assert fp   # 保留，返回指纹
    assert len(wf.scan_reject_calls) == 1
    assert not wf.output.get("recycled_items")


def test_scan_first_affix_only_off_recycles(monkeypatch):
    """对照：规则未声明仅首词条 → 同一装备按全词条评级命中回收"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        _first_affix_judge)
    base = _behavior_base(scan=ScanBehavior(
        enabled=True,
        rules=[BehaviorRule(ratings=["junk"], action="recycle")]))
    wf = _wf_with(base)
    fp = wf._process_equipment("待重置金装", _equip(3), WEAPON_DETAIL)

    assert fp == ""
    items = wf.output["recycled_items"]
    assert len(items) == 1 and items[0]["stage"] == "scan"


def test_scan_recycles_when_no_applicable_rule(monkeypatch):
    """无任何适用规则（全部跳过/不适用，如紫色武器）= 无调律
    价值兜底垃圾档 → 评级≤垃圾 的处置规则命中回收"""
    skipped = {"s": {"name": "通用会意", "rating": "",
                     "skipped": True, "not_applicable": False,
                     "reasons": ["品阶 purple 无调律价值"]}}
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(skipped))
    base = _behavior_base(scan=ScanBehavior(
        enabled=True,
        rules=[BehaviorRule(max_quality="purple", ratings=["junk"],
                            action="recycle")]))
    wf = _wf_with(base)
    fp = wf._process_equipment("紫色武器", _equip(1, quality="purple"),
                               WEAPON_DETAIL)

    assert fp == ""
    assert (CONTROL_SCENE, "confirm") in wf.clicks
    items = wf.output["recycled_items"]
    assert len(items) == 1 and items[0]["stage"] == "scan"


def test_scan_custom_scope_protects(monkeypatch):
    """custom 判定语义下他流派好胚不误收：自选规则判仍有潜力 → 保留"""
    def judge(equip_data, configs=None, keys=None):
        # 运行期配置（configs={}）判垃圾；custom 自选规则（configs=
        # None 默认配置）仍可达顶级 → 其他流派的好胚子
        return dict(_WORTHY) if configs is None else dict(_JUNK)
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential", judge)
    monkeypatch.setattr(tuning_judge, "judge_equipment_potential", judge)
    monkeypatch.setattr(tuning_judge, "get_rule_names",
                        lambda: {"huiyi": "会意"})
    base = _behavior_base(scan=ScanBehavior(
        enabled=True,
        rules=[BehaviorRule(ratings=["junk"], judge_scope="custom",
                            judge_rules=["huiyi"], action="recycle")]))
    wf = _wf_with(base)
    fp = wf._process_equipment("他派好胚", _equip(2), WEAPON_DETAIL)

    assert fp   # 保留，正常返回指纹
    assert "recycled_items" not in wf.output
    assert (CONTROL_SCENE, "confirm") not in wf.clicks


# 自选词条语义：紫武器带金色数值珍贵词条（首词条≥90%）→
# 命中跳过不回收；兜底回收规则排后验证不命中分支
_AFFIX_SKIP_RULE = BehaviorRule(
    parts=["武器"], max_quality="purple_only", judge_scope="affix",
    ratings=["最大外功攻击"], pct_op="ge", pct=90, action="skip")


def test_scan_affix_scope_protects_purple_weapon(monkeypatch):
    """紫武器含目标词条 + 首词条 ≥90% → 命中跳过（不回收）"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    base = _behavior_base(scan=ScanBehavior(
        enabled=True, rules=[_AFFIX_SKIP_RULE] + _RECYCLE_ALL))
    wf = _wf_with(base)
    fp = wf._process_equipment("紫武好词条", _equip(
        2, quality="purple", cap_pct=95), WEAPON_DETAIL)

    assert fp   # 保留，正常返回指纹
    assert "recycled_items" not in wf.output
    assert (CONTROL_SCENE, "confirm") not in wf.clicks


def test_scan_affix_scope_pct_insufficient_recycles(monkeypatch):
    """对照：首词条 80% < 90% 门槛 → 不命中，落兜底回收"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    base = _behavior_base(scan=ScanBehavior(
        enabled=True, rules=[_AFFIX_SKIP_RULE] + _RECYCLE_ALL))
    wf = _wf_with(base)
    fp = wf._process_equipment("紫武低分", _equip(
        2, quality="purple", cap_pct=80), WEAPON_DETAIL)

    assert fp == ""
    items = wf.output["recycled_items"]
    assert len(items) == 1 and items[0]["stage"] == "scan"


def test_scan_affix_scope_first_affix_only(monkeypatch):
    """勾选仅首词条：目标词条在非首位置 → 不命中，落兜底回收"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    rule = BehaviorRule(
        parts=["武器"], max_quality="purple_only", judge_scope="affix",
        ratings=["最大外功攻击"], pct_op="ge", pct=90,
        first_affix_only=True, action="skip")
    base = _behavior_base(scan=ScanBehavior(
        enabled=True, rules=[rule] + _RECYCLE_ALL))
    wf = _wf_with(base)
    # 目标词条在第二位（首词条为杂词，cap 95 满足 pct 条件）
    d = _equip(2, quality="purple", cap_pct=95)
    d["affix_1"], d["affix_2"] = d["affix_2"], d["affix_1"]
    d["affix_1"]["cap_pct"] = 95
    fp = wf._process_equipment("紫武非首", d, WEAPON_DETAIL)

    assert fp == ""
    items = wf.output["recycled_items"]
    assert len(items) == 1 and items[0]["stage"] == "scan"


def test_scan_rule_not_matched_keeps(monkeypatch):
    """扫描启用但规则不命中（cap 50 > max_pct 30）→ 忽略保留"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    base = _behavior_base(scan=ScanBehavior(
        enabled=True, rules=[BehaviorRule(pct=30, action="recycle")]))
    wf = _wf_with(base)
    fp = wf._process_equipment("低分胚", _equip(2, cap_pct=50),
                               WEAPON_DETAIL)

    assert fp
    assert "recycled_items" not in wf.output
    assert (EQUIP_DETAIL, "more_func") not in wf.clicks


def test_scan_no_recycle_button_keeps(monkeypatch):
    """子菜单无「回收」按钮 → 收起弹窗保留装备"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    base = _behavior_base(scan=ScanBehavior(enabled=True,
                                            rules=_RECYCLE_ALL))
    wf = _wf_with(base)
    wf._nav_tune_ok = False   # ocr_scene_by 返空 → 找不到回收按钮
    fp = wf._process_equipment("垃圾适子", _equip(2), WEAPON_DETAIL)

    assert fp
    assert "recycled_items" not in wf.output
    # 展开 + 收起共 2 次「更多」
    assert wf.clicks.count((EQUIP_DETAIL, "more_func")) == 2


def test_judge_by_scope_filter(monkeypatch):
    """_judge_by_scope：custom 过滤未知 key；全部无效/all 回落全部规则"""
    captured = {}

    def judge(equip_data, configs=None, keys=None):
        captured["keys"] = keys
        return {}
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential", judge)
    monkeypatch.setattr(tuning_judge, "judge_equipment_potential", judge)
    monkeypatch.setattr(tuning_judge, "get_rule_names",
                        lambda: {"huiyi": "会意"})
    wf = FakeWF()
    equip_data = EquipmentData.from_dict(_equip(2))

    wf.judge.judge_by_scope(equip_data, "custom", ["huiyi", "ghost"])
    assert captured["keys"] == ["huiyi"]      # 未知 key 已过滤
    wf.judge.judge_by_scope(equip_data, "custom", ["ghost"])
    assert captured["keys"] is None           # 全部无效 → 全部规则
    wf.judge.judge_by_scope(equip_data, "all", [])
    assert captured["keys"] is None           # all = 全部规则


def test_behavior_rating_logs_winning_rule_names(monkeypatch):
    """行为评级二次判定必须说明最高档由哪些调律规则产生。"""
    wf = FakeWF()
    equip_data = EquipmentData.from_dict(_equip(2))
    results = {
        "small": {"name": "会心小外", "rating": "顶级",
                  "skipped": False, "not_applicable": False},
        "fire": {"name": "治疗火拳", "rating": "垃圾",
                 "skipped": False, "not_applicable": False},
        "skip": {"name": "已跳过规则", "rating": "顶级",
                 "skipped": True, "not_applicable": False},
    }
    monkeypatch.setattr(wf.judge, "judge_by_scope",
                        lambda *args, **kwargs: results)
    info = MagicMock()
    monkeypatch.setattr(tuning_judge.logger, "info", info)

    rating_of = wf.judge.rating_provider(equip_data)

    assert rating_of("all", [], False) == "top"
    messages = [str(call.args[0]) for call in info.call_args_list]
    assert any("按全部规则判定为 顶级（命中规则：会心小外）" in msg
               for msg in messages)


def test_tune_recycles_after_hit(monkeypatch):
    """结束处理回收：首轮规则命中 recycle → back 回背包页后回收"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_WORTHY))
    base = _behavior_base(tune=TuneBehavior(enabled=True,
                                            rules=_RECYCLE_ALL))
    wf = _wf_with(base)
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100",
                               "tune_tip": ""}
    fp = wf._process_equipment("命中剑", _equip(2, quality="gold",
                                             cap_pct=50), WEAPON_DETAIL)

    assert fp == ""
    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "tuned"
    assert reports[0]["rounds"] == 1         # 首轮即命中，结束循环
    assert reports[0]["recycled"] is True
    items = wf.output["recycled_items"]
    assert len(items) == 1 and items[0]["stage"] == "tune"
    # 回收发生在 back 回背包页之后
    assert (wf.clicks.index((TUNE_SCENE, "back"))
            < wf.clicks.index((CONTROL_SCENE, "confirm")))


def test_tune_skip_ends_keeps(monkeypatch):
    """结束处理命中 skip → 跳过该装备，不回收"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_WORTHY))
    base = _behavior_base(tune=TuneBehavior(
        enabled=True, rules=[BehaviorRule(action="skip")]))
    wf = _wf_with(base)
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100",
                               "tune_tip": ""}
    fp = wf._process_equipment("保留剑", _equip(2, quality="gold",
                                             cap_pct=50), WEAPON_DETAIL)

    assert fp
    reports = wf.output["tuning_reports"]
    assert reports[0]["rounds"] == 1
    assert "命中" in reports[0]["stop_reason"]
    assert "recycled_items" not in wf.output


def test_tune_reset_restores_and_retunes(monkeypatch):
    """重置调律：清空至只剩首词条后继续调到满，词条满默认跳过该装备"""
    calls = {"n": 0}

    def judge(*a, **k):
        calls["n"] += 1
        return dict(_JUNK) if calls["n"] == 2 else dict(_WORTHY)
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential", judge)
    monkeypatch.setattr(tuning_judge, "judge_equipment_potential", judge)
    base = _behavior_base(
        tune=TuneBehavior(
            enabled=True,
            rules=[BehaviorRule(ratings=["junk"], action="reset")],
            max_resets=3))
    monkeypatch.setattr(auto_tuning, "get_game_config",
                        lambda: _mock_game_config([LevelConfig(level=110, allow_reset=True)]))
    wf = _wf_with(base)
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100",
                               "tune_tip": "", "reset_tune": "重置调律(3)",
                               "reset_check": "当前装备剩余可重置次数：3",
                               "reset_info": "持有 100"}
    fp = wf._process_equipment("可救剑", _equip(2, quality="gold",
                                             cap_pct=50), WEAPON_DETAIL)

    assert fp
    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "tuned"
    assert reports[0]["resets"] == 1
    assert reports[0]["rounds"] == 5              # 首轮重置后只剩首词条，1→5 共 4 轮
    assert reports[0]["final_affix_count"] == 5   # 重置后继续调到满
    assert "跳过该装备" in reports[0]["stop_reason"]  # 词条满默认
    assert (TUNE_SCENE, "reset_tune") in wf.clicks
    assert (TUNE_SCENE, "reset_confirm") in wf.clicks
    assert (CONTROL_SCENE, "confirm") in wf.clicks
    assert (TUNE_SCENE, "close_btn") in wf.clicks  # 关闭重置结果弹窗
    assert "recycled_items" not in wf.output


def test_tune_reset_blocked_ocr_unreadable(monkeypatch):
    """按钮文本无数字 = 识别异常 → 跳过该装备，不重置也不回收"""
    calls = {"n": 0}

    def judge(*a, **k):
        calls["n"] += 1
        return dict(_WORTHY) if calls["n"] == 1 else dict(_JUNK)
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential", judge)
    monkeypatch.setattr(tuning_judge, "judge_equipment_potential", judge)
    base = _behavior_base(
        tune=TuneBehavior(
            enabled=True,
            rules=[BehaviorRule(ratings=["junk"], action="reset")]))
    monkeypatch.setattr(auto_tuning, "get_game_config",
                        lambda: _mock_game_config([LevelConfig(level=110, allow_reset=True)]))
    wf = _wf_with(base)
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100",
                               "tune_tip": "", "reset_tune": "重置调律"}
    fp = wf._process_equipment("次数耗尽剑", _equip(2, quality="gold",
                                               cap_pct=50), WEAPON_DETAIL)

    assert fp
    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "tuned"
    assert reports[0]["rounds"] == 1
    assert reports[0]["resets"] == 0
    # 停止原因换成识别异常本身，而不是命中规则的决策说明——用户要能从报告
    # 里看出这件是"没看清楚"而不是"规则让它停"。
    assert "无法识别重置次数" in reports[0]["stop_reason"]
    assert (TUNE_SCENE, "reset_confirm") not in wf.clicks
    # 关键：识别不出次数绝不能触发回收。
    assert "recycled_items" not in wf.output


def test_tune_reset_local_cap(monkeypatch):
    """冷却期硬限：即使 max_resets 更大，本件也只重置一次，后续按转处置默认保留结束"""
    calls = {"n": 0}

    def judge(*a, **k):
        calls["n"] += 1
        return dict(_WORTHY) if calls["n"] == 1 else dict(_JUNK)
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential", judge)
    monkeypatch.setattr(tuning_judge, "judge_equipment_potential", judge)
    base = _behavior_base(
        tune=TuneBehavior(
            enabled=True,
            rules=[BehaviorRule(ratings=["junk"], action="reset")],
            max_resets=3))
    monkeypatch.setattr(auto_tuning, "get_game_config",
                        lambda: _mock_game_config([LevelConfig(level=110, allow_reset=True)]))
    wf = _wf_with(base)
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100",
                               "tune_tip": "", "reset_tune": "重置调律 3/3",
                               "reset_check": "当前装备剩余可重置次数：3",
                               "reset_info": "持有 100"}
    wf._process_equipment("重置一次剑", _equip(2, quality="gold",
                                             cap_pct=50), WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["resets"] == 1
    assert reports[0]["rounds"] == 2      # 重置一轮 + 冷却硬限后结束一轮
    assert wf.clicks.count((TUNE_SCENE, "reset_confirm")) == 1
    assert "recycled_items" not in wf.output


def test_tune_reset_cooldown_check_fails(monkeypatch):
    """冷却期：reset_check 是「6小时32分后可调律重置」→ 退回调律页强制跳过。

    原用例的 docstring 把「可调律重置」当成可重置的标志，实际相反——那正是
    冷却期文案；可重置文案是「当前装备剩余可重置次数:1」。fixture 也用的是
    编造的「冷却中」，没有真正验到游戏原文。
    """
    calls = {"n": 0}

    def judge(*a, **k):
        calls["n"] += 1
        return dict(_WORTHY) if calls["n"] == 1 else dict(_JUNK)
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential", judge)
    monkeypatch.setattr(tuning_judge, "judge_equipment_potential", judge)
    base = _behavior_base(
        tune=TuneBehavior(
            enabled=True,
            rules=[BehaviorRule(ratings=["junk"], action="reset")],
            reset_exhausted_action="recycle"))  # 回收配置不应生效
    monkeypatch.setattr(auto_tuning, "get_game_config",
                        lambda: _mock_game_config([LevelConfig(level=110, allow_reset=True)]))
    wf = _wf_with(base)
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100",
                               "tune_tip": "", "reset_tune": "重置调律(3)",
                               # 游戏原文
                               "reset_check": "6小时32分后可调律重置"}
    fp = wf._process_equipment("冷却剑", _equip(2, quality="gold",
                                             cap_pct=50), WEAPON_DETAIL)

    assert fp  # 指纹正常返回（未回收）
    reports = wf.output["tuning_reports"]
    assert reports[0]["resets"] == 0  # 未执行重置
    assert "冷却期" in reports[0]["stop_reason"]
    # 点了 reset_tune 后检查失败 → 点 back 回调律页，未点 reset_confirm
    assert (TUNE_SCENE, "reset_tune") in wf.clicks
    assert (TUNE_SCENE, "back") in wf.clicks
    assert (TUNE_SCENE, "reset_confirm") not in wf.clicks
    # 强制跳过，不走 reset_exhausted_action=recycle
    assert "recycled_items" not in wf.output


def test_tune_reset_exhausted_recycles(monkeypatch):
    """命中重置但次数用尽 + 转处置配回收 → back 后回收装备"""
    calls = {"n": 0}

    def judge(*a, **k):
        calls["n"] += 1
        return dict(_WORTHY) if calls["n"] == 1 else dict(_JUNK)
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential", judge)
    monkeypatch.setattr(tuning_judge, "judge_equipment_potential", judge)
    base = _behavior_base(
        tune=TuneBehavior(
            enabled=True,
            rules=[BehaviorRule(ratings=["junk"], action="reset")],
            reset_exhausted_action="recycle"))
    monkeypatch.setattr(auto_tuning, "get_game_config",
                        lambda: _mock_game_config([LevelConfig(level=110, allow_reset=True)]))
    wf = _wf_with(base)
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100",
                               "tune_tip": "", "reset_tune": "重置调律(0)"}
    fp = wf._process_equipment("用尽剑", _equip(2, quality="gold",
                                             cap_pct=50), WEAPON_DETAIL)

    assert fp == ""
    reports = wf.output["tuning_reports"]
    assert reports[0]["recycled"] is True
    assert "重置次数已用尽转回收" in reports[0]["recycle_reason"]
    items = wf.output["recycled_items"]
    assert len(items) == 1 and items[0]["stage"] == "tune"
    assert (TUNE_SCENE, "reset_confirm") not in wf.clicks


def test_full_equipment_recycled(monkeypatch):
    """背包已满装备：走扫描处理，recycle 规则命中即回收"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_WORTHY))
    base = _behavior_base(scan=ScanBehavior(
        enabled=True, rules=[BehaviorRule(action="recycle")]))
    wf = _wf_with(base)
    fp = wf._process_equipment("满词条剑", _equip(5), WEAPON_DETAIL)

    assert fp == ""
    assert len(wf.full_calls) == 1
    assert (CONTROL_SCENE, "confirm") in wf.clicks
    items = wf.output["recycled_items"]
    assert len(items) == 1 and items[0]["stage"] == "scan"
    assert not wf.output.get("tuning_reports")


def test_recycle_locked_equipment(monkeypatch):
    """装备锁定检测：回收确认弹窗无「确认」字样 = 装备被锁定，
    收起弹窗返回 RecycleOutcome.LOCKED，不卡死"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    base = _behavior_base(scan=ScanBehavior(enabled=True,
                                            rules=_RECYCLE_ALL))
    wf = _wf_with(base)
    # 模拟装备锁定：回收确认弹窗内无「确认」字样
    wf._ocr_map[CONTROL_SCENE] = {"confirm": "装备已锁定"}
    fp = wf._process_equipment("锁定剑", _equip(2), WEAPON_DETAIL)

    # 装备被锁定，应返回指纹（保留），不收集 recycled_items
    assert fp  # 非空指纹（装备保留原地）
    # 回收链应停在确认检测：展开「更多」→ 子菜单「回收」，但不应点击确认
    assert wf.clicks.count((EQUIP_DETAIL, "more_func")) >= 1
    assert (EQUIP_DETAIL, "sub_func_1") in wf.clicks
    # 关键：不应点击通用确认按钮（因为检测到锁定）
    assert (CONTROL_SCENE, "confirm") not in wf.clicks
    # 不应收集回收记录
    assert not wf.output.get("recycled_items")


def test_recycle_locked_equipment_not_retried(monkeypatch):
    """同一轮再次读到已锁定装备时直接跳过，不重复打开回收链"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    base = _behavior_base(scan=ScanBehavior(enabled=True,
                                            rules=_RECYCLE_ALL))
    wf = _wf_with(base)
    wf._ocr_map[CONTROL_SCENE] = {"confirm": "装备已锁定"}
    equip = _equip(2, name="锁定剑")

    fp1 = wf._process_equipment("锁定剑", equip, WEAPON_DETAIL, row=1, col=2)
    clicks_after_first = list(wf.clicks)
    fp2 = wf._process_equipment("锁定剑", equip, WEAPON_DETAIL, row=1, col=2)

    assert fp2 == fp1
    assert wf.clicks == clicks_after_first
    assert wf.clicks.count((EQUIP_DETAIL, "sub_func_1")) == 1
    assert not wf.output.get("recycled_items")


def test_recycle_unavailable_not_blocked(monkeypatch):
    """回收入口缺失不阻断（只记锁定）：重扫到允许再次尝试"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    base = _behavior_base(scan=ScanBehavior(enabled=True,
                                            rules=_RECYCLE_ALL))
    wf = _wf_with(base)
    wf._nav_tune_ok = False
    equip = _equip(2, name="无回收入口剑")

    fp1 = wf._process_equipment("无回收入口剑", equip, WEAPON_DETAIL,
                                row=1, col=2)
    fp2 = wf._process_equipment("无回收入口剑", equip, WEAPON_DETAIL,
                                row=1, col=2)

    assert fp2 == fp1
    # 两次扫描各完整尝试一次：展开「更多」+ 未找到后收起，共 4 次点击
    assert (EQUIP_DETAIL, "sub_func_1") not in wf.clicks
    assert wf.clicks.count((EQUIP_DETAIL, "more_func")) == 4
    assert not wf.output.get("recycled_items")


def test_locked_block_is_fingerprint_scoped(monkeypatch):
    """阻断按指纹生效：同指纹装备（锁态必然一致）重读直接跳过"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    base = _behavior_base(scan=ScanBehavior(enabled=True,
                                            rules=_RECYCLE_ALL))
    wf = _wf_with(base)
    wf._ocr_map[CONTROL_SCENE] = {"confirm": "装备已锁定"}
    equip = _equip(2, name="同款锁定剑")

    wf._process_equipment("同款锁定剑A", equip, WEAPON_DETAIL, row=1, col=2)
    clicks_after_first = list(wf.clicks)
    wf._process_equipment("同款锁定剑B", equip, WEAPON_DETAIL, row=1, col=3)

    # 同指纹锁定态必然一致：第二件不再打开回收链
    assert wf.clicks == clicks_after_first
    assert wf.clicks.count((EQUIP_DETAIL, "sub_func_1")) == 1
    assert not wf.output.get("recycled_items")


def test_materials_block_no_behavior(monkeypatch):
    """材料不足属阻断：不触发任何行为表（不重置不回收）"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_WORTHY))
    base = TuningGroup(
        materials=MaterialSettings(stone_check_enabled=True,
                                   stone_min_count=100, food_rules=[]),
        tune=TuneBehavior(enabled=True, rules=_RECYCLE_ALL))
    wf = _wf_with(base)
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律"}
    wf._material_infos = {(1, 2): _reference(count=3)}
    fp = wf._process_equipment("缺石剑", _equip(2, quality="gold",
                                             cap_pct=50), WEAPON_DETAIL)

    assert fp
    assert wf.executor.materials_exhausted
    assert "recycled_items" not in wf.output
    assert (TUNE_SCENE, "reset_tune") not in wf.clicks


def test_reset_remaining_parses():
    """括号/斜杠样式均可解；明确的 0 与"没读出数字"必须分开。"""
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"reset_tune": "重置调律(3)"}
    assert wf.resetter.reset_remaining() == 3
    wf._ocr_map[TUNE_SCENE] = {"reset_tune": "重置调律 2/3"}
    assert wf.resetter.reset_remaining() == 2
    # 明确读到 0 = 无疑义的次数用尽，可以转处置。
    wf._ocr_map[TUNE_SCENE] = {"reset_tune": "重置调律(0)"}
    assert wf.resetter.reset_remaining() == 0
    # 没读出数字 = 识别异常，绝不能当成 0。
    wf._ocr_map[TUNE_SCENE] = {"reset_tune": "重置调律"}
    assert wf.resetter.reset_remaining() is None
    wf._ocr_map[TUNE_SCENE] = {"reset_tune": ""}
    assert wf.resetter.reset_remaining() is None


def test_recycle_refill_reprocesses_slot():
    """回收后有格位信息 → 重读同格续处理补位装备"""
    wf = FakeWF()
    outcomes = [("", auto_tuning.RecycleOutcome.RECYCLED), ("fp_b", None)]
    names: list[str] = []
    wf._process_equipment_once = \
        lambda name, equip, scene, **k: (names.append(name), outcomes.pop(0))[1]
    wf._read_row = lambda scene, row, col=1: (
        "补位剑", "FB", {"name": "补位剑", "type": "剑", "level": 110})
    fp = wf._process_equipment("原剑", {"n": 0}, WEAPON_DETAIL, row=2)

    assert fp == "fp_b"
    assert names == ["原剑", "补位剑"]


def test_recycle_refill_empty_slot_ends():
    """回收后重读同格为空 → 背包尽头，返回空指纹"""
    wf = FakeWF()
    wf._process_equipment_once = \
        lambda *a, **k: ("", auto_tuning.RecycleOutcome.RECYCLED)
    wf._read_row = lambda scene, row, col=1: ("", "", {})
    assert wf._process_equipment("原剑", {"n": 0}, WEAPON_DETAIL,
                                 row=1) == ""


def test_recycle_without_row_returns_empty():
    """无格位信息（row=None）→ 回收后无法回读，返回空指纹"""
    wf = FakeWF()
    wf._process_equipment_once = \
        lambda *a, **k: ("", auto_tuning.RecycleOutcome.RECYCLED)
    assert wf._process_equipment("原剑", {"n": 0}, WEAPON_DETAIL) == ""


class ScrollFakeWF(FakeWF):
    """专供旧方案 _scroll_and_verify_step 的替身：桩掉拖拽/对齐/读行原语"""

    def __init__(self):
        super().__init__()
        self.row_fps: dict[int, str] = {}   # 窗口行号 -> 指纹
        self._n_rows = 3
        self.drags = 0

    def drag_grid(self, *a, **k):
        self.drags += 1

    def align_panel(self, *a, **k):
        class _A:
            pass
        obj = _A()
        obj.n_rows = self._n_rows
        return obj

    def _read_row(self, detail_scene, row, col=1):
        return ("name", self.row_fps.get(row, ""), {})


def test_scroll_tolerates_fp_drift_when_second_row_confirms():
    """首行指纹漂移但第二行==fps[+1] → 视为正常步进并更新指纹"""
    wf = ScrollFakeWF()
    fps = ["A", "B", "C"]
    wf.row_fps = {1: "X", 2: "C"}   # 首行漂移为 X，第二行 == fps[2]
    state, rows = PositionalTraversal()._scroll_and_verify_step(
        wf, WEAPON_DETAIL, fps, first_real_row=1, panel_rows=3)
    assert state == ScrollState.PROCESS
    assert rows == 3
    assert fps[1] == "X"            # 漂移指纹已更新


def test_scroll_assumes_step_when_second_row_mismatch():
    """首行未知且第二行也对不上 → 按步进假定：刷新指纹并 PROCESS"""
    wf = ScrollFakeWF()
    fps = ["A", "B", "C"]
    wf.row_fps = {1: "X", 2: "Z"}   # 第二行也对不上
    t = PositionalTraversal()
    state, rows = t._scroll_and_verify_step(
        wf, WEAPON_DETAIL, fps, first_real_row=1, panel_rows=3)
    assert state == ScrollState.PROCESS
    assert fps[1] == "X"            # 假定步进，该位置指纹已刷新
    assert t._assume_streak == 1


def test_scroll_frontier_unknown_fp_assumes_step():
    """前沿处（first_real_row==len(fps)-1）读到未知指纹且第二行为空
    → 无候选可反查，按步进假定而非抛异常（原线上崩溃场景）"""
    wf = ScrollFakeWF()
    fps = ["A", "B"]
    wf.row_fps = {1: "X"}           # 首行未知，第二行读空
    t = PositionalTraversal()
    state, rows = t._scroll_and_verify_step(
        wf, WEAPON_DETAIL, fps, first_real_row=1, panel_rows=3)
    assert state == ScrollState.PROCESS
    assert fps[1] == "X"
    assert t._assume_streak == 1


def test_scroll_drift_short_confirmed_by_second_row_bottoms():
    """首行漂移但第二行证明未步进 → 刷新指纹并入滚少了流程，
    补滚后指纹命中满窗到底（复现硬到底+首行漂移的线上现场）"""
    wf = ScrollFakeWF()
    fps = ["A", "B", "C"]
    wf.row_fps = {1: "X", 2: "B"}   # 第二行 == fps[1] → 滚动没动
    t = PositionalTraversal()
    state, rows = t._scroll_and_verify_step(
        wf, WEAPON_DETAIL, fps, first_real_row=1, panel_rows=3)
    assert state == ScrollState.BOTTOM
    assert fps[0] == "X"            # 原首行漂移指纹已刷新
    assert wf.drags == 2            # 大步进 + 1 次补滚，第 2 次即确认到底
    assert t._assume_streak == 0    # 反查命中，不计入假定


def test_scroll_two_consecutive_assumes_bottom():
    """连续两轮未知指纹 → 第二次不再假定，按到底收束防死循环"""
    wf = ScrollFakeWF()
    fps = ["A", "B", "C"]
    wf.row_fps = {1: "X", 2: "Z"}
    t = PositionalTraversal()
    state1, _ = t._scroll_and_verify_step(
        wf, WEAPON_DETAIL, fps, first_real_row=1, panel_rows=3)
    assert state1 == ScrollState.PROCESS    # 第一次：假定步进
    wf.row_fps = {1: "Y", 2: "Z"}           # 下一轮仍读到未知指纹
    state2, _ = t._scroll_and_verify_step(
        wf, WEAPON_DETAIL, fps, first_real_row=2, panel_rows=3)
    assert state2 == ScrollState.BOTTOM     # 第二次：收束
    assert t._assume_streak == 2


def test_scroll_assume_streak_resets_on_match():
    """假定后下一轮指纹真实命中 → 连续假定计数清零"""
    wf = ScrollFakeWF()
    t = PositionalTraversal()
    t._assume_streak = 1
    fps = ["A", "B", "C"]
    wf.row_fps = {1: "B"}           # 正常步进
    state, _ = t._scroll_and_verify_step(
        wf, WEAPON_DETAIL, fps, first_real_row=1, panel_rows=3)
    assert state == ScrollState.PROCESS
    assert t._assume_streak == 0


def test_scroll_first_real_row_beyond_fps_bottoms():
    """假定步进越过全部已知行且无新行 → 无候选可比，直接到底不再拖拽"""
    wf = ScrollFakeWF()
    state, rows = PositionalTraversal()._scroll_and_verify_step(
        wf, WEAPON_DETAIL, ["A", "B"], first_real_row=2, panel_rows=3)
    assert state == ScrollState.BOTTOM
    assert wf.drags == 0


def test_scroll_short_full_window_bottom_after_two():
    """滚少了且补滚后窗口仍满行（拖拽无效果）→ 第 2 次即确认到底"""
    wf = ScrollFakeWF()
    fps = ["A", "B", "C"]
    wf.row_fps = {1: "A"}           # 首行始终 == fps[0]，滚不动
    state, rows = PositionalTraversal()._scroll_and_verify_step(
        wf, WEAPON_DETAIL, fps, first_real_row=1, panel_rows=3)
    assert state == ScrollState.BOTTOM
    assert wf.drags == 2            # 大步进 + 1 次补滚，第 2 次判定即结束


def test_scroll_short_partial_window_needs_three():
    """滚少了但窗口非满行（真滚少）→ 补滚到第 3 次仍滚少才按到底收束"""
    wf = ScrollFakeWF()
    wf._n_rows = 2                  # align 只检测到 2/3 行
    fps = ["A", "B", "C"]
    wf.row_fps = {1: "A"}
    state, rows = PositionalTraversal()._scroll_and_verify_step(
        wf, WEAPON_DETAIL, fps, first_real_row=1, panel_rows=3)
    assert state == ScrollState.BOTTOM
    assert wf.drags == 3            # 大步进 + 2 次补滚


class RowColsFakeWF(FakeWF):
    """专供列遍历的替身：按 (行,列) 脚本化读格，记录单件处理调用"""

    def __init__(self):
        super().__init__()
        # (win_row, col) -> (名, 指纹, 装备dict)；缺省空 slot
        self.cell_map: dict[tuple[int, int], tuple[str, str, dict]] = {}
        self.processed: list[str] = []
        self.recycled_names: set[str] = set()   # 模拟回收后格位已空

    def _read_row(self, detail_scene, row, col=1):
        return self.cell_map.get((row, col), ("", "", {}))

    @staticmethod
    def _equipment_read_state(equip):
        # 本组测试的最小装备替身用 {"n": ...} 表示有效装备。
        return "valid" if equip else "empty"

    def _process_equipment(self, name, equip, detail_scene,
                           row=None, col=1):
        self.processed.append(name)
        if name in self.recycled_names:
            self.recorder.equipment_recycled = True
            # 模拟回收后背包补位：移除当前格，后续列前移
            if row is not None and col is not None:
                self.cell_map.pop((row, col), None)
                for c in range(col + 1, 10):
                    item = self.cell_map.pop((row, c), None)
                    if item is None:
                        break
                    self.cell_map[(row, c - 1)] = item
            return ""   # 回收后该格已空（由上层重读判断）
        self.recorder.equipment_recycled = False
        return f"fp_{name}"   # 模拟调律后指纹变化


def test_row_cols_processes_all_columns():
    """第 2..cols 列逐个点击识别并处理"""
    wf = RowColsFakeWF()
    wf.cell_map = {
        (1, 2): ("b", "FB", {"n": 2}),
        (1, 3): ("c", "FC", {"n": 3}),
    }
    wf._process_row_cols(WEAPON_DETAIL, win_row=1, logical_row=1, cols=3)
    assert wf.processed == ["b", "c"]


def test_row_cols_stops_at_empty_slot():
    """空 slot → 本行到此为止，后续列不再点击"""
    wf = RowColsFakeWF()
    wf.cell_map = {
        (1, 3): ("c", "FC", {"n": 3}),   # 第 2 列空，第 3 列有装备也不应处理
    }
    wf._process_row_cols(WEAPON_DETAIL, win_row=1, logical_row=1, cols=3)
    assert wf.processed == []


def test_empty_ocr_model_with_cached_fingerprint_is_empty_slot():
    """回归：OCR 噪声名和旧固定 _fp 都不能把空槽变成装备。"""
    empty = {
        "type": None, "name": "王", "level": None, "quality": None,
        "is_chengyin": False, "base_attr": None, "base_attr_2": None,
        "dingyin": None, "_fp": "116f370e",
    }
    assert AutoTuningWorkflow._make_fingerprint(empty) == ""


def test_row_cols_recycle_stays_at_column():
    """非首列回收 → 补位装备前移到当前列 → 留在当前列继续处理而非跳下一列"""
    wf = RowColsFakeWF()
    wf.cell_map = {
        (1, 2): ("a", "FA", {"n": 1}),   # 第 2 列：装备 a（将被回收）
        (1, 3): ("b", "FB", {"n": 2}),   # 第 3 列：装备 b（a 回收后补位到第 2 列）
    }
    wf.recycled_names = {"a"}   # a 被回收
    wf._process_row_cols(WEAPON_DETAIL, win_row=1, logical_row=1, cols=3)
    # a 在第 2 列被回收 → b 补位到第 2 列 → 留在第 2 列处理 b
    # → 第 3 列已空 → 本行结束
    assert wf.processed == ["a", "b"]


def test_row_cols_recycle_cascade_stays_at_column():
    """连续回收：第 2 列回收后补位也被回收，再次留在当前列"""
    wf = RowColsFakeWF()
    wf.cell_map = {
        (1, 2): ("a", "FA", {"n": 1}),
        (1, 3): ("b", "FB", {"n": 2}),
        (1, 4): ("c", "FC", {"n": 3}),
    }
    wf.recycled_names = {"a", "b"}   # a 和 b 都会被回收
    wf._process_row_cols(WEAPON_DETAIL, win_row=1, logical_row=1, cols=4)
    # a 回收 → b 补位到 col 2 → b 也回收 → c 补位到 col 2 → c 保留
    # → col 3 空 → 结束
    assert wf.processed == ["a", "b", "c"]


def test_new_rows_full_row_traversal_first_col_fp_only():
    """整行遍历：每行处理全部列，但仅首列指纹记入 fps（含调律后覆盖）"""
    wf = RowColsFakeWF()
    wf.cell_map = {
        (1, 1): ("a1", "F11", {"n": 1}),
        (1, 2): ("a2", "F12", {"n": 2}),
        (2, 1): ("b1", "F21", {"n": 3}),
        # (2, 2) 空 → 第 2 行只有首列
    }
    fps: list[str] = []
    idx = PositionalTraversal()._process_new_rows(
        wf, WEAPON_DETAIL, fps, 0, 1, 2, cols=2)
    assert idx == 2
    assert wf.processed == ["a1", "a2", "b1"]      # 行内先首列后余列
    assert fps == ["fp_a1", "fp_b1"]               # 仅首列指纹，已被调律后指纹覆盖


def test_new_rows_recycled_empty_slot_stops():
    """首列回收后格位已空（无补位）→ 不占位指纹，按到底收束"""
    wf = RowColsFakeWF()
    wf.cell_map = {
        (1, 1): ("a1", "F11", {"n": 1}),
        (2, 1): ("b1", "F21", {"n": 2}),
    }
    wf.recycled_names = {"a1"}
    fps: list[str] = []
    idx = PositionalTraversal()._process_new_rows(
        wf, WEAPON_DETAIL, fps, 0, 1, 2, cols=2)
    assert idx == 0                # 未计入已处理行
    assert fps == []               # 回收空格不占位
    assert wf.processed == ["a1"]  # 后续行不再处理


class TestTuningDocIntegration:
    """调律说明文档端到端：假流程注入 ctx.doc_dir 后跑通并检查叙事内容"""

    def test_doc_written_only_for_tuned(self, monkeypatch, tmp_path):
        """好剑调律到满写入文档；垃圾剑被跳过完全不出现在文档中"""
        monkeypatch.setattr(
            auto_tuning, "judge_equipment_potential",
            lambda equip_data, *a, **k: dict(
                _WORTHY if equip_data.name == "好剑" else _JUNK))
        wf = FakeWF()
        wf._engine.run_username = "小明"
        wf.ctx.doc_dir = tmp_path
        wf._ocr_map[TUNE_SCENE] = {
            "auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
            "tune_affix": "最大外功攻击 100", "tune_tip": ""}

        wf._open_doc(["main_weapon"])
        wf._process_equipment(
            "好剑", _equip(2, quality="gold", cap_pct=50, name="好剑"),
            WEAPON_DETAIL)
        wf._process_equipment("垃圾剑", _equip(2, name="垃圾剑"),
                              WEAPON_DETAIL)
        wf._close_doc()

        docs = list(tmp_path.glob("调律说明_小明_*.md"))
        assert len(docs) == 1
        text = docs[0].read_text(encoding="utf-8")
        # 文档头
        assert "- 操作用户：小明" in text
        assert "- 调律部位：主武器" in text
        # 装备节：只写命中的规则与轮次叙事
        assert "## 1. 好剑 · 剑（110级 金色）" in text
        assert "- 血河：顶级（词条匹配）" in text
        assert "狗粮策略：" in text
        assert "第 1 轮：一键添加律准石 → 新词条「最大外功攻击 100」" in text
        assert "  → 无行为规则命中 → 继续调律" in text
        assert "  → 词条已满，无行为规则命中 → 跳过该装备" in text
        assert ("本件小结：共 3 轮，词条 5/5，结束原因："
                "词条已满，无行为规则命中 → 跳过该装备") in text
        # 运行小结
        assert "## 运行结束" in text
        assert "（正常完成）" in text
        assert "- 实际调律 1 件，共 3 轮" in text
        # 成品清单：好剑终局顶级 → 入选，含首词条与其余词条
        assert "### 成品清单（一般及以上）" in text
        assert "1. 好剑 · 剑（110级 金色）— 血河：顶级" in text
        assert "   - 首词条：最大外功攻击 100（50%）" in text
        assert "   - 其余词条：劲 10" in text
        # 被判定不值得的装备完全不写
        assert "垃圾剑" not in text

    def test_summary_items_filter(self):
        """_summary_items：按适用规则最高评级筛一般及以上；
        跳过/不适用规则不参与；垃圾/无适用规则排除"""
        def _report(name, judgement):
            return {"name": name, "type": "剑", "level": 110,
                    "quality": "gold", "final_judgement": judgement,
                    "final_affixes": [{"name": "劲", "value": 10}]}

        def _j(rating, skipped=False, na=False):
            return {"name": "血河", "rating": rating,
                    "skipped": skipped, "not_applicable": na,
                    "reasons": []}

        tuned = [
            _report("顶级剑", {"s": _j("顶级")}),
            _report("一般剑", {"s": _j("一般")}),
            _report("垃圾剑", {"s": _j("垃圾")}),
            _report("跳过剑", {"s": _j("顶级", skipped=True)}),
            _report("不适用剑", {"s": _j("顶级", na=True)}),
            _report("双规则剑", {"a": _j("垃圾"),
                     "b": {"name": "会意", "rating": "优秀",
                           "skipped": False, "not_applicable": False,
                           "reasons": []}}),
        ]
        items = TuningRecorder.summary_items(tuned)
        assert [i["name"] for i in items] == ["顶级剑", "一般剑", "双规则剑"]
        assert items[0]["rating_text"] == "血河：顶级"
        # 双规则：最高档优秀入选，rating_text 罗列全部适用规则
        assert items[2]["rating_text"] == "血河：垃圾；会意：优秀"

    def test_doc_none_when_not_opened(self, monkeypatch):
        """未走 run()/_open_doc（_doc 为 None）时各插桩点静默跳过"""
        monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                            lambda *a, **k: dict(_WORTHY))
        wf = FakeWF()
        wf._ocr_map[TUNE_SCENE] = {
            "auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
            "tune_affix": "最大外功攻击 100", "tune_tip": ""}
        wf._process_equipment("待调剑", _equip(2), WEAPON_DETAIL)
        assert wf.output["tuning_reports"][0]["status"] == "tuned"

    def test_interrupted_current_report_is_finalized(self, tmp_path):
        """F10 时当前装备的部分报告必须先写入 Markdown 再关闭。"""
        wf = FakeWF()
        wf._engine.run_username = "小明"
        wf.ctx.doc_dir = tmp_path
        wf._open_doc(["main_weapon"])
        equip = _equip(2, name="中断剑")
        wf.recorder.start_report("中断剑", equip, 2)
        wf.recorder.doc_start_equipment(equip)
        wf.recorder.report_set("rounds", 1)
        wf.recorder.report_set("final_affix_count", 3)
        wf.recorder.report_set("latest_affixes", [
            equip["affix_1"], equip["affix_2"],
            {"name": "敏", "value": 10},
        ])

        assert wf.recorder.finalize_interrupted_current()
        wf._stopped = True
        wf._close_doc()

        report = wf.recorder.collect_reports()[-1]
        assert report["status"] == "interrupted"
        assert report["rounds"] == 1
        assert report["stop_reason"] == "用户中断"
        text = next(tmp_path.glob("调律说明_小明_*.md")).read_text(encoding="utf-8")
        assert "用户中断，保存当前装备的部分调律结果" in text
        assert "本件小结：共 1 轮，词条 3/5" in text
        assert "## 运行结束" in text

    def test_open_doc_failure_degrades(self, monkeypatch):
        """文档创建失败（OSError）→ 只警告，_doc 置 None，流程不中断"""
        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(auto_tuning, "TuningDocWriter", boom)
        wf = FakeWF()
        wf._open_doc(["main_weapon"])
        assert wf.recorder.doc is None
        wf._close_doc()   # 幂等，不抛


class TestResolveSelectedSlots:
    """调律部位解析：设备端（ctx 未注入）回退读插件会话

    设备端经 task_runner 启动 auto_tuning 时 run_ctx 为默认实例
    （selected_slots=None）；部位必须从插件会话 tuning.selected_slots
    回退读取，否则配置页保存的部位不生效（恒按全部 8 部位）。
    """

    @pytest.fixture
    def session(self, tmp_path, monkeypatch):
        import lvjiang.constants as constants_mod
        import lvjiang.core.config.session as store_mod
        path = tmp_path / "session.json"
        monkeypatch.setattr(constants_mod, "SESSION_PATH", path)
        store_mod.reset_session_store()
        return store_mod.get_session_store()

    def _device_wf(self):
        wf = FakeWF()
        wf.run_ctx = TuningRunContext()  # selected_slots=None，模拟设备端未注入
        return wf

    def test_device_reads_session(self, session):
        session.set_node("wf_configs", {"auto_tuning": {"selected_slots": ["ring", "head"]}})
        assert self._device_wf()._resolve_selected_slots() == ["ring", "head"]

    def test_empty_session_raises(self, session):
        """未配置调律部位时抛异常，不默认全部部位"""
        wf = self._device_wf()
        with pytest.raises(ValueError, match="未配置调律部位"):
            wf._resolve_selected_slots()

    def test_injected_ctx_ignores_session(self, session):
        session.set_node("wf_configs", {"auto_tuning": {"selected_slots": ["ring"]}})
        wf = self._device_wf()
        wf.run_ctx = TuningRunContext(selected_slots=["main_weapon"])  # UI 已注入
        assert wf._resolve_selected_slots() == ["main_weapon"]

    def test_unknown_slot_keys_dropped(self, session):
        session.set_node("wf_configs",
                         {"auto_tuning": {"selected_slots": ["ring", "bogus"]}})
        assert self._device_wf()._resolve_selected_slots() == ["ring"]


# ─── 滚动定位 / 指定调律 / 初始跳过 ─────────────────────


class SkipTargetFakeWF(FakeWF):
    """扩展 FakeWF：支持 drag_grid/align_panel/_read_row 记录"""

    def __init__(self):
        super().__init__()
        self.drags = 0
        self.aligns = 0
        self.read_row_calls: list[tuple] = []
        self.process_equipment_calls: list[tuple] = []
        self.traverse_calls: list[str] = []
        # 脚本化 _read_row 返回
        self._read_row_result: tuple[str, str, dict] = ("", "", {})

    def drag_grid(self, scene, panel, direction, distance=1.0, hold=0.3):
        self.drags += 1

    def align_panel(self, scene, panel):
        self.aligns += 1

        class _A:
            n_rows = 3
        return _A()

    def _find_panel(self, scene, panel):
        class _P:
            rows = 3
            cols = 6
        return _P()

    def _read_row(self, detail_scene, row, col=1):
        self.read_row_calls.append((detail_scene, row, col))
        return self._read_row_result

    def _process_equipment(self, name, equip, detail_scene, row=None, col=1):
        self.process_equipment_calls.append((name, equip, detail_scene, row, col))
        return "fp_after"

    def _traverse_bag(self, detail_scene):
        self.traverse_calls.append(detail_scene)

    def _open_doc(self, slots):
        pass

    def _close_doc(self):
        pass

    def _ensure_judge_config(self):
        pass

    def call_function(self, func_name: str, args: list, engine=None) -> any:
        """Mock 阻塞式内置函数，避免测试中触发原生弹窗"""
        if func_name == "confirm":
            return True  # confirm() 总是返回 True
        if func_name == "pause":
            return  # pause() 直接返回，不阻塞
        return super().call_function(func_name, args, engine)


class TestScrollToRow:
    def test_no_scroll_for_row_1(self):
        wf = SkipTargetFakeWF()
        wf._scroll_to_row(1)
        assert wf.drags == 0

    def test_scroll_count_equals_target_minus_1(self):
        wf = SkipTargetFakeWF()
        wf._scroll_to_row(5)
        assert wf.drags == 4
        assert wf.aligns == 1  # 滚动结束后对齐一次

    def test_scroll_stops_when_stopped(self):
        wf = SkipTargetFakeWF()
        wf._stopped = True
        wf._scroll_to_row(10)
        assert wf.drags == 0  # is_stopped 立即中断


class TestProcessSingleTarget:
    def test_processes_equipment_at_target(self):
        wf = SkipTargetFakeWF()
        wf._read_row_result = ("测试剑", "fp123", _equip(3))
        wf._process_single_target(WEAPON_DETAIL, 3, 2)
        assert wf.drags == 2  # 滚到第 3 行
        assert wf.read_row_calls == [(WEAPON_DETAIL, 1, 2)]  # 可见区第 1 行、第 2 列
        assert len(wf.process_equipment_calls) == 1
        assert wf.process_equipment_calls[0][0] == "测试剑"

    def test_empty_target_skips_processing(self):
        wf = SkipTargetFakeWF()
        wf._read_row_result = ("", "", {})  # 空格
        wf._process_single_target(WEAPON_DETAIL, 2, 1)
        assert wf.process_equipment_calls == []


class TestRunWithTargetCell:
    def test_target_cell_processes_one_and_ends(self):
        wf = SkipTargetFakeWF()
        wf.run_ctx = TuningRunContext(
            selected_slots=["main_weapon", "ring"],
            target_cell=(2, 3),
            base_group=TuningGroup(),
        )
        wf._read_row_result = ("目标剑", "fp_x", _equip(3))
        wf.run()
        # 只处理了一件装备
        assert len(wf.process_equipment_calls) == 1
        assert wf.process_equipment_calls[0][0] == "目标剑"
        # 没有遍历背包
        assert wf.traverse_calls == []


class TestRunWithSkipStart:
    def test_skip_start_scrolls_first_slot_only(self):
        wf = SkipTargetFakeWF()
        wf.run_ctx = TuningRunContext(
            selected_slots=["main_weapon", "head"],
            skip_start=(4, 1),
            base_group=TuningGroup(),
        )
        wf.run()
        # 第一个部位：先滚动再遍历
        assert wf.drags == 3  # row 4 - 1 = 3
        assert wf.traverse_calls == [WEAPON_DETAIL, "equip_armor_detail"]
        # 第二个部位不滚动（总 drags 仍为 3）
        assert wf.drags == 3


# ─── 基础规则组回退链 ──────────────────────────────

class TestBaseGroupFallback:
    """回退链：ctx 注入优先，未注入则读 wf_configs，读不到抛异常"""

    @pytest.fixture
    def session(self, tmp_path, monkeypatch):
        import lvjiang.constants as constants_mod
        import lvjiang.core.config.session as store_mod
        path = tmp_path / "session.json"
        monkeypatch.setattr(constants_mod, "SESSION_PATH", path)
        store_mod.reset_session_store()
        return store_mod.get_session_store()

    def test_ctx_injection_used_directly(self, session):
        # ctx.base_group 已注入时直接使用
        custom = TuningGroup(key="custom", name="自定义")
        wf = FakeWF()
        wf.run_ctx.base_group = custom
        result = wf._ensure_base_group()
        assert result is custom

    def test_no_config_raises(self, session):
        """未配置基础规则组时抛异常，不默认空 TuningGroup"""
        wf = FakeWF()
        wf.run_ctx.base_group = None
        with pytest.raises(ValueError, match="未配置基础规则组"):
            wf._ensure_base_group()

    def test_reads_wf_configs_and_caches(self, session, monkeypatch):
        """回退读 wf_configs 并缓存在 ctx"""
        group = TuningGroup(key="test_grp", name="测试组")
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.tuning_rules.get_tuning_group",
            lambda key: group if key == "test_grp" else None)
        session.set_node("wf_configs", {"auto_tuning": {"base_group": "test_grp"}})
        wf = FakeWF()
        wf.run_ctx.base_group = None
        result1 = wf._ensure_base_group()
        result2 = wf._ensure_base_group()
        assert result1 is group
        assert result1 is result2
        assert wf.run_ctx.base_group is result1


# ─── 武器分组：槽位类型未知时的退化语义 ────────────────────

class TestGroupedTypeUnknownDegrades:
    """槽位已装备装备解析失败时，不得谎称「不同类型」。

    这份数据唯一的用途就是武器分组判定。拿不到类型却按「不同类型」处理，
    会把一次 OCR 抖动误判成「该类型组已到底」，静默砍掉整个部位后面所有
    装备——而日志还写着「不同类型」，排障时指向完全错误的方向。
    约定：类型未知 → 退化为按部位终止语义，并在读取处记 error。
    """

    def test_matches_returns_none_when_slot_unread(self):
        wf = FakeWF()
        wf._equipped_items = {}
        assert wf._grouped_type_matches("main_weapon", "剑") is None

    def test_matches_returns_none_when_slot_parse_failed(self):
        wf = FakeWF()
        wf._equipped_items = {"main_weapon": None}
        assert wf._grouped_type_matches("main_weapon", "剑") is None

    def test_matches_returns_none_when_current_type_missing(self):
        wf = FakeWF()
        wf._equipped_items = {"main_weapon": {"type": "剑"}}
        assert wf._grouped_type_matches("main_weapon", "") is None

    def test_matches_discriminates_same_and_different(self):
        wf = FakeWF()
        wf._equipped_items = {"main_weapon": {"type": "剑"}}
        assert wf._grouped_type_matches("main_weapon", "剑") is True
        assert wf._grouped_type_matches("main_weapon", "枪") is False

    def test_same_type_wuku_does_not_end_the_slot(self):
        wf = FakeWF()
        wf._current_slot = "main_weapon"
        wf._equipped_items = {"main_weapon": {"type": "剑"}}
        equip = _equip(2, name="武库剑")
        equip["is_wuku"] = True
        equip["type"] = "剑"

        wf._process_equipment_once("武库剑", equip, ARMOR_DETAIL)

        assert not wf.slot_level_exhausted, "同类型武库不应结束武器部位"

    def test_unknown_type_wuku_degrades_to_slot_bottom(self):
        """类型未知 → 保守终止（与非分组部位一致），但绝不进入处理。"""
        wf = FakeWF()
        wf._current_slot = "main_weapon"
        wf._equipped_items = {"main_weapon": None}
        equip = _equip(2, name="武库剑")
        equip["is_wuku"] = True
        equip["type"] = "剑"

        fp, outcome = wf._process_equipment_once("武库剑", equip, ARMOR_DETAIL)

        assert wf.slot_level_exhausted
        assert outcome is None
        assert not wf.output.get("tuning_reports")
        assert not wf.full_calls


class TestWukuPanelEvents:
    """武库装备必须在面板上有明确提示，不能让扫描毫无征兆地停下。"""

    @staticmethod
    def _attach_hub(wf) -> list[dict]:
        """挂一个假 progress hub —— 真实信号经 engine._progress_hub 发出。"""
        from types import SimpleNamespace

        seen: list[dict] = []
        wf._engine = SimpleNamespace(_progress_hub=SimpleNamespace(
            equipment_finished=SimpleNamespace(emit=seen.append)))
        return seen

    @staticmethod
    def _statuses(seen) -> list[str]:
        return [info.get("status") for info in seen]

    def test_non_weapon_reports_slot_boundary(self):
        wf = FakeWF()
        wf._current_slot = "head"
        seen = self._attach_hub(wf)
        wf._mark_non_weapon_wuku_bottom(True, "武库冠")
        assert self._statuses(seen) == ["wuku_bottom"]

    def test_marking_twice_reports_once(self):
        """同一部位重复置位不刷屏。"""
        wf = FakeWF()
        wf._current_slot = "head"
        seen = self._attach_hub(wf)
        wf._mark_non_weapon_wuku_bottom(True, "武库冠")
        wf._mark_non_weapon_wuku_bottom(True, "武库靴")
        assert self._statuses(seen) == ["wuku_bottom"]

    def test_same_weapon_group_reports_skip(self):
        wf = FakeWF()
        wf._current_slot = "main_weapon"
        wf._equipped_items = {"main_weapon": {"type": "剑"}}
        equip = _equip(2, name="武库剑")
        equip["is_wuku"] = True
        equip["type"] = "剑"
        seen = self._attach_hub(wf)
        wf._process_equipment_once("武库剑", equip, ARMOR_DETAIL)
        assert self._statuses(seen) == ["wuku_skip"]
        assert not wf.slot_level_exhausted

    def test_different_weapon_group_reports_boundary(self):
        wf = FakeWF()
        wf._current_slot = "main_weapon"
        wf._equipped_items = {"main_weapon": {"type": "剑"}}
        equip = _equip(2, name="武库枪")
        equip["is_wuku"] = True
        equip["type"] = "枪"
        seen = self._attach_hub(wf)
        wf._process_equipment_once("武库枪", equip, ARMOR_DETAIL)
        assert self._statuses(seen) == ["wuku_bottom"]
        assert wf.slot_level_exhausted


def test_food_refund_popup_closed_only_when_detected(patch_worth, monkeypatch):
    """识别到提示才补关并记返还；没出字就什么都不做。

    只要真的返还，提示区必然出字，所以按识别结果开关即可。这里不做文本
    区分——两端文案不同（安卓「点击空白区域关闭」/ PC「[space]继续」，其中
    [space] 基本 OCR 不出），非空即算命中。
    """
    base = TuningGroup(materials=MaterialSettings(food_rules=[
        FoodRule(pct=90, min_expect="excellent", food="金狗粮")]))

    def run(tip: str):
        wf = _wf_with(base)
        wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "",
                                   "tune_btn": "调律",
                                   "tune_affix": "最大外功攻击 100",
                                   "tune_tip": tip}
        wf._material_infos = {(1, 3): _reference("金狗粮", count=42)}
        wf._process_equipment("返还剑", _equip(2, quality="gold", cap_pct=95),
                              WEAPON_DETAIL)
        return wf, wf.output["tuning_reports"][0]["rounds"]

    # 没出字：每轮只关一次结果弹窗，不补关，也不标返还。
    wf, rounds = run("")
    assert rounds == 3
    assert wf.clicks.count((TUNE_SCENE, "close_btn")) == rounds
    assert wf.executor.round_food_refunded is False

    # PC 端只认得出「继续」两字，同样算命中：每轮补关一次。
    wf, rounds = run("继续")
    assert wf.clicks.count((TUNE_SCENE, "close_btn")) == rounds * 2
    assert wf.executor.round_food_refunded is True

    # 安卓端文案，不做文本区分，一样命中。
    wf, rounds = run("点击空白区域关闭")
    assert wf.clicks.count((TUNE_SCENE, "close_btn")) == rounds * 2
    assert wf.executor.round_food_refunded is True
