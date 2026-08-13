"""流派配置面板

左侧为流派列表（对应游戏十大流派，可增删、直接编辑重命名），
右侧为选中流派的配置表单，分三行：
- 第一行：属性（下拉 鸣金 / 裂石 / 破竹 / 牵丝）；
- 第二行：主武器（下拉）+ 主武学（文本框），两列等宽；
- 第三行：副武器（下拉）+ 副武学（文本框），两列等宽。
武学增效已移至装备配置的武器类型中，每个武器绑定一种武学增效。
武器候选来自 weapon_types 注册表。
数据存于 attributes.yaml 顶层 schools：
    流派名 → {attr: 属性, main: {weapon, martial_art}, sub: {weapon, martial_art}}
修改即时写盘，并刷新 GameConfigManager 单例。
"""

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

# 配置文件（聚合键值，经 resolver 读合并视图、按模式写回）
_ATTRS_REL = "yysls/game_config.yaml"

# 流派属性候选
_SCHOOL_ATTRS = ["鸣金", "裂石", "破竹", "牵丝"]


class SchoolPanel(QWidget):
    """流派配置面板（左：流派列表；右：配置表单）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict = {}  # 完整配置数据
        self._names: list[str] = []  # 列表行 → 流派名（重命名时对照旧名）
        self._loading = False  # 防止刷新控件时触发保存
        self._init_ui()
        self._load_data()

    def _init_ui(self):
        layout = QHBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # ── 左侧：流派列表 ──
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("流派类型"))

        self._school_list = QListWidget()
        self._school_list.currentRowChanged.connect(self._on_school_changed)
        self._school_list.itemChanged.connect(self._on_item_renamed)
        left_layout.addWidget(self._school_list)

        btn_layout = QHBoxLayout()
        self._btn_add = QPushButton("添加")
        self._btn_add.clicked.connect(self._on_add_school)
        btn_layout.addWidget(self._btn_add)
        self._btn_del = QPushButton("删除")
        self._btn_del.clicked.connect(self._on_del_school)
        btn_layout.addWidget(self._btn_del)
        left_layout.addLayout(btn_layout)

        splitter.addWidget(left_widget)

        # ── 右侧：流派配置表单 ──
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        hint = QLabel(
            "武器候选来自装备配置的武器类型。武学增效已移至装备配置的武器类型中。"
        )
        hint.setStyleSheet("color: #888;")
        right_layout.addWidget(hint)

        # 第一行：属性（标签后留两字宽，使下拉框与下方主/副武器列对齐）
        row_attr = QHBoxLayout()
        row_attr.setSpacing(28)  # 属性(2字) + 2字间距 = 主武器(3字) + 1字间距
        row_attr.addWidget(QLabel("属性"))
        self._combo_attr = QComboBox()
        self._combo_attr.setFixedWidth(100)
        row_attr.addWidget(self._combo_attr)
        row_attr.addStretch()
        right_layout.addLayout(row_attr)

        # 第二/三行：两列等宽网格（主武器+主武学 / 副武器+副武学）
        # 每列内部：标签后紧接控件（约一个汉字间距），控件定宽，不贴右缘
        self._combo_main_weapon = QComboBox()
        self._combo_main_weapon.setFixedWidth(100)
        self._edit_main_martial = QLineEdit()
        self._edit_main_martial.setPlaceholderText("武学名称")
        self._edit_main_martial.setMaxLength(6)
        self._edit_main_martial.setFixedWidth(100)
        self._combo_sub_weapon = QComboBox()
        self._combo_sub_weapon.setFixedWidth(100)
        self._edit_sub_martial = QLineEdit()
        self._edit_sub_martial.setPlaceholderText("武学名称")
        self._edit_sub_martial.setMaxLength(6)
        self._edit_sub_martial.setFixedWidth(100)

        def _pair(label: str, widget) -> QHBoxLayout:
            lay = QHBoxLayout()
            lay.setSpacing(14)  # 约一个汉字宽度
            lay.addWidget(QLabel(label))
            lay.addWidget(widget)
            lay.addStretch()
            return lay

        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.addLayout(_pair("主武器", self._combo_main_weapon), 0, 0)
        grid.addLayout(_pair("主武学", self._edit_main_martial), 0, 1)
        grid.addLayout(_pair("副武器", self._combo_sub_weapon), 1, 0)
        grid.addLayout(_pair("副武学", self._edit_sub_martial), 1, 1)
        right_layout.addLayout(grid)

        right_layout.addStretch()

        for combo in self._combos():
            combo.currentTextChanged.connect(self._on_field_changed)
        for edit in self._edits():
            edit.textChanged.connect(self._on_field_changed)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

    def _combos(self) -> list[QComboBox]:
        return [
            self._combo_attr,
            self._combo_main_weapon,
            self._combo_sub_weapon,
        ]

    def _edits(self) -> list[QLineEdit]:
        return [self._edit_main_martial, self._edit_sub_martial]

    def showEvent(self, event):
        """每次显示时重新加载（武器类型可能已在其他面板变更）"""
        super().showEvent(event)
        self._load_data()

    # ── 数据加载 / 候选 ──────────────────────────────────────

    def _load_data(self):
        """从 YAML 加载数据并刷新列表与表单"""
        from lvjiang.core.config.resolver import get_resolver
        try:
            self._data = get_resolver().load_merged(_ATTRS_REL)
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            self._data = {}
        self._refresh_list()

    def _schools(self) -> dict[str, dict]:
        return self._data.get("schools") or {}

    def _weapon_candidates(self) -> list[str]:
        """武器候选：支持新格式（dict 列表）和旧格式（字符串列表）"""
        raw = self._data.get("weapon_types") or []
        return [
            str(t["name"]) if isinstance(t, dict) else str(t)
            for t in raw
        ]

    # ── 左侧列表 ──────────────────────────────────────────────

    def _refresh_list(self, select: str | None = None):
        """重建流派列表；select 指定选中项（默认保持当前选中）"""
        if select is None:
            current = self._school_list.currentItem()
            select = current.text() if current else None
        self._loading = True
        self._names = list(self._schools().keys())
        self._school_list.clear()
        for name in self._names:
            self._school_list.addItem(name)
            item = self._school_list.item(self._school_list.count() - 1)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self._loading = False
        row = self._names.index(select) if select in self._names else 0
        if self._names:
            self._school_list.setCurrentRow(row)
            self._on_school_changed(row)
        else:
            self._on_school_changed(-1)

    def _current_school(self) -> str | None:
        row = self._school_list.currentRow()
        return self._names[row] if 0 <= row < len(self._names) else None

    def _on_school_changed(self, row: int):
        """切换流派 → 刷新右侧表单"""
        name = self._names[row] if 0 <= row < len(self._names) else None
        cfg = (self._schools().get(name) or {}) if name else {}
        main = cfg.get("main") or {}
        sub = cfg.get("sub") or {}

        prev_loading = self._loading  # 可能由 _refresh_list 嵌套触发，保持外层标志
        self._loading = True
        weapons = self._weapon_candidates()
        self._fill_combo(self._combo_attr, _SCHOOL_ATTRS, cfg.get("attr"))
        self._fill_combo(self._combo_main_weapon, weapons, main.get("weapon"))
        self._edit_main_martial.setText(main.get("martial_art", "") or "")
        self._fill_combo(self._combo_sub_weapon, weapons, sub.get("weapon"))
        self._edit_sub_martial.setText(sub.get("martial_art", "") or "")
        enabled = name is not None
        for combo in self._combos():
            combo.setEnabled(enabled)
        for edit in self._edits():
            edit.setEnabled(enabled)
        self._loading = prev_loading

    @staticmethod
    def _fill_combo(combo: QComboBox, candidates: list[str], value: str | None):
        """重建候选并选中当前值；未配置时留空，失效值也保留展示便于改正"""
        combo.clear()
        combo.addItem("")  # 未配置占位
        combo.addItems(candidates)
        value = value or ""
        if value and value not in candidates:
            combo.addItem(value)
        combo.setCurrentText(value)

    def _on_item_renamed(self, item):
        """列表项编辑 → 流派重命名（保持原顺序）"""
        if self._loading:
            return
        row = self._school_list.row(item)
        old_name = self._names[row] if 0 <= row < len(self._names) else None
        if old_name is None:
            return
        new_name = item.text().strip()
        if new_name == old_name:
            return
        if not new_name or new_name in self._schools():
            QMessageBox.warning(self, "无法重命名", "流派名不能为空或与已有流派重名。")
            self._refresh_list(select=old_name)
            return
        schools = {
            (new_name if name == old_name else name): cfg
            for name, cfg in self._schools().items()
        }
        self._data["schools"] = schools
        self._save_data()
        self._refresh_list(select=new_name)

    def _on_add_school(self):
        name, ok = QInputDialog.getText(self, "添加流派", "流派名称：")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if name in self._schools():
            QMessageBox.warning(self, "无法添加", f"流派「{name}」已存在。")
            return
        self._data.setdefault("schools", {})[name] = {}
        self._save_data()
        self._refresh_list(select=name)

    def _on_del_school(self):
        name = self._current_school()
        if name is None:
            return
        ret = QMessageBox.question(self, "确认删除", f"确定删除流派「{name}」？")
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._data.get("schools", {}).pop(name, None)
        self._save_data()
        self._refresh_list()

    # ── 右侧表单保存 ──────────────────────────────────────────

    def _on_field_changed(self, _text: str):
        """任一控件变化 → 回写当前流派配置（空值省略对应键）"""
        if self._loading:
            return
        name = self._current_school()
        if name is None:
            return
        cfg: dict = {}
        attr = self._combo_attr.currentText()
        if attr:
            cfg["attr"] = attr
        for key, combo_w, edit_m in (
            ("main", self._combo_main_weapon, self._edit_main_martial),
            ("sub", self._combo_sub_weapon, self._edit_sub_martial),
        ):
            group = {}
            if combo_w.currentText():
                group["weapon"] = combo_w.currentText()
            martial = edit_m.text().strip()
            if martial:
                group["martial_art"] = martial
            if group:
                cfg[key] = group
        self._data.setdefault("schools", {})[name] = cfg
        self._save_data()

    # ── 保存 ──────────────────────────────────────────────────

    def _save_data(self):
        """保存数据到 YAML 并刷新 GameConfigManager 单例"""
        from lvjiang.core.config.resolver import get_resolver
        try:
            get_resolver().save_merged(_ATTRS_REL, self._data)
            logger.debug(f"配置已保存: {_ATTRS_REL}")
            from lvjiang.apps.yysls.config import get_game_config
            get_game_config()._load()
        except Exception as e:
            logger.error(f"保存失败: {e}")
