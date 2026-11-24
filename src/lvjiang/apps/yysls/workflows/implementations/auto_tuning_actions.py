"""自动调律的公共调律处理、智能调律、重置与行为处置混入类。"""

import copy
import hashlib
import json
from typing import Any

from loguru import logger

from lvjiang.apps.yysls.core.equip_parser import EquipmentData
from lvjiang.apps.yysls.core.tuning_rules import (
    BEHAVIOR_ACTION_LABELS,
    RATING_LABELS,
    RATING_RANK,
)
from lvjiang.apps.yysls.workflows.implementations.tuning import (
    BehaviorAction,
    BehaviorDecision,
    RecycleOutcome,
    TuningMode,
)

from .....i18n import tr


def _at_mod():
    from . import auto_tuning as _m
    return _m



class _AutoTuningActionsMixin:
    # ─── 调律处理公共逻辑（首次 + 每轮结束共用）──────────

    def _execute_tuning_processing(
        self, tune_cfg, equip_data: EquipmentData, affix_count: int,
        resets_used: int, base_affixes: list, potential: dict | None = None,
        is_initial: bool = False,
        pre_incoming: dict | None = None,
        pre_expect: str | None = None,
        pre_decision: BehaviorDecision | None = None,
        decision_emitted: bool = False,
    ) -> tuple[str, str, int, int]:
        """执行调律处理逻辑（首次或每轮结束后调用）

        Args:
            tune_cfg: 调律行为配置
            equip_data: 装备数据
            affix_count: 当前词条数
            resets_used: 当前已重置次数
            base_affixes: 首词条快照（重置后仅剩首词条）
            potential: 首次处理使用的完整装备潜力数据
            is_initial: 是否为进入调律前的首次处理
            pre_decision: 在装备详情页已经得出的首次决策

        Returns:
            (action, why, new_resets_used, new_affix_count):
            - action: "continue"=继续调律
                      "recycle"=回收该装备
                      "skip"=跳过/结束该装备
            - why: 最终原因
            - new_resets_used: 更新后的重置次数
            - new_affix_count: 更新后的词条数（重置后为1）
        """
        prefix = "首次调律处理" if is_initial else "调律处理"
        full = affix_count >= self.MAX_AFFIX

        # 调满后回收模式：跳过规则判定，调满即回收
        if self.equipment_session.tune_full_recycle and not is_initial:
            if full:
                keep_min = (
                    self.equipment_session.tune_full_recycle_keep_min_rating)
                current = self.equipment_session.expected_rating
                if (keep_min in RATING_RANK and current in RATING_RANK
                        and RATING_RANK[current] >= RATING_RANK[keep_min]):
                    current_label = RATING_LABELS.get(current, current)
                    keep_label = RATING_LABELS.get(keep_min, keep_min)
                    why = (
                        "调满后回收模式：当前规则判定为"
                        f"{current_label}，达到强制保留门槛"
                        f"（≥{keep_label}），跳过回收")
                    logger.info(f"  [{prefix}] {why}")
                    return "skip", why, resets_used, affix_count
                why = "调满后回收模式：词条已满，执行回收"
                logger.info(f"  [{prefix}] {why}")
                return "recycle", why, resets_used, affix_count
            else:
                why = f"调满后回收模式：继续调律（{affix_count}/{self.MAX_AFFIX}）"
                logger.info(f"  [{prefix}] {why}")
                return "continue", why, resets_used, affix_count

        # 刷新预期评级（首次处理使用详情页已经计算的完整装备结果）
        if is_initial:
            incoming = potential
        elif pre_incoming is not None:
            # 使用调用方预计算的结果（避免 tune_round_completed 信号前已跑过的判定重复执行）
            incoming = pre_incoming
            self.equipment_session.expected_rating = pre_expect
        else:
            incoming, self.equipment_session.expected_rating = \
                self.judge.refresh_expectation(equip_data)

        # 纯策略层只返回动作和值对象；本方法负责执行副作用。
        decision = pre_decision or self.judge.decide_end(
            tune_cfg, equip_data, incoming, full=full)
        action, why = decision.action, decision.reason
        if not decision_emitted:
            logger.info(f"  [{prefix}] {why}")
            self._emit_operation(
                "decision", f"{prefix}命中：{action}", reason=why,
                action=action, affix_count=affix_count, resets=resets_used)

        # 处理各动作
        if action is BehaviorAction.CONTINUE:
            return action.value, why, resets_used, affix_count

        if action is BehaviorAction.LOCK:
            return action.value, why, resets_used, affix_count

        if action is BehaviorAction.RESET:
            # 重置会花传律石，先确认重置后还有提升空间再提交。
            blocked = self._smart_reset_precheck(
                tune_cfg, equip_data, resets_used, base_affixes, prefix=prefix)
            if blocked is not None:
                blocked_action, blocked_why, resets_used, _ = blocked
                return blocked_action, blocked_why, resets_used, affix_count
            # 调用重置公共逻辑
            reset_action, why, resets_used = self._execute_reset_action(
                tune_cfg, equip_data, resets_used, why, prefix=prefix,
                previous_affix_count=affix_count)
            if reset_action == "continue":
                # 重置成功，更新装备状态
                equip_data.affixes = base_affixes[:1]
                equip_data.extra_data["affix_count"] = 1
                # 重置后刷新预期评级（首词条不变，但词条数变化可能影响判定）
                incoming, self.equipment_session.expected_rating = \
                    self.judge.refresh_expectation(equip_data)
                self._emit_assessment(
                    incoming, self.equipment_session.expected_rating, stage="scan")
                return "continue", why, resets_used, 1
            # skip 或 recycle → 直接返回
            return reset_action, why, resets_used, affix_count

        if action is BehaviorAction.TUNE_FULL_RECYCLE:
            # 调律处理命中后进入与扫描处理相同的直通模式：后续轮次
            # 不再做规则判定/狗粮决策，调满后回到背包执行回收。
            self.equipment_session.mode = TuningMode.TUNE_FULL_RECYCLE
            self._emit_progress("scan_decision", {
                "name": equip_data.name or equip_data.type,
                "action": "tune_full_recycle",
                "reason": f"{why}（调满后回收模式）",
            })
            if full:
                return "recycle", f"{why}（词条已满，执行回收）", resets_used, affix_count
            return "continue", f"{why}（进入调满后回收模式）", resets_used, affix_count

        # recycle 或 skip → 直接返回
        return action.value, why, resets_used, affix_count

    # ─── 智能调律（现有调律处理后的二次处理）───

    def _prepare_smart_tuning(self) -> None:
        """按本次运行用户和玩法快照初始化智能调律。"""
        self._smart_evaluator = None
        self._emit_progress("smart_tuning_updated", {"enabled": False})
        group = self._ensure_base_group()
        config = group.smart_tuning
        self._smart_config = config
        user_enabled = self.ctx.smart_tuning_enabled
        if user_enabled is None:
            enabled_by_group = self._workflow_config().get(
                "smart_tuning_enabled", {})
            user_enabled = bool(
                enabled_by_group.get(group.key, False)
                if isinstance(enabled_by_group, dict) else False)
        if not user_enabled:
            logger.info(
                f"当前用户未对基础规则组「{group.name}」启用智能调律，"
                "本轮不执行智能判定")
            return
        if not config.evaluation.enabled:
            return
        try:
            from lvjiang.apps.yysls.core.graduation.smart_tuning import (
                SmartTuningEvaluator,
            )
            username = (getattr(self.engine, "run_username", "") or "")
            users_dir = getattr(self.engine, "users_dir", None)
            self._smart_evaluator = SmartTuningEvaluator(
                config,
                username=username,
                incoming_rule_configs=self.ctx.judge_configs,
                state=self.ctx.smart_state,
                users_dir=users_dir,
                # is_stopped 同时是暂停检查点；长组合搜索期间仍能
                # 响应暂停与结束，不会等整件装备算完才生效。
                stop_check=lambda: self.is_stopped,
            )
            if not self._smart_evaluator.active:
                logger.warning(
                    "智能调律本轮已放行："
                    f"{self._smart_evaluator.disabled_reason}")
            else:
                logger.info("智能调律已启用，备战方案快照加载完成")
            self._emit_progress("smart_tuning_updated", {
                "enabled": True,
                "state": "initial",
                "message": (self._smart_evaluator.disabled_reason
                            or "备战方案快照已加载"),
                "plans": list(self._smart_evaluator.plan_infos),
            })
        except Exception:  # noqa: BLE001 - 初始化失败不能阻断调律
            logger.exception("智能调律初始化失败，本轮已放行")
            self._smart_evaluator = None
            self._emit_progress("smart_tuning_updated", {
                "enabled": True,
                "state": "initial",
                "message": "初始化失败，智能调律不启用",
                "plans": [],
            })

    @staticmethod
    def _smart_plan_payload(item) -> dict:
        return {
            "plan_id": item.plan_id,
            "plan_name": item.plan_name,
            "rule_name": item.rule_name,
            "playstyle": item.playstyle,
            "status": item.status.value,
            "plan_maximum_rate": item.plan_maximum_rate,
            "baseline_rate": item.baseline_rate,
            "without_slot_rate": item.without_slot_rate,
            "maximum_rate": item.maximum_rate,
            "reason": item.reason,
            "evaluated_combinations": item.evaluated_combinations,
        }

    def _smart_display_plans(self, evaluator, result) -> list[dict]:
        """保留初始化时的不完整/无效方案，并按原顺序覆盖实际评估结果。"""
        evaluated: dict[tuple[str, str, str], list[dict]] = {}
        for item in result.plans:
            payload = self._smart_plan_payload(item)
            key = (item.plan_id, item.rule_name, item.playstyle)
            evaluated.setdefault(key, []).append(payload)
        rows: list[dict] = []
        for info in evaluator.plan_infos:
            row = dict(info)
            key = (
                str(row.get("plan_id") or ""),
                str(row.get("rule_name") or ""),
                str(row.get("playstyle") or ""),
            )
            matches = evaluated.get(key)
            if row.get("status") == "ready" and matches:
                rows.append(matches.pop(0))
            else:
                rows.append(row)
        for matches in evaluated.values():
            rows.extend(matches)
        return rows

    @staticmethod
    def _smart_rate_text(value) -> str:
        return (f"{value * 100:.2f}%"
                if isinstance(value, (int, float)) and not isinstance(value, bool)
                else "-")

    def _log_smart_plan_details(self, plans: list[dict], checkpoint: str) -> None:
        status_labels = {
            "ready": "等待分析",
            "improves": "存在提升",
            "no_improvement": "确定无法提升",
            "unknown": "无法确定，放行",
            "not_applicable": "不适用",
            "missing": "无备战方案",
            "incomplete": "备战方案不完整",
            "invalid": "方案无效",
        }
        for item in plans:
            status = str(item.get("status") or "")
            logger.debug(
                f"  [智能调律/{checkpoint}] 备战方案「"
                f"{item.get('plan_name')}」（{item.get('rule_name')}/"
                f"{item.get('playstyle')}）："
                f"方案极限={self._smart_rate_text(item.get('plan_maximum_rate'))}，"
                f"当前实值={self._smart_rate_text(item.get('baseline_rate'))}，"
                f"七件极限={self._smart_rate_text(item.get('without_slot_rate'))}，"
                f"候选极限={self._smart_rate_text(item.get('maximum_rate'))}，"
                f"判定={status_labels.get(status, status or '-')}，"
                f"说明={item.get('reason') or '-'}")

    def _smart_terminal_opinion(
        self, plans: list[dict], action_label: str,
    ) -> str:
        details = []
        for item in plans:
            if item.get("status") != "no_improvement":
                continue
            details.append(
                f"备战方案「{item.get('plan_name')}」分析最大极限毕业率 "
                f"{self._smart_rate_text(item.get('maximum_rate'))}，"
                f"低于或等于原方案极限 "
                f"{self._smart_rate_text(item.get('plan_maximum_rate'))}")
        analysis = "；".join(details) or "分析确定所有适用方案无法提升"
        return f"智能调律：{analysis}，处理结果：{action_label}。"

    def _emit_smart_final(
        self, plans: list[dict], checkpoint: str, action: str,
    ) -> None:
        label = BEHAVIOR_ACTION_LABELS.get(action, action)
        opinion = self._smart_terminal_opinion(plans, label)
        self._emit_progress("smart_tuning_updated", {
            "enabled": True,
            "state": "final",
            "message": opinion,
            "checkpoint": checkpoint,
            "plans": plans,
            "final_action": action,
            "final_action_label": label,
            "opinion": opinion,
        })

    def _execute_smart_processing(
        self, tune_cfg, equip_data: EquipmentData, affix_count: int,
        resets_used: int, base_affixes: list, *, checkpoint: str,
        _allow_reset: bool = True, _precomputed=None,
    ) -> tuple[str, str, int, int]:
        """在当前调律处理已放行后执行智能判定及可选动作。

        ``_precomputed`` 供重置前预检复用同一次评估结果，避免对同一状态
        重复搜索。
        """
        evaluator = self._smart_evaluator
        if evaluator is None or not evaluator.active:
            return "continue", "智能调律未启用或本轮不可用", resets_used, affix_count
        if self.equipment_session.tune_full_recycle:
            return "continue", "调满后回收模式跳过智能调律", resets_used, affix_count

        from lvjiang.apps.yysls.core.graduation.smart_tuning import (
            SmartTuningStatus,
        )
        slot = str(getattr(self, "_current_slot", "") or "")
        self._emit_progress("smart_tuning_updated", {
            "enabled": True,
            "state": "analyzing",
            "message": "正在推演当前装备的最大可能毕业率",
            "checkpoint": checkpoint,
            "plans": list(evaluator.plan_infos),
        })
        result = _precomputed or evaluator.evaluate(
            slot, equip_data.to_dict(include_fp=False))
        plan_summary = "；".join(
            f"{item.plan_name}: {item.reason}"
            for item in result.plans)
        decision_record: dict[str, Any] = {
            "checkpoint": checkpoint,
            "status": result.status.value,
            "reason": result.reason,
            "plans": self._smart_display_plans(evaluator, result),
        }
        self._log_smart_plan_details(decision_record["plans"], checkpoint)
        self.recorder.report_append("smart_tuning_decisions", decision_record)
        self._emit_operation(
            "smart_decision", result.reason,
            smart_status=result.status.value, checkpoint=checkpoint,
            plans=decision_record["plans"], affix_count=affix_count,
            resets=resets_used)
        self.recorder.doc_note(
            f"智能调律（{checkpoint}）：{result.reason}")
        self._emit_progress("smart_tuning_updated", {
            "enabled": True,
            "state": "evaluated",
            "message": result.reason,
            "checkpoint": checkpoint,
            "plans": decision_record["plans"],
        })
        if result.status is SmartTuningStatus.UNKNOWN:
            logger.warning(
                f"  [智能调律/{checkpoint}] {result.reason}"
                + (f"（{plan_summary}）" if plan_summary else ""))
            return "continue", result.reason, resets_used, affix_count
        if result.status is SmartTuningStatus.IMPROVES:
            logger.info(f"  [智能调律/{checkpoint}] {result.reason} → 继续调律")
            return "continue", result.reason, resets_used, affix_count

        config = evaluator.config.failure_action
        why = f"智能调律：{result.reason}"
        logger.info(f"  [智能调律/{checkpoint}] {why}")
        if not config.enabled:
            logger.info("  [智能调律] 处理动作未启用，仅记录并放行")
            return "continue", why, resets_used, affix_count

        action = config.action
        label = BEHAVIOR_ACTION_LABELS.get(action, action)
        why = f"{why} → {label}"
        if action == "continue":
            return "continue", why, resets_used, affix_count
        if action == "reset":
            if not _allow_reset:
                fallback = tune_cfg.reset_exhausted_action
                fallback_label = BEHAVIOR_ACTION_LABELS.get(fallback, fallback)
                self._emit_smart_final(
                    decision_record["plans"], checkpoint, fallback)
                return (
                    fallback,
                    f"{why}（重置后仍无法提升，转{fallback_label}）",
                    resets_used,
                    affix_count,
                )
            # 判定为 reset 只说明当前形态没戏，重置本身也会花传律石；
            # 先确认首词条状态还有提升空间，否则这次重置同样白花。
            blocked = self._smart_reset_precheck(
                tune_cfg, equip_data, resets_used, base_affixes,
                prefix="智能调律")
            if blocked is not None:
                blocked_action, blocked_why, resets_used, _ = blocked
                return blocked_action, blocked_why, resets_used, affix_count
            self._emit_smart_final(
                decision_record["plans"], checkpoint, "reset")
            reset_action, reset_why, resets_used = self._execute_reset_action(
                tune_cfg, equip_data, resets_used, why,
                prefix="智能调律", previous_affix_count=affix_count)
            if reset_action == "continue":
                equip_data.affixes = base_affixes[:1]
                equip_data.extra_data["affix_count"] = 1
                incoming, self.equipment_session.expected_rating = \
                    self.judge.refresh_expectation(equip_data)
                self._emit_assessment(
                    incoming, self.equipment_session.expected_rating,
                    stage="smart")
                # 重置本身也是断点：立即以首词条状态重算。
                # 若仍无提升可能，直接走基础规则组的重置耗尽
                # 处置，不对同一首词条反复执行无意义重置。
                post_action, post_why, resets_used, post_count = \
                    self._execute_smart_processing(
                        tune_cfg, equip_data, 1, resets_used, base_affixes,
                        checkpoint=f"{checkpoint}/重置后",
                        _allow_reset=False)
                if post_action == "continue":
                    return post_action, reset_why, resets_used, post_count
                return post_action, post_why, resets_used, post_count
            self._emit_smart_final(
                decision_record["plans"], checkpoint, reset_action)
            return reset_action, reset_why, resets_used, affix_count
        if action == "tune_full_recycle":
            self.equipment_session.mode = TuningMode.TUNE_FULL_RECYCLE
            self.equipment_session.tune_full_recycle_keep_min_rating = (
                config.keep_min_rating)
            self._emit_smart_final(
                decision_record["plans"], checkpoint, action)
            return "continue", why, resets_used, affix_count
        self._emit_smart_final(
            decision_record["plans"], checkpoint, action)
        return action, why, resets_used, affix_count

    def _smart_reset_precheck(
        self, tune_cfg, equip_data: EquipmentData, resets_used: int,
        base_affixes: list, *, prefix: str,
    ) -> tuple[str, str, int, int] | None:
        """重置调律提交前，先用「重置后的画像」问一次智能调律。

        重置会丢弃第 2 条起的词条并消耗传律石，而智能调律原来只在重置之后
        才判定；判定为无法提升时，这次重置已经白花。这里用只保留首词条的
        画像评估——它与重置后的实际状态一致，因此结论可以直接决定要不要
        重置。``UNKNOWN`` 无法证明没有收益，按原逻辑放行。

        返回 ``None`` 表示允许重置；否则是失败动作配置给出的处置，调用方
        直接采用即可，不会再提交一次重置。
        """
        evaluator = self._smart_evaluator
        if evaluator is None or not evaluator.active:
            return None
        from lvjiang.apps.yysls.core.graduation.smart_tuning import (
            SmartTuningStatus,
        )
        slot = str(getattr(self, "_current_slot", "") or "")
        probe = copy.deepcopy(equip_data)
        # 重置后保留的就是首词条快照，与 _execute_reset_action 成功后的
        # equip_data.affixes = base_affixes[:1] 用同一口径。
        probe.affixes = list(base_affixes[:1])
        probe.extra_data["affix_count"] = 1
        result = evaluator.evaluate(slot, probe.to_dict(include_fp=False))
        if result.status is not SmartTuningStatus.NO_IMPROVEMENT:
            return None
        # tune_full_recycle 也会返回 continue（表示原地继续调满），不能拿
        # 返回动作判断是否放行重置，必须看配置本身是否真的要停止调律。
        failure = evaluator.config.failure_action
        if not failure.enabled or failure.action == "continue":
            # 配置就是「仅记录/继续」，没有拦截动作可执行；此处不记决策、
            # 不发进度，把结论留给重置后的正式判定，避免重复条目。
            return None
        logger.info(
            f"  [{prefix}] 重置前智能判定：{result.reason}，跳过本次重置")
        return self._execute_smart_processing(
            tune_cfg, probe, 1, resets_used, base_affixes,
            checkpoint=f"{prefix}/重置前", _allow_reset=False,
            _precomputed=result)

    # ─── 重置调律公共逻辑（等级配置检查 + 重置执行）──────────

    def _execute_reset_action(
        self, tune_cfg, equip_data: EquipmentData, resets_used: int,
        why: str, prefix: str = "调律处理",
        previous_affix_count: int | None = None,
    ) -> tuple[str, str, int]:
        """执行重置调律的公共逻辑（等级配置检查 + 重置执行）

        Args:
            tune_cfg: 调律行为配置
            equip_data: 装备数据
            resets_used: 当前已重置次数
            why: 触发重置的原因
            prefix: 日志前缀（"首次调律处理"、"调律处理"或"智能调律"）

        Returns:
            (action, why, new_resets_used):
            - action: "continue"=重置成功继续调律
                      "skip"=跳过该装备
                      "recycle"=回收该装备
            - why: 最终原因
            - new_resets_used: 更新后的重置次数
        """
        # 等级配置检查：该等级是否允许重置
        game_cfg = _at_mod().get_game_config()
        level_cfg = game_cfg.level_config_for(equip_data.level or 0)
        if level_cfg is None:
            # 等级配置不存在 → 异常，跳过该装备
            logger.error(
                f"  [{prefix}] 等级配置缺失（level={equip_data.level}），"
                "视为异常，跳过该装备")
            why = f"等级配置缺失（level={equip_data.level}）"
            self._emit_operation(
                "reset", why, action="reset_failed", reason=why,
                reset_outcome="failed", resets=resets_used)
            return "skip", why, resets_used
        if level_cfg.allow_reset is False:
            # 该等级不支持重置 → 视为重置次数用尽
            action = tune_cfg.reset_exhausted_action
            why = (f"等级 {equip_data.level} 不支持重置"
                   f"（视为重置次数用尽）转{action}")
            logger.info(f"  [{prefix}] {why}")
            outcome = "exhausted_recycled" if action == "recycle" else "exhausted"
            self._emit_operation(
                "reset", why, action=f"reset_{outcome}", reason=why,
                reset_outcome=outcome, resets=resets_used)
            return action, why, resets_used
        # 等级配置允许重置，继续正常重置流程
        self._emit_operation(
            "reset", "规则已命中重置，正在检查次数、冷却和材料",
            reason=why, resets=resets_used)
        result = self.resetter.try_reset_tune(
            tune_cfg, resets_used, why,
            min_material_count=level_cfg.min_material_count,
            notify_if_cooling=not (
                equip_data.cooldown_kind == "reset"
                and equip_data.cooldown_state in {"cooling", "completed"}
            ),
        )
        if result is True:
            self.stone_stock.record_reset(
                equip_data,
                previous_affix_count
                if previous_affix_count is not None
                else len(equip_data.affixes))
            # 重置会改变材料数量，旧缓存已不可信。此时只作废；
            # 若后续确实要执行调律，再由 tune_once 展开面板后读取。
            self.executor.invalidate_cache()
            self._emit_progress("equipment_reset", {
                "name": equip_data.name or equip_data.type,
                "type": equip_data.type,
                "level": equip_data.level,
                "quality": equip_data.quality,
                "before_affixes": [a.to_dict() for a in equip_data.affixes],
                "before_affix_count": len(equip_data.affixes),
                "after_affixes": [a.to_dict() for a in equip_data.affixes[:1]],
                "after_affix_count": min(len(equip_data.affixes), 1),
                "resets_used": resets_used + 1,
                # recorder 中的评级属于重置前装备；重置后等待重新判定。
                "before_rating": (
                    (self.equipment_session.expected_rating or "")
                    if len(equip_data.affixes) >= self.MAX_AFFIX else ""),
                "expect_rating": "",
                "tuning_mode": self.equipment_session.mode.value,
            })
            self._emit_operation(
                "reset", "重置完毕，继续调律", action="reset_completed",
                reason=why, reset_outcome="completed", resets=resets_used + 1)
            return "continue", why, resets_used + 1
        if isinstance(result, tuple):
            # 冷却期/材料不足/识别异常 → 强制跳过（不走 reset_exhausted_action）。
            # outcome 由 resetter 直接给出，不再从中文原因串反推——那在英文
            # 界面下 tr() 之后必然认错档。
            outcome, message = result
            self._emit_operation(
                "reset", message, action=f"reset_{outcome}", reason=message,
                reset_outcome=outcome, resets=resets_used)
            return "skip", message, resets_used
        # 重置次数已用尽（本地上限或按钮无次数）→ 按配置转处置
        action = tune_cfg.reset_exhausted_action
        if action == "recycle":
            why = f"重置次数已用尽转回收（{why}）"
            logger.info(f"  [{prefix}] {why}")
        outcome = "exhausted_recycled" if action == "recycle" else "exhausted"
        self._emit_operation(
            "reset", why, action=f"reset_{outcome}", reason=why,
            reset_outcome=outcome, resets=resets_used)
        return action, why, resets_used

    # ─── 行为处置（按 tune_config.behavior 行为点配置驱动）──────

    def _on_scan_reject(self, equip_data: EquipmentData, potential: dict,
                        detail_scene: str | None = None,
                        already_full: bool = False,
                        incoming_first_affix_only: bool = False,
                        ) -> RecycleOutcome | None:
        """扫描阶段处置（scan 行为点）。

        两种入口：
        1. 未满装备预期评级未达进入门槛 → 按处置表决定回收/保留；
        2. 已满装备（already_full=True）→ 同走处置表，其中
           tune_full_recycle 视为直接回收（已满无需再调）。

        处置表评级按各规则自身判定语义懒取（incoming 复用进入
        决策的判定结果，all/custom 按需另跑潜力判定并缓存；
        逐规则声明的 first_affix_only 由评级提供者注入首词条）→
        处置表首条命中；仅 recycle 动作落地，tune_full_recycle
        设置标记后返回 None（继续走调律流程），其余保留。

        Returns:
            RECYCLED：已回收（该格已被补位装备占据）；
            LOCKED/UNAVAILABLE：尝试回收但装备仍保留；
            STOPPED：用户在异常处理对话框中要求结束任务；
            None：没有触发回收动作。
        """
        label = equip_data.name or equip_data.type
        cfg = self.base_group.scan
        if detail_scene is None:
            logger.info(f"  [扫描处理] {label} 缺少装备详情场景，保留")
            self._emit_progress("scan_decision", {
                "name": label, "action": "kept",
                "reason": "缺少装备详情场景，保留"})
            return None
        # potential 若来自"仅首词条"门槛判定，只能复用给同口径规则
        decision = self.judge.decide_scan(
            cfg, equip_data, potential,
            incoming_first_affix_only=incoming_first_affix_only)
        action, why = decision.action, decision.reason
        if action is BehaviorAction.TUNE_FULL_RECYCLE:
            if already_full:
                # 已满装备无需再调，“调满后回收”意图等价于直接回收
                logger.info(f"  [扫描处理] {label} {why}（已满，直接回收）")
                self._emit_progress("scan_decision", {
                    "name": label,
                    "action": ("locked" if equip_data.lock_status == "locked"
                               else "recycled"),
                    "reason": ("装备标记为锁定，跳过回收"
                               if equip_data.lock_status == "locked"
                               else f"{why}（已满，直接回收）")})
                return self._recycle_current(
                    equip_data, detail_scene, "scan", why)
            # 未满：设置标记，继续走调律流程
            logger.info(f"  [扫描处理] {label} {why}（调满后回收模式）")
            self.equipment_session.mode = TuningMode.TUNE_FULL_RECYCLE
            self._emit_progress("scan_decision", {
                "name": label, "action": "tune_full_recycle",
                "reason": f"{why}（调满后回收模式）"})
            return None
        if action is BehaviorAction.TUNE_THIS:
            # 强制调律：无视进入门槛，强制进入调律页
            # 强制调律装备仍统一执行首次调律处理
            logger.info(f"  [扫描处理] {label} {why}（强制调律模式）")
            self.equipment_session.mode = TuningMode.FORCE_TUNE
            self._emit_progress("scan_decision", {
                "name": label, "action": "force_tune",
                "reason": f"{why}（强制调律）"})
            return None
        if action is not BehaviorAction.RECYCLE:
            logger.info(f"  [扫描处理] {label} {why}")
            self._emit_progress("scan_decision", {
                "name": label, "action": "kept", "reason": why})
            return None
        self._emit_progress("scan_decision", {
            "name": label,
            "action": ("locked" if equip_data.lock_status == "locked"
                       else "recycled"),
            "reason": ("装备标记为锁定，跳过回收"
                       if equip_data.lock_status == "locked" else why)})
        return self._recycle_current(equip_data, detail_scene,
                                     "scan", why)

    # ─── 工具方法（bag_traversal 经 wf 调用）──────────────

    @staticmethod
    def _make_fingerprint(equip: dict) -> str:
        """获取或计算装备指纹。

        优先读取 dict 内已有的 _fp 字段（由 to_equipment / to_dict 自动设置），
        仅当缺失时才回退计算。type 未被装备解析器确认时必须返回空串，
        即使 OCR 噪声形成了 name 或上游错误缓存了 _fp；这是背包遍历判定
        空 slot 的入口隔离。
        """
        if not isinstance(equip, dict) or not equip.get("type"):
            return ""
        fp = equip.get("_fp", "")
        if fp:
            return fp
        from ...core.equip_parser.models import make_fingerprint
        return make_fingerprint(equip)

    @staticmethod
    def _equipment_read_state(equip: dict) -> str:
        """将 OCR 装备结果分为 empty / invalid / valid 三态。"""
        if not isinstance(equip, dict):
            return "empty"
        level = equip.get("level")
        has_level = (isinstance(level, (int, float))
                     and not isinstance(level, bool) and level > 0)
        has_type = bool(equip.get("type"))
        if not has_level and not has_type:
            return "empty"
        if not has_level or not has_type:
            return "invalid"
        return "valid"

    @staticmethod
    def _make_invalid_read_fingerprint(equip: dict, raw: dict) -> str:
        """为存在但不可理解的装备生成仅用于遍历的稳定锚点。"""
        payload = json.dumps(
            {"equip": equip, "raw": raw}, ensure_ascii=False,
            sort_keys=True, default=str).encode("utf-8")
        return "invalid:" + hashlib.sha1(payload).hexdigest()

    def _close_equipment_detail(self) -> None:
        """仅在确认当前存在装备详情时执行环境对应的关闭动作。"""
        if not getattr(self, "_equipment_detail_open", False):
            return
        self.route_strategy.close_equipment_detail()
        self._equipment_detail_open = False

    @staticmethod
    def _equip_label(equip: dict) -> str:
        """日志用装备标识：name|type（缺失部分省略，全空返回'空'）"""
        if not isinstance(equip, dict) or not equip:
            return "空"
        label = "|".join(
            str(p) for p in (equip.get("name"), equip.get("type")) if p)
        return label or "空"

    # ─── 判定配置兜底 ──────────────────────────────────────

    @property
    def base_group(self):
        """当前基础规则组（启动时经 run() 解析；未注入时惰性兜底）"""
        group = self.ctx.base_group
        return group if group is not None else self._ensure_base_group()

    def _ensure_base_group(self):
        """保证 ctx.base_group 可用。

        优先用启动时注入的规则组；未注入时读取执行用户配置中的
        base_group；读不到或无效则抛异常。
        """
        group = self.ctx.base_group
        if group is not None:
            return group
        # 设备端启动时 ctx 未注入，回退读取执行用户配置。
        group_key = self._workflow_config().get("base_group", "")
        if not group_key:
            raise ValueError(tr("未配置基础规则组，请在调律页选择基础规则组"))
        from lvjiang.apps.yysls.core.tuning_rules import get_tuning_group
        group = get_tuning_group(group_key)
        if group is None:
            raise ValueError(f"基础规则组 '{group_key}' 不存在，请检查配置")
        self.ctx.base_group = group
        return group

    def _ensure_judge_config(self):
        """保证 ctx.judge_configs/judge_rule_keys 可用。

        优先用 run_control 注入的启动快照；未注入时读取执行用户配置中的
        rules + switches；读不到则抛异常。
        """
        ctx = self.ctx
        if ctx.judge_configs is not None or ctx.judge_rule_keys is not None:
            return
        tc = self._workflow_config()
        rules = tc.get("rules", {})
        if not rules:
            raise ValueError(tr("未配置调律规则，请在调律页选择至少一个规则"))
        switches = tc.get("switches", {})
        enabled = {k: {**cfg, "switches": switches}
                   for k, cfg in rules.items()
                   if isinstance(cfg, dict) and cfg.get("enabled")}
        if not enabled:
            raise ValueError(tr("没有启用任何调律规则，请在调律页勾选至少一个规则"))
        ctx.judge_rule_keys = list(enabled)
        ctx.judge_configs = enabled

    def _resolve_selected_slots(self) -> list[str]:
        """调律部位：优先 UI 注入的 ctx.selected_slots；设备端经 task_runner
        启动时 ctx 未注入（selected_slots=None），回退读取执行用户配置；
        读不到则抛异常。"""
        selected = self.ctx.selected_slots
        if selected is None:
            raw = self._workflow_config().get("selected_slots")
            if not isinstance(raw, list) or not raw:
                raise ValueError(tr("未配置调律部位，请在调律页选择至少一个部位"))
            valid = set(self.WEAPON_SLOTS) | set(self.ARMOR_SLOTS)
            selected = [s for s in raw if s in valid]
        if not selected:
            raise ValueError(tr("调律部位配置无效，请在调律页重新选择部位"))
        return selected
