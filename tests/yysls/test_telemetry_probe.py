"""调律事件采集探针：record_tuning_roll() 的字段映射正确性、丢弃规则、
永不中断契约，以及本文件最关键的一条——payload 里绝不出现任何 PII。
"""
from __future__ import annotations

import json

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.equip_parser.models import Affix, EquipmentData
from lvjiang.apps.yysls.telemetry import vocab
from lvjiang.apps.yysls.telemetry.probe import record_tuning_roll
from lvjiang.core.config.session import reset_session_store
from lvjiang.core.telemetry import consent
from lvjiang.core.telemetry import spool as spool_mod


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    from lvjiang import constants
    monkeypatch.setattr(constants, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "config" / "session" / "session.json")
    reset_session_store()
    yield
    reset_session_store()


@pytest.fixture
def enabled():
    consent.record_consent_choice(True)


@pytest.fixture
def sample():
    gc = get_game_config()
    return {
        "affix_name": gc.get_normal_affix_names()[0],
        "weapon_type": gc.get_weapon_types()[0],
    }


def _flush_and_take():
    spool_mod.flush()
    return spool_mod.take_batches(10)


class TestDisabledMeansNoLocalDataEither:
    """未同意/关闭统计时，连本地缓冲都不该产生——不只是不发网络请求。"""

    def test_no_spool_entry_when_disabled(self, sample):
        equip = EquipmentData(type=sample["weapon_type"], level=110, quality="gold")
        affix = Affix(name=sample["affix_name"], value=10.0, cap_pct=80.0)
        for _ in range(50):
            record_tuning_roll(
                equip_data=equip, new_affix=affix, slot=1, roll_index=1, resets=0,
                food_label="", mode="normal", rule_keys=[])
        assert _flush_and_take() == []


class TestValidRollIsRecorded(object):
    def test_full_field_mapping(self, enabled, sample):
        equip = EquipmentData(type=sample["weapon_type"], level=110, quality="gold")
        affix = Affix(name=sample["affix_name"], value=12.3, cap_pct=91.5, is_transferred=True)
        record_tuning_roll(
            equip_data=equip, new_affix=affix, slot=3, roll_index=7, resets=2,
            food_label="", mode="force_tune", rule_keys=["heal_fire"])
        events = _flush_and_take()[0].events
        assert len(events) == 1
        e = events[0]
        assert e["part"] == "weapon"
        assert e["weapon_type"] == sample["weapon_type"]
        assert e["level"] == 110
        assert e["quality"] == "gold"
        assert e["food"] == "none"
        assert e["slot"] == 3
        assert e["roll_index"] == 7
        assert e["resets"] == 2
        assert e["mode"] == "force_tune"
        assert e["affix"] == sample["affix_name"]
        assert e["cap_pct"] == 91.5
        assert e["is_transferred"] is True
        assert len(e["install_id"]) == 32
        assert "active_rule" in e

    def test_active_rule_keeps_known_drops_unknown(self, enabled, sample):
        from lvjiang.apps.yysls.core.tuning_rules import get_tuning_rule_manager
        known = sorted(get_tuning_rule_manager().get_rules().keys())
        if not known:
            pytest.skip("本机没有可用的调律规则清单")
        equip = EquipmentData(type=sample["weapon_type"], level=110, quality="gold")
        affix = Affix(name=sample["affix_name"], value=1.0, cap_pct=1.0)
        record_tuning_roll(
            equip_data=equip, new_affix=affix, slot=1, roll_index=1, resets=0,
            food_label="", mode="normal",
            rule_keys=[known[0], "__definitely_not_a_real_rule__"])
        e = _flush_and_take()[0].events[0]
        assert e["active_rule"] == known[0]

    def test_no_active_rule_is_none(self, enabled, sample):
        equip = EquipmentData(type=sample["weapon_type"], level=110, quality="gold")
        affix = Affix(name=sample["affix_name"], value=1.0, cap_pct=1.0)
        record_tuning_roll(
            equip_data=equip, new_affix=affix, slot=1, roll_index=1, resets=0,
            food_label="", mode="normal", rule_keys=[])
        assert _flush_and_take()[0].events[0]["active_rule"] == "none"


class TestDroppedRatherThanGuessed:
    """OCR 误读/未知场景一律丢弃整条事件，不产出 "unknown" 兜底值。"""

    def test_parse_failure_dropped(self, enabled, sample):
        equip = EquipmentData(type=sample["weapon_type"], level=110)
        record_tuning_roll(
            equip_data=equip, new_affix=None, slot=1, roll_index=1, resets=0,
            food_label="", mode="normal", rule_keys=[])
        assert _flush_and_take() == []

    def test_unrecognized_affix_name_dropped(self, enabled, sample):
        equip = EquipmentData(type=sample["weapon_type"], level=110)
        bad = Affix(name="绝对不存在于配置里的乱码词条ABCXYZ", value=1.0, cap_pct=1.0)
        record_tuning_roll(
            equip_data=equip, new_affix=bad, slot=1, roll_index=1, resets=0,
            food_label="", mode="normal", rule_keys=[])
        assert _flush_and_take() == []

    def test_unknown_equip_type_dropped(self, enabled, sample):
        equip = EquipmentData(type="不是任何已知装备类型的乱码", level=110)
        affix = Affix(name=sample["affix_name"], value=1.0, cap_pct=1.0)
        record_tuning_roll(
            equip_data=equip, new_affix=affix, slot=1, roll_index=1, resets=0,
            food_label="", mode="normal", rule_keys=[])
        assert _flush_and_take() == []

    def test_unknown_food_label_dropped(self, enabled, sample):
        equip = EquipmentData(type=sample["weapon_type"], level=110)
        affix = Affix(name=sample["affix_name"], value=1.0, cap_pct=1.0)
        record_tuning_roll(
            equip_data=equip, new_affix=affix, slot=1, roll_index=1, resets=0,
            food_label="某种识别不出来的乱码材料标签", mode="normal", rule_keys=[])
        assert _flush_and_take() == []

    def test_missing_level_dropped(self, enabled, sample):
        equip = EquipmentData(type=sample["weapon_type"], level=None)
        affix = Affix(name=sample["affix_name"], value=1.0, cap_pct=1.0)
        record_tuning_roll(
            equip_data=equip, new_affix=affix, slot=1, roll_index=1, resets=0,
            food_label="", mode="normal", rule_keys=[])
        assert _flush_and_take() == []

    def test_unrecognized_quality_omitted_not_rejected(self, enabled, sample):
        """品阶不是主载荷，识别不出就整键省略，不丢弃整条事件。"""
        equip = EquipmentData(type=sample["weapon_type"], level=110, quality="不认识的品阶")
        affix = Affix(name=sample["affix_name"], value=1.0, cap_pct=1.0)
        record_tuning_roll(
            equip_data=equip, new_affix=affix, slot=1, roll_index=1, resets=0,
            food_label="", mode="normal", rule_keys=[])
        events = _flush_and_take()[0].events
        assert len(events) == 1
        assert "quality" not in events[0]


class TestNeverRaises:
    def test_survives_internal_exception(self, enabled, sample, monkeypatch):
        monkeypatch.setattr(vocab, "normalize_part", lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
        equip = EquipmentData(type=sample["weapon_type"], level=110)
        affix = Affix(name=sample["affix_name"], value=1.0, cap_pct=1.0)
        # 不抛异常即通过
        record_tuning_roll(
            equip_data=equip, new_affix=affix, slot=1, roll_index=1, resets=0,
            food_label="", mode="normal", rule_keys=[])

    def test_keyboard_interrupt_propagates(self, enabled, sample, monkeypatch):
        monkeypatch.setattr(vocab, "normalize_part",
                            lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        equip = EquipmentData(type=sample["weapon_type"], level=110)
        affix = Affix(name=sample["affix_name"], value=1.0, cap_pct=1.0)
        with pytest.raises(KeyboardInterrupt):
            record_tuning_roll(
                equip_data=equip, new_affix=affix, slot=1, roll_index=1, resets=0,
                food_label="", mode="normal", rule_keys=[])


class TestNoPII:
    """PII 冒烟测试：构造一件带真实姓名/指纹/其余四条词条的装备，
    断言上报事件里一个字符都不出现，且没有能重建完整词条组合的字段。
    """

    def test_forbidden_strings_never_appear(self, enabled, sample):
        gc = get_game_config()
        names = gc.get_normal_affix_names()
        equip = EquipmentData(
            type=sample["weapon_type"], name="踏雪含光·绝版限定",
            level=110, quality="gold",
            affixes=[
                Affix(name=names[0], value=100.0, cap_pct=99.9),
                Affix(name=names[1] if len(names) > 1 else names[0], value=50.0, cap_pct=50.0),
                Affix(name=names[0], value=1.0, cap_pct=1.0),
            ],
            dingyin={"name": "外功穿透", "value": 14.2},
        )
        fp = equip.to_dict()["_fp"]
        new_affix = Affix(name=names[0], value=77.7, cap_pct=88.8)
        record_tuning_roll(
            equip_data=equip, new_affix=new_affix, slot=4, roll_index=3, resets=0,
            food_label="", mode="normal", rule_keys=[])
        events = _flush_and_take()[0].events
        blob = json.dumps(events, ensure_ascii=False)

        forbidden = ["踏雪含光", fp, "外功穿透", "会心率的角色", "测试虚构角色名九九八",
                    str(__import__("pathlib").Path.home())]
        for s in forbidden:
            assert s not in blob, f"PII 泄漏: {s!r} 出现在上报 payload 里"

        for key in ("name", "_fp", "affixes", "dingyin", "extra_data", "warnings", "value", "unit"):
            assert key not in events[0], f"禁止字段 {key!r} 出现在上报 payload 里"

    def test_only_one_affix_category_present_per_event(self, enabled, sample):
        """单条事件只报「这一轮」的一个词条，不可能从中重建完整装备词条组合。"""
        gc = get_game_config()
        names = gc.get_normal_affix_names()
        equip = EquipmentData(type=sample["weapon_type"], level=110)
        new_affix = Affix(name=names[0], value=1.0, cap_pct=1.0)
        record_tuning_roll(
            equip_data=equip, new_affix=new_affix, slot=1, roll_index=1, resets=0,
            food_label="", mode="normal", rule_keys=[])
        e = _flush_and_take()[0].events[0]
        affix_like_keys = [k for k in e if "affix" in k.lower()]
        assert affix_like_keys == ["affix"]  # 只有单数的 affix 字段，没有 affixes 复数


class TestVocabStructural:
    """归一化只与 game_config 的实时枚举比对，不自建可能脱节的映射表——
    这正是为什么它不受工具 UI 语言影响：比对的是当前进程里实际生效的
    那份常量，而不是本模块里另写一份可能过期的 Chinese literal 表。
    """

    def test_normalize_part_matches_live_group_mapping(self):
        gc = get_game_config()
        mapping = gc.get_type_to_group()
        for equip_type, group in list(mapping.items())[:5]:
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
