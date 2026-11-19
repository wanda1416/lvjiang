"""条目编辑器：新增 / 修改一条清单来源。

「类型」决定这条怎么记，也决定能绑哪类 key：

- **固定**：单次或可累加的来源，勾选 / 填数字，记到库存 key
- **逐位**：每一枚对应一个具体对象（以武会友），展开逐位填名字，记到库存 key
- **周期**：绑配额 key，本周期已得就是配额当前值，按那个周期自动归零

所以类型不是随手可改的装饰：周期必须绑配额 key，库存 key 只能是固定或逐位；
反过来选了周期却没有可用配额 key 时，这个选项不会出现在下拉里。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr, tr_label
from .....ui.widgets.button_styles import apply_button_style
from .....ui.widgets.combo_box import AutoWidthComboBox, ComboWidthMode
from ...core.linger import booking
from ...core.linger.models import (
    DEFAULT_TARGET,
    ITEM_PERIODIC,
    ITEM_ROSTER,
    ITEM_SINGLE,
    LingerItem,
    LingerRegion,
)

#: 内置类型（清单内容里用过的会自动并入，见 category_choices）。
#: 分类对齐游戏内任务体系：主章/残章/探游/奇遇/侠迹/镇守/试炼，
#: 加上途径本质分类：妙妙庙（独立）/兑换/图鉴/宝箱/探索/成就。
BUILTIN_CATEGORIES = (
    "主章", "残章", "探游", "奇遇", "侠迹", "镇守", "试炼",
    "妙妙庙", "兑换", "图鉴", "宝箱", "探索", "成就", "以武会友",
    "百珍商店", "万事值", "乌何有集",
)


def category_choices() -> list[str]:
    """类型下拉的选项：内置 + 清单里实际用过的（含用户自建的），去重排序。

    下拉本身可编辑：输入新名字保存条目，它就自动成为下次可选的类型之一——
    「添加分类」就是正常新建/编辑一条条目时顺手做的事，不需要单独入口。
    """
    from ...core.linger import known_categories

    return sorted({*BUILTIN_CATEGORIES, *known_categories()})

_KIND_CHOICES = (
    (ITEM_SINGLE, "固定"),
    (ITEM_ROSTER, "逐位"),
    (ITEM_PERIODIC, "周期"),
)


def target_choices(model: str) -> list[tuple[str, str]]:
    """某模型下可选的入账目标：``(值, 展示名)``。"""
    from .....core.profile.schema import get_profile_config

    config = get_profile_config()
    result = [
        (f"{model}:{key_def.key}", f"{key_def.label or key_def.key}（{key_def.key}）")
        for key_def in config.get_keys_by_model(model)
    ]
    if not result and model == booking.MODEL_STOCK:
        result.append((DEFAULT_TARGET, DEFAULT_TARGET))
    return result


class LingerItemDialog(QDialog):
    """新增或修改一条来源；``values()`` 返回可直接写进覆盖文件的字段。"""

    def __init__(self, regions: list[LingerRegion], item: LingerItem | None = None,
                 region_id: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._item = item
        self.setWindowTitle(tr("新增") if item is None else tr("编辑"))
        self.setModal(True)
        self.setMinimumWidth(480)
        self._build_ui(regions, region_id)
        if item is not None:
            self._fill(item)

    def _build_ui(self, regions: list[LingerRegion], region_id: str) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(12)
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(form.labelAlignment())

        self.region_combo = AutoWidthComboBox(self, width_mode=ComboWidthMode.POPUP)
        for region in regions:
            self.region_combo.addItem(region.label, region.id)
        index = self.region_combo.findData(region_id)
        if index >= 0:
            self.region_combo.setCurrentIndex(index)
        # 条目换地区等于换 id，历史记录会断，所以只允许新增时选。
        self.region_combo.setEnabled(self._item is None)
        form.addRow(tr_label("地区"), self.region_combo)

        self.category_edit = QComboBox(self)
        self.category_edit.setEditable(True)
        self.category_edit.addItems(["", *category_choices()])
        self.category_edit.setCurrentIndex(0)
        # 输入不在列表里的名字就是新建类型：保存条目后它进入下拉选项
        completer = self.category_edit.completer()
        if completer is not None:
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        form.addRow(tr_label("类型"), self.category_edit)

        self.label_edit = QLineEdit(self)
        form.addRow(tr_label("条目"), self.label_edit)

        self.kind_combo = QComboBox(self)
        for value, label in _KIND_CHOICES:
            if value == ITEM_PERIODIC and not target_choices(booking.MODEL_QUOTA):
                continue  # 没有配额 key 时不提供「周期」，免得选了个没法落的类型
            self.kind_combo.addItem(tr(label), value)
        self.kind_combo.currentIndexChanged.connect(self._reload_targets)
        form.addRow(tr_label("类型"), self.kind_combo)

        self.count_spin = QSpinBox(self)
        self.count_spin.setRange(1, 999)
        form.addRow(tr_label("枚数"), self.count_spin)

        # 来源是 meta：仅编辑可见，列表不展示；默认= 类型：条目
        self.source_edit = QLineEdit(self)
        form.addRow(tr_label("来源"), self.source_edit)

        self.target_combo = QComboBox(self)
        form.addRow(tr_label("目标"), self.target_combo)
        self._reload_targets()

        self.detail_edit = QPlainTextEdit(self)
        self.detail_edit.setMinimumHeight(120)
        form.addRow(tr_label("详情"), self.detail_edit)
        outer.addLayout(form)

        buttons = QDialogButtonBox(self)
        ok = buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        if ok is not None:
            ok.setText(tr("确定"))
            apply_button_style(ok, variant="action")
        if cancel is not None:
            cancel.setText(tr("取消"))
            apply_button_style(cancel, variant="neutral")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _reload_targets(self) -> None:
        kind = str(self.kind_combo.currentData() or ITEM_SINGLE)
        model = booking.MODEL_QUOTA if kind == ITEM_PERIODIC else booking.MODEL_STOCK
        current = self.target_combo.currentData()
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        for value, label in target_choices(model):
            self.target_combo.addItem(label, value)
        index = self.target_combo.findData(current)
        self.target_combo.setCurrentIndex(index if index >= 0 else 0)
        self.target_combo.blockSignals(False)
        # 只有周期型需要自己的 key；固定/逐位统一走顶部「同步模型」
        self.target_combo.setEnabled(kind == ITEM_PERIODIC)

    def _fill(self, item: LingerItem) -> None:
        index = self.category_edit.findText(item.category)
        if index < 0 and item.category:
            self.category_edit.addItem(item.category)
            index = self.category_edit.count() - 1
        self.category_edit.setCurrentIndex(max(0, index))
        self.label_edit.setText(item.label)
        self.count_spin.setValue(item.count)
        self.source_edit.setText(item.source)
        self.detail_edit.setPlainText(item.detail)
        index = self.kind_combo.findData(item.kind)
        if index < 0:
            self.kind_combo.addItem(tr("固定"), item.kind)
            index = self.kind_combo.count() - 1
        self.kind_combo.setCurrentIndex(index)
        self._reload_targets()
        target_index = self.target_combo.findData(item.target)
        if target_index < 0:
            self.target_combo.addItem(item.target, item.target)
            target_index = self.target_combo.count() - 1
        self.target_combo.setCurrentIndex(target_index)

    def values(self) -> dict:
        label = self.label_edit.text().strip()
        category = self.category_edit.currentText().strip()
        source = self.source_edit.text().strip() or (
            f"{category}：{label}" if category else label)
        kind = str(self.kind_combo.currentData() or ITEM_SINGLE)
        # 固定/逐位不单独存目标：统一用顶部「同步模型」，只有周期型各有自己的 key
        target = (str(self.target_combo.currentData() or DEFAULT_TARGET)
                  if kind == ITEM_PERIODIC else "")
        return {
            "category": category,
            "label": label,
            "count": self.count_spin.value(),
            "source": source,
            "target": target,
            "kind": kind,
            "detail": self.detail_edit.toPlainText().strip(),
        }

    def region_id(self) -> str:
        return str(self.region_combo.currentData() or "")

    def accept(self) -> None:  # noqa: D401 — Qt 接口
        if not self.label_edit.text().strip():
            self.label_edit.setFocus()
            return
        super().accept()
