"""调律过程记录器 — 收拢文档与报告职责

AutoTuningWorkflow 只编排流程、传递数据，所有记录操作经 TuningRecorder 完成：
- 调律说明文档（TuningDocWriter 协调）
- 调律报告累积（tuning_reports）
- 装备回收补位标记（迁移期兼容字段，后续由结构化处理结果替代）
"""
from __future__ import annotations

from lvjiang.apps.yysls.core.evaluator import get_rule_names
from lvjiang.apps.yysls.core.tuning_rules import (
    RATING_LABELS,
    RATING_RANK,
)
from lvjiang.apps.yysls.workflows.tuning_doc import TuningDocWriter


class TuningRecorder:
    """调律过程记录器

    职责：
    - 协调 TuningDocWriter 写入说明文档
    - 累积调律报告（tuning_reports）
    - 管理装备回收补位标记
    """

    def __init__(self):
        # 文档
        self._doc: TuningDocWriter | None = None
        self._doc_seq: int = 0
        self._doc_equipment_open: bool = False

        # 装备回收补位标记
        self.equipment_recycled: bool = False

        # 调律报告
        self._reports: list[dict] = []
        self._current_report: dict | None = None

    # ─── 文档生命周期 ─────────────────────────────────────

    @property
    def doc(self) -> TuningDocWriter | None:
        return self._doc

    def set_doc(self, doc: TuningDocWriter | None):
        """设置文档写手（run 开始时创建，结束时关闭）"""
        self._doc = doc
        self._doc_seq = 0

    def close_doc(self):
        """关闭文档写手"""
        if self._doc is not None:
            self._doc.close()
            self._doc = None

    # ─── 文档写入代理 ─────────────────────────────────────

    def doc_start_run(self, username: str, rules_desc: list[str],
                      slots: list[str], switches: dict[str, bool]):
        """文档头：开始时间、操作用户、启用规则及玩法、开关、部位"""
        if self._doc:
            self._doc.start_run(username, rules_desc, slots, switches)

    def doc_start_equipment(self, equip: dict):
        """装备节开始：名称 · 类型（等级 品阶）+ 进入调律时已有词条列表"""
        if self._doc:
            self._doc_seq += 1
            self._doc.start_equipment(self._doc_seq, equip)
            self._doc_equipment_open = True

    def doc_worthiness_matched(self, potential: dict):
        """符合的规则：只写命中 顶级/优秀 的规则"""
        if self._doc:
            self._doc.worthiness_matched(potential)

    def doc_note(self, text: str):
        """异常/特殊情况说明行"""
        if self._doc:
            self._doc.note(text)

    def doc_food_strategy(self, text: str):
        """狗粮策略一行"""
        if self._doc:
            self._doc.food_strategy(text)

    def doc_tune_round(self, round_no: int, food: str, new_affix: str):
        """一轮调律：添加的材料 → 产出的新词条"""
        if self._doc:
            self._doc.tune_round(round_no, food, new_affix)

    def doc_round_decision(self, text: str):
        """紧随轮次之后的继续/结束判定说明"""
        if self._doc:
            self._doc.round_decision(text)

    def doc_finish_equipment(self, rounds: int, affix_count: int,
                             stop_reason: str, judgement: dict):
        """本件收尾：最终评级 + 小结"""
        if self._doc:
            self._doc.finish_equipment(rounds, affix_count, stop_reason,
                                       judgement)
            self._doc_equipment_open = False

    def doc_end_run(self, interrupted: bool, tuned_count: int,
                    total_rounds: int):
        """运行结束：结束时间、正常/中断、实际调律件数与总轮数"""
        if self._doc:
            self._doc.end_run(interrupted, tuned_count, total_rounds)

    def doc_run_summary(self, items: list[dict]):
        """成品清单"""
        if self._doc:
            self._doc.run_summary(items)

    # ─── 报告管理 ─────────────────────────────────────────

    def start_report(self, name: str, equip: dict, affix_count: int):
        """开始一件装备的报告"""
        self._current_report = {
            "name": name,
            "type": equip.get("type"),
            "level": equip.get("level"),
            "quality": equip.get("quality"),
            "affix_count": affix_count,
            "resets": 0,
        }

    @property
    def current_report(self) -> dict | None:
        return self._current_report

    def report_set(self, key: str, value):
        """设置当前报告的字段"""
        if self._current_report is not None:
            self._current_report[key] = value

    def report_append(self, key: str, value):
        """追加到当前报告的列表字段"""
        if self._current_report is not None:
            self._current_report.setdefault(key, []).append(value)

    def commit_report(self, status: str = "tuned"):
        """提交当前报告到报告列表"""
        if self._current_report is not None:
            self._current_report["status"] = status
            self._reports.append(self._current_report)
            self._current_report = None

    def discard_report(self):
        """丢弃当前报告（不提交）"""
        self._current_report = None

    def collect_reports(self) -> list[dict]:
        """获取所有已提交的报告"""
        return self._reports

    def clear_reports(self):
        """清空报告列表（run 开始时调用）"""
        self._reports.clear()
        self._current_report = None

    def finalize_interrupted_current(self) -> bool:
        """封存 F10 时尚未正常提交的当前装备报告。"""
        if self._current_report is None:
            return False
        report = self._current_report
        rounds = int(report.get("rounds") or len(report.get("tune_results") or []))
        affix_count = int(report.get("final_affix_count")
                          or report.get("affix_count") or 0)
        report.setdefault("rounds", rounds)
        report.setdefault("final_affix_count", affix_count)
        report.setdefault("final_affixes", report.get("latest_affixes") or [])
        report.setdefault("final_judgement", {})
        report["stop_reason"] = "用户中断（F10）"
        if self._doc_equipment_open:
            self.doc_note("用户中断（F10），保存当前装备的部分调律结果")
            self.doc_finish_equipment(
                rounds, affix_count, report["stop_reason"],
                report.get("final_judgement") or {})
        self.commit_report("interrupted")
        return True

    # ─── 成品清单 ─────────────────────────────────────────

    @staticmethod
    def summary_items(tuned: list[dict]) -> list[dict]:
        """从已调律 report 筛选最终评级一般及以上的成品清单项

        取各适用规则（非跳过/非不适用）的最高评级为准；
        rating_text 罗列各适用规则的评级结论。已回收的装备不入清单。
        """
        label_to_key = {v: k for k, v in RATING_LABELS.items()}
        items: list[dict] = []
        for r in tuned:
            if r.get("recycled"):
                continue
            ratings: list[str] = []
            best: str | None = None
            for j in (r.get("final_judgement") or {}).values():
                if j.get("skipped") or j.get("not_applicable"):
                    continue
                rating = j.get("rating", "")
                ratings.append(f"{j.get('name', '')}：{rating}")
                key = label_to_key.get(rating)
                if key and (best is None
                            or RATING_RANK[key] > RATING_RANK[best]):
                    best = key
            if best is None or RATING_RANK[best] < RATING_RANK["normal"]:
                continue
            items.append({
                "name": r.get("name"),
                "type": r.get("type"),
                "level": r.get("level"),
                "quality": r.get("quality"),
                "rating_text": "；".join(ratings),
                "affixes": r.get("final_affixes") or [],
            })
        return items

    # ─── 规则描述 ─────────────────────────────────────────

    @staticmethod
    def describe_rules(judge_configs: dict) -> list[str]:
        """由判定配置生成规则描述：显示名（玩法：勾选项）"""
        if not judge_configs:
            return []
        names = get_rule_names()
        descs = []
        for key, cfg in judge_configs.items():
            name = names.get(key, key)
            playstyles = cfg.get("playstyles")
            if playstyles:
                name += f"（玩法：{'、'.join(playstyles)}）"
            descs.append(name)
        return descs

    # ─── 重置状态 ─────────────────────────────────────────

    def reset(self):
        """重置所有状态（run 开始时调用）"""
        self.equipment_recycled = False
        self._doc_seq = 0
        self._doc_equipment_open = False
        self._reports.clear()
        self._current_report = None
