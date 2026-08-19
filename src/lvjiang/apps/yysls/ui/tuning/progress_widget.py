"""调律进度控件 —— 可嵌入 Tab 或 Dialog 的 QWidget

连接 TuningProgressHub 信号，在调律工作流运行期间展示：
- 批次进度（部位 X / Y）
- 当前装备信息（名称、类型、等级、品阶）
- 词条进度（当前 / 目标）
- 调律状态（轮次、预期评级、实际评级、最近结果）

hub 可在构造时为空，工作流启动后通过 reconnect() 绑定。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr
from .progress_hub import TuningProgressHub

# 品阶 key → 颜色
_QUALITY_COLORS = {
    "gold": "#FFD700",
    "purple": "#9C27B0",
    "blue": "#2196F3",
}

# 评级 key → 中文 + 颜色
_RATING_STYLE = {
    "top": (tr("顶级"), "#FF6F00; font-weight: bold"),
    "excellent": (tr("优秀"), "#388E3C"),
    "normal": (tr("能用"), "#757575"),
    "junk": (tr("垃圾"), "#D32F2F"),
}

_MAX_AFFIX = 5  # 装备最大词条数（固定占位，避免布局抖动）


class TuningProgressWidget(QWidget):
    """调律进度控件（可嵌入 Tab 页或 Dialog）"""

    def __init__(self, hub: TuningProgressHub | None = None, parent=None):
        super().__init__(parent)
        self._hub = hub
        self._equipment_count = 0
        self._round_count = 0
        self._current_equipment_info: dict = {}
        self._current_events: list[str] = []
        self._build_ui()
        if hub is not None:
            self._connect_signals()

    # ─── UI 构建 ─────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── 批次进度条 ──
        batch_group = QGroupBox(tr("批次进度"))
        batch_layout = QVBoxLayout(batch_group)
        self._batch_label = QLabel(tr("准备中..."))
        self._batch_label.setStyleSheet("font-size: 12px;")
        batch_layout.addWidget(self._batch_label)
        self._batch_progress = QProgressBar()
        self._batch_progress.setValue(0)
        batch_layout.addWidget(self._batch_progress)
        layout.addWidget(batch_group)

        # ── 双列装备区：左侧实时，右侧固定保留刚完成的一件 ──
        columns = QWidget()
        columns_layout = QHBoxLayout(columns)
        columns_layout.setContentsMargins(0, 0, 0, 0)
        columns_layout.setSpacing(8)
        current_panel = QWidget()
        current_layout = QVBoxLayout(current_panel)
        current_layout.setContentsMargins(0, 0, 0, 0)
        current_layout.setSpacing(8)
        columns_layout.addWidget(current_panel, 1)

        # ── 当前装备 ──
        equip_group = QGroupBox(tr("当前装备"))
        equip_layout = QVBoxLayout(equip_group)
        self._equip_name_label = QLabel(tr("等待中..."))
        self._equip_name_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        equip_layout.addWidget(self._equip_name_label)
        self._equip_info_label = QLabel("")
        self._equip_info_label.setStyleSheet("font-size: 12px; color: #666;")
        equip_layout.addWidget(self._equip_info_label)
        self._scan_decision_label = QLabel("")
        self._scan_decision_label.setStyleSheet(
            "font-size: 12px; color: #1565C0; padding: 2px;"
            "background: #E3F2FD; border-radius: 3px;")
        self._scan_decision_label.setWordWrap(True)
        self._scan_decision_label.setVisible(False)
        equip_layout.addWidget(self._scan_decision_label)
        current_layout.addWidget(equip_group)

        # ── 词条进度 ──
        affix_group = QGroupBox(tr("词条进度"))
        affix_layout = QVBoxLayout(affix_group)
        self._affix_current_label = QLabel(tr("当前词条：-"))
        self._affix_current_label.setStyleSheet("font-size: 12px;")
        self._affix_current_label.setWordWrap(True)
        affix_layout.addWidget(self._affix_current_label)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        affix_layout.addWidget(line)

        self._target_label = QLabel(tr("目标词条：-"))
        self._target_label.setStyleSheet("font-size: 12px;")
        self._target_label.setWordWrap(True)
        affix_layout.addWidget(self._target_label)
        current_layout.addWidget(affix_group)

        rule_group = QGroupBox(tr("规则评级"))
        rule_layout = QVBoxLayout(rule_group)
        self._rule_ratings_label = QLabel(tr("等待评级..."))
        self._rule_ratings_label.setWordWrap(True)
        self._rule_ratings_label.setStyleSheet("font-size: 12px;")
        rule_layout.addWidget(self._rule_ratings_label)
        current_layout.addWidget(rule_group)

        # ── 调律状态 ──
        status_group = QGroupBox(tr("调律状态"))
        status_layout = QVBoxLayout(status_group)
        self._operation_label = QLabel(tr("当前阶段：等待开始"))
        self._operation_label.setStyleSheet(
            "font-size: 12px; color: #0D47A1; padding: 4px;"
            "background: #E3F2FD; border-radius: 4px; font-weight: bold;")
        self._operation_label.setWordWrap(True)
        status_layout.addWidget(self._operation_label)
        self._round_label = QLabel(tr("轮次：0"))
        self._round_label.setStyleSheet("font-size: 12px;")
        status_layout.addWidget(self._round_label)
        self._expect_label = QLabel(tr("最大预期：-"))
        self._expect_label.setStyleSheet("font-size: 12px;")
        status_layout.addWidget(self._expect_label)
        self._actual_label = QLabel(tr("实际评级：-"))
        self._actual_label.setStyleSheet("font-size: 12px;")
        status_layout.addWidget(self._actual_label)
        self._last_result_label = QLabel("")
        self._last_result_label.setStyleSheet("font-size: 11px; color: #555; padding: 2px;")
        self._last_result_label.setWordWrap(True)
        status_layout.addWidget(self._last_result_label)
        self._status_msg_label = QLabel("")
        self._status_msg_label.setStyleSheet(
            "font-size: 12px; color: #E65100; padding: 4px;"
            "background: #FFF3E0; border-radius: 4px;")
        self._status_msg_label.setWordWrap(True)
        self._status_msg_label.setVisible(False)
        self._material_label = QLabel("")
        self._material_label.setStyleSheet(
            "font-size: 11px; color: #555; padding: 2px;"
            "background: #F5F5F5; border-radius: 3px;")
        self._material_label.setWordWrap(True)
        self._material_label.setVisible(False)
        status_layout.addWidget(self._material_label)
        status_layout.addWidget(self._status_msg_label)
        current_layout.addWidget(status_group)
        current_layout.addStretch()

        self._previous_group = QGroupBox(tr("上一件装备"))
        previous_layout = QVBoxLayout(self._previous_group)
        self._previous_name_label = QLabel(tr("暂无已完成装备"))
        self._previous_name_label.setStyleSheet(
            "font-size: 14px; font-weight: bold;")
        previous_layout.addWidget(self._previous_name_label)
        self._previous_info_label = QLabel("")
        self._previous_info_label.setStyleSheet("font-size: 12px; color: #666;")
        self._previous_info_label.setWordWrap(True)
        previous_layout.addWidget(self._previous_info_label)
        self._previous_process = QPlainTextEdit()
        self._previous_process.setReadOnly(True)
        self._previous_process.setPlaceholderText(tr("当前装备完成后将在这里归档"))
        previous_layout.addWidget(self._previous_process, 1)
        columns_layout.addWidget(self._previous_group, 1)
        layout.addWidget(columns, 1)

    # ─── 信号连接 ─────────────────────────────────────────────

    def _connect_signals(self):
        if self._hub is None:
            return
        self._hub.slot_entered.connect(self._on_slot_entered)
        self._hub.equipment_started.connect(self._on_equipment_started)
        self._hub.equipment_assessed.connect(self._on_equipment_assessed)
        self._hub.round_prepared.connect(self._on_round_prepared)
        self._hub.tune_round_completed.connect(self._on_tune_round_completed)
        self._hub.operation_updated.connect(self._on_operation_updated)
        self._hub.equipment_reset.connect(self._on_equipment_reset)
        self._hub.equipment_finished.connect(self._on_equipment_finished)
        self._hub.batch_progress.connect(self._on_batch_progress)
        self._hub.tuning_finished.connect(self._on_tuning_finished)
        self._hub.status_message.connect(self._on_status_message)
        self._hub.scan_decision.connect(self._on_scan_decision)

    def _disconnect_signals(self):
        if self._hub is None:
            return
        try:
            self._hub.slot_entered.disconnect(self._on_slot_entered)
            self._hub.equipment_started.disconnect(self._on_equipment_started)
            self._hub.equipment_assessed.disconnect(self._on_equipment_assessed)
            self._hub.round_prepared.disconnect(self._on_round_prepared)
            self._hub.tune_round_completed.disconnect(self._on_tune_round_completed)
            self._hub.operation_updated.disconnect(self._on_operation_updated)
            self._hub.equipment_reset.disconnect(self._on_equipment_reset)
            self._hub.equipment_finished.disconnect(self._on_equipment_finished)
            self._hub.batch_progress.disconnect(self._on_batch_progress)
            self._hub.tuning_finished.disconnect(self._on_tuning_finished)
            self._hub.status_message.disconnect(self._on_status_message)
            self._hub.scan_decision.disconnect(self._on_scan_decision)
        except TypeError:
            pass

    # ─── 公共方法 ─────────────────────────────────────────────

    def reconnect(self, hub: TuningProgressHub):
        """切换到新 hub（每次启动工作流 hub 重建）"""
        self._disconnect_signals()
        self._hub = hub
        self._connect_signals()

    def reset_state(self):
        """重置 UI 到初始状态（新工作流启动时调用）"""
        self._equipment_count = 0
        self._round_count = 0
        self._current_equipment_info = {}
        self._current_events = []
        self._batch_label.setText(tr("准备中..."))
        self._batch_progress.setValue(0)
        self._equip_name_label.setText(tr("等待中..."))
        self._equip_info_label.setText("")
        self._affix_current_label.setText(tr("当前词条：-"))
        self._target_label.setText(tr("目标词条：-"))
        self._rule_ratings_label.setText(tr("等待评级..."))
        self._round_label.setText(tr("轮次：0"))
        self._operation_label.setText(tr("当前阶段：等待开始"))
        self._expect_label.setText(tr("最大预期：-"))
        self._actual_label.setText(tr("实际评级：-"))
        self._last_result_label.setText("")
        self._status_msg_label.setVisible(False)
        self._scan_decision_label.setVisible(False)
        self._material_label.setVisible(False)
        self._previous_name_label.setText(tr("暂无已完成装备"))
        self._previous_group.setTitle(tr("上一件装备"))
        self._previous_info_label.setText("")
        self._previous_process.clear()

    def mark_done(self):
        """工作流结束回调"""
        pass  # Tab 模式无需按钮状态变更

    def _record_event(self, text: str) -> None:
        """记录当前装备的紧凑过程，完成后整体归档到右栏。"""
        text = str(text or "").strip()
        if text and (not self._current_events or self._current_events[-1] != text):
            self._current_events.append(text)

    def _archive_current_equipment(self, finish: dict) -> None:
        """将当前装备快照和过程原子切换到“上一件装备”栏。"""
        started = self._current_equipment_info
        name = finish.get("name") or started.get("name") or started.get("type") or tr("未知")
        quality = started.get("quality", "")
        quality_cn = {
            "gold": tr("金色"), "purple": tr("紫色"), "blue": tr("蓝色")
        }.get(quality, quality or tr("未知品阶"))
        level = started.get("level")
        rounds = finish.get("rounds", 0)
        rating = finish.get("final_rating", "")
        rating_cn = _RATING_STYLE.get(rating, (rating or tr("未评级"), ""))[0]
        status = finish.get("status", "done")
        status_labels = {
            "recycled": tr("已回收"),
            "done": tr("已保留"),
            "skipped": tr("已跳过"),
            "reset_before": tr("重置前快照（已执行重置）"),
            "interrupted": tr("调律中断"),
            "below_level": tr("等级不足，部位结束"),
            "invalid_quality": tr("品阶识别异常"),
            "locked": tr("已锁定，跳过"),
            "already_full": tr("满词条，扫描完成"),
            "no_tune_entry": tr("未找到调律入口"),
            "skip_tuning": tr("测试模式跳过调律"),
        }
        is_reset_before = status == "reset_before"
        self._previous_group.setTitle(
            tr("上一件装备 · 重置前") if is_reset_before else tr("上一件装备"))
        self._previous_name_label.setText(name)
        if is_reset_before:
            self._previous_name_label.setStyleSheet(
                "font-size: 14px; font-weight: bold; color: #E65100;")
        else:
            self._previous_name_label.setStyleSheet(
                "font-size: 14px; font-weight: bold;")
        self._previous_info_label.setText(
            tr("等级 {level} | {quality} | {rounds} 轮 | {rating} | {status}").format(
                level=level if level is not None else "-",
                quality=quality_cn, rounds=rounds,
                rating=tr(rating_cn), status=status_labels.get(status, status)))

        lines = list(self._current_events)
        final_affixes = finish.get("final_affixes", [])
        if final_affixes:
            lines.append(tr("最终词条："))
            for affix in final_affixes:
                cap = affix.get("cap_pct")
                cap_text = f" ({cap}%)" if cap is not None else ""
                lines.append(
                    f"  • {affix.get('name', '?')} {affix.get('value', '')}{cap_text}")
        self._previous_process.setPlainText("\n".join(lines))

    def _on_equipment_reset(self, info: dict):
        """重置是一条装备生命周期边界：先归档旧状态，再启动新状态。"""
        before_name = info.get("name") or self._current_equipment_info.get("name") or tr("未知")
        self._record_event(tr("重置边界：以下数据为执行重置前的装备状态"))
        self._archive_current_equipment({
            "name": f"{before_name}（重置前）",
            "final_rating": info.get("before_rating", ""),
            "rounds": self._round_count,
            "final_affixes": info.get("before_affixes", []),
            "status": "reset_before",
        })

        # 重置后的首词条状态作为新的当前装备，不继承重置前过程。
        target_affixes = self._current_equipment_info.get("target_affixes", [])
        self._on_equipment_started({
            "name": f"{before_name}（重置后）",
            "type": info.get("type", self._current_equipment_info.get("type", "")),
            "level": info.get("level", self._current_equipment_info.get("level", 0)),
            "quality": info.get("quality", self._current_equipment_info.get("quality", "")),
            "affixes": info.get("after_affixes", []),
            "expect_rating": info.get("expect_rating", ""),
            "target_affixes": target_affixes,
        })
        self._operation_label.setText(
            tr("当前阶段：重置调律 — 重置完成，按新装备继续调律"))
        self._record_event(tr("重置完成：以首词条状态重新开始调律"))

    def _clear_current_equipment(self) -> None:
        """归档后清空左栏，避免把上一件误认为仍在处理。"""
        self._current_equipment_info = {}
        self._current_events = []
        self._equip_name_label.setText(tr("等待下一件装备..."))
        self._equip_info_label.setText("")
        self._affix_current_label.setText(tr("当前词条：-"))
        self._target_label.setText(tr("目标词条：-"))
        self._rule_ratings_label.setText(tr("等待评级..."))
        self._round_label.setText(tr("轮次：0"))
        self._expect_label.setText(tr("最大预期：-"))
        self._actual_label.setText(tr("实际评级：-"))
        self._last_result_label.setText("")
        self._operation_label.setText(tr("当前阶段：等待下一件装备"))
        self._scan_decision_label.setVisible(False)
        self._status_msg_label.setVisible(False)
        self._material_label.setVisible(False)

    # ─── 格式化 ───────────────────────────────────────────────

    @staticmethod
    def _format_affix_lines(affixes: list[dict], count: int | None = None,
                            header_label: str | None = None) -> str:
        total = _MAX_AFFIX
        n = count if count is not None else len(affixes)
        lines = []
        for i in range(total):
            if i < len(affixes):
                a = affixes[i]
                cap = a.get("cap_pct")
                cap_text = f" ({cap}%)" if cap is not None else ""
                lines.append(f"  • {a.get('name', '?')} {a.get('value', '')}{cap_text}")
            else:
                lines.append("  • —")
        prefix = header_label if header_label is not None else tr("当前词条")
        header = tr("{prefix}（{n}/{total}）：").format(prefix=prefix, n=n, total=total)
        return header + "\n" + "\n".join(lines)

    # ─── 槽函数 ───────────────────────────────────────────────

    def _on_status_message(self, message: str):
        self._status_msg_label.setText(message)
        self._status_msg_label.setVisible(True)

    def _on_operation_updated(self, info: dict):
        """展示工作流正在执行的动作，而不必等待该动作完成。"""
        phase_labels = {
            "scan": tr("扫描判定"),
            "navigation": tr("页面导航"),
            "material": tr("材料准备"),
            "tuning": tr("执行调律"),
            "decision": tr("结束处理"),
            "reset": tr("重置调律"),
            "finish": tr("收尾处理"),
        }
        phase = phase_labels.get(info.get("phase", ""), info.get("phase", ""))
        message = info.get("message", "")
        reason = info.get("reason", "")
        text = tr("当前阶段：{phase}").format(phase=phase or tr("处理中"))
        if message:
            text += f" — {message}"
        if reason and reason != message:
            text += f"<br><span style='font-weight:normal'>{reason}</span>"
        self._operation_label.setText(text)
        event = f"{phase or tr('处理中')}：{message}"
        if reason and reason != message:
            event += f"｜{reason}"
        self._record_event(event)

    def _on_slot_entered(self, slot_key: str, slot_name: str):
        self._batch_label.setText(tr("正在处理：{name}").format(name=slot_name))

    def _on_equipment_started(self, info: dict):
        self._equipment_count += 1
        self._round_count = 0
        self._current_equipment_info = dict(info)
        self._current_events = []
        name = info.get("name") or info.get("type") or tr("未知")
        quality = info.get("quality", "")
        color = _QUALITY_COLORS.get(quality, "#333")
        self._equip_name_label.setText(
            f"<span style='color:{color}'>{name}</span>")
        level = info.get("level", 0)
        quality_cn = {"gold": tr("金色"), "purple": tr("紫色"), "blue": tr("蓝色")}.get(
            quality, quality)
        self._equip_info_label.setText(
            tr("等级 {level} | {quality} | 词条 {count}/5").format(
                level=level, quality=quality_cn, count=len(info.get('affixes', []))))
        affixes = info.get("affixes", [])
        self._affix_current_label.setText(
            self._format_affix_lines(affixes, len(affixes)))
        target = info.get("target_affixes", [])
        if target:
            current_names = {a.get("name") for a in affixes}
            parts = []
            for t in target[:12]:
                if t in current_names:
                    parts.append(f"✓{t}")
                else:
                    parts.append(f"○{t}")
            remaining = len(target) - 12
            suffix = f" 等+{remaining}" if remaining > 0 else ""
            self._target_label.setText(tr("目标：") + "、".join(parts) + suffix)
        else:
            self._target_label.setText(tr("目标：-"))
        expect = info.get("expect_rating", "")
        if expect:
            rating_cn, rating_style = _RATING_STYLE.get(expect, (expect, "#333"))
            self._expect_label.setText(
                f"{tr('最大预期：')}<span style='{rating_style}'>{tr(rating_cn)}</span>")
        else:
            self._expect_label.setText(tr("最大预期：判定中..."))
        self._actual_label.setText(tr("实际评级：-"))
        self._round_label.setText(tr("轮次：0"))
        self._operation_label.setText(tr("当前阶段：扫描判定 — 已读取装备，计算调律潜力"))
        self._last_result_label.setText("")
        self._scan_decision_label.setVisible(False)
        self._material_label.setVisible(False)
        self._status_msg_label.setVisible(False)
        self._rule_ratings_label.setText(tr("等待评级..."))
        self._record_event(f"读取：{name}，初始词条 {len(affixes)}/5")

    def _on_equipment_assessed(self, info: dict):
        expect = info.get("expect_rating", "")
        if expect:
            rating_cn, rating_style = _RATING_STYLE.get(expect, (expect, "#333"))
            self._expect_label.setText(
                f"{tr('最大预期：')}<span style='{rating_style}'>{tr(rating_cn)}</span>")
        plain_parts = []
        html_parts = []
        for key, result in (info.get("rule_ratings") or {}).items():
            result = result or {}
            name = result.get("name") or key
            if result.get("skipped") or result.get("not_applicable"):
                html_parts.append(f"<span style='color:#999'>{name}：不适用</span>")
                plain_parts.append(f"{name}：不适用")
                continue
            rating = result.get("rating", "")
            rating_cn, style = _RATING_STYLE.get(
                rating, (rating or tr("未评级"), "#333"))
            html_parts.append(f"{name}：<span style='{style}'>{tr(rating_cn)}</span>")
            plain_parts.append(f"{name}：{tr(rating_cn)}")
        self._rule_ratings_label.setText(
            "<br>".join(html_parts) or tr("无可用判定规则"))
        stage = info.get("stage", "scan")
        self._record_event(
            f"规则评级（{'调律后' if stage == 'round' else '初始'}）：" +
            ("；".join(plain_parts) or "无可用判定规则"))

    def _on_round_prepared(self, info: dict):
        round_no = info.get("round_no", self._round_count + 1)
        food = info.get("food_used") or tr("不添加狗粮")
        reason = info.get("food_reason", "")
        action = tr("准备执行") if info.get("will_tune", True) else tr("本轮停止")
        self._operation_label.setText(
            tr("当前阶段：材料准备 — 第 {n} 轮{action}，{food}").format(
                n=round_no, action=action, food=food))
        self._last_result_label.setText(
            tr("第 {n} 轮方案：{food}").format(n=round_no, food=food) +
            (f"<br><span style='color:#888'>{reason}</span>" if reason else ""))
        stock = info.get("material_stock") or {}
        if stock:
            self._material_label.setText(
                tr("材料：") + "、".join(f"{k}×{v}" for k, v in stock.items()))
            self._material_label.setVisible(True)
        self._record_event(
            f"第 {round_no} 轮准备：{food}" + (f"｜{reason}" if reason else ""))

    def _on_tune_round_completed(self, info: dict):
        self._round_count += 1
        round_no = info.get("round_no", self._round_count)
        self._round_label.setText(tr("轮次：{n}").format(n=round_no))
        self._operation_label.setText(
            tr("当前阶段：结束处理 — 第 {n} 轮完成，正在匹配处理规则").format(n=round_no))
        affixes = info.get("current_affixes", [])
        affix_count = info.get("affix_count", len(affixes))
        self._affix_current_label.setText(
            self._format_affix_lines(affixes, affix_count))
        new_affix = info.get("new_affix")
        food = info.get("food_used", "")
        food_reason = info.get("food_reason", "")
        if isinstance(new_affix, dict):
            result_text = (f"#{round_no} +{new_affix.get('name', '?')} "
                           f"{new_affix.get('value', '')}")
        elif isinstance(new_affix, str) and new_affix:
            result_text = f"#{round_no} +{new_affix}"
        else:
            result_text = f"#{round_no}"
        if food:
            result_text += f"（{food}）"
        self._last_result_label.setText(result_text)
        if food_reason:
            self._last_result_label.setText(
                f"{result_text}<br>"
                f"<span style='color:#888'>{food_reason}</span>")
        stock = info.get("material_stock", {})
        if stock:
            parts = [f"{k}×{v}" for k, v in stock.items()]
            self._material_label.setText(tr("材料：") + "、".join(parts))
            self._material_label.setVisible(True)
        expect = info.get("expect_rating")
        if expect:
            rating_cn, rating_style = _RATING_STYLE.get(expect, (expect, "#333"))
            self._expect_label.setText(
                f"{tr('最大预期：')}<span style='{rating_style}'>{tr(rating_cn)}</span>")
        actual = info.get("actual_rating")
        if actual:
            rating_cn, rating_style = _RATING_STYLE.get(actual, (actual, "#333"))
            self._actual_label.setText(
                f"{tr('实际评级：')}<span style='{rating_style}'>{tr(rating_cn)}</span>")
        else:
            self._actual_label.setText(tr("实际评级：-"))
        if "rule_ratings" in info:
            self._on_equipment_assessed({
                "expect_rating": expect,
                "rule_ratings": info.get("rule_ratings"),
                "stage": "round",
            })
        event = result_text
        if food_reason:
            event += f"｜{food_reason}"
        self._record_event(event)

    def _on_equipment_finished(self, info: dict):
        name = info.get("name", "")
        rating = info.get("final_rating", "")
        rounds = info.get("rounds", 0)
        status = info.get("status", "done")
        rating_cn, rating_style = _RATING_STYLE.get(rating, (rating, "#333"))
        status_text = {
            "recycled": tr("已回收"),
            "interrupted": tr("调律中断，已保存部分报告"),
            "skipped": tr("已跳过"),
            "below_level": tr("等级不足，当前部位结束"),
            "invalid_quality": tr("品阶识别异常"),
            "locked": tr("已锁定，跳过"),
            "already_full": tr("满词条，已完成扫描处理"),
            "no_tune_entry": tr("未找到调律入口"),
            "skip_tuning": tr("测试模式跳过调律"),
        }.get(status, tr("已保留"))
        reason = info.get("reason", "")
        if reason:
            self._record_event(f"结束原因：{reason}")
        self._last_result_label.setText(
            f"<b>{name}</b> → "
            f"<span style='{rating_style}'>{tr(rating_cn)}</span> "
            f"({rounds}轮, {status_text})")
        self._operation_label.setText(
            tr("当前阶段：收尾处理 — 当前装备处理完成"))
        if rating:
            self._actual_label.setText(
                f"{tr('实际评级：')}<span style='{rating_style}'>{tr(rating_cn)}</span>")
        else:
            self._actual_label.setText(tr("实际评级：-"))
        final_affixes = info.get("final_affixes", [])
        self._affix_current_label.setText(
            self._format_affix_lines(final_affixes, len(final_affixes),
                                     header_label=tr("最终词条")))
        self._record_event(
            tr("完成：{rounds} 轮，{status}").format(
                rounds=rounds, status=status_text))
        self._archive_current_equipment(info)
        self._clear_current_equipment()

    def _on_scan_decision(self, info: dict):
        name = info.get("name", "")
        action = info.get("action", "")
        reason = info.get("reason", "")
        action_labels = {
            "recycled": tr("回收"),
            "kept": tr("保留"),
            "force_tune": tr("强制调律"),
            "tune_full_recycle": tr("调满后回收"),
        }
        action_cn = action_labels.get(action, action)
        self._scan_decision_label.setText(
            tr("扫描处理：{name} → ").format(name=name) + f"<b>{action_cn}</b>\n{reason}")
        self._scan_decision_label.setVisible(True)
        self._record_event(f"扫描处理：{action_cn}｜{reason}")

    def _on_batch_progress(self, info: dict):
        slot_key = info.get("current_slot", "")
        slot_idx = info.get("slot_index", 0)
        total = info.get("total_slots", 0)
        from lvjiang.apps.yysls.ui.tuning.progress_hub import SLOT_NAMES
        slot_cn = SLOT_NAMES.get(slot_key, slot_key)
        self._batch_label.setText(
            tr("正在处理：{name}（{idx}/{total}）").format(
                name=slot_cn, idx=slot_idx, total=total))
        self._batch_progress.setMaximum(total)
        self._batch_progress.setValue(slot_idx)

    def _on_tuning_finished(self, info: dict):
        # 工作流的旧字段曾按部位计数；面板以实际收到的装备生命周期为准。
        # 重置后按产品约定视作一件新装备，equipment_started 会自然 +1。
        total = self._equipment_count
        rounds = info.get("total_rounds", 0)
        interrupted = info.get("interrupted", False)
        suffix = tr("（已中断）") if interrupted else ""
        self._batch_label.setText(
            tr("调律结束{suffix}：{total} 件装备，{rounds} 轮调律").format(
                suffix=suffix, total=total, rounds=rounds))
        self._batch_progress.setValue(self._batch_progress.maximum())
