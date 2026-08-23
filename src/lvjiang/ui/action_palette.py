"""指令面板 —— 快捷指令式的「选操作 → 填槽位 → 预览 → 插入」

数据来自 ``workflows.action_catalog``（纯数据），本模块只做 Qt 呈现：
搜索框 + 分类列表 + 按槽位类型生成的表单 + 实时预览 + 插入按钮。

槽位取值来源（由调用方注入 providers，便于测试时全 mock）：
- scene / region：场景注册表（区域 + 坐标点 + 面板合并成一个下拉，带类型标记）
- delay：命名延迟配置
- template：templates/ 目录
- coord / rect / color：画布最近一次取点 / 框选（「取画布」按钮）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..workflows import action_catalog as cat


@dataclass
class PaletteProviders:
    """面板需要的外部数据；每个都可缺省为空"""
    scenes: Callable[[], list[tuple[str, str]]] = lambda: []           # [(key, name)]
    regions: Callable[[str], list[tuple[str, str]]] = lambda _s: []    # [(key, "名 (类型)")]
    delays: Callable[[], list[tuple[str, str]]] = lambda: []           # [(key, label)]
    templates: Callable[[], list[str]] = lambda: []
    last_coord: Callable[[], str | None] = lambda: None                # "(x, y)"
    last_rect: Callable[[], str | None] = lambda: None                 # "(x, y, w, h)"
    last_color: Callable[[], str | None] = lambda: None                # '"#rrggbb"'


def default_providers(main_window, debug_panel=None) -> PaletteProviders:
    """从主窗口 / 调试面板组装真实数据源"""
    from ..core import scene_registry as sr
    from ..core.config.resolver import get_resolver
    from .script_workbench import snippet_color, snippet_point, snippet_rect

    def scenes():
        try:
            reg = sr.get_registry()
            return [(k, s.name or k) for k, s in reg.all_scenes().items()]
        except Exception:  # noqa: BLE001
            return []

    def regions(scene: str):
        out: list[tuple[str, str]] = []
        try:
            out += [(k, f"{n} ({tr('区域')})") for k, n in sr.get_scene_regions(scene)]
            out += [(k, f"{n} ({tr('点')})") for k, n in sr.get_scene_point_pairs(scene)]
            out += [(p.key, f"{p.name} ({tr('面板')})") for p in sr.get_panel_defs(scene)]
        except Exception:  # noqa: BLE001
            pass
        return out

    def delays():
        cfg = getattr(main_window, "_user_config", None)
        params = getattr(cfg, "delay_params", None) or {}
        return [(k, getattr(v, "label", "") or k) for k, v in params.items()]

    def templates():
        try:
            return sorted(n[:-4] for n in get_resolver().enumerate_entities("templates", "*.png"))
        except Exception:  # noqa: BLE001
            return []

    def last_coord():
        p = getattr(debug_panel, "last_pick", None)
        return snippet_point(p[0], p[1]) if p else None

    def last_rect():
        r = getattr(debug_panel, "last_rect", None)
        return snippet_rect(*r) if r else None

    def last_color():
        p = getattr(debug_panel, "last_pick", None)
        return snippet_color(p[2], p[3], p[4]) if p else None

    return PaletteProviders(scenes, regions, delays, templates, last_coord, last_rect, last_color)


class ActionPalette(QWidget):
    """选指令 → 填槽位 → 预览 → 插入"""

    insert_requested = pyqtSignal(str)   # 渲染好的 DSL 文本

    def __init__(self, providers: PaletteProviders | None = None, parent=None):
        super().__init__(parent)
        self._p = providers or PaletteProviders()
        self._action: cat.Action | None = None
        self._fields: dict[str, QWidget] = {}
        self._setup_ui()
        self._populate("")

    # ─── UI ────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜索指令：点击 / 等待 / 找图 / 颜色…"))
        self.search.textChanged.connect(self._populate)
        root.addWidget(self.search)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        root.addWidget(self.list, 2)
        self.lbl_doc = QLabel("")
        self.lbl_doc.setWordWrap(True)
        self.lbl_doc.setStyleSheet("color: palette(mid); padding: 2px;")
        root.addWidget(self.lbl_doc)
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        self.form.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.form_host)
        self.preview = QLabel("")
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.preview.setStyleSheet(
            "font-family: Menlo, Consolas, monospace; background: palette(alternate-base); "
            "padding: 6px; border: 1px solid palette(midlight);")
        self.preview.setWordWrap(True)
        root.addWidget(self.preview)
        row = QHBoxLayout()
        row.addStretch()
        self.btn_insert = QPushButton(tr("插入到脚本"))
        self.btn_insert.clicked.connect(self._on_insert)
        self.btn_insert.setEnabled(False)
        row.addWidget(self.btn_insert)
        root.addLayout(row)
        root.addStretch()

    def _populate(self, query: str):
        self.list.blockSignals(True)
        self.list.clear()
        current_cat = None
        for a in cat.search(query):
            if a.category != current_cat:
                current_cat = a.category
                header = QListWidgetItem(f"— {a.category} —")
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list.addItem(header)
            item = QListWidgetItem("  " + a.label)
            item.setData(Qt.ItemDataRole.UserRole, a.key)
            item.setToolTip(a.template)
            self.list.addItem(item)
        self.list.blockSignals(False)

    # ─── 选中 → 表单 ───────────────────────────────────

    def select_action(self, key: str):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == key:
                self.list.setCurrentRow(i)
                return
        # 搜索过滤掉了：直接建表单
        self._build_form(cat.get_action(key))

    def _on_select(self, item: QListWidgetItem | None, _prev=None):
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if key:
            self._build_form(cat.get_action(key))

    def _clear_form(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        self._fields = {}

    def _build_form(self, action: cat.Action):
        self._action = action
        self._clear_form()
        self.lbl_doc.setText(action.doc)
        for slot in action.slots:
            w = self._make_field(slot)
            self._fields[slot.key] = w
            label = slot.label + ("" if slot.optional else " *")
            if slot.help:
                w.setToolTip(slot.help)
            self.form.addRow(label, w)
        self._wire_scene_region()
        self._refresh_preview()

    def _make_field(self, slot: cat.Slot) -> QWidget:
        kind = slot.kind
        if kind == "scene":
            cb = QComboBox()
            cb.setEditable(True)
            for k, n in self._p.scenes():
                cb.addItem(f"{k}  {n}", k)
            cb.setCurrentIndex(-1)
            cb.setEditText(slot.default)
            cb.currentTextChanged.connect(self._refresh_preview)
            return cb
        if kind == "region":
            cb = QComboBox()
            cb.setEditable(True)
            cb.setCurrentIndex(-1)
            cb.setEditText(slot.default)
            cb.currentTextChanged.connect(self._refresh_preview)
            return cb
        if kind == "delay":
            cb = QComboBox()
            cb.setEditable(True)
            if slot.optional:
                cb.addItem("", "")
            for k, label in self._p.delays():
                cb.addItem(f"{k}  {label}", k)
            cb.setCurrentIndex(0 if slot.optional else -1)
            if not slot.optional:
                cb.setEditText(slot.default)
            cb.currentTextChanged.connect(self._refresh_preview)
            return cb
        if kind == "template":
            cb = QComboBox()
            cb.setEditable(True)
            for n in self._p.templates():
                cb.addItem(n, n)
            cb.setCurrentIndex(-1)
            cb.setEditText(slot.default)
            cb.currentTextChanged.connect(self._refresh_preview)
            return cb
        if kind == "choice":
            cb = QComboBox()
            for c in slot.choices:
                cb.addItem(c or tr("（无）"), c)
            idx = cb.findData(slot.default)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            cb.currentIndexChanged.connect(self._refresh_preview)
            return cb
        if kind in ("coord", "rect", "color"):
            host = QWidget()
            hl = QHBoxLayout(host)
            hl.setContentsMargins(0, 0, 0, 0)
            edit = QLineEdit(slot.default)
            edit.setPlaceholderText({"coord": "(x, y)", "rect": "(x, y, w, h)", "color": "#rrggbb"}[kind])
            edit.textChanged.connect(self._refresh_preview)
            btn = QPushButton(tr("取画布"))
            getter = {"coord": self._p.last_coord, "rect": self._p.last_rect, "color": self._p.last_color}[kind]

            def fill(_checked=False, _e=edit, _g=getter):
                v = _g()
                if v:
                    _e.setText(v)

            btn.clicked.connect(fill)
            hl.addWidget(edit, 1)
            hl.addWidget(btn)
            host.edit = edit  # type: ignore[attr-defined]
            # 画布已有值时直接预填，省一次点击
            v = getter()
            if v and not slot.default:
                edit.setText(v)
            return host
        edit = QLineEdit(slot.default)
        if kind == "var":
            edit.setPlaceholderText(tr("变量名（不用写 $）"))
        edit.textChanged.connect(self._refresh_preview)
        return edit

    def _wire_scene_region(self):
        scene_w = next((w for k, w in self._fields.items() if self._slot(k).kind == "scene"), None)
        region_ws = [w for k, w in self._fields.items() if self._slot(k).kind == "region"]
        if scene_w is None or not region_ws:
            return

        def refill(_text=None):
            scene = self._value_of("scene")
            for rw in region_ws:
                assert isinstance(rw, QComboBox)
                cur = rw.currentText()
                rw.blockSignals(True)
                rw.clear()
                for k, n in self._p.regions(scene):
                    rw.addItem(f"{k}  {n}", k)
                rw.setCurrentIndex(-1)
                rw.setEditText(cur)
                rw.blockSignals(False)

        assert isinstance(scene_w, QComboBox)
        scene_w.currentTextChanged.connect(refill)
        refill()

    def _slot(self, key: str) -> cat.Slot:
        assert self._action is not None
        return next(s for s in self._action.slots if s.key == key)

    # ─── 取值 / 预览 / 插入 ────────────────────────────

    def _value_of(self, key: str) -> str:
        w = self._fields.get(key)
        if w is None:
            return ""
        if isinstance(w, QComboBox):
            data = w.currentData()
            text = w.currentText()
            # 下拉项带 "key  名称" 展示文本：选中项用 data；手填用原文
            if data is not None and w.findText(text) >= 0:
                return str(data)
            return text.split("  ")[0].strip()
        if isinstance(w, QLineEdit):
            return w.text()
        edit = getattr(w, "edit", None)
        return edit.text() if edit is not None else ""

    def values(self) -> dict[str, str]:
        return {k: self._value_of(k) for k in self._fields}

    def set_value(self, key: str, value: str):
        w = self._fields[key]
        if isinstance(w, QComboBox):
            idx = w.findData(value)
            if idx >= 0:
                w.setCurrentIndex(idx)
            else:
                w.setEditText(value)
        elif isinstance(w, QLineEdit):
            w.setText(value)
        else:
            w.edit.setText(value)  # type: ignore[attr-defined]

    def rendered(self) -> str | None:
        if self._action is None:
            return None
        try:
            return cat.render(self._action, self.values())
        except cat.RenderError:
            return None

    def _refresh_preview(self, *_):
        if self._action is None:
            self.preview.setText("")
            self.btn_insert.setEnabled(False)
            return
        try:
            text = cat.render(self._action, self.values())
            self.preview.setText(text)
            self.preview.setStyleSheet(
                "font-family: Menlo, Consolas, monospace; background: palette(alternate-base); "
                "padding: 6px; border: 1px solid palette(midlight);")
            self.btn_insert.setEnabled(True)
        except cat.RenderError as e:
            self.preview.setText(str(e))
            self.preview.setStyleSheet(
                "font-family: Menlo, Consolas, monospace; background: palette(alternate-base); "
                "padding: 6px; border: 1px solid #c62828; color: #c62828;")
            self.btn_insert.setEnabled(False)

    def _on_insert(self):
        text = self.rendered()
        if text:
            self.insert_requested.emit(text)
