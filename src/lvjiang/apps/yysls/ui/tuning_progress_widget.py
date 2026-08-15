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
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr
from .tuning_progress_hub import TuningProgressHub

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
        layout.addWidget(equip_group)

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
        layout.addWidget(affix_group)

        # ── 调律状态 ──
        status_group = QGroupBox(tr("调律状态"))
        status_layout = QVBoxLayout(status_group)
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
        layout.addWidget(status_group)

        layout.addStretch()

    # ─── 信号连接 ─────────────────────────────────────────────

    def _connect_signals(self):
        if self._hub is None:
            return
        self._hub.slot_entered.connect(self._on_slot_entered)
        self._hub.equipment_started.connect(self._on_equipment_started)
        self._hub.tune_round_completed.connect(self._on_tune_round_completed)
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
            self._hub.tune_round_completed.disconnect(self._on_tune_round_completed)
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
        self._batch_label.setText(tr("准备中..."))
        self._batch_progress.setValue(0)
        self._equip_name_label.setText(tr("等待中..."))
        self._equip_info_label.setText("")
        self._affix_current_label.setText(tr("当前词条：-"))
        self._target_label.setText(tr("目标词条：-"))
        self._round_label.setText(tr("轮次：0"))
        self._expect_label.setText(tr("最大预期：-"))
        self._actual_label.setText(tr("实际评级：-"))
        self._last_result_label.setText("")
        self._status_msg_label.setVisible(False)
        self._scan_decision_label.setVisible(False)
        self._material_label.setVisible(False)

    def mark_done(self):
        """工作流结束回调"""
        pass  # Tab 模式无需按钮状态变更

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

    def _on_slot_entered(self, slot_key: str, slot_name: str):
        self._batch_label.setText(tr("正在处理：{name}").format(name=slot_name))

    def _on_equipment_started(self, info: dict):
        self._equipment_count += 1
        self._round_count = 0
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
        rating_cn, rating_style = _RATING_STYLE.get(expect, (expect, "#333"))
        self._expect_label.setText(
            f"{tr('最大预期：')}<span style='{rating_style}'>{tr(rating_cn)}</span>")
        self._actual_label.setText(tr("实际评级：-"))
        self._round_label.setText(tr("轮次：0"))
        self._last_result_label.setText("")
        self._scan_decision_label.setVisible(False)
        self._material_label.setVisible(False)

    def _on_tune_round_completed(self, info: dict):
        self._round_count += 1
        round_no = info.get("round_no", self._round_count)
        self._round_label.setText(tr("轮次：{n}").format(n=round_no))
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

    def _on_equipment_finished(self, info: dict):
        name = info.get("name", "")
        rating = info.get("final_rating", "")
        rounds = info.get("rounds", 0)
        status = info.get("status", "done")
        rating_cn, rating_style = _RATING_STYLE.get(rating, (rating, "#333"))
        status_text = tr("已回收") if status == "recycled" else tr("已保留")
        self._last_result_label.setText(
            f"<b>{name}</b> → "
            f"<span style='{rating_style}'>{tr(rating_cn)}</span> "
            f"({rounds}轮, {status_text})")
        if rating:
            self._actual_label.setText(
                f"{tr('实际评级：')}<span style='{rating_style}'>{tr(rating_cn)}</span>")
        else:
            self._actual_label.setText(tr("实际评级：-"))
        final_affixes = info.get("final_affixes", [])
        self._affix_current_label.setText(
            self._format_affix_lines(final_affixes, len(final_affixes),
                                     header_label=tr("最终词条")))

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

    def _on_batch_progress(self, info: dict):
        slot_key = info.get("current_slot", "")
        slot_idx = info.get("slot_index", 0)
        total = info.get("total_slots", 0)
        from lvjiang.apps.yysls.ui.tuning_progress_hub import SLOT_NAMES
        slot_cn = SLOT_NAMES.get(slot_key, slot_key)
        self._batch_label.setText(
            tr("正在处理：{name}（{idx}/{total}）").format(
                name=slot_cn, idx=slot_idx, total=total))
        self._batch_progress.setMaximum(total)
        self._batch_progress.setValue(slot_idx)

    def _on_tuning_finished(self, info: dict):
        total = info.get("total_equipment", 0)
        rounds = info.get("total_rounds", 0)
        interrupted = info.get("interrupted", False)
        suffix = tr("（已中断）") if interrupted else ""
        self._batch_label.setText(
            tr("调律结束{suffix}：{total} 件装备，{rounds} 轮调律").format(
                suffix=suffix, total=total, rounds=rounds))
        self._batch_progress.setValue(self._batch_progress.maximum())
