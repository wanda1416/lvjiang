"""赛季配置面板

管理游戏赛季配置：
- 赛季编号：用于排序（1, 2, 3...）
- 赛季名称：如"黄钟长鸣"、"夹钟并作"
- 赛季时间：开始日期 ~ 结束日期
- 上半赛季结束日期：用于区分上下半赛季
- 装备等级：当前赛季的装备等级（如 90, 96, 100）

单表格结构，支持新增/删除/上移/下移。
数据存于 game_config.yaml 的 season_configs 段。
修改即时校验写盘，失败时状态栏红字提示。
"""

from datetime import date, datetime

from loguru import logger
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QDateEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.config import SeasonConfig, get_game_config

from .....i18n import tr
from .factory_guard import (
    GAME_CONFIG_REL,
    READONLY_HINT,
    deletable,
    factory_list_values,
)
from .level_combo import LevelCombo


def _today_qdate() -> QDate:
    """返回今天的 QDate"""
    today = date.today()
    return QDate(today.year, today.month, today.day)

# 列定义
_SEQ_COL = 0
_NUMBER_COL = 1
_NAME_COL = 2
_START_DATE_COL = 3
_END_DATE_COL = 4
_FIRST_HALF_COL = 5
_EQUIP_LEVEL_COL = 6
_COLS = ("#", tr("赛季编号"), tr("赛季名称"), tr("开始日期"), tr("结束日期"), "上半赛季结束", "装备等级")  # runtime tr()


class SeasonConfigPanel(QWidget):
    """赛季配置面板（表格形式）"""

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
            "<b>赛季配置</b>（管理游戏赛季时间与装备等级）：\n"
            "相邻赛季结束与开始日期需相同（赛季结束于当日凌晨 5 点）"))

        # 表格
        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels([tr(c) for c in _COLS])
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        # 序号列固定宽度
        self._table.horizontalHeader().setSectionResizeMode(
            _SEQ_COL, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_SEQ_COL, 32)
        # 赛季编号列固定宽度
        self._table.horizontalHeader().setSectionResizeMode(
            _NUMBER_COL, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_NUMBER_COL, 70)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumHeight(140)
        layout.addWidget(self._table)

        # 底部按钮
        btn_row = QHBoxLayout()
        add_btn = QPushButton(tr("添加赛季"))
        add_btn.clicked.connect(self._on_add_row)
        btn_row.addWidget(add_btn)
        self._del_btn = QPushButton(tr("删除选中"))
        self._del_btn.clicked.connect(self._on_del_row)
        btn_row.addWidget(self._del_btn)
        btn_row.addSpacing(8)
        self._up_btn = QPushButton(tr("▲ 上移"))
        self._up_btn.setToolTip(tr("将选中赛季上移一行"))
        self._up_btn.clicked.connect(self._on_move_up)
        self._up_btn.setEnabled(False)
        btn_row.addWidget(self._up_btn)
        self._down_btn = QPushButton(tr("▼ 下移"))
        self._down_btn.setToolTip(tr("将选中赛季下移一行"))
        self._down_btn.clicked.connect(self._on_move_down)
        self._down_btn.setEnabled(False)
        btn_row.addWidget(self._down_btn)
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
        configs = manager.get_season_configs()
        self._table.setRowCount(0)
        for cfg in configs:
            self._make_row_widgets(cfg)
        self._update_move_buttons()

    def _make_row_widgets(self, cfg: SeasonConfig) -> None:
        """在表尾新增一行并填充该赛季的编辑控件"""
        row = self._table.rowCount()
        self._table.insertRow(row)

        # 序号列（只读标签）
        seq_label = QLabel(str(row + 1))
        seq_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setCellWidget(row, _SEQ_COL, seq_label)

        # 赛季编号（必填）
        number_spin = QSpinBox()
        number_spin.setRange(1, 999)
        number_spin.setToolTip(tr("赛季编号（1, 2, 3...，用于排序，不可重复）"))
        number_spin.setValue(cfg.season_number if cfg.season_number > 0 else 1)
        number_spin.valueChanged.connect(lambda _v: self._apply())
        self._table.setCellWidget(row, _NUMBER_COL, number_spin)

        # 赛季名称（必填）
        name_edit = QLineEdit()
        name_edit.setPlaceholderText(tr("如：黄钟长鸣"))
        name_edit.setToolTip(tr("赛季名称（如：黄钟长鸣、夹钟并作）"))
        name_edit.setText(cfg.name)
        name_edit.editingFinished.connect(self._apply)
        self._table.setCellWidget(row, _NAME_COL, name_edit)

        # 开始日期（默认为今天）
        start_date_edit = QDateEdit()
        start_date_edit.setCalendarPopup(True)
        start_date_edit.setDisplayFormat("yyyy-MM-dd")
        start_date_edit.setToolTip(tr("赛季开始日期"))
        if cfg.start_date:
            start_date_edit.setDate(QDate(cfg.start_date.year, cfg.start_date.month, cfg.start_date.day))
        else:
            start_date_edit.setDate(_today_qdate())
        start_date_edit.dateChanged.connect(lambda _v: self._apply())
        self._table.setCellWidget(row, _START_DATE_COL, start_date_edit)

        # 结束日期（默认为今天 + 84 天）
        end_date_edit = QDateEdit()
        end_date_edit.setCalendarPopup(True)
        end_date_edit.setDisplayFormat("yyyy-MM-dd")
        end_date_edit.setToolTip(tr("赛季结束日期"))
        if cfg.end_date:
            end_date_edit.setDate(QDate(cfg.end_date.year, cfg.end_date.month, cfg.end_date.day))
        else:
            end_date_edit.setDate(_today_qdate().addDays(84))
        end_date_edit.dateChanged.connect(lambda _v: self._apply())
        self._table.setCellWidget(row, _END_DATE_COL, end_date_edit)

        # 上半赛季结束日期
        first_half_edit = QDateEdit()
        first_half_edit.setCalendarPopup(True)
        first_half_edit.setDisplayFormat("yyyy-MM-dd")
        first_half_edit.setToolTip(tr("上半赛季结束日期"))
        if cfg.first_half_end_date:
            first_half_edit.setDate(QDate(cfg.first_half_end_date.year, cfg.first_half_end_date.month, cfg.first_half_end_date.day))
        else:
            first_half_edit.setDate(_today_qdate().addDays(42))
        first_half_edit.dateChanged.connect(lambda _v: self._apply())
        self._table.setCellWidget(row, _FIRST_HALF_COL, first_half_edit)

        # 装备等级（下拉选择）
        level_combo = LevelCombo(allow_empty=True)
        level_combo.setToolTip(tr("当前赛季装备等级（从等级配置中选择）"))
        level_combo.set_level(cfg.equip_level)
        level_combo.currentIndexChanged.connect(lambda _v: self._apply())
        self._table.setCellWidget(row, _EQUIP_LEVEL_COL, level_combo)

    # ── 行增删移动 ──

    def _on_add_row(self):
        self._loading = True
        # 新赛季编号默认为当前最大值 + 1
        max_number = 0
        for row in range(self._table.rowCount()):
            spin: QSpinBox = self._table.cellWidget(row, _NUMBER_COL)
            if spin.value() > max_number:
                max_number = spin.value()
        new_cfg = SeasonConfig(season_number=max_number + 1)
        self._make_row_widgets(new_cfg)
        self._loading = False
        self._update_move_buttons()
        self._apply()

    def _on_del_row(self):
        row = self._table.currentRow()
        if row < 0:
            self._set_status(tr("请先选中要删除的赛季行"), True)
            return
        self._table.removeRow(row)
        self._refresh_seq_numbers()
        self._update_move_buttons()
        self._apply()

    def _on_move_up(self):
        row = self._table.currentRow()
        if row <= 0:
            self._set_status(tr("已是第一条赛季，无法上移"), True)
            return
        self._swap_rows(row, row - 1)
        self._table.selectRow(row - 1)
        self._refresh_seq_numbers()
        self._update_move_buttons()
        self._apply()

    def _on_move_down(self):
        row = self._table.currentRow()
        if row < 0 or row >= self._table.rowCount() - 1:
            self._set_status(tr("已是最后一条赛季，无法下移"), True)
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
        """出厂行不允许用户删除，置灰并说明原因"""
        # 本表所有列都是 setCellWidget，item() 恒为 None——身份要从
        # _NUMBER_COL 上的 QSpinBox 取值，不能读单元格文本。
        ident = None
        if row >= 0:
            widget = self._table.cellWidget(row, _NUMBER_COL)
            if isinstance(widget, QSpinBox):
                ident = widget.value()
        ok, hint = deletable(
            ident, factory_list_values(GAME_CONFIG_REL, "season_configs", field="season_number"),
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
        """交换两行的赛季数据"""
        values_a = self._row_values(row_a)
        values_b = self._row_values(row_b)
        self._set_row_values(row_a, values_b)
        self._set_row_values(row_b, values_a)

    def _row_values(self, row: int) -> dict:
        """收集一行的赛季控件值"""
        number_spin: QSpinBox = self._table.cellWidget(row, _NUMBER_COL)
        name_edit: QLineEdit = self._table.cellWidget(row, _NAME_COL)
        start_date_edit: QDateEdit = self._table.cellWidget(row, _START_DATE_COL)
        end_date_edit: QDateEdit = self._table.cellWidget(row, _END_DATE_COL)
        first_half_edit: QDateEdit = self._table.cellWidget(row, _FIRST_HALF_COL)
        level_combo: LevelCombo = self._table.cellWidget(row, _EQUIP_LEVEL_COL)

        # 转换 QDate 到 date
        def qdate_to_date(qd: QDate) -> date | None:
            if qd.isNull():
                return None
            return date(qd.year(), qd.month(), qd.day())

        return {
            "season_number": number_spin.value(),
            "name": name_edit.text().strip(),
            "start_date": qdate_to_date(start_date_edit.date()),
            "end_date": qdate_to_date(end_date_edit.date()),
            "first_half_end_date": qdate_to_date(first_half_edit.date()),
            "equip_level": level_combo.get_level(),
        }

    def _set_row_values(self, row: int, values: dict) -> None:
        """将值写回指定行的控件"""
        number_spin: QSpinBox = self._table.cellWidget(row, _NUMBER_COL)
        number_spin.blockSignals(True)
        number_spin.setValue(values["season_number"])
        number_spin.blockSignals(False)

        name_edit: QLineEdit = self._table.cellWidget(row, _NAME_COL)
        name_edit.blockSignals(True)
        name_edit.setText(values["name"])
        name_edit.blockSignals(False)

        def date_to_qdate(d: date | None) -> QDate:
            if d is None:
                return QDate()
            return QDate(d.year, d.month, d.day)

        start_date_edit: QDateEdit = self._table.cellWidget(row, _START_DATE_COL)
        start_date_edit.blockSignals(True)
        start_date_edit.setDate(date_to_qdate(values.get("start_date")))
        start_date_edit.blockSignals(False)

        end_date_edit: QDateEdit = self._table.cellWidget(row, _END_DATE_COL)
        end_date_edit.blockSignals(True)
        end_date_edit.setDate(date_to_qdate(values.get("end_date")))
        end_date_edit.blockSignals(False)

        first_half_edit: QDateEdit = self._table.cellWidget(row, _FIRST_HALF_COL)
        first_half_edit.blockSignals(True)
        first_half_edit.setDate(date_to_qdate(values.get("first_half_end_date")))
        first_half_edit.blockSignals(False)

        level_combo: LevelCombo = self._table.cellWidget(row, _EQUIP_LEVEL_COL)
        level_combo.blockSignals(True)
        level_combo.set_level(values.get("equip_level"))
        level_combo.blockSignals(False)

    # ── 校验 ──

    def _validate(self) -> str | None:
        """校验数据合法性，返回错误信息或 None"""
        # 检查赛季编号是否重复
        numbers = []
        for row in range(self._table.rowCount()):
            spin: QSpinBox = self._table.cellWidget(row, _NUMBER_COL)
            numbers.append(spin.value())
        if len(numbers) != len(set(numbers)):
            return tr("赛季编号不可重复")

        # 检查赛季名称是否为空
        for row in range(self._table.rowCount()):
            name_edit: QLineEdit = self._table.cellWidget(row, _NAME_COL)
            if not name_edit.text().strip():
                return f"第 {row + 1} 行赛季名称不能为空"

        # 检查时间重叠
        seasons = []
        for row in range(self._table.rowCount()):
            values = self._row_values(row)
            if values["start_date"] and values["end_date"]:
                seasons.append((values["season_number"], values["start_date"], values["end_date"]))

        seasons.sort(key=lambda x: x[1])
        for i in range(len(seasons) - 1):
            _, _, end1 = seasons[i]
            _, start2, _ = seasons[i + 1]
            # 允许同一天（赛季结束于凌晨 5 点，新赛季可于同日开始）
            if end1 > start2:
                return f"赛季 {seasons[i][0]} 和 {seasons[i+1][0]} 时间重叠"

        return None

    # ── 收集 → 校验 → 写盘 → reload ──

    def _configs_raw(self) -> list[dict]:
        """收集表格数据，过滤掉 None 值"""
        result = []
        for row in range(self._table.rowCount()):
            values = self._row_values(row)
            # 转换日期为 ISO 格式字符串
            if values["start_date"]:
                values["start_date"] = values["start_date"].isoformat()
            if values["end_date"]:
                values["end_date"] = values["end_date"].isoformat()
            if values["first_half_end_date"]:
                values["first_half_end_date"] = values["first_half_end_date"].isoformat()
            # 过滤 None 值
            filtered = {k: v for k, v in values.items() if v is not None}
            result.append(filtered)
        return result

    def _apply(self):
        if self._loading:
            return
        # 先校验
        error = self._validate()
        if error:
            self._set_status(error, True)
            return

        manager = get_game_config()
        data = manager.get_raw()
        data["season_configs"] = self._configs_raw()
        try:
            manager.save(data)
        except Exception as e:
            logger.exception("赛季配置保存失败")
            self._set_status(f"保存失败：{e}", True)
            return
        now = datetime.now().strftime("%H:%M:%S")
        self._set_status(f"已保存并生效（{now}）", False)

    def _set_status(self, text: str, is_error: bool):
        """更新状态标签"""
        color = "#c62828" if is_error else "#2e7d32"
        self._status_label.setStyleSheet(f"color: {color};")
        self._status_label.setText(text)

    def refresh_level_combos(self):
        """刷新所有行的装备等级下拉列表（等级配置变更后调用）"""
        for row in range(self._table.rowCount()):
            combo = self._table.cellWidget(row, _EQUIP_LEVEL_COL)
            if isinstance(combo, LevelCombo):
                combo.refresh()
