"""auto_tuning 端到端链路的分支覆盖测试

用 FakeWF 覆写场景交互原语（click_region/ocr_scene/ocr_scene_by/
recognize_materials_by/wait_delay）并 spy 空接口，monkeypatch 模块级
判定函数 judge_equipment_potential（结构化结果，预期评级由真实的
summarize_potential/_expect_key 归纳），驱动 _process_equipment 的各分支：
already_full / 未达进入门槛 / no_tune_entry / tuned（含材料不足提前
结束），不依赖真实规则与真实 OCR。行为处置（扫描处理/结束处理）
由注入 behavior 配置的 TuningBase 驱动，钩子委派真实实现。
"""

import pytest

from lvjiang.apps.yysls.equip_parser import EquipmentData
from lvjiang.apps.yysls.evaluator.tuning_rules import (
    BehaviorRule,
    BehaviorSettings,
    FoodRule,
    MaterialSettings,
    ScanBehavior,
    TuneBehavior,
    TuningBase,
)
from lvjiang.apps.yysls.workflows.implementations import auto_tuning
from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
    AutoTuningWorkflow,
)
from lvjiang.apps.yysls.workflows.implementations.bag_traversal import (
    PositionalTraversal,
    ScrollState,
)
from lvjiang.apps.yysls.workflows.run_context import TuningRunContext

WEAPON_DETAIL = AutoTuningWorkflow.WEAPON_DETAIL
# 调律页与调律结果弹窗已合并为同一场景（结果在 result 视图），
# 故 _ocr_map 里两者字段共用一个场景条目
TUNE_SCENE = AutoTuningWorkflow.TUNE_SCENE

# judge_equipment_potential 的结构化结果样本：命中顶级 / 判为垃圾
_WORTHY = {"s": {"name": "血河", "rating": "顶级", "skipped": False,
                 "not_applicable": False, "reasons": ["词条匹配"]}}
_JUNK = {"s": {"name": "血河", "rating": "垃圾", "skipped": False,
               "not_applicable": False, "reasons": ["词条不符"]}}


class FakeWF(AutoTuningWorkflow):
    """不走 BaseWorkflow.__init__ 的测试替身，记录调用并脚本化识别响应"""

    def __init__(self):
        self.output = {}
        self.run_ctx = TuningRunContext(judge_configs={}, judge_rule_keys=[])
        self._stopped = False
        self.clicks: list[tuple[str, str]] = []
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
        return self._stopped or self._materials_exhausted

    def wait_delay(self, name: str):
        pass

    def wait_seconds(self, seconds: float):
        pass

    def click_region(self, scene_key, field_key, jitter: bool = True):
        self.clicks.append((scene_key, field_key))

    def ocr_scene(self, scene_key, field_keys=None):
        data = dict(self._ocr_map.get(scene_key, {}))
        if field_keys:
            return {k: v for k, v in data.items() if k in field_keys}
        return data

    def ocr_scene_by(self, scene_key, field_keys, target_value, mode):
        return "sub_func_1" if self._nav_tune_ok else ""

    def recognize_materials_by(self, scene_key, field_keys, target_value,
                               mode, group=None):
        return self._material_result.get(target_value, "")

    def recognize_materials_info(self, scene_key, slot_keys=None, group=None):
        self.material_info_calls += 1
        return dict(self._material_infos)

    def _collect_new_affix(self, equip_data, text):
        # 循环词条计数由 _process_equipment 本地维护，测试无需真实解析
        pass

    def _on_scan_reject(self, equip_data, potential, detail_scene=None):
        self.scan_reject_calls.append(equip_data)
        return super()._on_scan_reject(equip_data, potential, detail_scene)

    def _on_full_equipment(self, equip_data, judgement, report,
                           detail_scene=None):
        self.full_calls.append((equip_data, judgement, report))
        return super()._on_full_equipment(equip_data, judgement, report,
                                          detail_scene)


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


@pytest.fixture
def patch_worth(monkeypatch):
    """默认：值得调律（血河 顶级）；终局判定返回同一结构"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_WORTHY))


def test_already_full(monkeypatch):
    """词条满 → 不进调律，调 _on_full_equipment，未处理过不收集 report"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: {"s": {"name": "x", "rating": "顶级",
                                               "skipped": False,
                                               "not_applicable": False,
                                               "reasons": []}})
    wf = FakeWF()
    wf._process_equipment("满词条剑", _equip(5), WEAPON_DETAIL)

    assert not wf.output.get("tuning_reports")
    assert len(wf.full_calls) == 1
    # 钩子仍能拿到完整 report（含终局判定）
    assert wf.full_calls[0][2]["status"] == "already_full"
    assert wf.full_calls[0][2]["final_judgement"]
    # 未进入调律导航；tune 行为表默认关 → 保留不回收
    assert (WEAPON_DETAIL, "more_func") not in wf.clicks
    assert not wf.scan_reject_calls
    assert "recycled_items" not in wf.output


def test_below_entry_not_tuned(monkeypatch):
    """预期未达进入门槛 → 调 _on_scan_reject，不进调律，不收集 report"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    # 使用默认配置（无扫描处置规则）→ 保留，不碰回收链
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: TuningBase())
    wf = FakeWF()
    wf._process_equipment("垃圾胚子", _equip(2), WEAPON_DETAIL)

    assert not wf.output.get("tuning_reports")
    assert len(wf.scan_reject_calls) == 1
    assert not wf.full_calls
    # 处置表无规则（默认）→ 保留，不碰回收链
    assert (WEAPON_DETAIL, "more_func") not in wf.clicks


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
    assert wf.clicks.count((WEAPON_DETAIL, "more_func")) == 2


def test_worth_tuned_to_full(patch_worth, monkeypatch):
    """值得 → 调律循环到 5 条 → tuned + 返回 back。

    结束处理默认关 → 每轮走默认「继续调律」，词条满走默认「跳过该装备」。
    石头检查等材料配置注入代码默认值，不读真实 yaml（开关变更不应破测）。
    """
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: TuningBase())
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
    assert wf.clicks.count((WEAPON_DETAIL, "more_func")) == 2
    # 词条满 → 结束处理默认「跳过该装备」，不回收
    assert "跳过该装备" in reports[0]["stop_reason"]
    assert "recycled_items" not in wf.output
    # 每轮调律结果挂在本件 report 下，与装备一一对应（不再全局平铺）
    assert len(reports[0]["tune_results"]) == 3
    assert "tune_results" not in wf.output


def test_food_skip_rule_stops_equipment(patch_worth, monkeypatch):
    """狗粮规则命中但库存不足且 on_insufficient=skip → 跳过该装备（rounds=0）"""
    base = TuningBase(materials=MaterialSettings(food_rules=[
        FoodRule(food="紫狗粮", on_insufficient="skip")]))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律"}
    wf._material_infos = {}   # 材料区读不到紫狗粮 → 不足
    wf._process_equipment("缺料剑", _equip(2, quality="purple", cap_pct=50),
                          WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "tuned"
    assert reports[0]["rounds"] == 0
    assert "跳过" in reports[0]["stop_reason"]
    assert (TUNE_SCENE, "back") in wf.clicks
    assert wf.clicks.count((WEAPON_DETAIL, "more_func")) == 2
    assert not wf.full_calls
    assert not wf._materials_exhausted   # 只跳过该装备，遍历继续


def test_food_rule_feeds_each_round(patch_worth, monkeypatch):
    """规则命中且库存充足 → 每轮先点狗粮槽位再一键添加"""
    base = TuningBase(materials=MaterialSettings(food_rules=[
        FoodRule(pct=90, min_expect="excellent", food="金狗粮")]))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100", "tune_tip": ""}
    wf._material_infos = {"material_3": _Stone(type="金狗粮", count=42)}
    wf._process_equipment("高分剑", _equip(2, quality="gold", cap_pct=95),
                          WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "tuned"
    assert reports[0]["rounds"] == 3
    # 每轮：共用一次材料识别 + 点狗粮槽位
    assert wf.material_info_calls == 3
    assert wf.clicks.count((TUNE_SCENE, "material_3")) == 3


def test_no_recognition_when_stone_off_and_no_rules(patch_worth, monkeypatch):
    """石头检查关闭且无狗粮规则 → 全程不识别材料区"""
    base = TuningBase(materials=MaterialSettings(food_rules=[]))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100", "tune_tip": ""}
    wf._process_equipment("待调剑", _equip(2), WEAPON_DETAIL)

    assert wf.material_info_calls == 0
    assert wf.output["tuning_reports"][0]["rounds"] == 3


def test_ghost_duplicate_slot_not_mask_stock(patch_worth, monkeypatch):
    """低置信度误匹配的同名幽灵槽（数量 None）不得覆盖真槽库存，
    且狗粮点击定位到数量有效的真槽（复刻 20260730 雁南飞甲现场）"""
    base = TuningBase(materials=MaterialSettings(food_rules=[
        FoodRule(pct=90, min_expect="excellent", food="紫狗粮")]))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100", "tune_tip": ""}
    wf._material_infos = {
        "material_1": _Stone(type="紫狗粮"),                     # 前置幽灵槽
        "material_2": _Stone(type="紫狗粮", count=0, owned=103),  # 真槽
        "material_6": _Stone(type="紫狗粮"),                     # 后置幽灵槽
    }
    wf._process_equipment("紫胸甲", _equip(2, quality="purple", cap_pct=95),
                          WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "tuned"
    assert reports[0]["rounds"] == 3
    # 库存取真槽 owned=103 → 每轮都喂；点击落在真槽而非幽灵槽
    assert wf.clicks.count((TUNE_SCENE, "material_2")) == 3
    assert (TUNE_SCENE, "material_1") not in wf.clicks


# ─── 大律准石数量检查 ─────────────────────────

class _Stone:
    """MaterialInfo 最小替身（只需 type/count/owned 三字段）"""

    def __init__(self, type="大律准石", count=None, owned=None):
        self.type = type
        self.count = count
        self.owned = owned


@pytest.fixture
def stone_check_on(monkeypatch):
    """打开石头检查开关（基准 100），狗粮规则保持默认"""
    base = TuningBase(materials=MaterialSettings(
        stone_check_enabled=True, stone_min_count=100))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)


# _check_stone_stock 单测直接传 settings + infos（识别已上提到 _tune_once）
_STONE_ON = MaterialSettings(stone_check_enabled=True, stone_min_count=100)


def test_stone_check_disabled_passes():
    """开关关闭（内置配置默认）→ 不看 infos 直接放行"""
    wf = FakeWF()
    assert wf._check_stone_stock(MaterialSettings(), None) is True
    assert not wf._materials_exhausted


def test_stone_check_enough_passes():
    """库存 ≥ 基准 → 放行；无斜杠样式取 count（×1253）"""
    wf = FakeWF()
    infos = {"material_2": _Stone(count=1253)}
    assert wf._check_stone_stock(_STONE_ON, infos) is True
    assert not wf._materials_exhausted


def test_stone_check_owned_priority():
    """x/y 样式 owned 优先：count=7 但 owned=117 ≥ 100 → 放行"""
    wf = FakeWF()
    infos = {"material_2": _Stone(count=7, owned=117)}
    assert wf._check_stone_stock(_STONE_ON, infos) is True
    assert not wf._materials_exhausted


def test_stone_check_ocr_fail_passes():
    """找到大律准石但数量 OCR 失败 → 警告放行不误杀"""
    wf = FakeWF()
    infos = {"material_2": _Stone(count=None, owned=None)}
    assert wf._check_stone_stock(_STONE_ON, infos) is True
    assert not wf._materials_exhausted


def test_stone_check_low_stops_all():
    """库存 < 基准 → 置标志全退，记 stop_reason，触发不足钩子"""
    wf = FakeWF()
    infos = {
        "material_1": _Stone(type="小律准石", count=8),
        "material_2": _Stone(count=50),
    }
    hook_calls = []
    wf._on_materials_insufficient = \
        lambda stock, baseline: hook_calls.append((stock, baseline))
    assert wf._check_stone_stock(_STONE_ON, infos) is False
    assert wf._materials_exhausted
    assert "大律准石 50" in wf.output["stop_reason"]
    assert "材料不足" in wf._tune_abort_reason
    assert hook_calls == [(50, 100)]


def test_stone_check_missing_slot_stops():
    """材料区没有大律准石 → 视为已耗尽（stock=0）全退"""
    wf = FakeWF()
    infos = {"material_1": _Stone(type="小律准石", count=8)}
    assert wf._check_stone_stock(_STONE_ON, infos) is False
    assert wf._materials_exhausted
    assert "大律准石 0" in wf.output["stop_reason"]


def test_stone_check_skip_action():
    """不足处理 skip → 本件终止但不全退，不足钩子仍触发"""
    wf = FakeWF()
    settings = MaterialSettings(stone_check_enabled=True, stone_min_count=100,
                                stone_insufficient_action="skip")
    hook_calls = []
    wf._on_materials_insufficient = \
        lambda stock, baseline: hook_calls.append((stock, baseline))
    assert wf._check_stone_stock(
        settings, {"material_2": _Stone(count=50)}) is False
    assert not wf._materials_exhausted
    assert "stop_reason" not in wf.output
    assert "跳过该装备" in wf._tune_abort_reason
    assert hook_calls == [(50, 100)]


def test_stone_check_ask_continue():
    """不足处理 ask + 用户确认 → 放行，本次运行不再检查不再询问"""
    wf = FakeWF()
    settings = MaterialSettings(stone_check_enabled=True, stone_min_count=100,
                                stone_insufficient_action="ask")
    asked = []
    wf._confirm_continue = lambda msg: asked.append(msg) or True
    infos = {"material_2": _Stone(count=50)}
    assert wf._check_stone_stock(settings, infos) is True
    assert wf._stone_check_waived
    assert not wf._materials_exhausted
    assert len(asked) == 1 and "大律准石 50" in asked[0]
    # 后续轮次直接放行，不再弹窗
    assert wf._check_stone_stock(settings, infos) is True
    assert len(asked) == 1


def test_stone_check_ask_decline():
    """不足处理 ask + 用户拒绝 → 同 abort 全退"""
    wf = FakeWF()
    settings = MaterialSettings(stone_check_enabled=True, stone_min_count=100,
                                stone_insufficient_action="ask")
    wf._confirm_continue = lambda msg: False
    assert wf._check_stone_stock(
        settings, {"material_2": _Stone(count=50)}) is False
    assert wf._materials_exhausted
    assert not wf._stone_check_waived
    assert "材料不足" in wf.output["stop_reason"]


def test_stone_low_aborts_tuning_flow(patch_worth, stone_check_on):
    """集成：调律循环内石头不足 → rounds=0，仍正常 back 退出调律页"""
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律"}
    wf._material_infos = {"material_2": _Stone(count=3)}
    wf._process_equipment("缺石剑", _equip(2, quality="gold", cap_pct=50),
                          WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["rounds"] == 0
    assert wf._materials_exhausted
    assert wf.output["stop_reason"]
    # 石头检查与狗粮决策共用同一次识别
    assert wf.material_info_calls == 1
    # 退出路径仍收束：调律页 back 正常点击
    assert (TUNE_SCENE, "back") in wf.clicks


def test_stone_low_skip_continues_flow(patch_worth, monkeypatch):
    """集成：不足处理 skip → 本件 rounds=0 结束，不置全退标志，
    遍历可继续（is_stopped 仍为假）"""
    base = TuningBase(materials=MaterialSettings(
        stone_check_enabled=True, stone_min_count=100,
        stone_insufficient_action="skip"))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律"}
    wf._material_infos = {"material_2": _Stone(count=3)}
    wf._process_equipment("缺石剑", _equip(2, quality="gold", cap_pct=50),
                          WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["rounds"] == 0
    assert "跳过该装备" in reports[0]["stop_reason"]
    assert not wf._materials_exhausted
    assert not wf.is_stopped
    assert "stop_reason" not in wf.output
    assert (TUNE_SCENE, "back") in wf.clicks


# ─── 一键添加后「调律」按钮就绪检查 ─────────────────


def test_tune_btn_not_ready_default_skips():
    """按钮未就绪 + 石头检查未启用 → 兜底 skip：本件终止不全退"""
    wf = FakeWF()   # _ocr_map 无 tune_btn → OCR 为空 → 重扫后仍未就绪
    assert wf._ensure_tune_ready(MaterialSettings()) is False
    assert not wf._materials_exhausted
    assert "stop_reason" not in wf.output
    assert "未就绪" in wf._tune_abort_reason
    assert "结束本件调律" in wf._tune_abort_reason


def test_tune_btn_not_ready_abort():
    """按钮未就绪 + 不足处理 abort → 置标志全退，记 stop_reason"""
    wf = FakeWF()
    settings = MaterialSettings(stone_check_enabled=True,
                                stone_insufficient_action="abort")
    assert wf._ensure_tune_ready(settings) is False
    assert wf._materials_exhausted
    assert "未就绪" in wf.output["stop_reason"]


def test_tune_btn_not_ready_ask_continue():
    """按钮未就绪 + ask 确认 → 按 skip 跳过本件，本次运行不再询问"""
    wf = FakeWF()
    settings = MaterialSettings(stone_check_enabled=True,
                                stone_insufficient_action="ask")
    asked = []
    wf._confirm_continue = lambda msg: asked.append(msg) or True
    assert wf._ensure_tune_ready(settings) is False
    assert not wf._materials_exhausted
    assert wf._tune_ready_waived
    assert len(asked) == 1 and "未就绪" in asked[0]
    # 后续装备再次未就绪：直接按跳过处理，不再弹窗
    assert wf._ensure_tune_ready(settings) is False
    assert len(asked) == 1
    assert not wf._materials_exhausted


def test_tune_btn_not_ready_ask_decline():
    """按钮未就绪 + ask 拒绝 → 同 abort 全退"""
    wf = FakeWF()
    settings = MaterialSettings(stone_check_enabled=True,
                                stone_insufficient_action="ask")
    wf._confirm_continue = lambda msg: False
    assert wf._ensure_tune_ready(settings) is False
    assert wf._materials_exhausted
    assert not wf._tune_ready_waived
    assert "未就绪" in wf.output["stop_reason"]


def test_tune_btn_retry_recovers():
    """首扫未就绪、重扫变「调律」 → 放行（防 UI 刷新慢/OCR 波动误杀）"""
    wf = FakeWF()
    scans = [{"tune_btn": ""}, {"tune_btn": "调律"}]
    wf.ocr_scene = lambda scene, keys=None: scans.pop(0)
    assert wf._ensure_tune_ready(MaterialSettings()) is True
    assert not scans          # 恰好扫了两次
    assert not wf._materials_exhausted


def test_tune_btn_not_ready_flow(patch_worth, monkeypatch):
    """集成：一键添加后按钮没变「调律」→ 本件 rounds=0 结束，
    未启用石头检查也兜底：不盲点调律、不全退，仍正常 back 退出"""
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: TuningBase())
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "",
                               "tune_btn": "一键添加"}   # 添加失败，文字未变
    wf._process_equipment("无料剑", _equip(2, quality="gold", cap_pct=50),
                          WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["rounds"] == 0
    assert "未就绪" in reports[0]["stop_reason"]
    assert not wf._materials_exhausted
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
    assert (WEAPON_DETAIL, "sub_func_1") in wf.clicks
    assert (TUNE_SCENE, "back") in wf.clicks
    assert wf.clicks.count((WEAPON_DETAIL, "more_func")) == 2
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
    assert (WEAPON_DETAIL, "more_func") not in wf.clicks
    assert (TUNE_SCENE, "back") not in wf.clicks


def test_skip_tuning_junk_not_entered(monkeypatch):
    """开关开启但预期未达门槛 → 仍走扫描处理，不进调律页也不收集 report"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    # 使用默认配置（无扫描处置规则）→ 保留，不碰回收链
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: TuningBase())
    wf = FakeWF()
    wf.ctx.skip_tuning = True
    wf._process_equipment("垃圾胚子", _equip(2), WEAPON_DETAIL)

    assert not wf.output.get("tuning_reports")
    assert len(wf.scan_reject_calls) == 1
    assert (WEAPON_DETAIL, "more_func") not in wf.clicks
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
    assert wf.clicks.count((WEAPON_DETAIL, "more_func")) == 2


# ─── 等级门槛 + 品阶异常前置拦截 ───────────────────


def test_below_min_level_skips(monkeypatch):
    """等级低于门槛 → 直接跳过，不走任何行为判定"""
    base = TuningBase(min_level=120)
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    fp = wf._process_equipment_once("低级剑", _equip(2, name="低级剑"),
                                    WEAPON_DETAIL)

    assert fp   # 保留装备，返回指纹
    assert not wf.output.get("tuning_reports")   # 不进调律
    assert not wf.scan_reject_calls   # 不走扫描处置
    assert not wf.full_calls   # 不走已满处理
    assert (WEAPON_DETAIL, "more_func") not in wf.clicks   # 不触发回收


def test_quality_unrecognized_skips(monkeypatch):
    """品阶识别失败（quality 为空）→ 视为异常直接跳过"""
    wf = FakeWF()
    equip = _equip(2, name="异常剑")
    equip["quality"] = None   # 模拟品阶识别失败
    fp = wf._process_equipment_once("异常剑", equip, WEAPON_DETAIL)

    assert fp   # 保留装备，返回指纹
    assert not wf.output.get("tuning_reports")
    assert not wf.scan_reject_calls
    assert not wf.full_calls
    assert (WEAPON_DETAIL, "more_func") not in wf.clicks


def test_min_level_default_100_passes(monkeypatch):
    """默认门槛 100，等级 110 装备正常通过"""
    monkeypatch.setattr(auto_tuning, "get_tuning_base",
                        lambda: TuningBase())   # min_level 默认 100
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_WORTHY))
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "",
                               "tune_btn": "调律", "tune_affix": "",
                               "tune_tip": ""}
    fp = wf._process_equipment_once("满级剑", _equip(2), WEAPON_DETAIL)

    assert fp   # 正常处理，未被等级门槛拦截
    assert wf.output.get("tuning_reports")   # 进了调律


# ─── 行为处置（behavior 扫描处理 / 结束处理）──────────────


def _behavior_base(scan=None, tune=None) -> TuningBase:
    """构造带行为配置的 TuningBase（狗粮规则清空，免材料识别）"""
    return TuningBase(
        materials=MaterialSettings(food_rules=[]),
        behavior=BehaviorSettings(scan=scan or ScanBehavior(),
                                  tune=tune or TuneBehavior()))


_RECYCLE_ALL = [BehaviorRule(action="recycle")]   # 无条件 → 全部回收


def test_scan_recycles_junk(monkeypatch):
    """扫描处置回收：未达门槛 + 处置规则命中 → 更多→回收→确认"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    base = _behavior_base(scan=ScanBehavior(enabled=True,
                                            rules=_RECYCLE_ALL))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    fp = wf._process_equipment("垃圾适子", _equip(2), WEAPON_DETAIL)

    assert fp == ""   # row=None → 空指纹由上层按空 slot 处理
    assert len(wf.scan_reject_calls) == 1
    # 回收链：展开「更多」→ 子菜单「回收」→ 确认弹窗
    assert wf.clicks.count((WEAPON_DETAIL, "more_func")) == 1
    assert (WEAPON_DETAIL, "sub_func_1") in wf.clicks
    assert (WEAPON_DETAIL, "recycle_confirm") in wf.clicks
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
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
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
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
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
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    fp = wf._process_equipment("紫色武器", _equip(1, quality="purple"),
                               WEAPON_DETAIL)

    assert fp == ""
    assert (WEAPON_DETAIL, "recycle_confirm") in wf.clicks
    items = wf.output["recycled_items"]
    assert len(items) == 1 and items[0]["stage"] == "scan"


def test_scan_custom_scope_protects(monkeypatch):
    """custom 判定语义下他流派好胚不误收：自选规则判仍有潜力 → 保留"""
    def judge(equip_data, configs=None, keys=None):
        # 运行期配置（configs={}）判垃圾；custom 自选规则（configs=
        # None 默认配置）仍可达顶级 → 其他流派的好胚子
        return dict(_WORTHY) if configs is None else dict(_JUNK)
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential", judge)
    monkeypatch.setattr(auto_tuning, "get_rule_names",
                        lambda: {"huiyi": "会意"})
    base = _behavior_base(scan=ScanBehavior(
        enabled=True,
        rules=[BehaviorRule(ratings=["junk"], judge_scope="custom",
                            judge_rules=["huiyi"], action="recycle")]))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    fp = wf._process_equipment("他派好胚", _equip(2), WEAPON_DETAIL)

    assert fp   # 保留，正常返回指纹
    assert "recycled_items" not in wf.output
    assert (WEAPON_DETAIL, "recycle_confirm") not in wf.clicks


def test_scan_rule_not_matched_keeps(monkeypatch):
    """扫描启用但规则不命中（cap 50 > max_pct 30）→ 忽略保留"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    base = _behavior_base(scan=ScanBehavior(
        enabled=True, rules=[BehaviorRule(pct=30, action="recycle")]))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    fp = wf._process_equipment("低分胚", _equip(2, cap_pct=50),
                               WEAPON_DETAIL)

    assert fp
    assert "recycled_items" not in wf.output
    assert (WEAPON_DETAIL, "more_func") not in wf.clicks


def test_scan_no_recycle_button_keeps(monkeypatch):
    """子菜单无「回收」按钮 → 收起弹窗保留装备"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    base = _behavior_base(scan=ScanBehavior(enabled=True,
                                            rules=_RECYCLE_ALL))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    wf._nav_tune_ok = False   # ocr_scene_by 返空 → 找不到回收按钮
    fp = wf._process_equipment("垃圾适子", _equip(2), WEAPON_DETAIL)

    assert fp
    assert "recycled_items" not in wf.output
    # 展开 + 收起共 2 次「更多」
    assert wf.clicks.count((WEAPON_DETAIL, "more_func")) == 2


def test_judge_by_scope_filter(monkeypatch):
    """_judge_by_scope：custom 过滤未知 key；全部无效/all 回落全部规则"""
    captured = {}

    def judge(equip_data, configs=None, keys=None):
        captured["keys"] = keys
        return {}
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential", judge)
    monkeypatch.setattr(auto_tuning, "get_rule_names",
                        lambda: {"huiyi": "会意"})
    wf = FakeWF()
    equip_data = EquipmentData.from_dict(_equip(2))

    wf._judge_by_scope(equip_data, "custom", ["huiyi", "ghost"])
    assert captured["keys"] == ["huiyi"]      # 未知 key 已过滤
    wf._judge_by_scope(equip_data, "custom", ["ghost"])
    assert captured["keys"] is None           # 全部无效 → 全部规则
    wf._judge_by_scope(equip_data, "all", [])
    assert captured["keys"] is None           # all = 全部规则


def test_tune_recycles_after_hit(monkeypatch):
    """结束处理回收：首轮规则命中 recycle → back 回背包页后回收"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_WORTHY))
    base = _behavior_base(tune=TuneBehavior(enabled=True,
                                            rules=_RECYCLE_ALL))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
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
            < wf.clicks.index((WEAPON_DETAIL, "recycle_confirm")))


def test_tune_skip_ends_keeps(monkeypatch):
    """结束处理命中 skip → 跳过该装备，不回收"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_WORTHY))
    base = _behavior_base(tune=TuneBehavior(
        enabled=True, rules=[BehaviorRule(action="skip")]))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
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
    base = _behavior_base(tune=TuneBehavior(
        enabled=True,
        rules=[BehaviorRule(ratings=["junk"], action="reset")],
        max_resets=3))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100",
                               "tune_tip": "", "reset_tune": "重置调律(3)"}
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
    assert (TUNE_SCENE, "reset_confirm_2") in wf.clicks
    assert "recycled_items" not in wf.output


def test_tune_reset_blocked_ocr_zero(monkeypatch):
    """按钮文本无数字 = 次数用尽 → 不重置，默认转处置忽略不回收"""
    calls = {"n": 0}

    def judge(*a, **k):
        calls["n"] += 1
        return dict(_WORTHY) if calls["n"] == 1 else dict(_JUNK)
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential", judge)
    base = _behavior_base(tune=TuneBehavior(
        enabled=True,
        rules=[BehaviorRule(ratings=["junk"], action="reset")]))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100",
                               "tune_tip": "", "reset_tune": "重置调律"}
    fp = wf._process_equipment("次数耗尽剑", _equip(2, quality="gold",
                                               cap_pct=50), WEAPON_DETAIL)

    assert fp
    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "tuned"
    assert reports[0]["rounds"] == 1
    assert "resets" not in reports[0]
    assert "重置装备" in reports[0]["stop_reason"]   # 命中规则的决策说明
    assert (TUNE_SCENE, "reset_confirm") not in wf.clicks
    assert "recycled_items" not in wf.output


def test_tune_reset_local_cap(monkeypatch):
    """冷却期硬限：即使 max_resets 更大，本件也只重置一次，后续按转处置默认保留结束"""
    calls = {"n": 0}

    def judge(*a, **k):
        calls["n"] += 1
        return dict(_WORTHY) if calls["n"] == 1 else dict(_JUNK)
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential", judge)
    base = _behavior_base(tune=TuneBehavior(
        enabled=True,
        rules=[BehaviorRule(ratings=["junk"], action="reset")],
        max_resets=3))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100",
                               "tune_tip": "", "reset_tune": "重置调律 3/3"}
    wf._process_equipment("重置一次剑", _equip(2, quality="gold",
                                             cap_pct=50), WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["resets"] == 1
    assert reports[0]["rounds"] == 2      # 重置一轮 + 冷却硬限后结束一轮
    assert wf.clicks.count((TUNE_SCENE, "reset_confirm")) == 1
    assert "recycled_items" not in wf.output


def test_tune_reset_exhausted_recycles(monkeypatch):
    """命中重置但次数用尽 + 转处置配回收 → back 后回收装备"""
    calls = {"n": 0}

    def judge(*a, **k):
        calls["n"] += 1
        return dict(_WORTHY) if calls["n"] == 1 else dict(_JUNK)
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential", judge)
    base = _behavior_base(tune=TuneBehavior(
        enabled=True,
        rules=[BehaviorRule(ratings=["junk"], action="reset")],
        reset_exhausted_action="recycle"))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
                               "tune_affix": "最大外功攻击 100",
                               "tune_tip": "", "reset_tune": "重置调律"}
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
    """背包已满装备（case A）：reset 规则跳过，recycle 命中即回收"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_WORTHY))
    base = _behavior_base(tune=TuneBehavior(
        enabled=True, rules=[BehaviorRule(action="reset"),
                             BehaviorRule(action="recycle")]))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    fp = wf._process_equipment("满词条剑", _equip(5), WEAPON_DETAIL)

    assert fp == ""
    assert len(wf.full_calls) == 1
    # reset 无基线快照被跳过，recycle 规则命中
    assert (WEAPON_DETAIL, "recycle_confirm") in wf.clicks
    items = wf.output["recycled_items"]
    assert len(items) == 1 and items[0]["stage"] == "tune"
    assert not wf.output.get("tuning_reports")


def test_materials_block_no_behavior(monkeypatch):
    """材料不足属阻断：不触发任何行为表（不重置不回收）"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_WORTHY))
    base = TuningBase(
        materials=MaterialSettings(stone_check_enabled=True,
                                   stone_min_count=100, food_rules=[]),
        behavior=BehaviorSettings(
            tune=TuneBehavior(enabled=True, rules=_RECYCLE_ALL)))
    monkeypatch.setattr(auto_tuning, "get_tuning_base", lambda: base)
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律"}
    wf._material_infos = {"material_2": _Stone(count=3)}
    fp = wf._process_equipment("缺石剑", _equip(2, quality="gold",
                                             cap_pct=50), WEAPON_DETAIL)

    assert fp
    assert wf._materials_exhausted
    assert "recycled_items" not in wf.output
    assert (TUNE_SCENE, "reset_tune") not in wf.clicks


def test_reset_remaining_parses():
    """_reset_remaining：括号/斜杠样式均可解，无数字 = 用尽返 0"""
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"reset_tune": "重置调律(3)"}
    assert wf._reset_remaining() == 3
    wf._ocr_map[TUNE_SCENE] = {"reset_tune": "重置调律 2/3"}
    assert wf._reset_remaining() == 2
    wf._ocr_map[TUNE_SCENE] = {"reset_tune": "重置调律"}
    assert wf._reset_remaining() == 0


def test_recycle_refill_reprocesses_slot():
    """回收后有格位信息 → 重读同格续处理补位装备"""
    wf = FakeWF()
    outcomes = [("", True), ("fp_b", False)]
    names: list[str] = []
    wf._process_equipment_once = \
        lambda name, equip, scene: (names.append(name), outcomes.pop(0))[1]
    wf._read_row = lambda scene, row, col=1: ("补位剑", "FB", {"n": 1})
    fp = wf._process_equipment("原剑", {"n": 0}, WEAPON_DETAIL, row=2)

    assert fp == "fp_b"
    assert names == ["原剑", "补位剑"]


def test_recycle_refill_empty_slot_ends():
    """回收后重读同格为空 → 背包尽头，返回空指纹"""
    wf = FakeWF()
    wf._process_equipment_once = lambda *a: ("", True)
    wf._read_row = lambda scene, row, col=1: ("", "", {})
    assert wf._process_equipment("原剑", {"n": 0}, WEAPON_DETAIL,
                                 row=1) == ""


def test_recycle_without_row_returns_empty():
    """无格位信息（row=None）→ 回收后无法回读，返回空指纹"""
    wf = FakeWF()
    wf._process_equipment_once = lambda *a: ("", True)
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

    def _process_equipment(self, name, equip, detail_scene,
                           row=None, col=1):
        self.processed.append(name)
        if name in self.recycled_names:
            self._equipment_recycled = True
            # 模拟回收后背包补位：移除当前格，后续列前移
            if row is not None and col is not None:
                self.cell_map.pop((row, col), None)
                for c in range(col + 1, 10):
                    item = self.cell_map.pop((row, c), None)
                    if item is None:
                        break
                    self.cell_map[(row, c - 1)] = item
            return ""   # 回收后该格已空（由上层重读判断）
        self._equipment_recycled = False
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
        monkeypatch.setattr(auto_tuning, "get_tuning_base",
                            lambda: TuningBase())
        monkeypatch.setattr(
            auto_tuning, "judge_equipment_potential",
            lambda equip_data, *a, **k: dict(
                _WORTHY if equip_data.name == "好剑" else _JUNK))
        wf = FakeWF()
        wf.ctx.doc_username = "小明"
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
        items = AutoTuningWorkflow._summary_items(tuned)
        assert [i["name"] for i in items] == ["顶级剑", "一般剑", "双规则剑"]
        assert items[0]["rating_text"] == "血河：顶级"
        # 双规则：最高档优秀入选，rating_text 罗列全部适用规则
        assert items[2]["rating_text"] == "血河：垃圾；会意：优秀"

    def test_doc_none_when_not_opened(self, monkeypatch):
        """未走 run()/_open_doc（_doc 为 None）时各插桩点静默跳过"""
        monkeypatch.setattr(auto_tuning, "get_tuning_base",
                            lambda: TuningBase())
        monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                            lambda *a, **k: dict(_WORTHY))
        wf = FakeWF()
        wf._ocr_map[TUNE_SCENE] = {
            "auto_add": "一键添加", "auto_add_2": "", "tune_btn": "调律",
            "tune_affix": "最大外功攻击 100", "tune_tip": ""}
        wf._process_equipment("待调剑", _equip(2), WEAPON_DETAIL)
        assert wf.output["tuning_reports"][0]["status"] == "tuned"

    def test_open_doc_failure_degrades(self, monkeypatch):
        """文档创建失败（OSError）→ 只警告，_doc 置 None，流程不中断"""
        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(auto_tuning, "TuningDocWriter", boom)
        wf = FakeWF()
        wf._open_doc(["main_weapon"])
        assert wf._doc is None
        wf._close_doc()   # 幂等，不抛


class TestResolveSelectedSlots:
    """调律部位解析：设备端（ctx 未注入）回退读插件会话

    设备端经 task_runner 启动 auto_tuning 时 run_ctx 为默认实例
    （selected_slots=None）；部位必须从插件会话 tuning.selected_slots
    回退读取，否则配置页保存的部位不生效（恒按全部 8 部位）。
    """

    @pytest.fixture
    def session(self, tmp_path, monkeypatch):
        import lvjiang.apps.yysls.plugin_session as ps_module
        from lvjiang.apps.yysls.plugin_session import PluginSession
        sess = PluginSession(tmp_path / "session.json")
        monkeypatch.setattr(ps_module, "_session", sess)
        return sess

    def _device_wf(self):
        wf = FakeWF()
        wf.run_ctx = TuningRunContext()  # selected_slots=None，模拟设备端未注入
        return wf

    def test_device_reads_session(self, session):
        session.set_section("tuning", {"selected_slots": ["ring", "head"]})
        assert self._device_wf()._resolve_selected_slots() == ["ring", "head"]

    def test_empty_session_falls_back_to_all(self, session):
        wf = self._device_wf()
        assert wf._resolve_selected_slots() == (
            wf.WEAPON_SLOTS + wf.ARMOR_SLOTS)

    def test_injected_ctx_ignores_session(self, session):
        session.set_section("tuning", {"selected_slots": ["ring"]})
        wf = self._device_wf()
        wf.run_ctx = TuningRunContext(selected_slots=["main_weapon"])  # UI 已注入
        assert wf._resolve_selected_slots() == ["main_weapon"]

    def test_unknown_slot_keys_dropped(self, session):
        session.set_section("tuning",
                            {"selected_slots": ["ring", "bogus"]})
        assert self._device_wf()._resolve_selected_slots() == ["ring"]
