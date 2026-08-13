"""调律判定与评级 — TuningJudge

装备潜力判定、期望评级提取、行为表评级提供者。
纯逻辑类，不依赖 UI 操作原语，通过 ctx 获取判定配置。
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from loguru import logger

from lvjiang.apps.yysls.equip_parser import EquipmentData
from lvjiang.apps.yysls.evaluator import (
    get_rule_names,
    judge_equipment_potential,
    summarize_potential,
)
from lvjiang.apps.yysls.evaluator.tuning_rules import (
    RATING_LABELS,
    RATING_RANK,
    RatingProvider,
)

from ......i18n import tr

if TYPE_CHECKING:
    from lvjiang.apps.yysls.workflows.tuning_context import TuningRunContext


class TuningJudge:
    """调律判定与评级：潜力判定、期望评级、行为表评级提供者"""

    def __init__(self, ctx: TuningRunContext):
        self._ctx = ctx

    @staticmethod
    def expect_key(results: dict) -> str | None:
        """从潜力判定结果提取装备期望评级 key（适用规则最高档）

        跳过/不适用的规则不参与；无任何适用规则时返回 None
        （狗粮规则的期望条件永不命中）。
        """
        label_to_key = {v: k for k, v in RATING_LABELS.items()}
        best: str | None = None
        for r in (results or {}).values():
            if r.get("skipped") or r.get("not_applicable"):
                continue
            key = label_to_key.get(r.get("rating", ""))
            if key and (best is None or RATING_RANK[key] > RATING_RANK[best]):
                best = key
        return best

    def final_judge(self, equip_data: EquipmentData) -> dict:
        """终局判定：含转律模拟的各规则评级上限，返回结构化结果供 report/hook 消费"""
        results = judge_equipment_potential(
            equip_data, self._ctx.judge_configs, self._ctx.judge_rule_keys)
        for r in results.values():
            tag = (tr("不适用") if r["not_applicable"]
                   else tr("跳过") if r["skipped"] else r["rating"])
            logger.info(
                f"  终局判定 | {r['name']}: {tag}（{'；'.join(r['reasons'])}）")
        return results

    def judge_by_scope(self, equip_data: EquipmentData, scope: str,
                       keys: list[str]) -> dict:
        """按判定语义跑潜力判定（行为点的预期评级识别入口）

        incoming=运行期传入配置；all=全部规则默认配置；custom=
        自选 key 集 + 默认配置（已删除的 key 运行期过滤；全部无效
        时回落全部规则，宁多保留不误收）。
        """
        if scope == "incoming":
            return judge_equipment_potential(
                equip_data, self._ctx.judge_configs, self._ctx.judge_rule_keys)
        use_keys = None
        if scope == "custom" and keys:
            known = set(get_rule_names())
            use_keys = [k for k in keys if k in known]
            unknown = [k for k in keys if k not in known]
            if unknown:
                logger.warning(f"判定声明的规则不存在: {unknown}，已忽略")
            if not use_keys:
                logger.warning("判定声明的规则全部无效，按全部规则判定")
                use_keys = None
        return judge_equipment_potential(equip_data, None, use_keys)

    def rating_provider(self, equip_data: EquipmentData,
                        incoming: dict | None = None) -> RatingProvider:
        """构造行为表的评级提供者（同一装备当前词条状态内缓存）

        各规则按自身判定语义懒算评级（缓存键 (scope, keys,
        仅首词条)）；incoming 为已有的传入规则判定结果，作种子
        避免重复跑（基于全词条，仅首词条时不可复用）。
        first_affix_only 时只注入首词条（其余槽视作空槽由潜力
        判定自由填充），避免回收掉非首词条已成垃圾但可重置
        调律的装备。无任何适用规则（部位/品阶不在任何判定
        范围）= 无调律价值，按业务约定兜底为垃圾档。
        """
        cache: dict[tuple, str] = {}
        if incoming is not None:
            cache[("incoming", (), False)] = (
                self.expect_key(incoming) or "junk")
        label = equip_data.name or equip_data.type

        def rating_of(scope: str, keys: list[str],
                      first_affix_only: bool = False) -> str:
            # 单词条装备无需构造副本，归一到全词条缓存键
            fao = first_affix_only and len(equip_data.affixes) > 1
            ck = (scope, tuple(keys), fao)
            if ck not in cache:
                target = equip_data
                if fao:
                    target = replace(
                        equip_data, affixes=equip_data.affixes[:1],
                        extra_data={**equip_data.extra_data,
                                    "affix_count": 1})
                    logger.info(
                        f"  [行为评级] {label} 仅按首词条判定评级"
                        f"（忽略其他 {len(equip_data.affixes) - 1} 条）")
                results = self.judge_by_scope(target, scope, keys)
                cache[ck] = self.expect_key(results) or "junk"
            return cache[ck]

        return rating_of

    @staticmethod
    def recycle_inputs(equip_data: EquipmentData,
                       ) -> tuple[str, str | None, float | None]:
        """行为规则匹配输入：(部位, 品阶, 首词条初始数值 cap_pct)

        首词条 cap_pct 不因调律改变，各行为点可用同一输入。
        """
        cap_pct = (equip_data.affixes[0].cap_pct
                   if equip_data.affixes else None)
        return equip_data.part, equip_data.quality, cap_pct

    def refresh_expectation(self, equip_data: EquipmentData,
                            ) -> tuple[dict, str | None]:
        """按传入规则刷新装备预期评级（每轮调律结束后取值）

        行为表评级另经评级提供者按各规则判定语义懒取。

        Returns: (传入规则判定结果, 期望评级 key)。
        """
        results = self.judge_by_scope(equip_data, "incoming", [])
        _, logs = summarize_potential(results)
        for line in logs:
            logger.info(f"  潜力判定 | {line}")
        return results, self.expect_key(results)
