"""调律事件采集探针——业务侧唯一调用点，永不中断调律主流程。

一件装备一条事件，所以探针要跨轮攒状态：``begin_session`` 记下进调律页面
时的静态属性与初始词条，``record_roll`` 逐轮追加，``end_session`` 一次性
校验并落盘。三个函数都带 ``@never_raises``，任何一处出意外都不会波及调律。

**整条丢弃原则**：会话里只要有任何一轮的词条没能精确命中普通词条池，
整条记录作废，而不是只丢那一轮。理由是分析正确性而非隐私——序列里挖个洞
之后，第 4 轮会被下游当成"紧跟第 2 轮"，条件概率直接算错。宁可少一条，
不要一条错的。

采集点在 ``auto_tuning.py`` 的调律主循环：``begin_session`` 在进入循环前、
``record_roll`` 在 ``navigator.collect_new_affix()`` 之后、``end_session``
在 ``navigator.leave_tune()` 之后（此时最终评级才算得出来）。

词条名的 PII 风险已经在上游收敛过一道：``parser._parse_single_affix`` 匹配
成功时赋的是白名单条目本身而非 OCR 原文，匹配不上返回 None。唯一的例外是
``WUXUE_PATTERN``（``^(.+?)武学增[伤效]``）会把 OCR 匹配段整体当作词条名，
而本模块的 ``normalize_affix_name`` 要求精确命中那 37 条普通词条池、其中
武学词条是穷举列好的 11 个具体武器，所以拼不出来的名字到不了这里。
两层都在，缺一不可。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from ....core.telemetry.consent import NetFeature, is_network_feature_enabled
from ....core.telemetry.probe import never_raises
from ..core.equip_parser.models import Affix, EquipmentData
from . import vocab
from .schemas import TUNING_SESSION_SCHEMA

# rolls 的 max_items；超过即视为异常数据，整条不报（正常调律不会上百轮）
_MAX_ROLLS = 64


@dataclass
class _Session:
    """一件装备的在途采集状态。``poisoned`` 一旦置位就再也不会复位。"""

    fields: dict
    initial_affixes: list[dict] = field(default_factory=list)
    rolls: list[dict] = field(default_factory=list)
    poisoned: bool = False


# 调律是单线程主循环，但探针本身不该依赖这个前提：用锁保证任何时候
# 只有一件在途，且异常路径下不会把两件的轮次串到一起。
_LOCK = threading.Lock()
_CURRENT: _Session | None = None


def _affix_entry(affix: Affix | None) -> dict | None:
    """词条 → 事件条目；名字不在普通词条池一律返回 None（调用方据此作废整条）。"""
    if affix is None:
        return None
    name = vocab.normalize_affix_name(affix.name)
    if name is None:
        return None
    return {
        "affix": name,
        "cap_pct": float(affix.cap_pct) if affix.cap_pct is not None else 0.0,
        "is_transferred": bool(affix.is_transferred),
    }


@never_raises
def begin_session(
    *,
    equip_data: EquipmentData,
    initial_affixes: list[Affix] | None,
    mode: str,
    rule_keys: list[str] | None,
) -> None:
    """进入一件装备的调律流程。重复调用会丢弃上一件的在途数据。"""
    global _CURRENT
    with _LOCK:
        _CURRENT = None
        if not is_network_feature_enabled(NetFeature.TELEMETRY):
            return

        part = vocab.normalize_part(equip_data.type)
        if part is None:
            return  # 未知装备类型（配置未覆盖/OCR 误读），丢弃而非猜测
        if not equip_data.level or equip_data.level <= 0:
            return  # 等级缺失（OCR 漏识别且未能反查）的样本不具统计价值

        from datetime import date

        from ....core.telemetry import identity as identity_mod

        fields = {
            "install_id": identity_mod.get_identity().install_id,
            "date": date.today().isoformat(),
            "part": part,
            "level": equip_data.level,
            "mode": mode,
            "active_rule": vocab.normalize_active_rule(rule_keys),
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

        session = _Session(fields=fields)
        for affix in (initial_affixes or []):
            entry = _affix_entry(affix)
            if entry is None:
                session.poisoned = True
                break
            session.initial_affixes.append(entry)
        _CURRENT = session


@never_raises
def record_roll(
    *,
    new_affix: Affix | None,
    slot: int,
    resets: int,
    food_label: str,
) -> None:
    """记录一轮产出。调用方保证 ``new_affix`` 是本轮刚解析出的词条
    （解析失败时传 None）——解析失败即污染整条会话。
    """
    with _LOCK:
        session = _CURRENT
        if session is None or session.poisoned:
            return
        entry = _affix_entry(new_affix)
        food = vocab.normalize_food(food_label)
        if entry is None or food is None or len(session.rolls) >= _MAX_ROLLS:
            session.poisoned = True
            return
        entry["slot"] = slot
        entry["food"] = food
        entry["resets"] = resets
        session.rolls.append(entry)


@never_raises
def end_session(*, stop_reason: str, final_rating: str | None,
                total_rounds: int, resets: int) -> None:
    """离开调律页面：校验并落盘。无论成败都清空在途状态。"""
    global _CURRENT
    with _LOCK:
        session, _CURRENT = _CURRENT, None
        if session is None or session.poisoned:
            return
        if not session.rolls:
            return  # 一轮都没调（初始判定即跳过）——没有统计价值

        from ....core.telemetry.spool import append as spool_append

        fields = dict(session.fields)
        fields["initial_affixes"] = session.initial_affixes
        fields["rolls"] = session.rolls
        fields["stop_reason"] = vocab.normalize_stop_reason(stop_reason)
        fields["total_rounds"] = total_rounds
        fields["resets"] = resets
        rating = vocab.normalize_rating(final_rating)
        if rating is not None:
            fields["final_rating"] = rating

        spool_append(TUNING_SESSION_SCHEMA.validate(fields))


@never_raises
def abort_session() -> None:
    """异常路径下丢弃在途会话（例如装备处理中途抛错）。"""
    global _CURRENT
    with _LOCK:
        _CURRENT = None
