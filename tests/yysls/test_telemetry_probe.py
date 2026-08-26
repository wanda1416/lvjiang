"""调律事件采集探针：会话生命周期、整条丢弃规则、永不中断契约，
以及本文件最关键的一条——payload 里绝不出现任何 PII。
"""
from __future__ import annotations

import json

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.equip_parser.models import Affix, EquipmentData
from lvjiang.apps.yysls.telemetry import probe, vocab
from lvjiang.core.config.session import reset_session_store
from lvjiang.core.telemetry import consent
from lvjiang.core.telemetry import spool as spool_mod


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    from lvjiang import constants
    monkeypatch.setattr(constants, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "config" / "session" / "session.json")
    reset_session_store()
    probe.abort_session()      # 清掉上一个用例可能遗留的在途会话
    yield
    probe.abort_session()
    reset_session_store()


@pytest.fixture
def enabled():
    consent.record_consent_choice(True)


@pytest.fixture
def sample():
    gc = get_game_config()
    return {
        "affix_name": gc.get_normal_affix_names()[0],
        "other_affix": gc.get_normal_affix_names()[1],
        "weapon_type": gc.get_weapon_types()[0],
    }


def _equip(sample, **kw):
    kw.setdefault("level", 110)
    kw.setdefault("quality", "gold")
    return EquipmentData(type=sample["weapon_type"], **kw)


def _affix(name, cap_pct=50.0, transferred=False):
    a = Affix(name=name, value=10.0, cap_pct=cap_pct)
    a.is_transferred = transferred
    return a


def _run_session(sample, *, rolls, initial=(), stop_reason="completed",
                 final_rating=None, resets=0):
    """跑完一件装备的完整采集生命周期。``rolls`` 是 (affix, food) 序列。"""
    probe.begin_session(equip_data=_equip(sample), initial_affixes=list(initial),
                        mode="normal", rule_keys=[])
    for i, (affix, food) in enumerate(rolls, start=2):
        probe.record_roll(new_affix=affix, slot=min(i, 5), resets=resets,
                          food_label=food)
    probe.end_session(stop_reason=stop_reason, final_rating=final_rating,
                      total_rounds=len(rolls), resets=resets)


def _events():
    spool_mod.flush()
    batches = spool_mod.take_batches(10)
    return [e for b in batches for e in b.events]


class TestDisabledMeansNoLocalDataEither:
    """未同意/关闭统计时，连本地缓冲都不该产生——不只是不发网络请求。"""

    def test_no_spool_entry_when_disabled(self, sample):
        for _ in range(10):
            _run_session(sample, rolls=[(_affix(sample["affix_name"]), "")])
        assert _events() == []


class TestValidSessionIsRecorded:
    def test_full_field_mapping(self, enabled, sample):
        from lvjiang.apps.yysls.core.tuning_rules.models import FOOD_LABELS
        _run_session(
            sample,
            initial=[_affix(sample["affix_name"], 88.2)],
            rolls=[(_affix(sample["other_affix"], 42.0), ""),
                   (_affix(sample["other_affix"], 43.0), FOOD_LABELS[0])],
            stop_reason="decided_recycle", final_rating="优秀")
        events = _events()
        assert len(events) == 1
        e = events[0]
        assert e["schema"] == "yysls.tuning_session"
        assert e["part"] == vocab.normalize_part(sample["weapon_type"])
        assert e["level"] == 110 and e["quality"] == "gold"
        assert e["stop_reason"] == "decided_recycle"
        assert e["final_rating"] == "excellent"       # 中文显示名 → ascii 档位 key
        assert [r["food"] for r in e["rolls"]] == ["none", "gold"]
        assert len(e["initial_affixes"]) == 1 and len(e["rolls"]) == 2

    def test_roll_order_is_preserved(self, enabled, sample):
        """序列顺序就是分析条件概率的全部价值，必须与调用顺序一致。"""
        caps = [11.0, 22.0, 33.0, 44.0]
        _run_session(sample, rolls=[(_affix(sample["affix_name"], c), "") for c in caps])
        assert [r["cap_pct"] for r in _events()[0]["rolls"]] == caps

    def test_active_rule_keeps_known_drops_unknown(self, enabled, sample):
        from lvjiang.apps.yysls.core.tuning_rules import get_tuning_rule_manager
        known = sorted(get_tuning_rule_manager().get_rules().keys())[:1]
        probe.begin_session(equip_data=_equip(sample), initial_affixes=[],
                            mode="normal", rule_keys=[*known, "definitely_not_a_rule"])
        probe.record_roll(new_affix=_affix(sample["affix_name"]), slot=2,
                          resets=0, food_label="")
        probe.end_session(stop_reason="completed", final_rating=None,
                          total_rounds=1, resets=0)
        assert _events()[0]["active_rule"] == ("+".join(known) if known else "none")

    def test_no_active_rule_is_none(self, enabled, sample):
        _run_session(sample, rolls=[(_affix(sample["affix_name"]), "")])
        assert _events()[0]["active_rule"] == "none"

    def test_unknown_stop_reason_falls_back_not_drops(self, enabled, sample):
        """stop_reason 是元数据：个别退出点漏登记不该让整条词条序列作废。"""
        _run_session(sample, rolls=[(_affix(sample["affix_name"]), "")],
                     stop_reason="某个没登记的原因")
        assert _events()[0]["stop_reason"] == "completed"


class TestWholeSessionDroppedNotPartial:
    """任何一轮不合法 → **整条**作废，而不是只丢那一轮。

    这条是分析正确性要求，不只是隐私：序列里挖个洞之后，第 4 轮会被下游
    当成紧跟第 2 轮，条件概率直接算错。宁可少一条，不要一条错的。
    """

    def test_parse_failure_drops_whole_session(self, enabled, sample):
        good = _affix(sample["affix_name"])
        _run_session(sample, rolls=[(good, ""), (None, ""), (good, "")])
        assert _events() == []

    def test_unrecognized_affix_name_drops_whole_session(self, enabled, sample):
        """模拟 WUXUE_PATTERN 那条路径漏出的 OCR 文本：拼出来的名字进不了白名单。"""
        _run_session(sample, rolls=[(_affix(sample["affix_name"]), ""),
                                    (_affix("张三武学增伤"), "")])
        assert _events() == []

    def test_unknown_food_label_drops_whole_session(self, enabled, sample):
        _run_session(sample, rolls=[(_affix(sample["affix_name"]), "某种没见过的狗粮")])
        assert _events() == []

    def test_bad_initial_affix_drops_whole_session(self, enabled, sample):
        _run_session(sample, initial=[_affix("张三武学增伤")],
                     rolls=[(_affix(sample["affix_name"]), "")])
        assert _events() == []

    def test_unknown_equip_type_dropped(self, enabled, sample):
        probe.begin_session(equip_data=EquipmentData(type="不存在的部位", level=110),
                            initial_affixes=[], mode="normal", rule_keys=[])
        probe.record_roll(new_affix=_affix(sample["affix_name"]), slot=2,
                          resets=0, food_label="")
        probe.end_session(stop_reason="completed", final_rating=None,
                          total_rounds=1, resets=0)
        assert _events() == []

    def test_missing_level_dropped(self, enabled, sample):
        probe.begin_session(equip_data=EquipmentData(type=sample["weapon_type"], level=0),
                            initial_affixes=[], mode="normal", rule_keys=[])
        probe.record_roll(new_affix=_affix(sample["affix_name"]), slot=2,
                          resets=0, food_label="")
        probe.end_session(stop_reason="completed", final_rating=None,
                          total_rounds=1, resets=0)
        assert _events() == []

    def test_zero_roll_session_not_recorded(self, enabled, sample):
        """一轮都没调（初始判定即跳过）——没有统计价值，不落盘。"""
        probe.begin_session(equip_data=_equip(sample), initial_affixes=[],
                            mode="normal", rule_keys=[])
        probe.end_session(stop_reason="judged_before_tuning", final_rating=None,
                          total_rounds=0, resets=0)
        assert _events() == []

    def test_unrecognized_quality_omitted_not_rejected(self, enabled, sample):
        """品阶认不出只省略该字段，不该连累整条——它不是主载荷。"""
        probe.begin_session(equip_data=_equip(sample, quality="珍珠白"),
                            initial_affixes=[], mode="normal", rule_keys=[])
        probe.record_roll(new_affix=_affix(sample["affix_name"]), slot=2,
                          resets=0, food_label="")
        probe.end_session(stop_reason="completed", final_rating=None,
                          total_rounds=1, resets=0)
        events = _events()
        assert len(events) == 1 and "quality" not in events[0]


class TestSessionIsolation:
    def test_new_session_discards_previous_in_flight(self, enabled, sample):
        """上一件没正常收尾时，下一件开场必须丢掉它，不能把两件的轮次串起来。"""
        probe.begin_session(equip_data=_equip(sample), initial_affixes=[],
                            mode="normal", rule_keys=[])
        probe.record_roll(new_affix=_affix(sample["affix_name"], 11.0), slot=2,
                          resets=0, food_label="")
        # 没有 end_session，直接开下一件
        _run_session(sample, rolls=[(_affix(sample["other_affix"], 99.0), "")])
        events = _events()
        assert len(events) == 1
        assert [r["cap_pct"] for r in events[0]["rolls"]] == [99.0]

    def test_roll_without_session_is_noop(self, enabled, sample):
        probe.abort_session()
        probe.record_roll(new_affix=_affix(sample["affix_name"]), slot=2,
                          resets=0, food_label="")
        probe.end_session(stop_reason="completed", final_rating=None,
                          total_rounds=1, resets=0)
        assert _events() == []


class TestNeverRaises:
    def test_survives_internal_exception(self, enabled, sample, monkeypatch):
        monkeypatch.setattr(vocab, "normalize_part",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        _run_session(sample, rolls=[(_affix(sample["affix_name"]), "")])  # 不抛即通过
        assert _events() == []

    def test_keyboard_interrupt_propagates(self, enabled, sample, monkeypatch):
        monkeypatch.setattr(vocab, "normalize_part",
                            lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            probe.begin_session(equip_data=_equip(sample), initial_affixes=[],
                                mode="normal", rule_keys=[])


class TestNoPII:
    """PII 冒烟测试：构造一件带装备名/指纹/定音词条的装备，
    断言上报事件里一个字符都不出现。
    """

    def test_forbidden_strings_never_appear(self, enabled, sample):
        import pathlib

        gc = get_game_config()
        names = gc.get_normal_affix_names()
        equip = EquipmentData(
            type=sample["weapon_type"], name="踏雪含光·绝版限定",
            level=110, quality="gold",
            affixes=[Affix(name=names[0], value=100.0, cap_pct=99.9)],
            dingyin={"name": "外功穿透", "value": 14.2},
        )
        fp = equip.to_dict()["_fp"]
        probe.begin_session(equip_data=equip, initial_affixes=list(equip.affixes),
                            mode="normal", rule_keys=[])
        probe.record_roll(new_affix=_affix(names[0], 88.8), slot=4,
                          resets=0, food_label="")
        probe.end_session(stop_reason="decided_recycle", final_rating="优秀",
                          total_rounds=1, resets=0)
        events = _events()
        blob = json.dumps(events, ensure_ascii=False)

        forbidden = ["踏雪含光", fp, "外功穿透", "测试虚构角色名九九八",
                     str(pathlib.Path.home())]
        for s in forbidden:
            assert s not in blob, f"PII 泄漏: {s!r} 出现在上报 payload 里"

        for key in ("name", "_fp", "affixes", "dingyin", "extra_data",
                    "warnings", "value", "unit"):
            assert key not in events[0], f"禁止字段 {key!r} 出现在上报 payload 里"

    def test_every_affix_name_comes_from_the_whitelist(self, enabled, sample):
        """**这条取代了旧的「单条事件不可能重建完整词条组合」。**

        按件上报是刻意的设计变更：条件概率与「为什么结束」这两个问题，
        逐轮独立上报的事件永远拼不回来。词条组合因此不再被结构性阻断，
        改由白名单兜底——事件里出现的每一个词条名都必须精确命中普通词条池
        （当前 37 条），任何 OCR 拼出来的字符串都到不了这里。
        """
        pool = set(get_game_config().get_normal_affix_names())
        _run_session(sample,
                     initial=[_affix(sample["affix_name"])],
                     rolls=[(_affix(sample["other_affix"]), "")])
        e = _events()[0]
        seen = [a["affix"] for a in e["initial_affixes"]] + [r["affix"] for r in e["rolls"]]
        assert seen, "样例应至少含一个词条"
        for name in seen:
            assert name in pool, f"词条名 {name!r} 不在普通词条池里"


class TestVocabStructural:
    """归一化只与 game_config 的实时枚举比对，不自建可能脱节的映射表——
    这正是为什么它不受工具 UI 语言影响：比对的是当前进程里实际生效的
    那份常量，而不是本模块里另写一份可能过期的 Chinese literal 表。
    """

    def test_normalize_part_matches_live_group_mapping(self):
        gc = get_game_config()
        for equip_type, group in list(gc.get_type_to_group().items())[:5]:
            assert vocab.normalize_part(equip_type) == group

    def test_normalize_affix_name_matches_live_pool(self):
        gc = get_game_config()
        for name in gc.get_normal_affix_names()[:5]:
            assert vocab.normalize_affix_name(name) == name

    def test_food_labels_match_live_tuning_rules_constant(self):
        from lvjiang.apps.yysls.core.tuning_rules.models import FOOD_LABELS
        expected = dict(zip(FOOD_LABELS, ("gold", "purple", "rainbow"), strict=True))
        for label, key in expected.items():
            assert vocab.normalize_food(label) == key

    def test_rating_keys_match_live_tuning_rules_constant(self):
        from lvjiang.apps.yysls.core.tuning_rules.models import (
            RATING_KEYS,
            RATING_LABELS,
        )
        assert vocab.rating_choices() == tuple(RATING_KEYS)
        for key, label in RATING_LABELS.items():
            assert vocab.normalize_rating(label) == key
