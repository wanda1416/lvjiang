"""装备卡片组件 —— 槽位卡片、背包装备卡片、状态标签栏。

从 status_tab.py 拆出，仅包含 UI 卡片组件及其依赖的常量/样式。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from functools import partial
from math import ceil
from typing import Literal

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ......i18n import tr
from ......ui.button_styles import apply_dialog_button_box_style
from ....core.affix_cap import affix_dict_cap_pct, equip_affix_cap_pcts
from ....core.equip_parser.dingyin_parser import (
    DINGYIN_NOTICE_KEY,
    is_zhige_dingyin,
)
from ....core.equip_validator import illegal_reasons_of
from ....core.equipment_cooldown import next_cooldown_expiry
from ....core.loadout.transmute import TARGET_NAME_KEY, TARGET_VALUE_KEY


class _ElidedLabel(QLabel):
    """单行文本可收缩的 QLabel —— 空间不足时以省略号截断。

    默认 QLabel 的 minimumSizeHint 等于完整文本宽度（不换行），
    会把父布局/网格列的最小宽度钉死在文字宽度上。宽度不足时
    网格无法收缩导致溢出。本类重写 paintEvent 绘制省略号，
    并压低 minimumSizeHint 使所在列可收缩到小于文字宽度。
    """

    def minimumSizeHint(self):  # type: ignore[override]
        # 保留一个最小可读宽度（约 3 个汉字 / 6 个字符宽），
        # 避免 stretch 项被压缩到 0 导致文字与兄弟控件重叠。
        # 仍允许比完整文字更窄，使网格在宽度不足时能等分收缩。
        size = super().minimumSizeHint()
        fm = self.fontMetrics()
        min_width = fm.horizontalAdvance("中") * 3
        # 取当前完整文字宽度与最小可读宽度的较小者（不强制撑到 min_width）
        size.setWidth(min(min_width, size.width()))
        return size

    def paintEvent(self, event: QPaintEvent | None):  # type: ignore[override]
        # 富文本（含内联 <span> 颜色）无法用 elidedText 直接省略，回退默认绘制
        if self.textFormat() == Qt.TextFormat.RichText:
            super().paintEvent(event)
            return
        # 手动绘制省略号：样式表颜色/字号会反映到 palette 与字体上
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        fm = painter.fontMetrics()
        text = self.text()
        avail = self.width() - self.margin() * 2 - 4
        elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, max(1, avail))
        color = self.palette().color(self.foregroundRole())
        if self.isEnabled():
            painter.setPen(color)
        else:
            painter.setPen(self.palette().color(
                self.palette().Disabled, self.foregroundRole()))
        # 垂直对齐：QLabel 默认居中；水平由 alignment 决定
        flags = int(self.alignment())
        rect = self.rect().adjusted(self.margin(), 0, -self.margin(), 0)
        painter.drawText(rect, flags, elided)
        painter.end()


# 品质背景色（半透明，仅金/紫）
_QUALITY_BG_COLORS = {
    "gold": "rgba(210, 179, 102, 0.25)",
    "purple": "rgba(113, 102, 120, 0.25)",
}

# 槽位卡片样式
_SLOT_STYLE_EMPTY = (
    "_SlotCard { background-color: palette(alternate-base); "
    "border: 2px dashed palette(mid); "
    "border-radius: 6px; }"
)


def _slot_style_normal(
    bg: str = "palette(base)", border: str = "palette(midlight)"
) -> str:
    return (
        f"_SlotCard {{ background-color: {bg}; border: 2px solid {border}; "
        "border-radius: 6px; }"
    )


def _slot_style_selected(bg: str = "palette(alternate-base)") -> str:
    return (
        f"_SlotCard {{ background-color: {bg}; border: 2px solid palette(highlight); "
        "border-radius: 7px; }"
    )


def _slot_style_hovered(bg: str = "palette(base)") -> str:
    return (
        f"_SlotCard {{ background-color: {bg}; border: 2px solid palette(mid); "
        "border-radius: 7px; }"
    )


def transmute_target_color(widget: QWidget) -> str:
    """转律目标的黄色：深色主题用亮黄，浅色主题用深琥珀，保证可读。"""
    dark = widget.palette().color(widget.backgroundRole()).lightness() < 128
    return "#FFD54F" if dark else "#B26A00"


def _transmute_target_text(affix: dict) -> str:
    """词条上保存的转律目标 ``(目标名)``；没有或字段不完整时为空。"""
    name = str(affix.get(TARGET_NAME_KEY) or "").strip()
    try:
        value = float(affix.get(TARGET_VALUE_KEY) or 0)
    except (TypeError, ValueError):
        value = 0.0
    return f"({name})" if name and value > 0 else ""


def _transmute_target_tooltip(affix: dict) -> str:
    name = str(affix.get(TARGET_NAME_KEY) or "").strip()
    value = affix.get(TARGET_VALUE_KEY)
    if not name:
        return ""
    return tr("转律目标：{name} {value}（未承音按普通上限 100%，承音按承音上限；"
              "仅在勾选「模拟转律」时计入属性）").format(name=name, value=value)


def _affix_value_color(cap_pct: int | float | None) -> str:
    """词条数值颜色：>=90 金，[70,90) 紫，<70 蓝"""
    if cap_pct is None:
        return "palette(text)"
    if cap_pct >= 90:
        return "#B8860B"
    if cap_pct >= 70:
        return "#8B5CF6"
    return "#2563EB"


# ── 标签样式 ──────────────────────────────────────────

_TAG_STYLE = (
    "color: white; border-radius: 8px; "
    "font-size: 11px; font-weight: 600; padding: 2px 7px;"
)


def _set_quality(widget: QLabel, quality: str) -> None:
    """设置动态品质属性并刷新全局主题选择器。"""
    widget.setProperty("quality", quality)
    style = widget.style()
    assert style is not None
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def _show_illegal_reasons(parent, reasons: list[str]) -> None:
    """在事件循环里展示装备异常原因。

    与触发它的控件解耦（只收窗口与原因文本的副本），原因见
    :meth:`_IllegalBadge.mousePressEvent`。
    """
    if not reasons:
        return
    try:
        parent.isVisible()  # 触碰一下：窗口已销毁时 PyQt 抛 RuntimeError
    except RuntimeError:
        parent = None       # 绝不把已释放对象当 parent 递给模态框
    QMessageBox.warning(
        parent, tr("装备状态异常"),
        tr("该装备存在以下异常，游戏中不会出现这样的装备，"
           "通常是识别误读，请手工校正：") + "\n\n"
        + "\n".join(f"· {r}" for r in reasons),
    )


def _format_equipment_time(value) -> str:
    """ISO 时间转本地可读文本；无数据保持空，绝不使用 epoch 默认值。"""
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _parse_cooldown_time(value) -> datetime | None:
    """解析冷却到期时间，无时区历史值按 UTC 处理。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cooldown_has_expired(value, *, now: datetime | None = None) -> bool:
    """有合法冷却时间且已到期。"""
    expires_at = _parse_cooldown_time(value)
    if expires_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return expires_at <= current.astimezone(timezone.utc)


def _format_cooldown_remaining(
    value,
    *,
    now: datetime | None = None,
) -> str:
    """格式化为天/小时/分钟；不足一分钟向上取整。"""
    expires_at = _parse_cooldown_time(value)
    if expires_at is None:
        return ""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    seconds = max(
        0.0,
        (expires_at - current.astimezone(timezone.utc)).total_seconds(),
    )
    total_minutes = ceil(seconds / 60)
    days, remainder = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)
    return tr("{days} 天 {hours} 小时 {minutes} 分钟").format(
        days=days, hours=hours, minutes=minutes)


def _format_card_cooldown(
    equip: dict,
    *,
    now: datetime | None = None,
) -> str:
    """生成装备卡片底部的冷却摘要；只在卡片刷新时计算。"""
    kind = str(equip.get("cooldown_kind") or "")
    state = str(equip.get("cooldown_state") or "")
    expires_at = equip.get("cooldown_expires_at")
    if not (kind or state or _parse_cooldown_time(expires_at)):
        return ""

    kind_label = {
        "reset": tr("重置调律"),
        "transmute": tr("词条转律"),
        "unknown": tr("调律"),
    }.get(kind, tr("调律"))
    if state == "completed" or _cooldown_has_expired(expires_at, now=now):
        return f"{kind_label}{tr('冷却完成')}"

    if state == "cooling" or _parse_cooldown_time(expires_at):
        remaining = _format_cooldown_remaining(expires_at, now=now)
        suffix = f"：{remaining}" if remaining else ""
        return f"{kind_label}{tr('冷却中')}{suffix}"
    return ""


def _refresh_card_cooldown(label: QLabel, equip: dict) -> None:
    text = _format_card_cooldown(equip)
    label.setText(text)
    label.setVisible(bool(text))


def _preserve_card_content_height(container: QWidget) -> None:
    """保持内容自然行高；卡片不足时让尾部由父控件裁切。"""
    container.setMinimumHeight(0)
    layout = container.layout()
    if layout is not None:
        layout.invalidate()
        layout.activate()
    container.setMinimumHeight(container.sizeHint().height())


def _equipment_property_rows(equip: dict) -> list[tuple[str, str]]:
    """构建所有装备卡片共用的属性行。"""
    is_mock = bool((equip.get("_extra") or {}).get("is_mock"))
    source = tr("模拟") if is_mock else tr("扫描")
    lock_status = {
        "locked": tr("已锁定"),
        "unlock": tr("未锁定"),
    }.get(str(equip.get("lock_status") or ""), "")
    cooldown_kind = {
        "reset": tr("重置调律"),
        "transmute": tr("词条转律"),
        "unknown": tr("未知"),
    }.get(str(equip.get("cooldown_kind") or ""), "")
    cooldown_state = {
        "cooling": tr("冷却中"),
        "completed": tr("冷却完成"),
    }.get(str(equip.get("cooldown_state") or ""), "")
    return [
        (tr("来源"), source),
        (tr("指纹"), str(equip.get("_fp") or "")),
        (tr("状态"), lock_status),
        (tr("冷却类型"), cooldown_kind),
        (tr("冷却状态"), cooldown_state),
        (tr("原始等级"), str(int(equip.get("original_level") or 0))),
        (tr("冷却时间"), _format_equipment_time(
            equip.get("cooldown_expires_at"))),
        (tr("创建时间"), _format_equipment_time(equip.get("created_at"))),
        (tr("更新时间"), _format_equipment_time(equip.get("updated_at"))),
    ]


def _equipment_properties_text(equip: dict) -> str:
    """构建所有装备卡片共用的属性文本。"""
    return "\n".join(f"{name}：{value}" for name, value in
                     _equipment_property_rows(equip))


class _EquipmentPropertiesDialog(QDialog):
    """不触发系统消息提示音的装备属性只读对话框。"""

    def __init__(
        self,
        equip: dict,
        parent: QWidget | None = None,
        cooldown_changed: Callable[[str], bool] | None = None,
    ):
        super().__init__(parent)
        self._equip = dict(equip)
        self._cooldown_changed = cooldown_changed
        self.setObjectName("equipmentPropertiesDialog")
        self.setWindowTitle(tr("装备属性"))
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(12)
        self._value_labels: dict[str, QLabel] = {}
        for name, value in _equipment_property_rows(self._equip):
            field_name = QLabel(f"{name}：")
            field_name.setStyleSheet("color: palette(mid); font-weight: 600;")
            field_value = QLabel(value)
            field_value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            field_value.setWordWrap(True)
            form.addRow(field_name, field_value)
            self._value_labels[name] = field_value

            if name == tr("冷却时间"):
                self._remaining_name = QLabel(f"{tr('剩余时间')}：")
                self._remaining_name.setStyleSheet(
                    "color: palette(mid); font-weight: 600;")
                self._remaining_value = QLabel("")
                self._remaining_value.setObjectName("cooldownRemainingText")
                self._remaining_value.setStyleSheet(
                    "color: #D97706; font-weight: 600;")
                form.addRow(self._remaining_name, self._remaining_value)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        clear_cooldown_button = buttons.addButton(
            tr("清除冷却时间"), QDialogButtonBox.ButtonRole.ActionRole)
        reset_cooldown_button = buttons.addButton(
            tr("重置冷却时间"), QDialogButtonBox.ButtonRole.ActionRole)
        assert clear_cooldown_button is not None
        assert reset_cooldown_button is not None
        self._clear_cooldown_button: QPushButton = clear_cooldown_button
        self._reset_cooldown_button: QPushButton = reset_cooldown_button
        apply_dialog_button_box_style(buttons)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.setText(tr("关闭"))
        self._clear_cooldown_button.clicked.connect(self._clear_cooldown)
        self._reset_cooldown_button.clicked.connect(self._reset_cooldown)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_cooldown()

    def _set_cooldown(self, value: str) -> None:
        if (self._cooldown_changed is not None
                and not self._cooldown_changed(value)):
            return
        self._equip["cooldown_expires_at"] = value
        # 与仓储一致：改时间不改已有冷却类型，仅空类型时默认转律
        self._equip["cooldown_kind"] = (
            (self._equip.get("cooldown_kind") or "transmute") if value else "")
        self._equip["cooldown_state"] = "cooling" if value else ""
        self._refresh_cooldown()

    def _clear_cooldown(self) -> None:
        self._set_cooldown("")

    def _reset_cooldown(self) -> None:
        from ....config import get_game_config

        game_config = get_game_config()
        self._set_cooldown(next_cooldown_expiry(
            self._equip.get("cooldown_expires_at"),
            days=game_config.get_equipment_cooldown_days(),
            carryover=game_config.is_equipment_cooldown_carryover_enabled(),
        ))

    def _refresh_cooldown(self) -> None:
        value = self._equip.get("cooldown_expires_at")
        formatted = _format_equipment_time(value)
        self._value_labels[tr("冷却时间")].setText(formatted)
        remaining = _format_cooldown_remaining(value)
        visible = bool(remaining)
        self._remaining_name.setVisible(visible)
        self._remaining_value.setVisible(visible)
        self._remaining_value.setText(f"（{remaining}）" if remaining else "")
        # 冷却完成态没有到期时间但仍带 kind/state，冷却管理器会把它列出来；
        # 只看时间会让它「看得见、清不掉」，只剩重置一条路反而再加一轮冷却。
        self._clear_cooldown_button.setEnabled(bool(
            _parse_cooldown_time(value)
            or self._equip.get("cooldown_kind")
            or self._equip.get("cooldown_state")))


def _show_equipment_properties(
    parent,
    equip: dict,
    cooldown_changed: Callable[[str], bool] | None = None,
) -> None:
    try:
        parent.isVisible()
    except (AttributeError, RuntimeError):
        parent = None
    _EquipmentPropertiesDialog(
        equip, parent, cooldown_changed=cooldown_changed).exec()


class _IllegalBadge(QLabel):
    """装备状态异常提醒「!」

    只在装备被合法性判定器标记时显示（``_extra.illegal_equip`` 非空）。
    点击弹出全部异常原因，提示用户手工校正——这类数据基本来自 OCR 误读，
    程序不替用户猜正确值。
    """

    def __init__(self, parent=None):
        super().__init__("!", parent)
        self._reasons: list[str] = []
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(16, 16)
        self.setStyleSheet(
            "color: #ffffff; background: #f44336; border-radius: 8px;"
            "font-weight: bold; font-size: 11px;")
        self.hide()

    def set_reasons(self, reasons: list[str]) -> None:
        self._reasons = list(reasons or [])
        if self._reasons:
            self.setToolTip(tr("装备状态异常，点击查看原因"))
            self.show()
        else:
            self.setToolTip("")
            self.hide()

    def mousePressEvent(self, event):  # type: ignore[override]
        # 必须吃掉事件：卡片本身响应点击做选中/详情，点「!」不应连带触发
        if event is not None:
            event.accept()
        if not self._reasons:
            return
        # 不能在事件处理里直接开模态框。模态框会跑嵌套事件循环，而扫描
        # 期间 equipment_changed 会在那个循环里重建装备网格，把本卡片连同
        # 本控件 deleteLater 掉——deleteLater 是在嵌套循环内发出的，同一个
        # 循环就会处理掉它。于是模态框的 parent 与当前调用栈上的 self 双双
        # 变成已释放对象，返回时即 Windows 0xc0000374 堆损坏。
        # 故：窗口与原因文本按值取出，排队到事件循环里再弹，不依赖 self 存活。
        QTimer.singleShot(
            0, partial(_show_illegal_reasons, self.window(), list(self._reasons)))


class _LockBadge(QWidget):
    """卡片右上角的小型锁形角标。"""

    _TOP = 6
    _RIGHT = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(18, 18)
        self.setToolTip(tr("装备已锁定"))
        self.setAccessibleName(tr("装备已锁定"))
        self.hide()

    def paintEvent(self, event: QPaintEvent | None):  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 图标不依赖字体或 Emoji，在不同 DPI/主题下始终是同一个锁形。
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#B45353"))
        painter.drawEllipse(QRectF(0.5, 0.5, 17.0, 17.0))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#FFFFFF"), 1.6))
        painter.drawArc(QRectF(5.0, 3.2, 8.0, 9.0), 0, 180 * 16)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(QRectF(4.1, 8.0, 9.8, 7.0), 1.5, 1.5)
        painter.end()

    @property
    def reserved_width(self) -> int:
        return self.width() + 4

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.move(
                max(0, parent.width() - self.width() - self._RIGHT),
                self._TOP,
            )
            self.raise_()

    def set_locked(self, locked: bool) -> None:
        self.setVisible(locked)
        if locked:
            self.reposition()


def _make_tag(text: str, bg: str = "#607D8B", parent=None) -> QLabel:
    """创建标准标签胶囊（用于 name_row 的标签序列）。"""
    lbl = QLabel(text, parent)
    lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    lbl.setStyleSheet(f"background-color: {bg}; {_TAG_STYLE}")
    return lbl


# ── 状态标签栏 ──────────────────────────────────────────


class _StatusTagBar(QWidget):
    """名称行右侧的通用多状态标签容器。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._tags: dict[str, QLabel] = {}

    def define(self, key: str, text: str, bg: str = "#607D8B") -> None:
        label = _make_tag(text, bg, self)
        label.setVisible(False)
        self._tags[key] = label
        self._layout.addWidget(label)

    def set_visible(self, key: str, visible: bool) -> None:
        self._tags[key].setVisible(visible)

    def is_visible(self, key: str) -> bool:
        return not self._tags[key].isHidden()


# ── 顶部：可点击槽位卡片 ──────────────────────────────


class _SlotCard(QFrame):
    """可点击的装备槽位卡片，支持选中/取消选中"""

    def __init__(
        self,
        slot_key: str,
        display_name: str,
        filter_type: str,
        display_params: dict | None = None,
        parent=None,
        *,
        read_only: bool = False,
    ):
        super().__init__(parent)
        self.slot_key = slot_key
        self.filter_type = filter_type
        self._read_only = read_only
        self._selected = False
        self._hovered = False
        self._attention = False
        self._display_name = display_name
        self._equip_data: dict = {}

        dp = display_params or {}
        self._name_fs = dp.get("name_font_size", 13)
        self._level_fs = dp.get("level_font_size", 12)
        self._affix_fs = dp.get("affix_font_size", 11)
        self._card_h = dp.get("card_min_height", 160)

        self.setFixedHeight(self._card_h)
        if not read_only:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._quality_bg: str | None = None
        self._apply_style(_SLOT_STYLE_EMPTY)

        self.lock_badge = _LockBadge(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        self.hypothesis_label = QLabel("")
        self.hypothesis_label.setWordWrap(True)
        self.hypothesis_label.setStyleSheet(
            "font-size: 10px; color: #B26A00; font-weight: 700;")
        self.hypothesis_label.setContentsMargins(
            0, 0, self.lock_badge.reserved_width, 0)
        self.hypothesis_label.setVisible(False)
        layout.addWidget(self.hypothesis_label)

        # 槽位名 + 标签序列
        header = QHBoxLayout()
        header.setContentsMargins(
            0, 0, self.lock_badge.reserved_width, 0)
        header.setSpacing(6)
        self.lbl_name = _ElidedLabel(display_name)
        self.lbl_name.setProperty("equipmentName", True)
        _set_quality(self.lbl_name, "")
        self.lbl_name.setStyleSheet(
            f"font-weight: bold; font-size: {self._name_fs}px;")
        header.addWidget(self.lbl_name, stretch=1)
        self.status_tags = _StatusTagBar()
        self.status_tags.define("filtered", tr("筛选"))
        self.status_tags.define("mock", tr("模拟"), "#7E57C2")
        header.addWidget(self.status_tags)
        self.illegal_badge = _IllegalBadge()
        header.addWidget(self.illegal_badge)
        layout.addLayout(header)

        # 等级行
        self.lbl_info = QLabel("")
        self.lbl_info.setStyleSheet(
            f"font-size: {self._level_fs}px; color: palette(mid);")
        layout.addWidget(self.lbl_info)

        # 分割线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: palette(midlight);")
        line.setFixedHeight(1)
        layout.addWidget(line)

        # 词条区域
        self.affix_container = QWidget()
        self.affix_layout = QVBoxLayout(self.affix_container)
        self.affix_layout.setContentsMargins(0, 0, 0, 0)
        self.affix_layout.setSpacing(2)
        self.affix_container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.cooldown_label = _ElidedLabel("")
        self.cooldown_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.cooldown_label.setStyleSheet(
            f"font-size: {self._affix_fs}px; color: #D97706; "
            "font-weight: 600;")
        self.cooldown_label.hide()
        self.affix_layout.addWidget(self.cooldown_label)
        layout.addWidget(self.affix_container)

        layout.addStretch()

    # ── 选中态 ──

    def _normal_border(self) -> str:
        """常态边框色：需要引起注意的卡片用强调色，其余按品质区分。"""
        if self._attention:
            return "palette(highlight)"
        return "#b0a080" if self._quality_bg else "palette(midlight)"

    def set_attention(self, attention: bool) -> None:
        """用强调色边框标出这张卡片（如最优组合里需要更换的部位）。"""
        self._attention = attention
        if self._selected or self._hovered:
            return
        if self.lbl_info.text() == tr("未装备"):
            self._apply_style(_SLOT_STYLE_EMPTY)
        else:
            self._apply_style(_slot_style_normal(
                self._quality_bg or "palette(base)", self._normal_border()))

    def set_selected(self, selected: bool):
        self._selected = selected
        self.status_tags.set_visible("filtered", selected)
        bg = self._quality_bg or "palette(base)"
        if selected:
            self._apply_style(_slot_style_selected(bg))
        elif self.lbl_info.text() == tr("未装备"):
            self._apply_style(_SLOT_STYLE_EMPTY)
        else:
            self._apply_style(_slot_style_normal(bg, self._normal_border()))

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self.lock_badge.reposition()

    def is_selected(self) -> bool:
        return self._selected

    def _apply_style(self, style: str):
        self.setStyleSheet(style)

    def enterEvent(self, event):
        self._hovered = True
        if not self._selected:
            self._apply_style(_slot_style_hovered(self._quality_bg or "palette(base)"))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        if not self._selected:
            if self.lbl_info.text() == tr("未装备"):
                self._apply_style(_SLOT_STYLE_EMPTY)
            else:
                self._apply_style(_slot_style_normal(
                    self._quality_bg or "palette(base)", self._normal_border()
                ))
        super().leaveEvent(event)

    # ── 点击事件 ──

    def mousePressEvent(self, event):
        from .status_tab import EquipStatusTab
        # 仅左键触发部位筛选，右键留给 contextMenuEvent
        if event.button() == Qt.MouseButton.LeftButton:
            parent = self.parent()
            while parent and not isinstance(parent, EquipStatusTab):
                parent = parent.parent()
            if parent:
                parent._on_slot_clicked(self.slot_key)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        """右键菜单：已装备物品可卸载、养成或编辑，并可复制。"""
        from .status_tab import EquipStatusTab
        if self._read_only or not getattr(self, '_equip_data', None):
            event.ignore()
            return
        is_mock = (
            self._equip_data.get("_extra", {})
            .get("is_mock", False)
        )
        menu = QMenu(self)
        unequip_action = menu.addAction(tr("卸载"))
        edit_action = menu.addAction(tr("编辑") if is_mock else tr("养成"))
        edit_action.setToolTip(
            tr("编辑模拟装备数据") if is_mock
            else tr("记录扫描装备的转律、承音或培养"))
        copy_action = menu.addAction(tr("复制"))
        copy_action.setToolTip(tr("复制装备数据到创建对话框"))
        locked = self._equip_data.get("lock_status") == "locked"
        lock_action = menu.addAction(tr("解锁") if locked else tr("锁定"))
        lock_action.setToolTip(tr("解锁此装备") if locked else tr("锁定此装备"))
        properties_action = menu.addAction(tr("属性"))
        clear_target_action = None
        if any(
            _transmute_target_text(self._equip_data.get(f"affix_{i}") or {})
            for i in range(1, 6)
        ):
            clear_target_action = menu.addAction(tr("清除转律目标"))
            clear_target_action.setToolTip(
                tr("删除该装备保存的模拟转律目标；影响所有引用此装备的方案"))
        action = menu.exec(event.globalPos())
        if clear_target_action is not None and action == clear_target_action:
            parent = self.parent()
            while parent and not isinstance(parent, EquipStatusTab):
                parent = parent.parent()
            if parent:
                parent._on_clear_transmute_target(self._equip_data)
        elif action == unequip_action:
            parent = self.parent()
            while parent and not isinstance(parent, EquipStatusTab):
                parent = parent.parent()
            if parent:
                parent._on_slot_unequip(self.slot_key)
        elif action == edit_action:
            parent = self.parent()
            while parent and not isinstance(parent, EquipStatusTab):
                parent = parent.parent()
            if parent:
                parent._on_slot_edit(self.slot_key)
        elif action == copy_action:
            parent = self.parent()
            while parent and not isinstance(parent, EquipStatusTab):
                parent = parent.parent()
            if parent:
                parent._on_copy_requested(self._equip_data, self._equip_data.get("_extra", {}).get("group_key", ""))
        elif action == lock_action:
            parent = self.parent()
            while parent and not isinstance(parent, EquipStatusTab):
                parent = parent.parent()
            if parent:
                parent._on_lock_requested(self._equip_data, not locked)
        elif action == properties_action:
            parent = self.parent()
            while parent and not isinstance(parent, EquipStatusTab):
                parent = parent.parent()
            if parent:
                parent._on_properties_requested(self._equip_data)
        event.accept()

    # ── 数据填充 ──

    def set_empty(self):
        self.set_hypotheses([])
        self._quality_bg = None
        self._equip_data = {}
        self.lock_badge.set_locked(False)
        self.status_tags.set_visible("mock", False)
        self.illegal_badge.set_reasons([])
        self.lbl_name.setText(self._display_name)
        _set_quality(self.lbl_name, "")
        self.lbl_name.setStyleSheet(
            f"font-weight: bold; font-size: {self._name_fs}px;")
        self.lbl_info.setText(tr("未装备"))
        self.lbl_info.setStyleSheet(
            f"font-size: {self._level_fs}px; color: palette(mid);")
        self._clear_affixes()
        self._finish_affixes({})
        if not self._selected:
            self._apply_style(_SLOT_STYLE_EMPTY)

    def set_equip(self, equip_data: dict):
        self.set_hypotheses([])
        self._equip_data = equip_data
        self.lock_badge.set_locked(
            equip_data.get("lock_status") == "locked")
        self.status_tags.set_visible(
            "mock", bool(equip_data.get("_extra", {}).get("is_mock", False)),
        )
        self.illegal_badge.set_reasons(illegal_reasons_of(equip_data))
        quality = equip_data.get("quality") or ""
        self._quality_bg = _QUALITY_BG_COLORS.get(quality)

        name = equip_data.get("name", tr("未知"))
        self.lbl_name.setText(f"{self._display_name} · {name}")
        _set_quality(self.lbl_name, quality)
        self.lbl_name.setStyleSheet(
            f"font-weight: bold; font-size: {self._name_fs}px;")

        equip_level = equip_data.get("level")
        level = equip_level or "?"
        is_chengyin = equip_data.get("is_chengyin", False)
        tag = " [" + tr("承音") + "]" if is_chengyin else ""

        # 词条平均百分比（内联在等级后面，字号跟随 affix_font_size）
        pct_fs = self._affix_fs
        cap_pcts = equip_affix_cap_pcts(equip_data)
        if cap_pcts:
            avg_pct = sum(cap_pcts) / len(cap_pcts)
            pct_color = _affix_value_color(avg_pct)
            pct_html = f'&nbsp;&nbsp;<span style="font-size:{pct_fs}px;color:{pct_color};font-weight:bold;">{avg_pct:.0f}%</span>'
            self.lbl_info.setTextFormat(Qt.TextFormat.RichText)
            self.lbl_info.setText(f"Lv{level}{tag}{pct_html}")
        else:
            self.lbl_info.setTextFormat(Qt.TextFormat.PlainText)
            self.lbl_info.setText(f"Lv{level}{tag}")

        self.lbl_info.setStyleSheet(
            f"font-size: {self._level_fs}px; color: palette(mid); font-weight: bold;")

        if not self._selected:
            bg = self._quality_bg or "palette(base)"
            self._apply_style(_slot_style_normal(bg, self._normal_border()))

        # 词条
        self._clear_affixes()
        for i in range(1, 6):
            affix = equip_data.get(f"affix_{i}")
            if not affix or not affix.get("name"):
                continue
            self._add_affix_row(affix, equip_level)

        # 定音
        dingyin = equip_data.get("dingyin")
        zhige_dingyin = is_zhige_dingyin(equip_data)
        normal_dingyin = isinstance(dingyin, dict) and bool(dingyin.get("name"))
        if zhige_dingyin or normal_dingyin:
            dash = QFrame()
            dash.setFrameShape(QFrame.Shape.NoFrame)
            dash.setStyleSheet(
                "border: none; border-top: 1px dashed palette(mid);")
            dash.setFixedHeight(1)
            self.affix_layout.addWidget(dash)
            if zhige_dingyin:
                notice = str(
                    (equip_data.get("_extra") or {}).get(DINGYIN_NOTICE_KEY) or ""
                )
                self._add_affix_row(
                    {"name": tr("<止戈定音>")}, equip_level, tooltip=notice,
                )
            else:
                assert isinstance(dingyin, dict)
                self._add_affix_row(dingyin, equip_level)
        self._finish_affixes(equip_data)

    def update_lock_status(self, value: str) -> None:
        """原地更新单卡锁定状态，不重建卡片。"""
        self._equip_data["lock_status"] = value
        self.lock_badge.set_locked(value == "locked")

    def update_cooldown(self, value: str) -> None:
        """原地更新单卡冷却状态，不重建卡片。"""
        self._equip_data["cooldown_expires_at"] = value
        self._equip_data["cooldown_kind"] = (
            (self._equip_data.get("cooldown_kind") or "transmute")
            if value else "")
        self._equip_data["cooldown_state"] = "cooling" if value else ""
        _refresh_card_cooldown(self.cooldown_label, self._equip_data)
        _preserve_card_content_height(self.affix_container)

    def set_hypotheses(self, assumptions) -> None:
        """显示计算假设；装备数据本身始终保持原始值。"""
        labels = [str(value) for value in (assumptions or ()) if str(value)]
        self.set_note(tr("计算假设：") + "、".join(labels) if labels else "")

    def set_note(self, text: str) -> None:
        """在卡片顶部显示一行说明文字（空则隐藏）。"""
        self.hypothesis_label.setText(text or "")
        self.hypothesis_label.setVisible(bool(text))

    def _add_affix_row(self, affix: dict, level=None, *, tooltip: str = ""):
        value = affix.get("value", "")
        unit = affix.get("unit", "")
        cap_pct = affix_dict_cap_pct(affix, level)

        if isinstance(value, (int, float)):
            val_str = f"{value}%" if unit == "%" else (
                f"{value:.1f}" if isinstance(value, float) else str(value))
        else:
            val_str = str(value)

        val_color = _affix_value_color(cap_pct)

        is_transferred = affix.get("is_transferred", False)
        transfer_mark = " ⟳" if is_transferred else ""

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)

        lbl = _ElidedLabel(f"{affix['name']}{transfer_mark}")
        if tooltip:
            lbl.setToolTip(tooltip)
        lbl.setStyleSheet(
            f"font-size: {self._affix_fs}px; color: palette(mid); font-weight: bold;")
        row.addWidget(lbl, stretch=1)

        # 转律目标作为独立分段标签：_ElidedLabel 自绘纯文本，不解析富文本。
        target_text = _transmute_target_text(affix)
        if target_text:
            target = QLabel(target_text)
            target.setObjectName("transmuteTargetLabel")
            target.setToolTip(_transmute_target_tooltip(affix))
            target.setStyleSheet(
                f"font-size: {self._affix_fs}px; font-weight: bold; "
                f"color: {transmute_target_color(self)};")
            row.addWidget(target)

        val = QLabel(val_str)
        val.setStyleSheet(
            f"font-size: {self._affix_fs}px; color: {val_color}; font-weight: bold;")
        row.addWidget(val, alignment=Qt.AlignmentFlag.AlignRight)

        self.affix_layout.addLayout(row)

    def _clear_affixes(self):
        self.affix_container.setMinimumHeight(0)
        while self.affix_layout.count() > 0:
            item = self.affix_layout.takeAt(0)
            if item.widget():
                if item.widget() is self.cooldown_label:
                    self.cooldown_label.hide()
                else:
                    item.widget().deleteLater()
            elif item.layout():
                sub = item.layout()
                while sub.count() > 0:
                    child = sub.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
                sub.deleteLater()

    def _finish_affixes(self, equip_data: dict) -> None:
        _refresh_card_cooldown(self.cooldown_label, equip_data)
        self.affix_layout.addWidget(self.cooldown_label)
        _preserve_card_content_height(self.affix_container)


# ── 底部：背包装备卡片 ──────────────────────────────


class _CompactEquipCard(QFrame):
    """紧凑装备卡片 —— 用于背包网格

    Signals:
        equip_requested(dict, str): 请求装备到槽位，参数为 (equip_data, group_key)
        edit_requested(dict, str): 请求编辑/养成装备，参数为 (equip_data, group_key)
        delete_requested(dict, str): 请求删除装备，参数为 (equip_data, group_key)
    """

    equip_requested = pyqtSignal(dict, str)
    edit_requested = pyqtSignal(dict, str)
    delete_requested = pyqtSignal(dict, str)
    copy_requested = pyqtSignal(dict, str)
    lock_requested = pyqtSignal(dict, bool)
    properties_requested = pyqtSignal(dict)
    selection_changed = pyqtSignal(str, bool)

    def __init__(
        self,
        display_params: dict | None = None,
        parent=None,
        *,
        context_mode: Literal["full", "properties"] = "full",
    ):
        super().__init__(parent)
        self._context_mode = context_mode
        dp = display_params or {}
        self._name_fs = dp.get("name_font_size", 13)
        self._level_fs = dp.get("level_font_size", 12)
        self._affix_fs = dp.get("affix_font_size", 11)

        # 装备数据和分组 key（用于右键菜单）
        self._equip_data: dict = {}
        self._group_key: str = ""
        self._quality_bg: str | None = None
        self._selection_mode = False
        self._selected = False

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._apply_card_style()
        self.setFixedHeight(dp.get("card_min_height", 180))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("右键菜单"))

        self.lock_badge = _LockBadge(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        # 装备名 + 标签序列
        name_row = QHBoxLayout()
        name_row.setContentsMargins(
            0, 0, self.lock_badge.reserved_width, 0)
        name_row.setSpacing(6)
        self.selection_checkbox = QCheckBox()
        self.selection_checkbox.setToolTip(tr("选择要复制的模拟装备"))
        self.selection_checkbox.setVisible(False)
        self.selection_checkbox.toggled.connect(self._on_selection_toggled)
        name_row.addWidget(self.selection_checkbox)
        self.lbl_name = _ElidedLabel()
        self.lbl_name.setProperty("equipmentName", True)
        self.lbl_name.setProperty("quality", "")
        self.lbl_name.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.lbl_name.setStyleSheet(
            f"font-weight: bold; font-size: {self._name_fs}px;")
        name_row.addWidget(self.lbl_name, stretch=1)
        self.status_tags = _StatusTagBar()
        self.status_tags.define("mock", tr("模拟"), "#7E57C2")
        self.status_tags.define("loadout", tr("备战"), "#00897B")
        name_row.addWidget(self.status_tags)
        self.illegal_badge = _IllegalBadge()
        name_row.addWidget(self.illegal_badge)
        layout.addLayout(name_row)

        self.lbl_level = QLabel()
        self.lbl_level.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.lbl_level.setStyleSheet(
            f"font-size: {self._level_fs}px; color: palette(mid);")
        layout.addWidget(self.lbl_level)

        line = QFrame()
        line.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: palette(midlight);")
        line.setFixedHeight(1)
        layout.addWidget(line)

        self.affix_container = QWidget()
        self.affix_container.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.affix_layout = QVBoxLayout(self.affix_container)
        self.affix_layout.setContentsMargins(0, 0, 0, 0)
        self.affix_layout.setSpacing(2)
        self.affix_container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.cooldown_label = _ElidedLabel("")
        self.cooldown_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.cooldown_label.setStyleSheet(
            f"font-size: {self._affix_fs}px; color: #D97706; "
            "font-weight: 600;")
        self.cooldown_label.hide()
        self.affix_layout.addWidget(self.cooldown_label)
        layout.addWidget(self.affix_container)

        layout.addStretch()

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self.lock_badge.reposition()

    def _apply_card_style(self, hovered: bool = False):
        selected = self._selection_mode and self._selected
        border = (
            "palette(highlight)" if selected
            else "palette(mid)" if hovered else "palette(midlight)"
        )
        width = 2 if hovered or selected else 1
        bg = self._quality_bg or "palette(base)"
        self.setStyleSheet(f"""
            _CompactEquipCard {{
                background-color: {bg};
                border: {width}px solid {border};
                border-radius: 6px;
                padding: 4px;
            }}
        """)

    def enterEvent(self, event):
        self._apply_card_style(hovered=True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._apply_card_style(hovered=False)
        super().leaveEvent(event)

    def contextMenuEvent(self, event):
        """右键菜单：所有装备卡片均弹出。"""
        if self._selection_mode:
            event.ignore()
            return
        if not self._equip_data:
            event.ignore()
            return
        self._show_context_menu(event.globalPos())
        event.accept()

    def mousePressEvent(self, event):
        if (self._selection_mode
                and event.button() == Qt.MouseButton.LeftButton):
            self.selection_checkbox.toggle()
            event.accept()
            return
        super().mousePressEvent(event)

    def set_selection_mode(self, enabled: bool, *, selected: bool = False) -> None:
        """切换批量选择模式；该模式下卡片只负责选中，不开放普通操作。"""
        self._selection_mode = enabled
        self.selection_checkbox.setVisible(enabled)
        self.selection_checkbox.blockSignals(True)
        self.selection_checkbox.setChecked(enabled and selected)
        self.selection_checkbox.blockSignals(False)
        self._selected = enabled and selected
        self.setToolTip(tr("点击选择要复制的装备") if enabled else tr("右键菜单"))
        self._apply_card_style()

    def _on_selection_toggled(self, checked: bool) -> None:
        self._selected = checked
        self._apply_card_style()
        fp = str(self._equip_data.get("_fp") or "")
        if fp:
            self.selection_changed.emit(fp, checked)

    def _show_context_menu(self, global_pos):
        """显示右键菜单：装备/编辑/复制/删除。

        用 popup() 而非 exec()：exec() 会跑嵌套事件循环，扫描期间
        equipment_changed 会在循环里重建网格并 deleteLater 掉本卡片，
        exec() 返回后对 self 的任何访问都落在已释放对象上（堆损坏）。
        popup() 立即返回；菜单是 self 的子对象，卡片没了菜单一起销毁，
        动作自然不会触发。装备数据按值捕获，避免期间被复用改写。
        """
        data = self._equip_data
        group = self._group_key
        is_mock = (data.get("_extra", {}) or {}).get("is_mock", False)

        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        entries = []
        if self._context_mode == "full":
            entries.append(
                (tr("装备"), tr("穿戴到对应槽位"), self.equip_requested))
            entries.append((
                tr("编辑") if is_mock else tr("养成"),
                tr("编辑模拟装备数据") if is_mock
                else tr("记录扫描装备的转律、承音或培养"),
                self.edit_requested,
            ))
            entries.append((tr("复制"), tr("复制装备数据到创建对话框"),
                            self.copy_requested))
            entries.append(
                (tr("删除"), tr("删除此装备"), self.delete_requested))
        for text, tip, signal in entries:
            action = menu.addAction(text)
            action.setToolTip(tip)
            action.triggered.connect(
                partial(self._emit_menu_action, signal, data, group))
        locked = data.get("lock_status") == "locked"
        lock_action = menu.addAction(
            tr("解锁") if locked else tr("锁定"))
        lock_action.setToolTip(
            tr("解锁此装备") if locked else tr("锁定此装备"))
        lock_action.triggered.connect(partial(
            self._emit_lock_action,
            self.lock_requested,
            data,
            not locked,
        ))
        properties_action = menu.addAction(tr("属性"))
        properties_action.setToolTip(tr("查看装备来源、指纹、原始等级和时间"))
        properties_action.triggered.connect(
            partial(self._emit_properties_action, self.properties_requested, data))
        menu.popup(global_pos)

    @staticmethod
    def _emit_menu_action(signal, data, group, _checked=False) -> None:
        """菜单动作统一出口（triggered 会多传一个 checked 参数）。"""
        signal.emit(data, group)

    @staticmethod
    def _emit_properties_action(signal, data, _checked=False) -> None:
        signal.emit(data)

    @staticmethod
    def _emit_lock_action(signal, data, locked, _checked=False) -> None:
        signal.emit(data, locked)

    def set_equip(
        self, equip_data: dict, part_label: str,
        group_key: str = "", is_mock: bool = False,
        is_loadout: bool = False,
    ):
        # 存储装备数据和分组 key（用于右键菜单）
        self._equip_data = equip_data
        self._group_key = group_key
        self.lock_badge.set_locked(
            equip_data.get("lock_status") == "locked")

        quality = equip_data.get("quality") or ""
        self._quality_bg = _QUALITY_BG_COLORS.get(quality)
        self._apply_card_style()

        name = equip_data.get("name", tr("未知"))
        self.lbl_name.setText(f"{part_label} · {name}")
        _set_quality(self.lbl_name, quality)
        self.lbl_name.setStyleSheet(
            f"font-weight: bold; font-size: {self._name_fs}px;")
        self.status_tags.set_visible(
            "mock",
            bool(is_mock or equip_data.get("_extra", {}).get("is_mock", False)),
        )
        self.status_tags.set_visible("loadout", is_loadout)
        self.illegal_badge.set_reasons(illegal_reasons_of(equip_data))

        equip_level = equip_data.get("level")
        level = equip_level or "?"
        is_chengyin = equip_data.get("is_chengyin", False)
        tag = " [" + tr("承音") + "]" if is_chengyin else ""

        # 词条平均百分比（内联在等级后面，字号跟随 affix_font_size）
        pct_fs = self._affix_fs
        cap_pcts = equip_affix_cap_pcts(equip_data)
        if cap_pcts:
            avg_pct = sum(cap_pcts) / len(cap_pcts)
            pct_color = _affix_value_color(avg_pct)
            pct_html = f'&nbsp;&nbsp;<span style="font-size:{pct_fs}px;color:{pct_color};font-weight:bold;">{avg_pct:.0f}%</span>'
            self.lbl_level.setTextFormat(Qt.TextFormat.RichText)
            self.lbl_level.setText(f"Lv{level}{tag}{pct_html}")
        else:
            self.lbl_level.setTextFormat(Qt.TextFormat.PlainText)
            self.lbl_level.setText(f"Lv{level}{tag}")

        self.lbl_level.setStyleSheet(
            f"font-size: {self._level_fs}px; color: palette(mid); font-weight: bold;")

        self._clear_affixes()
        for i in range(1, 6):
            affix = equip_data.get(f"affix_{i}")
            if not affix or not affix.get("name"):
                continue

            value = affix.get("value", "")
            unit = affix.get("unit", "")
            cap_pct = affix_dict_cap_pct(affix, equip_level)

            if isinstance(value, (int, float)):
                val_str = f"{value}%" if unit == "%" else (
                    f"{value:.1f}" if isinstance(value, float) else str(value))
            else:
                val_str = str(value)

            val_color = _affix_value_color(cap_pct)

            is_transferred = affix.get("is_transferred", False)
            transfer_mark = " ⟳" if is_transferred else ""

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(2)

            lbl_name = _ElidedLabel(f"{affix['name']}{transfer_mark}")
            lbl_name.setStyleSheet(
                f"font-size: {self._affix_fs}px; color: palette(mid); font-weight: bold;")
            row.addWidget(lbl_name, stretch=1)

            target_text = _transmute_target_text(affix)
            if target_text:
                lbl_target = QLabel(target_text)
                lbl_target.setObjectName("transmuteTargetLabel")
                lbl_target.setToolTip(_transmute_target_tooltip(affix))
                lbl_target.setStyleSheet(
                    f"font-size: {self._affix_fs}px; font-weight: bold; "
                    f"color: {transmute_target_color(self)};")
                row.addWidget(lbl_target)

            lbl_val = QLabel(val_str)
            lbl_val.setStyleSheet(
                f"font-size: {self._affix_fs}px; color: {val_color}; font-weight: bold;")
            row.addWidget(lbl_val, alignment=Qt.AlignmentFlag.AlignRight)

            self.affix_layout.addLayout(row)

        # 定音
        dingyin = equip_data.get("dingyin")
        zhige_dingyin = is_zhige_dingyin(equip_data)
        normal_dingyin = isinstance(dingyin, dict) and bool(dingyin.get("name"))
        if zhige_dingyin or normal_dingyin:
            dash = QFrame()
            dash.setFrameShape(QFrame.Shape.NoFrame)
            dash.setStyleSheet(
                "border: none; border-top: 1px dashed palette(mid);")
            dash.setFixedHeight(1)
            self.affix_layout.addWidget(dash)

            if zhige_dingyin:
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                lbl_name = _ElidedLabel(tr("<止戈定音>"))
                notice = str(
                    (equip_data.get("_extra") or {}).get(DINGYIN_NOTICE_KEY) or ""
                )
                if notice:
                    lbl_name.setToolTip(notice)
                lbl_name.setStyleSheet(
                    f"font-size: {self._affix_fs}px; color: palette(mid); font-weight: bold;")
                row.addWidget(lbl_name, stretch=1)
                self.affix_layout.addLayout(row)
            else:
                assert isinstance(dingyin, dict)
                dy_value = dingyin.get("value", "")
                dy_val_str = (
                    f"{dy_value}%" if isinstance(dy_value, (int, float))
                    else str(dy_value))
                dy_cap_pct = affix_dict_cap_pct(dingyin, equip_level)
                dy_color = _affix_value_color(dy_cap_pct)

                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(2)

                lbl_name = _ElidedLabel(dingyin["name"])
                lbl_name.setStyleSheet(
                    f"font-size: {self._affix_fs}px; color: palette(mid); font-weight: bold;")
                row.addWidget(lbl_name, stretch=1)

                lbl_val = QLabel(dy_val_str)
                lbl_val.setStyleSheet(
                    f"font-size: {self._affix_fs}px; color: {dy_color}; font-weight: bold;")
                row.addWidget(lbl_val, alignment=Qt.AlignmentFlag.AlignRight)

                self.affix_layout.addLayout(row)
        self._finish_affixes(equip_data)

    def update_lock_status(self, value: str) -> None:
        """原地更新单卡锁定状态，不重建卡片。"""
        self._equip_data["lock_status"] = value
        self.lock_badge.set_locked(value == "locked")

    def update_cooldown(self, value: str) -> None:
        """原地更新单卡冷却状态，不重建卡片。"""
        self._equip_data["cooldown_expires_at"] = value
        self._equip_data["cooldown_kind"] = (
            (self._equip_data.get("cooldown_kind") or "transmute")
            if value else "")
        self._equip_data["cooldown_state"] = "cooling" if value else ""
        _refresh_card_cooldown(self.cooldown_label, self._equip_data)
        _preserve_card_content_height(self.affix_container)

    def _clear_affixes(self):
        self.affix_container.setMinimumHeight(0)
        while self.affix_layout.count() > 0:
            item = self.affix_layout.takeAt(0)
            if item.widget():
                if item.widget() is self.cooldown_label:
                    self.cooldown_label.hide()
                else:
                    item.widget().deleteLater()
            elif item.layout():
                sub = item.layout()
                while sub.count() > 0:
                    child = sub.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
                sub.deleteLater()

    def _finish_affixes(self, equip_data: dict) -> None:
        _refresh_card_cooldown(self.cooldown_label, equip_data)
        self.affix_layout.addWidget(self.cooldown_label)
        _preserve_card_content_height(self.affix_container)
