"""等级配置面板

按等级区分重置支持、材料要求与抗性：
- 等级：装备等级（1-999，不可重复）
- 支持重置：该等级是否允许重置调律
- 支持承音：该等级装备是否允许进入承音合并候选
- 无限转律：该等级已转律词条是否允许再次转律
- 承音后转律：该原始等级装备承音后是否仍允许再次转律
- 最低材料数量：该等级要求的最低材料数量
- 判定抗性：判定抗性百分比（>= 0）
- 增益抗性：增益抗性百分比（>= 0）

单表格结构，支持新增/删除/上移/下移。
数据存于 attributes.yaml 的 level_configs 段。
修改即时校验写盘，失败时状态栏红字提示。
"""

from datetime import datetime

from loguru import logger
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.config import LevelConfig, get_game_config
from lvjiang.ui.button_styles import apply_button_style

from .....i18n import tr
from .factory_guard import (
    GAME_CONFIG_REL,
    READONLY_HINT,
    deletable,
    factory_list_values,
)

# 列定义
_SEQ_COL = 0
_LEVEL_COL = 1
_RESET_COL = 2
_CHENGYIN_COL = 3
_RETRANSFER_COL = 4
_CHENGYIN_RETRANSFER_COL = 5
_MATERIAL_COL = 6
_JUDGE_RES_COL = 7
_BUFF_RES_COL = 8
_COLS = (
    "#", tr("等级"), tr("支持重置"), tr("支持承音"), tr("无限转律"),
    tr("承音后转律"),
    tr("最低材料数量"), tr("判定抗性(%)"), "增益抗性(%)",
)  # runtime tr()


class LevelConfigPanel(QWidget):
    """等级配置面板（表格形式，按等级区分重置支持、材料要求与抗性）"""

    # 等级配置保存后发出信号，通知其他面板刷新 LevelCombo
    level_configs_saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = True
        self._init_ui()
        self._load_data()
        self._loading = False

    # ── UI 构建 ──

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 页面说明
        layout.addWidget(QLabel(
            "<b>等级配置</b>（按等级区分重置支持、材料要求与抗性）：\n"
            "不同等级装备可设置不同的重置权限、最低材料数量与抗性"))

        # 表格
        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels([tr(c) for c in _COLS])
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        # 序号列固定宽度
        self._table.horizontalHeader().setSectionResizeMode(
            _SEQ_COL, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_SEQ_COL, 32)
        # 布尔能力列按内容自适应
        for col in (
            _RESET_COL, _CHENGYIN_COL, _RETRANSFER_COL,
            _CHENGYIN_RETRANSFER_COL,
        ):
            self._table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumHeight(140)
        layout.addWidget(self._table)

        # 底部按钮
        btn_row = QHBoxLayout()
        add_btn = QPushButton(tr("添加配置"))
        add_btn.clicked.connect(self._on_add_row)
        btn_row.addWidget(add_btn)
        self._del_btn = QPushButton(tr("删除选中"))
        self._del_btn.clicked.connect(self._on_del_row)
        btn_row.addWidget(self._del_btn)
        btn_row.addSpacing(8)
        self._up_btn = QPushButton(tr("▲ 上移"))
        self._up_btn.setToolTip(tr("将选中配置上移一行"))
        self._up_btn.clicked.connect(self._on_move_up)
        self._up_btn.setEnabled(False)
        btn_row.addWidget(self._up_btn)
        self._down_btn = QPushButton(tr("▼ 下移"))
        self._down_btn.setToolTip(tr("将选中配置下移一行"))
        self._down_btn.clicked.connect(self._on_move_down)
        self._down_btn.setEnabled(False)
        btn_row.addWidget(self._down_btn)
        apply_button_style(add_btn)
        apply_button_style(self._del_btn, variant="danger")
        apply_button_style(self._up_btn, self._down_btn, variant="neutral")
        btn_row.addStretch()
        # 状态标签
        self._status_label = QLabel("")
        btn_row.addWidget(self._status_label)
        layout.addLayout(btn_row)
        layout.addStretch()

        # 表格选中变化时更新移动按钮状态
        self._table.itemSelectionChanged.connect(self._update_move_buttons)

    # ── 数据加载 ──

    def _load_data(self):
        """从 GameConfigManager 加载数据并填充表格"""
        manager = get_game_config()
        configs = manager.get_level_configs()
        self._table.setRowCount(0)
        for cfg in configs:
            self._make_row_widgets(cfg)
        self._update_move_buttons()

    def _make_row_widgets(self, cfg: LevelConfig) -> None:
        """在表尾新增一行并填充该配置的编辑控件"""
        row = self._table.rowCount()
        self._table.insertRow(row)

        # 序号列（只读标签）
        seq_label = QLabel(str(row + 1))
        seq_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setCellWidget(row, _SEQ_COL, seq_label)

        # 等级（必填，不预填默认值）
        level_spin = QSpinBox()
        level_spin.setRange(0, 999)
        level_spin.setSpecialValueText("")
        level_spin.setToolTip(tr("装备等级（1-999，不可重复，必填）"))
        level_spin.setValue(cfg.level)
        level_spin.valueChanged.connect(lambda _v: self._apply())
        self._table.setCellWidget(row, _LEVEL_COL, level_spin)

        # 支持重置（可选，默认未勾选 = None）
        reset_cb = QCheckBox()
        reset_cb.setToolTip(tr("该等级是否允许重置调律（可选）"))
        reset_cb.setTristate(False)
        if cfg.allow_reset is not None:
            reset_cb.setChecked(cfg.allow_reset)
        reset_cb.stateChanged.connect(lambda _s: self._apply())
        # 居中显示
        reset_widget = QWidget()
        reset_layout = QHBoxLayout(reset_widget)
        reset_layout.setContentsMargins(0, 0, 0, 0)
        reset_layout.addWidget(reset_cb)
        reset_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setCellWidget(row, _RESET_COL, reset_widget)

        # 支持承音
        chengyin_cb = QCheckBox()
        chengyin_cb.setToolTip(tr("该等级装备是否允许进入承音装备合并候选"))
        chengyin_cb.setChecked(cfg.allow_chengyin)
        chengyin_cb.stateChanged.connect(lambda _s: self._apply())
        chengyin_widget = QWidget()
        chengyin_layout = QHBoxLayout(chengyin_widget)
        chengyin_layout.setContentsMargins(0, 0, 0, 0)
        chengyin_layout.addWidget(chengyin_cb)
        chengyin_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setCellWidget(row, _CHENGYIN_COL, chengyin_widget)

        # 无限转律
        retransfer_cb = QCheckBox()
        retransfer_cb.setToolTip(tr("该等级已转律词条是否允许无限再次转律"))
        retransfer_cb.setChecked(cfg.allow_retransfer)
        retransfer_cb.stateChanged.connect(lambda _s: self._apply())
        retransfer_widget = QWidget()
        retransfer_layout = QHBoxLayout(retransfer_widget)
        retransfer_layout.setContentsMargins(0, 0, 0, 0)
        retransfer_layout.addWidget(retransfer_cb)
        retransfer_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setCellWidget(row, _RETRANSFER_COL, retransfer_widget)

        # 承音后转律（按装备名称识别出的原始等级判断）
        cy_retransfer_cb = QCheckBox()
        cy_retransfer_cb.setToolTip(tr(
            "该原始等级装备承音后是否仍允许在已转律词条上无限转律"))
        cy_retransfer_cb.setChecked(cfg.allow_retransfer_after_chengyin)
        cy_retransfer_cb.stateChanged.connect(lambda _s: self._apply())
        cy_retransfer_widget = QWidget()
        cy_retransfer_layout = QHBoxLayout(cy_retransfer_widget)
        cy_retransfer_layout.setContentsMargins(0, 0, 0, 0)
        cy_retransfer_layout.addWidget(cy_retransfer_cb)
        cy_retransfer_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setCellWidget(
            row, _CHENGYIN_RETRANSFER_COL, cy_retransfer_widget)

        # 最低材料数量（可选，默认空）
        material_spin = QSpinBox()
        material_spin.setRange(0, 99999)
        material_spin.setSpecialValueText("")
        material_spin.setToolTip(tr("该等级要求的最低材料数量（可选）"))
        if cfg.min_material_count is not None:
            material_spin.setValue(cfg.min_material_count)
        material_spin.valueChanged.connect(lambda _v: self._apply())
        self._table.setCellWidget(row, _MATERIAL_COL, material_spin)

        # 判定抗性（可选，百分比 >= 0，默认空）
        judge_spin = QSpinBox()
        judge_spin.setRange(0, 99999)
        judge_spin.setSpecialValueText("")
        judge_spin.setToolTip(tr("判定抗性百分比（可选，>= 0）"))
        if cfg.judge_resistance is not None:
            judge_spin.setValue(cfg.judge_resistance)
        judge_spin.valueChanged.connect(lambda _v: self._apply())
        self._table.setCellWidget(row, _JUDGE_RES_COL, judge_spin)

        # 增益抗性（可选，百分比 >= 0，默认空）
        buff_spin = QSpinBox()
        buff_spin.setRange(0, 99999)
        buff_spin.setSpecialValueText("")
        buff_spin.setToolTip(tr("增益抗性百分比（可选，>= 0）"))
        if cfg.buff_resistance is not None:
            buff_spin.setValue(cfg.buff_resistance)
        buff_spin.valueChanged.connect(lambda _v: self._apply())
        self._table.setCellWidget(row, _BUFF_RES_COL, buff_spin)

    # ── 行增删移动 ──

    def _on_add_row(self):
        self._loading = True
        self._make_row_widgets(LevelConfig())
        self._loading = False
        self._update_move_buttons()
        self._apply()

    def _on_del_row(self):
        row = self._table.currentRow()
        if row < 0:
            self._set_status(tr("请先选中要删除的配置行"), True)
            return
        self._table.removeRow(row)
        self._refresh_seq_numbers()
        self._update_move_buttons()
        self._apply()

    def _on_move_up(self):
        row = self._table.currentRow()
        if row <= 0:
            self._set_status(tr("已是第一条配置，无法上移"), True)
            return
        self._swap_rows(row, row - 1)
        self._table.selectRow(row - 1)
        self._refresh_seq_numbers()
        self._update_move_buttons()
        self._apply()

    def _on_move_down(self):
        row = self._table.currentRow()
        if row < 0 or row >= self._table.rowCount() - 1:
            self._set_status(tr("已是最后一条配置，无法下移"), True)
            return
        self._swap_rows(row, row + 1)
        self._table.selectRow(row + 1)
        self._refresh_seq_numbers()
        self._update_move_buttons()
        self._apply()

    def _update_move_buttons(self) -> None:
        """根据当前选中行更新移动按钮的启用状态"""
        row = self._table.currentRow()
        has_selection = row >= 0
        self._up_btn.setEnabled(has_selection and row > 0)
        self._down_btn.setEnabled(
            has_selection and row < self._table.rowCount() - 1)
        self._refresh_del_enabled(row)

    def _refresh_del_enabled(self, row: int) -> None:
        """系统行不允许用户删除，置灰并说明原因"""
        # 本表所有列都是 setCellWidget，item() 恒为 None——身份要从
        # _LEVEL_COL 上的 QSpinBox 取值，不能读单元格文本。
        ident = None
        if row >= 0:
            widget = self._table.cellWidget(row, _LEVEL_COL)
            if isinstance(widget, QSpinBox):
                ident = widget.value()
        ok, hint = deletable(
            ident, factory_list_values(GAME_CONFIG_REL, "level_configs", field="level"),
            hint=READONLY_HINT)
        self._del_btn.setEnabled(row >= 0 and ok)
        self._del_btn.setToolTip(hint)

    def _refresh_seq_numbers(self) -> None:
        """刷新序号列，保持与行序一致"""
        for r in range(self._table.rowCount()):
            w = self._table.cellWidget(r, _SEQ_COL)
            if isinstance(w, QLabel):
                w.setText(str(r + 1))

    def _swap_rows(self, row_a: int, row_b: int) -> None:
        """交换两行的配置数据"""
        values_a = self._row_values(row_a)
        values_b = self._row_values(row_b)
        self._set_row_values(row_a, values_b)
        self._set_row_values(row_b, values_a)

    def _row_values(self, row: int) -> dict:
        """收集一行的配置控件值（可选字段为空时返回 None）"""
        level_spin: QSpinBox = self._table.cellWidget(row, _LEVEL_COL)
        reset_widget = self._table.cellWidget(row, _RESET_COL)
        reset_cb = reset_widget.findChild(QCheckBox)
        chengyin_widget = self._table.cellWidget(row, _CHENGYIN_COL)
        chengyin_cb = chengyin_widget.findChild(QCheckBox)
        retransfer_widget = self._table.cellWidget(row, _RETRANSFER_COL)
        retransfer_cb = retransfer_widget.findChild(QCheckBox)
        cy_retransfer_widget = self._table.cellWidget(
            row, _CHENGYIN_RETRANSFER_COL)
        cy_retransfer_cb = cy_retransfer_widget.findChild(QCheckBox)
        material_spin: QSpinBox = self._table.cellWidget(row, _MATERIAL_COL)
        judge_spin: QSpinBox = self._table.cellWidget(row, _JUDGE_RES_COL)
        buff_spin: QSpinBox = self._table.cellWidget(row, _BUFF_RES_COL)
        # 等级：0 表示未填写（校验时会报错）
        level_val = level_spin.value()
        # 支持重置：checkbox 无法表达 None，用 False 作为默认
        # 但用户未勾选时我们仍记录为 False（不是 None）
        allow_reset_val = reset_cb.isChecked() if reset_cb else False
        # 最低材料数量：0 表示未填写，返回 None
        material_val = material_spin.value()
        # 判定抗性：0 表示未填写，返回 None
        judge_val = judge_spin.value()
        # 增益抗性：0 表示未填写，返回 None
        buff_val = buff_spin.value()
        return {
            "level": level_val,
            "allow_reset": allow_reset_val,
            "allow_chengyin": bool(chengyin_cb and chengyin_cb.isChecked()),
            "allow_retransfer": bool(
                retransfer_cb and retransfer_cb.isChecked()),
            "allow_retransfer_after_chengyin": bool(
                cy_retransfer_cb and cy_retransfer_cb.isChecked()),
            "min_material_count": material_val if material_val > 0 else None,
            "judge_resistance": judge_val if judge_val > 0 else None,
            "buff_resistance": buff_val if buff_val > 0 else None,
        }

    def _set_row_values(self, row: int, values: dict) -> None:
        """将值写回指定行的控件"""
        level_spin: QSpinBox = self._table.cellWidget(row, _LEVEL_COL)
        level_spin.blockSignals(True)
        level_spin.setValue(values["level"])
        level_spin.blockSignals(False)
        reset_widget = self._table.cellWidget(row, _RESET_COL)
        reset_cb = reset_widget.findChild(QCheckBox)
        if reset_cb:
            reset_cb.blockSignals(True)
            reset_cb.setChecked(values["allow_reset"])
            reset_cb.blockSignals(False)
        chengyin_widget = self._table.cellWidget(row, _CHENGYIN_COL)
        chengyin_cb = chengyin_widget.findChild(QCheckBox)
        if chengyin_cb:
            chengyin_cb.blockSignals(True)
            chengyin_cb.setChecked(values["allow_chengyin"])
            chengyin_cb.blockSignals(False)
        retransfer_widget = self._table.cellWidget(row, _RETRANSFER_COL)
        retransfer_cb = retransfer_widget.findChild(QCheckBox)
        if retransfer_cb:
            retransfer_cb.blockSignals(True)
            retransfer_cb.setChecked(values["allow_retransfer"])
            retransfer_cb.blockSignals(False)
        cy_retransfer_widget = self._table.cellWidget(
            row, _CHENGYIN_RETRANSFER_COL)
        cy_retransfer_cb = cy_retransfer_widget.findChild(QCheckBox)
        if cy_retransfer_cb:
            cy_retransfer_cb.blockSignals(True)
            cy_retransfer_cb.setChecked(
                values["allow_retransfer_after_chengyin"])
            cy_retransfer_cb.blockSignals(False)
        material_spin: QSpinBox = self._table.cellWidget(row, _MATERIAL_COL)
        material_spin.blockSignals(True)
        # None 表示空，设置为 0（显示为空）
        material_val = values["min_material_count"]
        material_spin.setValue(material_val if material_val is not None else 0)
        material_spin.blockSignals(False)
        judge_spin: QSpinBox = self._table.cellWidget(row, _JUDGE_RES_COL)
        judge_spin.blockSignals(True)
        judge_val = values.get("judge_resistance")
        judge_spin.setValue(judge_val if judge_val is not None else 0)
        judge_spin.blockSignals(False)
        buff_spin: QSpinBox = self._table.cellWidget(row, _BUFF_RES_COL)
        buff_spin.blockSignals(True)
        buff_val = values.get("buff_resistance")
        buff_spin.setValue(buff_val if buff_val is not None else 0)
        buff_spin.blockSignals(False)

    # ── 收集 → 校验 → 写盘 → reload ──

    def _configs_raw(self) -> list[dict]:
        """收集表格数据，过滤掉 None 值（不写入 YAML）"""
        result = []
        for row in range(self._table.rowCount()):
            values = self._row_values(row)
            # 过滤 None 值
            filtered = {k: v for k, v in values.items() if v is not None}
            result.append(filtered)
        return result

    def _apply(self):
        if self._loading:
            return
        manager = get_game_config()
        data = manager.get_raw()
        data["level_configs"] = self._configs_raw()
        try:
            manager.save(data)
        except Exception as e:
            logger.exception("等级配置保存失败")
            self._set_status(f"保存失败：{e}", True)
            return
        # 通知其他面板刷新 LevelCombo
        self.level_configs_saved.emit()
        now = datetime.now().strftime("%H:%M:%S")
        self._set_status(f"已保存并生效（{now}）", False)

    def _set_status(self, text: str, is_error: bool):
        """更新状态标签"""
        color = "#c62828" if is_error else "#2e7d32"
        self._status_label.setStyleSheet(f"color: {color};")
        self._status_label.setText(text)
