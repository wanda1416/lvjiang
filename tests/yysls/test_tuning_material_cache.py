"""调律材料区缓存的有效期。

一键添加会把小律准石一次性用光，图标随之从材料区消失，它后面的槽位整体
左移一格。缓存里记的是 (row, col)，位移之后旧坐标就指向别的材料——继续
拿它点狗粮等于点错东西。

但这条因果链的第一环存疑——一键添加理论上只消耗大律准石。所以失效机制
以开关形式保留、**默认关闭**（INVALIDATE_CACHE_ON_SMALL_STONE），用例
同时覆盖关（默认，缓存照常跨轮复用）与开（按轮失效）两种状态。
"""

from __future__ import annotations

from lvjiang.apps.yysls.core.recognizer.reference_adapter import TuningMaterial
from lvjiang.apps.yysls.core.tuning_rules import (
    SMALL_STONE_LABEL,
    STONE_LABEL,
)
from lvjiang.apps.yysls.workflows.implementations.tuning import executor as ex_mod
from lvjiang.apps.yysls.workflows.implementations.tuning.executor import (
    TuningExecutor,
)
from lvjiang.core.recognizers.reference_recognizer import ReferenceInfo


class _Settings:
    stone_check_enabled = True
    food_rules = ()


class _Group:
    materials = _Settings()


class _Wf:
    """最小宿主替身：只提供 cache_materials 用到的那几项。"""

    TUNE_SCENE = "equip_tune_detail"
    MATERIAL_PANEL = "materials"
    MATERIAL_GROUP = "调律材料"

    def __init__(self, panels: list[dict]):
        self.base_group = _Group()
        self._panels = list(panels)
        self.recognize_calls = 0

    def recognize_references_info_panel(self, *_a, **_kw):
        self.recognize_calls += 1
        # 用完最后一份就一直返回它，模拟稳定的材料区
        idx = min(self.recognize_calls - 1, len(self._panels) - 1)
        return self._panels[idx]


def _mat(label: str, count: int) -> TuningMaterial:
    return TuningMaterial(label=label, confidence=0.9, count=count,
                          count_recognized=True, devoted=0)


def _panel(*pairs) -> dict:
    return {(1, i): _mat(label, count)
            for i, (label, count) in enumerate(pairs, start=1)}


_WITH_SMALL = _panel((SMALL_STONE_LABEL, 10), (STONE_LABEL, 853),
                     ("紫狗粮", 102))
_WITHOUT_SMALL = _panel((STONE_LABEL, 853), ("紫狗粮", 102))


def _executor(panels) -> tuple[TuningExecutor, _Wf]:
    wf = _Wf(panels)
    ex = TuningExecutor(wf)
    # parse_tuning_material 对已经是领域对象的输入是幂等的这一点不做假设，
    # 直接绕过它：本用例关心的是缓存有效期，不是解析。
    ex._material_cache = None
    return ex, wf


class TestVolatility:
    def test_small_stone_present_marks_cache_volatile(self):
        ex, _ = _executor([_WITH_SMALL])
        assert ex._has_small_stone(_WITH_SMALL) is True

    def test_no_small_stone_is_stable(self):
        ex, _ = _executor([_WITHOUT_SMALL])
        assert ex._has_small_stone(_WITHOUT_SMALL) is False

    def test_exhausted_small_stone_does_not_count(self):
        """数量已经是 0 的槽点不动，也不会再位移。"""
        ex, _ = _executor([_WITHOUT_SMALL])
        assert ex._has_small_stone(_panel((SMALL_STONE_LABEL, 0))) is False

    def test_unrecognized_count_does_not_count(self):
        """数量 OCR 容错成 0 的幽灵槽不能当成真的有货。"""
        ex, _ = _executor([_WITHOUT_SMALL])
        ghost = {(1, 1): TuningMaterial(label=SMALL_STONE_LABEL,
                                        confidence=0.9, count=0,
                                        count_recognized=False)}
        assert ex._has_small_stone(ghost) is False


class TestCacheLifetime:
    def test_ensure_is_idempotent_while_valid(self):
        ex, wf = _executor([_WITHOUT_SMALL])
        ex._material_cache = _WITHOUT_SMALL
        ex._cache_valid = True
        ex._cache_volatile = False

        ex.ensure_materials_cached()
        ex.ensure_materials_cached()

        assert wf.recognize_calls == 0, "缓存仍有效时不该重新识别"

    def test_invalidate_forces_recognition(self):
        ex, wf = _executor([_WITHOUT_SMALL])
        ex._material_cache = _WITHOUT_SMALL
        ex._cache_valid = True

        ex.invalidate_cache()
        assert ex._material_cache is None
        assert ex._cache_valid is False
        assert ex._cache_volatile is False

    def test_reset_state_clears_everything(self):
        ex, _ = _executor([_WITHOUT_SMALL])
        ex._material_cache = _WITH_SMALL
        ex._cache_valid = True
        ex._cache_volatile = True

        ex.reset_state()

        assert ex._material_cache is None
        assert ex._cache_valid is False
        assert ex._cache_volatile is False


# ─── 走完整识别路径 ────────────────────────────────────────


def _info(label: str, count: str) -> ReferenceInfo:
    return ReferenceInfo(label=label, group="调律材料", confidence=0.9,
                         ocr_texts={"count_text": count, "level_text": ""})


class TestCacheMaterialsIntegration:
    """cache_materials 经真实 parse_tuning_material 后的易失判定。"""

    def test_default_keeps_cache_even_with_small_stone(self):
        """默认关闭：小律准石在场也照常跨轮复用缓存，不退化成每轮 OCR。"""
        assert ex_mod.INVALIDATE_CACHE_ON_SMALL_STONE is False
        wf = _Wf([{(1, 1): _info(SMALL_STONE_LABEL, "10"),
                   (1, 2): _info(STONE_LABEL, "853")}])
        ex = TuningExecutor(wf)

        ex.cache_materials()
        ex.ensure_materials_cached()

        assert ex._cache_volatile is False
        assert wf.recognize_calls == 1, "开关关闭时不该反复识别"

    def test_marks_volatile_when_switch_enabled(self, monkeypatch):
        monkeypatch.setattr(ex_mod, "INVALIDATE_CACHE_ON_SMALL_STONE", True)
        wf = _Wf([{(1, 1): _info(SMALL_STONE_LABEL, "10"),
                   (1, 2): _info(STONE_LABEL, "853")}])
        ex = TuningExecutor(wf)

        ex.cache_materials()

        assert ex._cache_valid is True
        assert ex._cache_volatile is True, "开关打开时小律准石在场应只用一轮"

    def test_stable_when_small_stone_absent(self):
        wf = _Wf([{(1, 1): _info(STONE_LABEL, "853"),
                   (1, 2): _info("紫狗粮", "102")}])
        ex = TuningExecutor(wf)

        ex.cache_materials()

        assert ex._cache_valid is True
        assert ex._cache_volatile is False

    def test_volatile_cache_is_refreshed_next_round(self, monkeypatch):
        """开关打开时：易失缓存失效后，下一轮必须重新识别到位移后的槽位。"""
        monkeypatch.setattr(ex_mod, "INVALIDATE_CACHE_ON_SMALL_STONE", True)
        before = {(1, 1): _info(SMALL_STONE_LABEL, "10"),
                  (1, 2): _info(STONE_LABEL, "853"),
                  (1, 3): _info("紫狗粮", "102")}
        after = {(1, 1): _info(STONE_LABEL, "853"),   # 小律准石耗尽，整体左移
                 (1, 2): _info("紫狗粮", "102")}
        wf = _Wf([before, after])
        ex = TuningExecutor(wf)

        ex.cache_materials()
        assert ex._cache_volatile is True
        assert [i.label for i in ex._material_cache.values()][0] == SMALL_STONE_LABEL

        ex.invalidate_cache()          # tune_once 在一键添加后做的事
        ex.ensure_materials_cached()

        assert wf.recognize_calls == 2
        labels = {slot: i.label for slot, i in ex._material_cache.items()}
        assert labels == {(1, 1): STONE_LABEL, (1, 2): "紫狗粮"}
        assert ex._cache_volatile is False, "小律准石没了，缓存重新可复用"
