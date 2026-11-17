"""模板图管理：按分组浏览模板图、看引用、按所有权改名/删除。

界面沿用图库其余页签的布局习惯：顶部筛选行（分组 + 名字 + 刷新）、左侧列表、
右侧分组框形式的详情、底部右对齐的操作按钮。名字较长时列表按中间省略显示，
完整名字与引用在 tooltip 与详情里。

所有权（写在库里，界面只是照着禁用）：

- **布局分组**（目录名 = 根布局 key）：由场景管理截取/删除，这里只展示——
  名字是「布局/场景/Region」、尺寸在 Layout 绑定里，两个入口同时改会破坏契约；
- **普通分组**（`common` 等）：归本页管理，可改名（普通分组之间移动）、删除；
- `role: sample`（如装备锁图标）：是模板图但不参与匹配，永久保留，不给尺寸与测试。

参考图库那一侧（`recognize` 的分类图）与此页无关：这里只读 `templates/` 目录与
`templates.yaml` 索引。
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from lvjiang.core import templates as tlib
from lvjiang.ui.widgets.combo_box import AutoWidthComboBox

from ...i18n import tr, tr_label
from ..widgets.button_styles import apply_button_style
from .template_form_panel import usage_snippet

#: 列表行图标尺寸（模板多为 30–50 px，32 一档足够辨认）
ICON_SIZE = 32


def _thumbnail(path) -> QIcon:
    """按原图读到 ICON_SIZE 见方的图标；失败返回空图标（不拖垮列表）。"""
    try:
        import cv2
        image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8),
                             cv2.IMREAD_UNCHANGED)
        if image is None:
            return QIcon()
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        elif image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        height, width = image.shape[:2]
        scale = ICON_SIZE / max(width, height, 1)
        resized = cv2.resize(
            image, (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        qimage = QImage(bytes(rgb.copy().data), w, h, w * 3,
                        QImage.Format.Format_RGB888)
        return QIcon(QPixmap.fromImage(qimage))
    except Exception:  # noqa: BLE001 单张坏图不该影响整页
        return QIcon()


def _muted_label(text: str) -> QLabel:
    """次要文字：标签列、副标题、说明——统一走 palette(mid)，不写死颜色。"""
    label = QLabel(text)
    label.setStyleSheet("color: palette(mid);")
    return label


def _divider() -> QFrame:
    """区块分隔线：把「基本信息」与「引用 / 用法」分开，避免一坨文字。"""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


class TemplatePanel(QWidget):
    """模板图清单 + 详情 + 所有权受控的操作。"""

    data_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._items: dict[str, tlib.TemplateInfo] = {}
        self._origin = ""
        self._init_ui()
        self.refresh()

    # ─── 构造 ────────────────────────────────────────────

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)

        # ── 筛选行 ──
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel(tr_label("分组")))
        self._group_filter = AutoWidthComboBox()
        self._group_filter.setMinimumWidth(140)
        self._group_filter.currentIndexChanged.connect(self.refresh)
        filter_layout.addWidget(self._group_filter)

        filter_layout.addWidget(QLabel(tr_label("名字")))
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("搜索模板名"))
        self._search.setMinimumWidth(180)
        self._search.textChanged.connect(self.refresh)
        filter_layout.addWidget(self._search)

        self._refresh_btn = QPushButton(tr("刷新"))
        self._refresh_btn.clicked.connect(self.refresh)
        apply_button_style(self._refresh_btn, variant="neutral")
        filter_layout.addWidget(self._refresh_btn)
        filter_layout.addStretch()
        main_layout.addLayout(filter_layout)

        # ── 主体：列表 + 详情 ──
        body = QSplitter(Qt.Orientation.Horizontal)

        self._list = QListWidget()
        self._list.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._list.setUniformItemSizes(True)
        self._list.setAlternatingRowColors(True)
        self._list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._list.setSpacing(2)
        self._list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setStyleSheet(
            "QListWidget { outline: none; }"
            "QListWidget::item { padding: 4px 6px; }"
            "QListWidget::item:selected {"
            "  background-color: #3a6ea5;"
            "  color: #ffffff;"
            "  border: 1px solid #2a5a8a;"
            "}"
        )
        self._list.currentItemChanged.connect(self._on_selected)
        body.addWidget(self._list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        detail = QGroupBox(tr("模板详情"))
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 12, 12, 12)
        detail_layout.setSpacing(10)

        # 标题行：名字就地可改（只对普通分组开放）+ 右上角删除
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        self._name_edit = QLineEdit()
        self._name_edit.setMinimumWidth(120)
        self._name_edit.textChanged.connect(self._update_save_state)
        header_row.addWidget(self._name_edit, 1)
        self._delete_btn = QPushButton(tr("删除"))
        self._delete_btn.clicked.connect(self._on_delete)
        apply_button_style(self._delete_btn, variant="danger")
        header_row.addWidget(self._delete_btn, 0, Qt.AlignmentFlag.AlignTop)
        detail_layout.addLayout(header_row)

        # 分组下拉与保存：改名与换分组是同一件事，就地改完点保存
        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        self._group_combo = AutoWidthComboBox()
        self._group_combo.setEditable(True)
        self._group_combo.setMinimumWidth(120)
        self._group_combo.setMaximumWidth(200)
        self._group_combo.currentTextChanged.connect(self._update_save_state)
        meta_row.addWidget(self._group_combo)
        self._kind_label = _muted_label("")
        meta_row.addWidget(self._kind_label)
        self._copy_name_btn = QPushButton(tr("复制名字"))
        self._copy_name_btn.clicked.connect(self._on_copy_name)
        apply_button_style(self._copy_name_btn, variant="neutral")
        meta_row.addWidget(self._copy_name_btn)
        meta_row.addStretch()
        self._save_btn = QPushButton(tr("保存修改"))
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)
        apply_button_style(self._save_btn, variant="action")
        meta_row.addWidget(self._save_btn)
        detail_layout.addLayout(meta_row)

        # 完整名字（含分组）始终以灰字显示，避免忘了自己在改哪一张
        self._path_label = _muted_label("")
        self._path_label.setWordWrap(True)
        detail_layout.addWidget(self._path_label)
        detail_layout.addWidget(_divider())

        # 基础信息：标签列灰、取值列黑，右对齐标签、行距收紧
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self._pixel_label = QLabel("")
        self._record_label = QLabel("")
        self._layer_label = QLabel("")
        for row, (title, value) in enumerate((
            (tr_label("像素尺寸"), self._pixel_label),
            (tr_label("录制画布"), self._record_label),
            (tr_label("来源层"), self._layer_label),
        )):
            label = _muted_label(title)
            label.setAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignTop)
            grid.addWidget(label, row, 0)
            value.setWordWrap(True)
            grid.addWidget(value, row, 1)
        detail_layout.addLayout(grid)
        detail_layout.addWidget(_divider())

        # 引用：长文本整行铺开，标题灰、内容换行
        detail_layout.addWidget(_muted_label(tr_label("引用")))
        self._refs_label = QLabel("")
        self._refs_label.setWordWrap(True)
        detail_layout.addWidget(self._refs_label)

        # 用法：只读输入框 + 复制，紧贴引用区
        usage_row = QHBoxLayout()
        usage_row.setSpacing(8)
        usage_row.addWidget(_muted_label(tr_label("用法")))
        self._usage_edit = QLineEdit()
        self._usage_edit.setReadOnly(True)
        usage_row.addWidget(self._usage_edit, 1)
        self._copy_usage_btn = QPushButton(tr("复制"))
        self._copy_usage_btn.clicked.connect(self._on_copy_usage)
        apply_button_style(self._copy_usage_btn, variant="neutral")
        usage_row.addWidget(self._copy_usage_btn)
        detail_layout.addLayout(usage_row)

        self._hint_label = _muted_label("")
        self._hint_label.setWordWrap(True)
        detail_layout.addWidget(self._hint_label)

        right_layout.addWidget(detail)
        right_layout.addStretch()
        body.addWidget(right)
        body.setStretchFactor(0, 5)
        body.setStretchFactor(1, 4)
        main_layout.addWidget(body, 1)

        self._set_buttons_enabled(False)

    # ─── 数据 ────────────────────────────────────────────

    def refresh(self) -> None:
        """重扫模板与索引，重建分组过滤器与列表。"""
        infos = tlib.templates()
        self._items = {info.name: info for info in infos}
        self._reload_group_filter(infos)
        self._reload_list()

    def _reload_group_filter(self, infos: list[tlib.TemplateInfo]) -> None:
        """分组过滤项：不给「全部」——一次只看一组才看得清。

        排序把**共享分组放在布局分组之前**，于是 common 永远排在第一位，
        新建模板时的默认落点与这里的默认视图保持一致。
        """
        current = self._group_filter.currentData()
        root_groups = tlib.root_layout_groups()
        groups = sorted({info.group for info in infos})
        shared = [g for g in groups if g not in root_groups]
        layouts = [g for g in groups if g in root_groups]
        self._group_filter.blockSignals(True)
        self._group_filter.clear()
        for group in shared + layouts:
            kind = tr("布局") if group in root_groups else tr("共享")
            self._group_filter.addItem(f"{group}（{kind}）", group)
        index = self._group_filter.findData(current) if current else -1
        self._group_filter.setCurrentIndex(index if index >= 0 else 0)
        self._group_filter.blockSignals(False)

    def _visible(self) -> list[tlib.TemplateInfo]:
        group = self._group_filter.currentData()
        keyword = self._search.text().strip().lower()
        return [info for info in self._items.values()
                if info.group == group
                and (not keyword or keyword in info.name.lower())]

    def _reload_list(self) -> None:
        selected = self._current_name()
        self._list.blockSignals(True)
        self._list.clear()
        for info in sorted(self._visible(), key=lambda i: i.name):
            item = QListWidgetItem(_thumbnail(info.path),
                                   info.name.split("/", 1)[-1])
            item.setData(Qt.ItemDataRole.UserRole, info.name)
            item.setToolTip(self._tooltip(info))
            self._list.addItem(item)
        self._list.blockSignals(False)
        first = self._list.item(0) if self._list.count() else None
        fallback = str(first.data(Qt.ItemDataRole.UserRole)) if first else ""
        self._select(selected or fallback)

    @staticmethod
    def _tooltip(info: tlib.TemplateInfo) -> str:
        parts = [info.name]
        if info.is_sample:
            parts.append(tr("标定样本，不参与匹配"))
        if info.bindings:
            parts.append(tr("绑定 {n} 处").format(n=len(info.bindings)))
        if info.scripts:
            parts.append(tr("脚本引用 {n} 处").format(n=len(info.scripts)))
        return " ｜ ".join(parts)

    def _current_name(self) -> str:
        item = self._list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else ""

    # ─── 详情 ────────────────────────────────────────────

    def _on_selected(self, current, _previous) -> None:
        name = self._current_name()
        info = self._items.get(name) if name else None
        if info is None:
            self._origin = ""
            self._name_edit.clear()
            self._group_combo.clear()
            self._kind_label.setText("")
            self._path_label.setText("")
            for label in (self._pixel_label, self._record_label,
                          self._layer_label, self._refs_label):
                label.setText("")
            self._usage_edit.clear()
            self._hint_label.setText("")
            self._set_buttons_enabled(False)
            return

        self._origin = info.name
        kind = tr("布局") if info.is_layout_group else tr("共享")
        self._kind_label.setText(f"（{kind}）")
        self._path_label.setText(info.name)
        self._pixel_label.setText(f"{info.width}×{info.height}")
        if info.is_sample:
            self._record_label.setText(tr("标定样本，不需要尺寸"))
        elif info.has_record:
            self._record_label.setText(f"{info.record_w}×{info.record_h}")
        else:
            self._record_label.setText(tr("未登记（同分辨率专用）"))
        self._layer_label.setText({"system": tr("系统"),
                                   "local": tr("本地")}.get(info.layer, "—"))
        self._refs_label.setText(self._references_text(info))
        self._usage_edit.setText(
            "" if info.is_sample else usage_snippet(info.name))
        self._hint_label.setText(self._hint_text(info))
        self._fill_editable_fields(info)
        self._set_buttons_enabled(True, info)

    def _fill_editable_fields(self, info: tlib.TemplateInfo) -> None:
        """名字/分组就地编辑：布局分组与标定样本只读，普通分组可改名与换组。"""
        editable = not info.is_layout_group and not info.is_sample
        groups = sorted({item.group for item in self._items.values()
                         if not item.is_layout_group})
        self._group_combo.blockSignals(True)
        self._group_combo.clear()
        for group in groups:
            self._group_combo.addItem(group)
        self._group_combo.setEditText(info.group)
        self._group_combo.blockSignals(False)
        self._group_combo.setEnabled(editable)

        self._name_edit.blockSignals(True)
        self._name_edit.setText(info.name.split("/", 1)[-1])
        self._name_edit.blockSignals(False)
        self._name_edit.setReadOnly(not editable)
        # 只读时去掉输入框外观，让它看起来就是标题
        self._name_edit.setFrame(editable)
        self._name_edit.setStyleSheet(
            "" if editable else
            "QLineEdit { background: transparent; border: none; padding: 0; }")
        self._name_edit.setStyleSheet(
            ("QLineEdit { border: 1px solid palette(mid); border-radius: 4px;"
             " padding: 3px 6px; font-weight: 600; }") if editable else
            "QLineEdit { background: transparent; border: none; padding: 0;"
            " font-size: 13px; font-weight: 600; }")
        self._update_save_state()

    def _target_name(self) -> str:
        group = self._group_combo.currentText().strip()
        stem = self._name_edit.text().strip()
        return f"{group}/{stem}" if group and stem else ""

    def _update_save_state(self) -> None:
        info = self._items.get(self._origin) if self._origin else None
        editable = (info is not None and not info.is_layout_group
                    and not info.is_sample)
        self._save_btn.setVisible(editable)
        self._save_btn.setEnabled(
            editable and self._target_name() not in ("", self._origin))

    @staticmethod
    def _references_text(info: tlib.TemplateInfo) -> str:
        parts: list[str] = []
        for binding in info.bindings:
            parts.append(f"{binding.layout} / {binding.scene} / {binding.region}")
        for script in info.scripts:
            parts.append(script)
        return "；".join(parts) if parts else tr("未被引用")

    @staticmethod
    def _hint_text(info: tlib.TemplateInfo) -> str:
        if info.is_sample:
            return tr("标定样本：运行时按颜色比例判定，不参与模板匹配，永久保留")
        if info.is_layout_group:
            return tr("布局分组的模板由场景管理截取/删除，这里只读")
        return ""

    def _set_buttons_enabled(self, has_selection: bool,
                             info: tlib.TemplateInfo | None = None) -> None:
        editable = (info is not None and not info.is_layout_group
                    and not info.is_sample)
        self._copy_name_btn.setEnabled(has_selection and info is not None)
        self._copy_usage_btn.setEnabled(
            info is not None and not info.is_sample)
        self._delete_btn.setEnabled(editable)
        self._save_btn.setVisible(editable)

    # ─── 操作 ────────────────────────────────────────────

    def _on_copy_name(self) -> None:
        name = self._current_name()
        clipboard = QApplication.clipboard()
        if name and clipboard is not None:
            clipboard.setText(name)

    def _on_copy_usage(self) -> None:
        text = self._usage_edit.text().strip()
        clipboard = QApplication.clipboard()
        if text and clipboard is not None:
            clipboard.setText(text)

    def _on_save(self) -> None:
        """就地改名/换分组：与核心层同一个 renamed 语义（普通分组之间可移动）。"""
        old = self._origin
        info = self._items.get(old) if old else None
        if info is None:
            return
        stem = self._name_edit.text().strip()
        if not stem:
            QMessageBox.warning(self, tr("重命名模板"), tr("请先填写模板名"))
            return
        if "/" in stem or "\\" in stem:
            QMessageBox.warning(self, tr("重命名模板"),
                                tr("模板名不能包含路径分隔符"))
            return
        target = self._target_name()
        if target == old:
            return
        try:
            tlib.rename_template(old, target)
        except (ValueError, FileExistsError, FileNotFoundError,
                PermissionError) as exc:
            QMessageBox.warning(self, tr("重命名模板"), str(exc))
            return
        self.data_changed.emit()
        # 换了分组就跟过去，否则模板会从当前过滤视图里"消失"
        group = target.split("/", 1)[0]
        index = self._group_filter.findData(group)
        if index >= 0:
            self._group_filter.setCurrentIndex(index)
        self.refresh()
        self._select(target)

    def _on_delete(self) -> None:
        name = self._current_name()
        info = self._items.get(name)
        if info is None:
            return
        detail = ""
        if info.bindings or info.scripts:
            detail = "\n\n" + self._references_text(info)
        if QMessageBox.question(
            self, tr("删除模板"),
            tr("确定删除模板「{name}」？{detail}").format(name=name, detail=detail),
        ) != QMessageBox.StandardButton.Yes:
            return
        if not tlib.remove_template(name):
            QMessageBox.warning(self, tr("删除模板"), tr("删除失败，详见日志"))
            return
        self.data_changed.emit()
        self.refresh()

    def _select(self, name: str) -> None:
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == name:
                self._list.setCurrentItem(item)
                return
        self._list.setCurrentRow(-1)
        self._on_selected(None, None)
