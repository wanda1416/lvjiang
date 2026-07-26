"""游戏配置对话框 UI 冒烟测试

对话框 3 Tab 冒烟 + 武器类型增删往返 + 流派配置行编辑往返
（在 tmp 目录的 attributes.yaml 副本上执行，不触碰真实配置）。
"""

import shutil
from pathlib import Path

import pytest
import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from src.apps.yysls.ui.attr_manager import AttrManagerDialog
from src.apps.yysls.ui.attr_manager import affix_caps_panel, base_attr_panel, school_panel
from src.apps.yysls.ui.attr_manager.base_attr_panel import BaseAttrPanel
from src.apps.yysls.ui.attr_manager.school_panel import SchoolPanel

PROJECT_ROOT = Path(__file__).parents[2]
ATTRS_FILE = PROJECT_ROOT / "config" / "system" / "yysls" / "attributes.yaml"


@pytest.fixture
def tmp_attrs(tmp_path, monkeypatch):
    """真实 attributes.yaml 的 tmp 副本（三个面板统一改写路径）"""
    dst = tmp_path / "attributes.yaml"
    shutil.copy(ATTRS_FILE, dst)
    monkeypatch.setattr(base_attr_panel, "_ATTRS_PATH", dst)
    monkeypatch.setattr(affix_caps_panel, "_ATTRS_PATH", dst)
    monkeypatch.setattr(school_panel, "_ATTRS_PATH", dst)
    return dst


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ─── 对话框冒烟 ────────────────────────────────────────────

class TestDialog:
    def test_dialog_tabs(self, qtbot, tmp_attrs):
        dialog = AttrManagerDialog()
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "游戏配置"
        tabs = dialog._tab._tabs
        assert tabs.count() == 3
        assert tabs.tabText(0) == "词组配置"
        assert tabs.tabText(1) == "装备配置"
        assert tabs.tabText(2) == "流派配置"


# ─── 武器类型增删往返 ──────────────────────────────────────

class TestWeaponTypes:
    def test_weapon_frame_only_on_main_weapon(self, qtbot, tmp_attrs):
        panel = BaseAttrPanel()
        qtbot.addWidget(panel)
        # 默认选中首行 main_weapon
        assert not panel._weapon_frame.isHidden()
        assert panel._weapon_list.count() == 10
        # 切到环：武器类型区块隐藏
        panel._part_list.setCurrentRow(2)
        assert panel._weapon_frame.isHidden()

    def test_add_and_delete_roundtrip(self, qtbot, tmp_attrs, monkeypatch):
        panel = BaseAttrPanel()
        qtbot.addWidget(panel)

        # 添加新武器「双剑」
        monkeypatch.setattr(
            base_attr_panel.QInputDialog, "getText",
            staticmethod(lambda *a, **k: ("双剑", True)),
        )
        panel._on_add_weapon()
        weapon_names = [t["name"] for t in _load_yaml(tmp_attrs)["weapon_types"]]
        assert "双剑" in weapon_names
        assert panel._weapon_list.count() == 11

        # 删除「双剑」（确认对话框选是）
        monkeypatch.setattr(
            base_attr_panel.QMessageBox, "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
        )
        items = panel._weapon_list.findItems("双剑", Qt.MatchFlag.MatchStartsWith)
        panel._weapon_list.setCurrentItem(items[0])
        panel._on_del_weapon()
        weapon_names = [t["name"] for t in _load_yaml(tmp_attrs)["weapon_types"]]
        assert "双剑" not in weapon_names
        assert panel._weapon_list.count() == 10

    def test_delete_refused_when_referenced(self, qtbot, tmp_attrs, monkeypatch):
        panel = BaseAttrPanel()
        qtbot.addWidget(panel)
        warnings: list[str] = []
        monkeypatch.setattr(
            base_attr_panel.QMessageBox, "warning",
            staticmethod(lambda *a, **k: warnings.append(a[2] if len(a) > 2 else "")),
        )
        # 「剑」被流派「鸣金·虹」等引用，删除须被拒绝
        items = panel._weapon_list.findItems("剑", Qt.MatchFlag.MatchStartsWith)
        panel._weapon_list.setCurrentItem(items[0])
        panel._on_del_weapon()
        assert warnings and "鸣金·虹" in warnings[0]
        weapon_names = [t["name"] for t in _load_yaml(tmp_attrs)["weapon_types"]]
        assert "剑" in weapon_names


# ─── 流派配置行编辑往返 ────────────────────────────────────

class TestSchoolPanel:
    def test_list_prefilled(self, qtbot, tmp_attrs):
        panel = SchoolPanel()
        qtbot.addWidget(panel)
        assert panel._school_list.count() == 10
        names = [panel._school_list.item(i).text() for i in range(10)]
        assert "鸣金·虹" in names and "破竹·鸢" in names

    def test_form_follows_selection(self, qtbot, tmp_attrs):
        panel = SchoolPanel()
        qtbot.addWidget(panel)
        names = [panel._school_list.item(i).text() for i in range(10)]
        panel._school_list.setCurrentRow(names.index("裂石·钧"))
        assert panel._combo_attr.currentText() == "裂石"
        assert panel._combo_main_weapon.currentText() == "横刀"
        assert panel._edit_main_martial.text() == "斩雪刀法"
        assert panel._combo_sub_weapon.currentText() == "陌刀"
        assert panel._edit_sub_martial.text() == "十方破阵"

    def test_field_edit_saves(self, qtbot, tmp_attrs):
        panel = SchoolPanel()
        qtbot.addWidget(panel)
        names = [panel._school_list.item(i).text() for i in range(10)]
        panel._school_list.setCurrentRow(names.index("鸣金·虹"))
        panel._combo_sub_weapon.setCurrentText("伞")
        cfg = _load_yaml(tmp_attrs)["schools"]["鸣金·虹"]
        assert cfg["sub"]["weapon"] == "伞"
        # 属性与主武器组不受影响（affix 已移至装备配置）
        assert cfg["attr"] == "鸣金"
        assert cfg["main"] == {"weapon": "剑", "martial_art": "无名剑法"}

    def test_add_rename_delete_roundtrip(self, qtbot, tmp_attrs, monkeypatch):
        panel = SchoolPanel()
        qtbot.addWidget(panel)

        # 添加流派
        monkeypatch.setattr(
            school_panel.QInputDialog, "getText",
            staticmethod(lambda *a, **k: ("测试流派", True)),
        )
        panel._on_add_school()
        assert "测试流派" in _load_yaml(tmp_attrs)["schools"]
        assert panel._school_list.count() == 11
        # 新增流派自动选中，表单置空
        assert panel._current_school() == "测试流派"
        assert panel._combo_attr.currentText() == ""

        # 配置属性 → 即时写盘
        panel._combo_attr.setCurrentText("鸣金")
        assert _load_yaml(tmp_attrs)["schools"]["测试流派"] == {"attr": "鸣金"}

        # 重命名（编辑列表项触发 itemChanged）
        row = [panel._school_list.item(i).text() for i in range(11)].index("测试流派")
        panel._school_list.item(row).setText("测试流派2")
        schools = _load_yaml(tmp_attrs)["schools"]
        assert "测试流派2" in schools and "测试流派" not in schools

        # 删除流派
        monkeypatch.setattr(
            school_panel.QMessageBox, "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
        )
        row = [panel._school_list.item(i).text() for i in range(11)].index("测试流派2")
        panel._school_list.setCurrentRow(row)
        panel._on_del_school()
        assert "测试流派2" not in _load_yaml(tmp_attrs)["schools"]
        assert panel._school_list.count() == 10
