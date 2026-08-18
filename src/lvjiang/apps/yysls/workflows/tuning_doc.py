"""调律说明文档写手 — 可交付他人阅读的叙事化 Markdown

与 loguru 技术日志完全分离：只记录"发生了什么、为什么"（操作用户、
配置、装备信息、命中的规则、每轮材料与结果、继续/结束原因），不含
坐标/OCR/滚动等实现细节。auto_tuning 每次运行生成一份新文档，
每次写入即 flush（F10 中断或崩溃时已写内容不丢失）。

口径（与需求确认一致）：
- 只写实际进入调律的装备；判定不值得/词条已满而跳过的装备完全不写；
- "符合哪个规则"只写命中 顶级/优秀 的规则，不写不符合的。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from lvjiang.constants import PROJECT_ROOT

from ....i18n import tr

# 文档输出目录（运行时自建；logs/ 不入库）
TUNING_DOC_DIR = PROJECT_ROOT / "logs" / "tuning"

# slot key → 标准部位名（与调律 Tab 部位勾选文案一致）
SLOT_NAMES = {
    "main_weapon": tr("主武器"), "sub_weapon": tr("副武器"),
    "ring": tr("环"), "pendant": tr("佩"),
    "head": tr("冠胄"), "chest": tr("胸甲"), "leg": tr("胫甲"), "wrist": tr("腕甲"),
}

# 品阶 key → 中文名
_QUALITY_NAMES = {"gold": tr("金色"), "purple": tr("紫色"), "blue": tr("蓝色")}

# 值得调律的评级（与 judge_tuning_worthiness 的 or 语义口径一致）
WORTH_RATINGS = (tr("顶级"), tr("优秀"))


def format_affix(affix: dict) -> str:
    """词条 dict → 展示文本：名称 数值单位（上限百分比）"""
    name = affix.get("name") or tr("未知词条")
    value = affix.get("value")
    unit = affix.get("unit") or ""
    text = f"{name} {value}{unit}" if value is not None else name
    cap_pct = affix.get("cap_pct")
    if cap_pct is not None:
        text += f"（{cap_pct}%）"
    return text


class TuningDocWriter:
    """调律说明文档写手（Markdown，顺序追加写 + 逐次 flush）"""

    def __init__(self, username: str, doc_dir: Path | str | None = None):
        base = Path(doc_dir) if doc_dir else TUNING_DOC_DIR
        base.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = base / f"调律说明_{username}_{ts}.md"
        self._fh = self.path.open("w", encoding="utf-8")
        # 结构化数据收集（用于末尾 JSON 数据块）
        self._json_records: list[dict] = []
        self._current_json: dict | None = None

    # ─── 底层写 ────────────────────────────────────────────

    def _write(self, text: str = ""):
        self._fh.write(text + "\n")
        self._fh.flush()

    def close(self):
        if not self._fh.closed:
            self._fh.close()

    # ─── 叙事 API ──────────────────────────────────────────

    def start_run(self, username: str, rules_desc: list[str],
                  slots: list[str], switches: dict[str, bool]):
        """文档头：开始时间、操作用户、启用规则及玩法、开关、部位

        switches 为开关显示名 → 状态（调用方负责 key → 显示名映射）。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._write(f"# 调律说明 — {now}")
        self._write()
        self._write(f"- 操作用户：{username}")
        rules_text = "、".join(rules_desc) if rules_desc else tr("全部规则（默认配置）")
        self._write(f"- 启用规则：{rules_text}")
        for name, state in (switches or {}).items():
            self._write(f"- 开关 {name}：{tr('是') if state else tr('否')}")
        slot_names = "、".join(SLOT_NAMES.get(s, s) for s in slots)
        self._write(f"- 调律部位：{slot_names}")

    def start_equipment(self, seq: int, equip: dict):
        """装备节：名称 · 类型（等级 品阶）+ 进入调律时已有词条列表"""
        name = equip.get("name") or tr("未知装备")
        etype = equip.get("type") or tr("未知类型")
        level = equip.get("level")
        quality_raw = equip.get("quality")
        quality = _QUALITY_NAMES.get(quality_raw, quality_raw or tr("品阶未知")) if quality_raw is not None else "品阶未知"
        level_txt = f"{level}级 " if level else ""
        self._write()
        self._write(f"## {seq}. {name} · {etype}（{level_txt}{quality}）")
        self._write()
        affixes = [equip.get(f"affix_{i}") for i in range(1, 6)]
        affixes = [a for a in affixes if isinstance(a, dict) and a.get("name")]
        self._write(f"进入调律时词条（{len(affixes)}/5）：")
        for a in affixes:
            assert isinstance(a, dict)
            self._write(f"- {format_affix(a)}")
        # 收集结构化数据
        self._current_json = {
            "name": name, "type": etype,
            "level": level, "quality": quality_raw,
            "initial_affixes": [
                {"name": a.get("name"), "value": a.get("value"),
                 "unit": a.get("unit") or "",
                 "cap_pct": a.get("cap_pct")}
                for a in affixes if isinstance(a, dict)
            ],
            "rounds": [],
            "final_rating": None,
            "stop_reason": None,
            "total_rounds": 0,
            "final_affix_count": len(affixes),
        }

    def worthiness_matched(self, results: dict[str, dict]):
        """符合的规则：只写命中 顶级/优秀 的规则（不写不符合的）

        Args:
            results: judge_equipment_potential 的结构化结果
        """
        self._write()
        self._write(tr("符合以下规则，开始调律："))
        for r in results.values():
            if r.get("skipped") or r.get("not_applicable"):
                continue
            if r.get("rating") not in WORTH_RATINGS:
                continue
            detail = "；".join(r.get("reasons") or [])
            self._write(f"- {r['name']}：{r['rating']}（{detail}）")

    def food_strategy(self, text: str):
        """狗粮策略一行（决策 + 依据）"""
        self._write()
        self._write(f"狗粮策略：{text}")
        self._write()

    def tune_round(self, round_no: int, food: str, new_affix: str):
        """一轮调律：添加的材料 → 产出的新词条"""
        action = (f"添加 {food} + 一键添加律准石" if food
                  else tr("一键添加律准石"))
        self._write(f"第 {round_no} 轮：{action} → 新词条「{new_affix}」")
        # 收集结构化数据
        if self._current_json is not None:
            self._current_json["rounds"].append({
                "round": round_no,
                "food": food or None,
                "new_affix": new_affix,
            })

    def round_decision(self, text: str):
        """紧随轮次之后的继续/结束判定说明"""
        self._write(f"  → {text}")

    def note(self, text: str):
        """异常/特殊情况说明行（如无调律入口、材料不足）"""
        self._write()
        self._write(f"> {text}")

    def finish_equipment(self, rounds: int, affix_count: int,
                         stop_reason: str, judgement: dict):
        """本件收尾：最终评级（有效结论，不写"不适用"）+ 小结"""
        self._write()
        lines = []
        rating_texts = []
        for r in (judgement or {}).values():
            if r.get("not_applicable"):
                continue
            tag = tr("跳过") if r.get("skipped") else r.get("rating", "")
            detail = "；".join(r.get("reasons") or [])
            lines.append(f"{r['name']}：{tag}（{detail}）")
            if not r.get("skipped"):
                rating_texts.append(f"{r['name']}：{tag}")
        final_rating_str = '；'.join(lines) if lines else tr('无有效结论')
        self._write(f"最终评级：{final_rating_str}")
        self._write(f"本件小结：共 {rounds} 轮，词条 {affix_count}/5，"
                    f"结束原因：{stop_reason}")
        # 收集结构化数据
        if self._current_json is not None:
            self._current_json["final_rating"] = (
                '；'.join(rating_texts) if rating_texts else None
            )
            self._current_json["stop_reason"] = stop_reason
            self._current_json["total_rounds"] = rounds
            self._current_json["final_affix_count"] = affix_count
            self._json_records.append(self._current_json)
            self._current_json = None

    def end_run(self, interrupted: bool, tuned_count: int, total_rounds: int):
        """运行结束：结束时间、正常/中断、实际调律件数与总轮数"""
        now = datetime.now().strftime("%H:%M:%S")
        state = tr("用户中断（F10）") if interrupted else tr("正常完成")
        self._write()
        self._write(tr("## 运行结束"))
        self._write()
        self._write(f"- 结束时间：{now}（{state}）")
        self._write(f"- 实际调律 {tuned_count} 件，共 {total_rounds} 轮")

    def run_summary(self, items: list[dict]):
        """成品清单：本次调律完成且最终评级一般及以上的装备

        每项 dict：name/type/level/quality/rating_text/affixes（首词条
        单独列出，其余词条合并一行）；筛选与排序由调用方负责。
        """
        self._write()
        self._write(tr("### 成品清单（一般及以上）"))
        self._write()
        if not items:
            self._write(tr("本次无一般及以上成品。"))
            self._write_json_block()
            return
        for i, it in enumerate(items, start=1):
            name = it.get("name") or tr("未知装备")
            etype = it.get("type") or tr("未知类型")
            level = it.get("level")
            quality_raw = it.get("quality")
            quality = _QUALITY_NAMES.get(quality_raw, quality_raw or tr("品阶未知")) if quality_raw is not None else "品阶未知"
            level_txt = f"{level}级 " if level else ""
            rating = it.get("rating_text") or tr("无有效结论")
            self._write(f"{i}. {name} · {etype}（{level_txt}{quality}）"
                        f"— {rating}")
            affixes = [a for a in (it.get("affixes") or [])
                       if isinstance(a, dict) and a.get("name")]
            if not affixes:
                self._write(tr("   - 词条未记录"))
                continue
            self._write(f"   - 首词条：{format_affix(affixes[0])}")
            rest = "、".join(format_affix(a) for a in affixes[1:])
            self._write(f"   - 其余词条：{rest if rest else tr('无')}")
        # 末尾输出结构化 JSON 数据块
        self._write_json_block()

    # ─── 结构化数据输出 ────────────────────────────────────

    def _write_json_block(self):
        """在报告末尾输出 JSON 数据块（HTML 注释包裹，不影响阅读）"""
        if not self._json_records:
            return
        self._write()
        self._write("<!-- TUNING_DATA_JSON")
        self._write(json.dumps(
            self._json_records, ensure_ascii=False, indent=2))
        self._write("TUNING_DATA_JSON -->")
