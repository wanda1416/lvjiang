"""调律装备总览使用的只读卡片。"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)

from .....i18n import tr
from ...config.tune_slots import SLOT_LABELS
from ...core.tuning_rules import RATING_LABELS
from .result_store import (
    RESET_ANOMALIES,
    RESET_COMPLETED,
    RESET_COOLDOWN,
    RESET_COUNT_UNREADABLE,
    RESET_EXHAUSTED,
    RESET_EXHAUSTED_RECYCLED,
    RESET_FAILED,
    RESET_MATERIAL_SHORTAGE,
    RESULT_RECYCLED,
    RESULT_RESET,
    RESULT_SKIPPED,
    RESULT_TUNED,
    RESULT_TUNED_RECYCLED,
    TuningEquipmentResult,
)

_QUALITY_ACCENT = {
    "gold": "#B58A35",
    "purple": "#7656A8",
    "blue": "#3D78C5",
}

_QUALITY_LABELS = {
    "gold": tr("金色"),
    "purple": tr("紫色"),
    "blue": tr("蓝色"),
    "green": tr("绿色"),
}

_RESULT_LABELS = {
    RESULT_RECYCLED: tr("已回收"),
    RESULT_SKIPPED: tr("已跳过"),
    RESULT_TUNED: tr("已调律"),
    RESULT_TUNED_RECYCLED: tr("调律后回收"),
    RESULT_RESET: tr("重置未执行"),
}

_RESULT_COLORS = {
    RESULT_RECYCLED: "#D32F2F",
    RESULT_SKIPPED: "#7A7F87",
    RESULT_TUNED: "#388E3C",
    RESULT_TUNED_RECYCLED: "#E65100",
    RESULT_RESET: "#607D8B",
}

_RESET_LABELS = {
    RESET_COMPLETED: tr("重置完毕"),
    RESET_COOLDOWN: tr("冷却期中"),
    RESET_EXHAUSTED: tr("重置次数耗尽"),
    RESET_EXHAUSTED_RECYCLED: tr("次数耗尽转回收"),
    RESET_MATERIAL_SHORTAGE: tr("传律石不够"),
    RESET_FAILED: tr("重置检查失败"),
    RESET_COUNT_UNREADABLE: tr("无法识别重置次数"),
}


def _result_label(result: TuningEquipmentResult) -> str:
    """重置终态优先于笼统的调律/回收分类。"""
    labels = {
        RESET_COMPLETED: tr("已重置"),
        RESET_COOLDOWN: tr("冷却期等待"),
        RESET_EXHAUSTED: tr("重置次数耗尽"),
        RESET_EXHAUSTED_RECYCLED: tr("次数耗尽转回收"),
        RESET_MATERIAL_SHORTAGE: tr("传律石不够"),
        RESET_FAILED: tr("重置检查失败"),
        RESET_COUNT_UNREADABLE: tr("无法识别重置次数"),
    }
    # 实时总览保持连续生命周期：成功重置后如果继续完成调律，主结果仍是
    # “已调律”。历史拆分出的重置前记录本身是 RESULT_RESET，显示“已重置”。
    if (result.reset_outcome == RESET_COMPLETED
            and result.result != RESULT_RESET):
        return _RESULT_LABELS.get(result.result, result.result)
    if result.reset_outcome in labels:
        return labels[result.reset_outcome]
    return _RESULT_LABELS.get(result.result, result.result)


def _cap_color(cap_pct) -> str:
    if cap_pct is None:
        return "palette(text)"
    if cap_pct >= 90:
        return "#B8860B"
    if cap_pct >= 70:
        return "#8B5CF6"
    return "#2563EB"


def _value_text(affix: dict) -> str:
    value = affix.get("value", "")
    if affix.get("unit") == "%" and value != "":
        return f"{value}%"
    return str(value)


class TuningResultCard(QFrame):
    """没有菜单和编辑行为的最终装备快照。"""

    clicked = pyqtSignal(object)

    def __init__(self, result: TuningEquipmentResult, parent=None):
        super().__init__(parent)
        self.result = result
        self.setObjectName("tuningResultCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(220)
        self.setMinimumHeight(215)
        accent = _QUALITY_ACCENT.get(result.quality, "#8A8A8A")
        self.setStyleSheet(
            "QFrame#tuningResultCard {"
            "background-color: palette(base);"
            "border: 1px solid palette(midlight);"
            f"border-left: 4px solid {accent};"
            "border-radius: 8px; padding: 2px; }"
            "QFrame#tuningResultCard:hover { border-color: palette(highlight); }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 7, 9, 7)
        layout.setSpacing(4)

        header = QHBoxLayout()
        self.id_label = QLabel(f"#{result.equipment_id:04d}")
        self.id_label.setStyleSheet(
            "font-size: 10px; font-weight: bold; color: palette(mid);"
            "background: palette(alternate-base); border-radius: 7px;"
            "padding: 2px 5px; border: none;"
        )
        header.addWidget(self.id_label)
        self.name_label = QLabel(result.name)
        self.name_label.setStyleSheet("font-size: 13px; font-weight: bold; border: none;")
        self.name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header.addWidget(self.name_label, 1)
        status = _result_label(result)
        self.status_label = QLabel(status)
        self.status_label.setStyleSheet(
            f"color: white; background: {_RESULT_COLORS.get(result.result, '#607D8B')};"
            "border: none; border-radius: 8px; padding: 2px 7px;"
            "font-size: 11px; font-weight: bold;"
        )
        header.addWidget(self.status_label)
        layout.addLayout(header)

        quality = _QUALITY_LABELS.get(result.quality, result.quality or tr("未知品阶"))
        rating = RATING_LABELS.get(
            result.final_rating, result.final_rating or tr("未评级"))
        level = result.level if result.level is not None else "-"
        slot = SLOT_LABELS.get(result.slot_key, result.slot_key)
        self.info_label = QLabel(
            tr("{slot} · {type} · Lv{level} · {quality} · {rounds} 轮 · {rating}").format(
                slot=slot,
                type=result.type or tr("未知类型"),
                level=level, quality=quality, rounds=result.rounds, rating=rating,
            )
        )
        self.info_label.setWordWrap(True)
        self.info_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.info_label.setMinimumWidth(0)
        self.info_label.setStyleSheet("font-size: 11px; color: palette(mid); border: none;")
        layout.addWidget(self.info_label)

        self.reset_label: QLabel | None = None
        if result.reset_outcome:
            reset_text = _RESET_LABELS.get(
                result.reset_outcome, result.reset_outcome)
            anomaly = result.reset_outcome in RESET_ANOMALIES
            self.reset_label = QLabel(
                (tr("异常：{result}") if anomaly else tr("重置结果：{result}"))
                .format(result=reset_text))
            self.reset_label.setProperty("anomaly", anomaly)
            self.reset_label.setStyleSheet(
                "font-size: 11px; font-weight: bold; border: none; color: "
                + ("#D32F2F" if anomaly else "#8A5A00") + ";")
            layout.addWidget(self.reset_label)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: palette(midlight); border: none;")
        layout.addWidget(line)

        self.affix_labels: list[QLabel] = []
        for affix in result.final_affixes:
            row = QHBoxLayout()
            name = QLabel(str(affix.get("name") or "?"))
            name.setStyleSheet("font-size: 11px; border: none;")
            row.addWidget(name, 1)
            value = QLabel(_value_text(affix))
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            value.setStyleSheet("font-size: 11px; border: none;")
            row.addWidget(value)
            cap = affix.get("cap_pct")
            if cap is not None:
                cap_label = QLabel(f"{cap:g}%" if isinstance(cap, (int, float)) else f"{cap}%")
                cap_label.setAlignment(Qt.AlignmentFlag.AlignRight)
                cap_label.setStyleSheet(
                    f"font-size: 11px; font-weight: bold; color: {_cap_color(cap)}; border: none;"
                )
                cap_label.setMinimumWidth(38)
                row.addWidget(cap_label)
            layout.addLayout(row)
            self.affix_labels.append(name)
        if not result.final_affixes:
            empty = QLabel(tr("无可展示词条"))
            empty.setStyleSheet("font-size: 11px; color: palette(mid); border: none;")
            layout.addWidget(empty)

        layout.addStretch(1)
        reason_title = QLabel(tr("处理意见"))
        reason_title.setStyleSheet("font-size: 11px; font-weight: bold; border: none;")
        layout.addWidget(reason_title)
        self.reason_label = QLabel(result.reason)
        self.reason_label.setWordWrap(True)
        # QLabel 的 sizeHint 默认会参考整句文本的单行宽度，长原因
        # 会反过来撑宽卡片。忽略水平 sizeHint，强制在卡片宽度内换行。
        self.reason_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.reason_label.setMinimumWidth(0)
        self.reason_label.setMaximumWidth(420)
        self.reason_label.setStyleSheet(
            "font-size: 11px; color: palette(mid); border: none;"
            "background: palette(alternate-base); border-radius: 3px; padding: 4px;"
        )
        layout.addWidget(self.reason_label)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.result)
