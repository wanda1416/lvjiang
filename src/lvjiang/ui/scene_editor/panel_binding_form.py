"""布局网格参数表单；不读写场景定义。"""

from dataclasses import dataclass

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QWidget,
)

from ...core.layout_models import Panel
from ...i18n import tr


@dataclass(frozen=True)
class PanelBindingConfig:
    rows: int = 3
    cols: int = 6
    min_visible: float = 0.95
    calibration: str = "auto"
    scroll_direction: str = "vertical"
    disabled: bool = False

    @classmethod
    def from_panel(cls, panel: Panel) -> "PanelBindingConfig":
        return cls(**{key: getattr(panel, key) for key in cls.__dataclass_fields__})

    def create_panel(self, key: str, x: float, y: float, w: float, h: float) -> Panel:
        return Panel(
            key=key, x_ratio=x, y_ratio=y, w_ratio=w, h_ratio=h,
            rows=self.rows, cols=self.cols, min_visible=self.min_visible,
            calibration=self.calibration, scroll_direction=self.scroll_direction,
            disabled=self.disabled,
        )


class PanelBindingForm(QWidget):
    """创建绑定和编辑绑定复用同一参数表单及校验。"""

    def __init__(self, panel: Panel | None = None, parent=None):
        super().__init__(parent)
        config = PanelBindingConfig.from_panel(panel) if panel else PanelBindingConfig()
        form = QFormLayout(self)
        self.rows = QSpinBox()
        self.cols = QSpinBox()
        for spin, count in ((self.rows, config.rows), (self.cols, config.cols)):
            spin.setRange(1, max(20, count))
            spin.setValue(count)
        form.addRow(tr("行数:"), self.rows)
        form.addRow(tr("列数:"), self.cols)
        self.visible = QDoubleSpinBox()
        self.visible.setRange(0.51, 1.0)
        self.visible.setDecimals(2)
        self.visible.setSingleStep(0.01)
        self.visible.setValue(config.min_visible)
        form.addRow(tr("最小可见比例:"), self.visible)
        self.calibration = QComboBox()
        for value, label in (("auto", "自动模式"), ("even", "等分网格"), ("image", "图像检测")):
            self.calibration.addItem(tr(label), value)
        self.calibration.setCurrentIndex(self.calibration.findData(config.calibration))
        form.addRow(tr("校准模式:"), self.calibration)
        self.direction = QComboBox()
        for value, label in (("vertical", "纵向滚动"), ("horizontal", "横向滚动"),
                             ("both", "双向滚动"), ("none", "固定网格")):
            self.direction.addItem(tr(label), value)
        self.direction.setCurrentIndex(self.direction.findData(config.scroll_direction))
        form.addRow(tr("滚动方向:"), self.direction)
        self.disabled = QCheckBox(tr("在此布局中禁用"))
        self.disabled.setChecked(config.disabled)
        form.addRow(self.disabled)
        if panel is not None and panel.w_ratio > 0 and panel.h_ratio > 0:
            form.addRow(tr("位置 / 尺寸:"), QLabel(
                f"{panel.x_ratio:.4f}, {panel.y_ratio:.4f} / "
                f"{panel.w_ratio:.4f}, {panel.h_ratio:.4f}"))
            form.addRow(QLabel(tr("位置和尺寸可在画布中拖动调整。")))
        self.error = QLabel()
        self.error.setStyleSheet("color: #c62828;")
        form.addRow(self.error)

    def value(self) -> PanelBindingConfig:
        return PanelBindingConfig(
            rows=self.rows.value(), cols=self.cols.value(),
            min_visible=self.visible.value(), calibration=self.calibration.currentData(),
            scroll_direction=self.direction.currentData(), disabled=self.disabled.isChecked(),
        )

    def validate(self) -> bool:
        try:
            self.value().create_panel("validation", 0, 0, 1, 1)
        except ValueError as exc:
            self.error.setText(str(exc))
            return False
        self.error.clear()
        return True
