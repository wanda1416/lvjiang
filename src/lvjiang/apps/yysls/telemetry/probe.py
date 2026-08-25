"""调律事件采集探针——业务侧唯一调用点，永不中断调律主流程。

采集点固定在 ``auto_tuning.py`` 主循环里 ``navigator.collect_new_affix()``
之后、``_emit_progress`` 之前：此刻且仅此刻同时拿得到部位/等级/品阶/
材料/槽位序号/结果词条/数值/cap_pct/是否转律。

明确不用 ``_emit_progress("tune_round_completed")`` 做钩子，尽管它已有
try 包裹和全部数据：它的 payload 里带 ``current_affixes``（完整五词条
组合，正是禁发数据），让统计代码去订阅一个装着红线数据的广播，等于把
防线交给下游自觉。
"""
from __future__ import annotations

from ....core.telemetry.consent import NetFeature, is_network_feature_enabled
from ....core.telemetry.probe import never_raises
from ..core.equip_parser.models import Affix, EquipmentData
from . import vocab
from .schemas import TUNING_ROLL_SCHEMA


@never_raises
def record_tuning_roll(
    *,
    equip_data: EquipmentData,
    new_affix: Affix | None,
    slot: int,
    roll_index: int,
    resets: int,
    food_label: str,
    mode: str,
    rule_keys: list[str] | None,
) -> None:
    """记录一次调律结果。调用方保证 ``new_affix`` 是本轮刚解析出的词条
    （解析失败时传 None，本函数直接跳过，不落 "unknown"）。
    """
    if new_affix is None:
        return
    if not is_network_feature_enabled(NetFeature.TELEMETRY):
        return

    from ....core.telemetry import identity as identity_mod
    from ....core.telemetry.spool import append as spool_append

    part = vocab.normalize_part(equip_data.type)
    if part is None:
        return  # 未知装备类型（配置未覆盖/OCR 误读），丢弃而非猜测
    affix_name = vocab.normalize_affix_name(new_affix.name)
    if affix_name is None:
        return  # 词条名不在普通词条池，多半是 OCR 误读，丢弃而非兜底
    food = vocab.normalize_food(food_label)
    if food is None:
        return  # 材料标签无法识别，丢弃而非猜测
    if not equip_data.level or equip_data.level <= 0:
        return  # 等级缺失（OCR 漏识别且未能反查）的样本不具统计价值

    identity = identity_mod.get_identity()
    from datetime import date

    fields = {
        "install_id": identity.install_id,
        "date": date.today().isoformat(),
        "part": part,
        "level": equip_data.level,
        "food": food,
        "slot": slot,
        "roll_index": roll_index,
        "resets": resets,
        "mode": mode,
        "active_rule": vocab.normalize_active_rule(rule_keys),
        "affix": affix_name,
        "cap_pct": float(new_affix.cap_pct) if new_affix.cap_pct is not None else 0.0,
        "is_transferred": bool(new_affix.is_transferred),
        "game_config_customized": vocab.game_config_customized(),
    }
    weapon_type = vocab.normalize_weapon_type(equip_data.type, part)
    if weapon_type is not None:
        fields["weapon_type"] = weapon_type
    quality = vocab.normalize_quality(equip_data.quality)
    if quality is not None:
        fields["quality"] = quality
    season = vocab.current_season_number()
    if season is not None:
        fields["season"] = season

    event = TUNING_ROLL_SCHEMA.validate(fields)
    spool_append(event)
