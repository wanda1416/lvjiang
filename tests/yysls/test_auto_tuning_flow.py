"""auto_tuning 端到端链路的分支覆盖测试

用 FakeWF 覆写场景交互原语（click_region/ocr_scene/ocr_scene_by/
recognize_materials_by/wait_delay）并 spy 空接口，monkeypatch 模块级判定
函数（judge_tuning_worthiness/judge_equipment_potential），驱动
_process_equipment 的各分支：already_full / junk_blank / no_tune_entry /
tuned（含材料不足提前结束），不依赖真实规则与真实 OCR。
"""

import pytest

from src.apps.yysls.workflows.implementations import auto_tuning
from src.apps.yysls.workflows.implementations.auto_tuning import (
    AutoTuningWorkflow,
)

WEAPON_DETAIL = AutoTuningWorkflow.WEAPON_DETAIL
TUNE_SCENE = AutoTuningWorkflow.TUNE_SCENE
RESULT_SCENE = AutoTuningWorkflow.RESULT_SCENE


class FakeWF(AutoTuningWorkflow):
    """不走 BaseWorkflow.__init__ 的测试替身，记录调用并脚本化识别响应"""

    def __init__(self):
        self.output = {}
        self._judge_configs = {}
        self._judge_schools = []
        self._stopped = False
        self.clicks: list[tuple[str, str]] = []
        self.junk_calls: list = []
        self.done_calls: list = []
        self._ocr_map: dict[str, dict] = {}
        self._material_result: dict[str, str] = {}
        self._nav_tune_ok = True

    @property
    def is_stopped(self) -> bool:
        return self._stopped

    def wait_delay(self, name: str):
        pass

    def click_region(self, scene_key, field_key, jitter: bool = True):
        self.clicks.append((scene_key, field_key))

    def ocr_scene(self, scene_key, field_keys=None):
        return dict(self._ocr_map.get(scene_key, {}))

    def ocr_scene_by(self, scene_key, field_keys, target_value, mode):
        return "sub_func_1" if self._nav_tune_ok else ""

    def recognize_materials_by(self, scene_key, field_keys, target_value,
                               mode, group=None):
        return self._material_result.get(target_value, "")

    def _collect_new_affix(self, equip_data, text):
        # 循环词条计数由 _process_equipment 本地维护，测试无需真实解析
        pass

    def _on_junk_blank(self, equip_data, logs):
        self.junk_calls.append(equip_data)

    def _on_equipment_done(self, equip_data, judgement, report):
        self.done_calls.append((equip_data, judgement, report))


def _equip(affix_count: int, quality: str = "gold", cap_pct: int = 50) -> dict:
    """构造装备 dict：affix_count 决定词条数，quality/cap_pct 影响狗粮策略"""
    d: dict = {
        "type": "剑", "name": "测试剑", "level": 110, "quality": quality,
        "_extra": {"affix_count": affix_count},
    }
    d["affix_1"] = {"name": "最大外功攻击", "value": 100, "cap_pct": cap_pct}
    for i in range(2, affix_count + 1):
        d[f"affix_{i}"] = {"name": "劲", "value": 10}
    return d


@pytest.fixture
def patch_worth(monkeypatch):
    """默认：值得调律 + 终局判定返回空 dict"""
    monkeypatch.setattr(auto_tuning, "judge_tuning_worthiness",
                        lambda *a, **k: (True, ["值得"]))
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: {})


def test_already_full(monkeypatch):
    """词条满 → already_full，不进调律，调 _on_equipment_done"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: {"s": {"name": "x", "rating": "顶级",
                                               "skipped": False,
                                               "not_applicable": False,
                                               "reasons": []}})
    wf = FakeWF()
    wf._process_equipment("满词条剑", _equip(5), WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert len(reports) == 1
    assert reports[0]["status"] == "already_full"
    assert reports[0]["final_judgement"]
    assert len(wf.done_calls) == 1
    # 未进入调律导航
    assert (WEAPON_DETAIL, "more_func") not in wf.clicks
    assert not wf.junk_calls


def test_junk_blank(monkeypatch):
    """潜力判定不值得 → junk_blank，调 _on_junk_blank，不进调律"""
    monkeypatch.setattr(auto_tuning, "judge_tuning_worthiness",
                        lambda *a, **k: (False, ["不值得"]))
    wf = FakeWF()
    wf._process_equipment("垃圾胚子", _equip(2), WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "junk_blank"
    assert reports[0]["worthiness"] == ["不值得"]
    assert len(wf.junk_calls) == 1
    assert not wf.done_calls
    assert (WEAPON_DETAIL, "more_func") not in wf.clicks


def test_no_tune_entry(patch_worth):
    """值得但未找到调律入口 → no_tune_entry，不回报完成"""
    wf = FakeWF()
    wf._nav_tune_ok = False
    wf._process_equipment("无入口剑", _equip(2), WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "no_tune_entry"
    assert not wf.done_calls
    assert (TUNE_SCENE, "back") not in wf.clicks
    # 「更多」弹窗已开却无调律按钮：再点一次收起 → more_func 共 2 次
    assert wf.clicks.count((WEAPON_DETAIL, "more_func")) == 2


def test_worth_tuned_to_full(patch_worth):
    """值得 → 调律循环到 5 条 → tuned + 返回 back + _on_equipment_done"""
    wf = FakeWF()
    # gold + cap_pct 50 → 不加狗粮，_tune_once 走无材料路径
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": ""}
    wf._ocr_map[RESULT_SCENE] = {"tune_affix": "最大外功攻击 100", "tune_tip": ""}
    wf._process_equipment("待调剑", _equip(2, quality="gold", cap_pct=50),
                          WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "tuned"
    assert reports[0]["rounds"] == 3          # 2 → 5，共 3 轮
    assert reports[0]["final_affix_count"] == 5
    assert (TUNE_SCENE, "back") in wf.clicks   # 单次 back 返回背包页
    # back 回背包后再点一次「更多」收起弹窗 → more_func 共 2 次（展开 + 收起）
    assert wf.clicks.count((WEAPON_DETAIL, "more_func")) == 2
    assert len(wf.done_calls) == 1
    assert len(wf.output.get("tune_results", [])) == 3


def test_material_shortage_stops(patch_worth):
    """紫色需加紫狗粮但材料不足 → _tune_once 返回 None，rounds=0 提前结束"""
    wf = FakeWF()
    wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": ""}
    wf._material_result = {}   # 任何狗粮都识别不到 → 材料不足
    wf._process_equipment("缺料剑", _equip(2, quality="purple", cap_pct=50),
                          WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "tuned"
    assert reports[0]["rounds"] == 0
    assert (TUNE_SCENE, "back") in wf.clicks
    assert wf.clicks.count((WEAPON_DETAIL, "more_func")) == 2
    assert len(wf.done_calls) == 1


def test_ensure_judge_config_keeps_injected():
    """已注入 _judge_configs 时 _ensure_judge_config 不覆盖"""
    wf = FakeWF()
    wf._judge_configs = {"huiyi": {"enabled": True}}
    wf._judge_schools = ["huiyi"]
    wf._ensure_judge_config()
    assert wf._judge_schools == ["huiyi"]
