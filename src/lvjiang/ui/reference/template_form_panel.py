"""新增图像 → 模板图：单矩形裁剪、命名、录制尺寸、测试匹配与用法片段。

模板图通常是**单张**图（不是一个网格），所以这里只做一次矩形裁剪；网格参数与切割
结果属于参考图工具。布局分组（目录名 = 根布局 key）的模板由场景管理截取，这里只
允许写入普通分组，避免两个入口同时改同一张图。
"""

from __future__ import annotations

import numpy as np
from loguru import logger
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lvjiang.core import templates as tlib
from lvjiang.core.recognizers.template_locator import (
    DEFAULT_MIN_SCORE,
    get_template_store,
    locate,
    template_from_image,
    validate_template_image,
    write_template,
)
from lvjiang.ui.widgets.combo_box import AutoWidthComboBox

from ...i18n import tr, tr_label
from ..widgets.button_styles import apply_button_style

#: 用法片段的输入框至少容纳一行完整语句
_MIN_USAGE_WIDTH = 260


class TemplateFormPanel(QWidget):
    """模板图的保存表单；``saved`` 在写入成功后发射模板名。"""

    saved = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._image: np.ndarray | None = None
        self._region: tuple[int, int, int, int] | None = None
        self._build()

    # ─── 构造 ────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        group_box = QGroupBox(tr("模板图"))
        body = QVBoxLayout(group_box)

        hint = QLabel(tr(
            "在画布上框选一个矩形即为模板图。分组留空写入 common；"
            "布局分组的模板由场景管理截取，这里不能写。"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        body.addWidget(hint)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._group_combo = AutoWidthComboBox()
        self._group_combo.setEditable(True)
        self._group_combo.setMinimumWidth(160)
        self._group_combo.setMaximumWidth(220)
        form.addRow(tr_label("分组"), self._group_combo)

        self._name_edit = QLineEdit()
        self._name_edit.setMinimumWidth(160)
        self._name_edit.setPlaceholderText(tr("模板名（不含分组）"))
        form.addRow(tr_label("名字"), self._name_edit)
        body.addLayout(form)

        self._size_label = QLabel(tr("未框选区域"))
        self._size_label.setStyleSheet("color: palette(mid);")
        body.addWidget(self._size_label)

        test_row = QHBoxLayout()
        self._test_btn = QPushButton(tr("测试匹配"))
        self._test_btn.setEnabled(False)
        self._test_btn.clicked.connect(self._on_test)
        test_row.addWidget(self._test_btn)
        self._test_label = QLabel("")
        test_row.addWidget(self._test_label, 1)
        body.addLayout(test_row)

        self._usage_edit = QLineEdit()
        self._usage_edit.setReadOnly(True)
        self._usage_edit.setMinimumWidth(_MIN_USAGE_WIDTH)
        usage_row = QHBoxLayout()
        usage_row.addWidget(QLabel(tr_label("用法")))
        usage_row.addWidget(self._usage_edit, 1)
        self._copy_btn = QPushButton(tr("复制"))
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._on_copy_usage)
        usage_row.addWidget(self._copy_btn)
        body.addLayout(usage_row)

        save_row = QHBoxLayout()
        save_row.addStretch()
        self._save_btn = QPushButton(tr("保存到模板库"))
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)
        save_row.addWidget(self._save_btn)
        body.addLayout(save_row)

        apply_button_style(self._test_btn, self._copy_btn, variant="neutral")
        root.addWidget(group_box)
        root.addStretch()

        self.refresh_groups()

    def refresh_groups(self) -> None:
        """分组候选：现有普通分组（布局分组不出现），默认 common。"""
        current = self._group_combo.currentText().strip()
        groups = sorted({item.group for item in tlib.templates()
                         if not item.is_layout_group})
        self._group_combo.blockSignals(True)
        self._group_combo.clear()
        for group in groups or ["common"]:
            self._group_combo.addItem(group)
        self._group_combo.setEditText(current or "common")
        self._group_combo.blockSignals(False)

    # ─── 数据源 ──────────────────────────────────────────

    def update_source(self, image: np.ndarray | None,
                      region: tuple[int, int, int, int] | None) -> None:
        """画布图片/框选变化时刷新尺寸与按钮可用性。"""
        self._image = image
        self._region = region
        self._test_label.setText("")
        ready = image is not None and region is not None
        self._test_btn.setEnabled(ready)
        self._save_btn.setEnabled(ready)
        if image is None:
            self._size_label.setText(tr("未导入图片"))
            return
        if region is None:
            self._size_label.setText(tr("未框选区域"))
            return
        x1, y1, x2, y2 = region
        width, height = x2 - x1, y2 - y1
        record_w, record_h = self.record_size(image)
        self._size_label.setText(
            tr("像素 {w}×{h} ｜ 录制画布 {rw}×{rh}").format(
                w=width, h=height, rw=record_w, rh=record_h))

    @staticmethod
    def record_size(image: np.ndarray) -> tuple[int, int]:
        """录制画布 = 当前截图按**当前激活布局**的画布比例换算后的宽高。

        与场景编辑器记录 record_w/record_h 的口径一致；取不到布局时按原始像素。
        """
        height, width = image.shape[:2]
        try:
            from lvjiang.core.layout.manager import LayoutConfigManager
            manager = LayoutConfigManager()
            layout = manager.load_layout(manager.get_active_layout_key())
            canvas = layout.get_canvas() if layout is not None else None
        except Exception as exc:  # noqa: BLE001 取不到布局就按原始像素记录
            logger.warning(f"读取当前布局失败，模板按原始像素记录: {exc}")
            canvas = None
        if canvas is None:
            return width, height
        return (max(1, round(width * canvas.w_ratio)),
                max(1, round(height * canvas.h_ratio)))

    # ─── 动作 ────────────────────────────────────────────

    def _crop(self) -> np.ndarray | None:
        if self._image is None or self._region is None:
            return None
        x1, y1, x2, y2 = self._region
        if x2 - x1 < 4 or y2 - y1 < 4:
            QMessageBox.warning(self, tr("裁剪模板"),
                                tr("模板至少需要 4×4 像素，请调整框选"))
            return None
        return self._image[y1:y2, x1:x2].copy()

    def target_name(self) -> str:
        group = self._group_combo.currentText().strip() or "common"
        name = self._name_edit.text().strip()
        return f"{group}/{name}" if name else ""

    def _on_test(self) -> None:
        crop = self._crop()
        image = self._image
        if crop is None or image is None:
            return
        width, height = self.record_size(image)
        try:
            template = template_from_image(
                self.target_name() or "probe", crop, width, height)
        except ValueError as exc:
            self._test_label.setText(str(exc))
            return
        h, w = image.shape[:2]
        hit = locate(image, template, 0, 0, w - 1, h - 1,
                     scales=(1.0,), min_score=0.0)
        score = hit.score if hit is not None else 0.0
        passed = hit is not None and score >= DEFAULT_MIN_SCORE
        self._test_label.setText(
            tr("分数 {score:.3f}（阈值 {threshold:.2f}）").format(
                score=score, threshold=DEFAULT_MIN_SCORE)
            + ("  ✓" if passed else ""))

    def _on_save(self) -> None:
        crop = self._crop()
        if crop is None:
            return
        group = self._group_combo.currentText().strip() or "common"
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, tr("保存到模板库"), tr("请先填写模板名"))
            return
        if "/" in name or "\\" in name:
            QMessageBox.warning(self, tr("保存到模板库"),
                                tr("模板名不能包含路径分隔符"))
            return
        full = f"{group}/{name}"
        try:
            if tlib.is_layout_group(full):
                QMessageBox.warning(
                    self, tr("保存到模板库"),
                    tr("「{group}」是布局分组，模板由场景管理截取").format(
                        group=group))
                return
            validate_template_image(crop)
        except ValueError as exc:
            QMessageBox.warning(self, tr("保存到模板库"), str(exc))
            return

        existing = get_template_store().get(full)
        if existing is not None and QMessageBox.question(
            self, tr("保存到模板库"),
            tr("模板「{name}」已存在，覆盖？").format(name=full),
        ) != QMessageBox.StandardButton.Yes:
            return

        image = self._image
        if image is None:
            return
        width, height = self.record_size(image)
        try:
            write_template(full, crop)
        except (OSError, ValueError) as exc:
            logger.exception("保存模板失败")
            QMessageBox.warning(self, tr("保存到模板库"), str(exc))
            return
        tlib.set_entry(full, record_w=width, record_h=height)
        self._usage_edit.setText(usage_snippet(full))
        self._copy_btn.setEnabled(True)
        self.refresh_groups()
        logger.info(f"模板已保存: {full}（录制 {width}×{height}）")
        self.saved.emit(full)

    def _on_copy_usage(self) -> None:
        from PyQt6.QtWidgets import QApplication
        text = self._usage_edit.text().strip()
        clipboard = QApplication.clipboard()
        if text and clipboard is not None:
            clipboard.setText(text)


def usage_snippet(name: str) -> str:
    """可复制的用法：布局分组给 scan 形态，普通分组给 find 形态。"""
    if tlib.is_layout_group(name):
        parts = name.split("/")
        if len(parts) >= 3:
            return f"scan [{parts[-2]}].[{parts[-1]}] as $hit by image"
    return f'find as $v by image "{name}"'
