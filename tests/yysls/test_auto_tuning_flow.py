"""auto_tuning 端到端链路的分支覆盖测试

用 FakeWF 覆写场景交互原语（click_region/ocr_scene/ocr_scene_by/
recognize_materials_by/wait_delay）并 spy 空接口，monkeypatch 模块级
判定函数 judge_equipment_potential（结构化结果，worth 由真实的
summarize_potential 归纳），驱动 _process_equipment 的各分支：
already_full / junk_blank / no_tune_entry / tuned（含材料不足提前
结束），不依赖真实规则与真实 OCR。
"""

import pytest

from src.apps.yysls.workflows.implementations import auto_tuning
from src.apps.yysls.workflows.implementations.auto_tuning import (
    AutoTuningWorkflow,
)
from src.apps.yysls.workflows.implementations.bag_traversal import (
    PositionalTraversal,
    ScrollState,
)

WEAPON_DETAIL = AutoTuningWorkflow.WEAPON_DETAIL
TUNE_SCENE = AutoTuningWorkflow.TUNE_SCENE
RESULT_SCENE = AutoTuningWorkflow.RESULT_SCENE

# judge_equipment_potential 的结构化结果样本：命中顶级 / 判为垃圾
_WORTHY = {"s": {"name": "血河", "rating": "顶级", "skipped": False,
                 "not_applicable": False, "reasons": ["词条匹配"]}}
_JUNK = {"s": {"name": "血河", "rating": "垃圾", "skipped": False,
               "not_applicable": False, "reasons": ["词条不符"]}}


class FakeWF(AutoTuningWorkflow):
    """不走 BaseWorkflow.__init__ 的测试替身，记录调用并脚本化识别响应"""

    def __init__(self):
        self.output = {}
        self._judge_configs = {}
        self._judge_rule_keys = []
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
    """词条满 → 不进调律，调 _on_equipment_done，未处理过不收集 report"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: {"s": {"name": "x", "rating": "顶级",
                                               "skipped": False,
                                               "not_applicable": False,
                                               "reasons": []}})
    wf = FakeWF()
    wf._process_equipment("满词条剑", _equip(5), WEAPON_DETAIL)

    assert not wf.output.get("tuning_reports")
    assert len(wf.done_calls) == 1
    # 钩子仍能拿到完整 report（含终局判定）
    assert wf.done_calls[0][2]["status"] == "already_full"
    assert wf.done_calls[0][2]["final_judgement"]
    # 未进入调律导航
    assert (WEAPON_DETAIL, "more_func") not in wf.clicks
    assert not wf.junk_calls


def test_junk_blank(monkeypatch):
    """潜力判定不值得 → 调 _on_junk_blank，不进调律，不收集 report"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    wf = FakeWF()
    wf._process_equipment("垃圾胚子", _equip(2), WEAPON_DETAIL)

    assert not wf.output.get("tuning_reports")
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
    # 每轮调律结果挂在本件 report 下，与装备一一对应（不再全局平铺）
    assert len(reports[0]["tune_results"]) == 3
    assert "tune_results" not in wf.output


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
    wf._judge_rule_keys = ["huiyi"]
    wf._ensure_judge_config()
    assert wf._judge_rule_keys == ["huiyi"]


def test_skip_tuning_switch(patch_worth):
    """跳过实际调律开关：值得调律的装备才真实进出调律页但不调律"""
    wf = FakeWF()
    wf._skip_tuning = True
    wf._process_equipment("测试剑", _equip(2), WEAPON_DETAIL)

    reports = wf.output["tuning_reports"]
    assert reports[0]["status"] == "skip_tuning"
    assert reports[0]["worthiness"] == ["血河: 顶级（词条匹配）"]   # 潜力判定正常执行
    # 装备未被改动：不走垃圾/完成后处理
    assert not wf.junk_calls
    assert not wf.done_calls
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
    wf._skip_tuning = True
    wf._process_equipment("满词条剑", _equip(5), WEAPON_DETAIL)

    assert not wf.output.get("tuning_reports")
    assert len(wf.done_calls) == 1
    assert (WEAPON_DETAIL, "more_func") not in wf.clicks
    assert (TUNE_SCENE, "back") not in wf.clicks


def test_skip_tuning_junk_not_entered(monkeypatch):
    """开关开启但潜力判定不值得 → 仍走垃圾胚子，不进调律页也不收集 report"""
    monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                        lambda *a, **k: dict(_JUNK))
    wf = FakeWF()
    wf._skip_tuning = True
    wf._process_equipment("垃圾胚子", _equip(2), WEAPON_DETAIL)

    assert not wf.output.get("tuning_reports")
    assert len(wf.junk_calls) == 1
    assert (WEAPON_DETAIL, "more_func") not in wf.clicks
    assert (TUNE_SCENE, "back") not in wf.clicks


def test_skip_tuning_no_entry(patch_worth):
    """开关开启且值得但无调律入口 → 仍走 no_tune_entry，不点 back"""
    wf = FakeWF()
    wf._skip_tuning = True
    wf._nav_tune_ok = False
    wf._process_equipment("无入口剑", _equip(2), WEAPON_DETAIL)

    assert wf.output["tuning_reports"][0]["status"] == "no_tune_entry"
    assert not wf.junk_calls
    assert (TUNE_SCENE, "back") not in wf.clicks
    # _nav_to_tune 失败分支自行收起弹窗 → more_func 共 2 次
    assert wf.clicks.count((WEAPON_DETAIL, "more_func")) == 2


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

    def _read_row(self, detail_scene, row, col=1):
        return self.cell_map.get((row, col), ("", "", {}))

    def _process_equipment(self, name, equip, detail_scene):
        self.processed.append(name)
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


class TestTuningDocIntegration:
    """调律说明文档端到端：假流程注入 _doc_dir 后跑通并检查叙事内容"""

    def test_doc_written_only_for_tuned(self, monkeypatch, tmp_path):
        """好剑调律到满写入文档；垃圾剑被跳过完全不出现在文档中"""
        monkeypatch.setattr(
            auto_tuning, "judge_equipment_potential",
            lambda equip_data, *a, **k: dict(
                _WORTHY if equip_data.name == "好剑" else _JUNK))
        wf = FakeWF()
        wf._doc_username = "小明"
        wf._doc_dir = tmp_path
        wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": ""}
        wf._ocr_map[RESULT_SCENE] = {"tune_affix": "最大外功攻击 100",
                                     "tune_tip": ""}

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
        assert "  → 仍可达 顶级/优秀（血河），继续" in text
        assert "  → 词条已满（5/5），调律完成" in text
        assert "本件小结：共 3 轮，词条 5/5，结束原因：词条已满，调律完成" in text
        # 运行小结
        assert "## 运行结束" in text
        assert "（正常完成）" in text
        assert "- 实际调律 1 件，共 3 轮" in text
        # 被判定不值得的装备完全不写
        assert "垃圾剑" not in text

    def test_doc_none_when_not_opened(self, monkeypatch):
        """未走 run()/_open_doc（_doc 为 None）时各插桩点静默跳过"""
        monkeypatch.setattr(auto_tuning, "judge_equipment_potential",
                            lambda *a, **k: dict(_WORTHY))
        wf = FakeWF()
        wf._ocr_map[TUNE_SCENE] = {"auto_add": "一键添加", "auto_add_2": ""}
        wf._ocr_map[RESULT_SCENE] = {"tune_affix": "最大外功攻击 100",
                                     "tune_tip": ""}
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
