"""调律进度对话框 —— 非模态浮动窗口，实时展示调律过程

连接 TuningProgressHub 信号，在调律工作流运行期间展示：
- 当前装备信息（名称、类型、等级、品阶）
- 当前词条列表（按 cap_pct 着色）
- 目标词条清单（已出 / 未出）
- 预期评级 + 当前轮次
- 批次进度（部位 X / Y）
- 最近调律结果

工作流结束后自动标记完成，用户可手动关闭。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from .tuning_progress_hub import TuningProgressHub

# 品阶 key → 颜色
_QUALITY_COLORS = {
    "gold": "#FFD700",
    "purple": "#9C27B0",
    "blue": "#2196F3",
}

# 评级 key → 中文 + 颜色
_RATING_STYLE = {
    "top": ("顶级", "#FF6F00; font-weight: bold"),
    "excellent": ("优秀", "#388E3C"),
    "normal": ("能用", "#757575"),
    "junk": ("垃圾", "#D32F2F"),
}


class _HideOnCloseDialog(QDialog):
    """关闭时隐藏而非销毁的 QDialog 子类"""

    def closeEvent(self, event):
        event.ignore()
        self.hide()


class TuningProgressDialog:
    """调律进度对话框（非模态，独立窗口）

    持有 QDialog 实例并连接 TuningProgressHub 信号。
    工作流运行期间实时更新 UI，结束后标记完成。
    关闭窗口 = 隐藏（不销毁），可通过“打开进度”按钮重新显示。
    """

    MAX_AFFIX = 5  # 装备最大词条数（固定占位，避免布局抖动）

    def __init__(self, hub: TuningProgressHub):
        self._hub = hub
        self._build_ui()
        self._connect_signals()
        # 内部计数
        self._equipment_count = 0
        self._round_count = 0

    @staticmethod
    def _format_affix_lines(affixes: list[dict], count: int | None = None) -> str:
        """格式化词条列表为固定 MAX_AFFIX 行的文本（空位灰色占位）"""
        total = TuningProgressDialog.MAX_AFFIX
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
        header = f"当前词条（{n}/{total}）："
        return header + "\n" + "\n".join(lines)

    # ─── UI 构建 ─────────────────────────────────────────────

    def _build_ui(self):
        self._dialog = _HideOnCloseDialog(None)  # 独立窗口，关闭=隐藏
        self._dialog.setWindowTitle("调律进度")
        self._dialog.setMinimumWidth(420)
        self._dialog.setModal(False)
        self._dialog.setWindowFlag(Qt.WindowType.Window, True)

        layout = QVBoxLayout(self._dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── 批次进度条 ──
        batch_group = QGroupBox("批次进度")
        batch_layout = QVBoxLayout(batch_group)
        self._batch_label = QLabel("准备中...")
        self._batch_label.setStyleSheet("font-size: 12px;")
        batch_layout.addWidget(self._batch_label)
        self._batch_progress = QProgressBar()
        self._batch_progress.setValue(0)
        batch_layout.addWidget(self._batch_progress)
        layout.addWidget(batch_group)

        # ── 当前装备 ──
        equip_group = QGroupBox("当前装备")
        equip_layout = QVBoxLayout(equip_group)
        self._equip_name_label = QLabel("等待中...")
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
        affix_group = QGroupBox("词条进度")
        affix_layout = QVBoxLayout(affix_group)
        self._affix_current_label = QLabel("当前词条：-")
        self._affix_current_label.setStyleSheet("font-size: 12px;")
        self._affix_current_label.setWordWrap(True)
        affix_layout.addWidget(self._affix_current_label)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        affix_layout.addWidget(line)

        self._target_label = QLabel("目标词条：-")
        self._target_label.setStyleSheet("font-size: 12px;")
        self._target_label.setWordWrap(True)
        affix_layout.addWidget(self._target_label)
        layout.addWidget(affix_group)

        # ── 调律状态 ──
        status_group = QGroupBox("调律状态")
        status_layout = QVBoxLayout(status_group)
        self._round_label = QLabel("轮次：0")
        self._round_label.setStyleSheet("font-size: 12px;")
        status_layout.addWidget(self._round_label)
        self._expect_label = QLabel("预期评级：-")
        self._expect_label.setStyleSheet("font-size: 12px;")
        status_layout.addWidget(self._expect_label)
        self._last_result_label = QLabel("")
        self._last_result_label.setStyleSheet(
            "font-size: 11px; color: #555; padding: 2px;")
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

        # ── 关闭按钮 ──
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self._close_btn = QPushButton("关闭")
        self._close_btn.setEnabled(False)
        self._close_btn.clicked.connect(self._on_close_clicked)
        self._close_btn.setFixedWidth(80)
        btn_layout.addWidget(self._close_btn)
        layout.addLayout(btn_layout)

    # ─── 信号连接 ─────────────────────────────────────────────

    def _connect_signals(self):
        self._hub.slot_entered.connect(self._on_slot_entered)
        self._hub.equipment_started.connect(self._on_equipment_started)
        self._hub.tune_round_completed.connect(self._on_tune_round_completed)
        self._hub.equipment_finished.connect(self._on_equipment_finished)
        self._hub.batch_progress.connect(self._on_batch_progress)
        self._hub.tuning_finished.connect(self._on_tuning_finished)
        self._hub.status_message.connect(self._on_status_message)
        self._hub.scan_decision.connect(self._on_scan_decision)

    # ─── 槽函数 ─────────────────────────────────────────────

    # ─── 公共方法 ─────────────────────────────────────────

    def show(self):
        self._dialog.show()
        self._dialog.raise_()

    def hide(self):
        self._dialog.hide()

    def is_visible(self) -> bool:
        return self._dialog.isVisible()

    def close(self):
        self._dialog.close()

    def reconnect(self, hub: TuningProgressHub):
        """切换到新 hub（每次启动工作流 hub 重建，对话框需重新连接）"""
        # 断开旧连接
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
            pass  # 部分信号可能未连接
        self._hub = hub
        self._connect_signals()

    def reset_state(self):
        """重置 UI 到初始状态（新工作流启动时调用）"""
        self._equipment_count = 0
        self._round_count = 0
        self._batch_label.setText("准备中...")
        self._batch_progress.setValue(0)
        self._equip_name_label.setText("等待中...")
        self._equip_info_label.setText("")
        self._affix_current_label.setText("当前词条：-")
        self._target_label.setText("目标词条：-")
        self._round_label.setText("轮次：0")
        self._expect_label.setText("预期评级：-")
        self._last_result_label.setText("")
        self._status_msg_label.setVisible(False)
        self._scan_decision_label.setVisible(False)
        self._material_label.setVisible(False)
        self._close_btn.setEnabled(False)
        self._close_btn.setText("关闭")

    def mark_done(self):
        """工作流结束回调：启用关闭按钮"""
        self._close_btn.setEnabled(True)
        self._close_btn.setText("完成")

    def _on_close_clicked(self):
        """关闭按钮：隐藏窗口而非销毁"""
        self._dialog.hide()

    def _on_status_message(self, message: str):
        """显示工作流状态消息（如材料不足确认）"""
        self._status_msg_label.setText(message)
        self._status_msg_label.setVisible(True)

    def _on_slot_entered(self, slot_key: str, slot_name: str):
        self._batch_label.setText(f"正在处理：{slot_name}")

    def _on_equipment_started(self, info: dict):
        self._equipment_count += 1
        self._round_count = 0
        # 装备名称 + 品阶颜色
        name = info.get("name") or info.get("type") or "未知"
        quality = info.get("quality", "")
        color = _QUALITY_COLORS.get(quality, "#333")
        self._equip_name_label.setText(
            f"<span style='color:{color}'>{name}</span>")
        # 基础信息
        level = info.get("level", 0)
        quality_cn = {"gold": "金色", "purple": "紫色", "blue": "蓝色"}.get(
            quality, quality)
        self._equip_info_label.setText(
            f"等级 {level} | {quality_cn} | 词条 {len(info.get('affixes', []))}/5")
        # 当前词条（固定 5 行占位，避免布局抖动）
        affixes = info.get("affixes", [])
        self._affix_current_label.setText(
            self._format_affix_lines(affixes, len(affixes)))
        # 目标词条
        target = info.get("target_affixes", [])
        if target:
            # 标记已有的
            current_names = {a.get("name") for a in affixes}
            parts = []
            for t in target[:12]:  # 限制显示数量
                if t in current_names:
                    parts.append(f"✓{t}")
                else:
                    parts.append(f"○{t}")
            remaining = len(target) - 12
            suffix = f" 等+{remaining}" if remaining > 0 else ""
            self._target_label.setText("目标：" + "、".join(parts) + suffix)
        else:
            self._target_label.setText("目标：-")
        # 预期评级
        expect = info.get("expect_rating", "")
        rating_cn, rating_style = _RATING_STYLE.get(expect, (expect, "#333"))
        self._expect_label.setText(
            f"预期评级：<span style='{rating_style}'>{rating_cn}</span>")
        self._round_label.setText("轮次：0")
        self._last_result_label.setText("")
        self._scan_decision_label.setVisible(False)
        self._material_label.setVisible(False)

    def _on_tune_round_completed(self, info: dict):
        self._round_count += 1
        round_no = info.get("round_no", self._round_count)
        self._round_label.setText(f"轮次：{round_no}")
        # 更新当前词条（固定 5 行占位）
        affixes = info.get("current_affixes", [])
        affix_count = info.get("affix_count", len(affixes))
        self._affix_current_label.setText(
            self._format_affix_lines(affixes, affix_count))
        # 本轮结果
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
        # 狗粮策略说明
        if food_reason:
            self._last_result_label.setText(
                f"{result_text}<br>"
                f"<span style='color:#888'>{food_reason}</span>")
        # 材料库存快照
        stock = info.get("material_stock", {})
        if stock:
            parts = [f"{k}×{v}" for k, v in stock.items()]
            self._material_label.setText("材料：" + "、".join(parts))
            self._material_label.setVisible(True)

    def _on_equipment_finished(self, info: dict):
        name = info.get("name", "")
        rating = info.get("final_rating", "")
        rounds = info.get("rounds", 0)
        status = info.get("status", "done")
        rating_cn, rating_style = _RATING_STYLE.get(rating, (rating, "#333"))
        status_text = "已回收" if status == "recycled" else "已保留"
        self._last_result_label.setText(
            f"<b>{name}</b> → "
            f"<span style='{rating_style}'>{rating_cn}</span> "
            f"({rounds}轮, {status_text})")
        # 更新最终词条（固定 5 行占位）
        final_affixes = info.get("final_affixes", [])
        self._affix_current_label.setText(
            self._format_affix_lines(final_affixes, len(final_affixes))
            .replace("当前词条", "最终词条", 1))

    def _on_scan_decision(self, info: dict):
        """显示扫描处理决策（评级未达门槛 / 词条已满时的处置结果）"""
        name = info.get("name", "")
        action = info.get("action", "")
        reason = info.get("reason", "")
        action_labels = {
            "recycled": "回收",
            "kept": "保留",
            "force_tune": "强制调律",
            "tune_full_recycle": "调满后回收",
        }
        action_cn = action_labels.get(action, action)
        self._scan_decision_label.setText(
            f"扫描处理：{name} → <b>{action_cn}</b>\n{reason}")
        self._scan_decision_label.setVisible(True)

    def _on_batch_progress(self, info: dict):
        slot_key = info.get("current_slot", "")
        slot_idx = info.get("slot_index", 0)
        total = info.get("total_slots", 0)
        from lvjiang.apps.yysls.ui.tuning_progress_hub import SLOT_NAMES
        slot_cn = SLOT_NAMES.get(slot_key, slot_key)
        self._batch_label.setText(f"正在处理：{slot_cn}（{slot_idx}/{total}）")
        self._batch_progress.setMaximum(total)
        self._batch_progress.setValue(slot_idx)

    def _on_tuning_finished(self, info: dict):
        total = info.get("total_equipment", 0)
        rounds = info.get("total_rounds", 0)
        interrupted = info.get("interrupted", False)
        suffix = "（已中断）" if interrupted else ""
        self._batch_label.setText(
            f"调律结束{suffix}：{total} 件装备，{rounds} 轮调律")
        self._batch_progress.setValue(self._batch_progress.maximum())
        self._close_btn.setEnabled(True)
        self._close_btn.setText("关闭")
