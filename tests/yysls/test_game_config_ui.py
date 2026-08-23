"""游戏配置对话框 UI 冒烟测试

对话框 3 Tab 冒烟 + 武器类型增删往返 + 流派配置行编辑往返
（在 tmp 目录的 game_config.yaml 副本上执行，不触碰真实配置）。
"""

import shutil
from pathlib import Path

import pytest
import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGroupBox, QLabel, QLineEdit, QMessageBox

from lvjiang.apps.yysls.config import EQUIP_PART_NAMES
from lvjiang.apps.yysls.ui.game_settings import (
    GameConfigDialog,
    affix_caps_panel,
    base_attr_panel,
    school_panel,
)
from lvjiang.apps.yysls.ui.game_settings.affix_caps_panel import AffixCapsPanel
from lvjiang.apps.yysls.ui.game_settings.base_attr_panel import BaseAttrPanel
from lvjiang.apps.yysls.ui.game_settings.school_panel import SchoolPanel

PROJECT_ROOT = Path(__file__).parents[2]
ATTRS_FILE = PROJECT_ROOT / "config" / "system" / "yysls" / "game_config.yaml"


@pytest.fixture
def tmp_attrs(tmp_path, monkeypatch):
    """真实 game_config.yaml 的 tmp 副本（resolver 根指向 tmp，开发模式直写 system）"""
    import lvjiang.core.config.resolver as cr
    dst = tmp_path / "system" / "yysls" / "game_config.yaml"
    dst.parent.mkdir(parents=True)
    shutil.copy(ATTRS_FILE, dst)
    monkeypatch.setattr(cr, "SYSTEM_CONFIG_DIR", tmp_path / "system")
    monkeypatch.setattr(cr, "LOCAL_CONFIG_DIR", tmp_path / "local")
    monkeypatch.setenv("LVJIANG_DEV_MODE", "1")
    return dst


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ─── 对话框冒烟 ────────────────────────────────────────────

class TestDialog:
    def test_dialog_tabs(self, qtbot, tmp_attrs):
        dialog = GameConfigDialog()
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "游戏配置"
        tabs = dialog._tab._tabs
        assert tabs.count() == 6
        assert tabs.tabText(0) == "词组配置"
        assert tabs.tabText(1) == "装备配置"
        assert tabs.tabText(2) == "流派配置"
        assert tabs.tabText(3) == "等级配置"
        assert tabs.tabText(4) == "赛季配置"
        assert tabs.tabText(5) == "字体设置"
        for nav in (
            dialog._tab._affix_panel._affix_list,
            dialog._tab._base_panel._part_list,
            dialog._tab._school_panel._school_list,
        ):
            assert nav.minimumWidth() >= 200
            assert nav.property("navigation") is True


# ─── 武器类型增删往返 ──────────────────────────────────────

class TestWeaponTypes:
    def test_weapon_frame_only_on_weapon_part(self, qtbot, tmp_attrs):
        panel = BaseAttrPanel()
        qtbot.addWidget(panel)
        # 默认选中首行 weapon
        assert not panel._weapon_frame.isHidden()
        assert panel._weapon_list.count() == 10
        # 切到环：武器类型区块隐藏
        panel._part_list.setCurrentRow(1)
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


# ─── 词条部位往返 ────────────────────────────────────

class TestAffixParts:
    def test_default_all_and_format(self, qtbot, tmp_attrs):
        panel = AffixCapsPanel()
        qtbot.addWidget(panel)
        # 未配置时默认全部位，展示「全部」
        assert panel._get_affix_parts("测试词条") == list(EQUIP_PART_NAMES)
        assert panel._format_parts(list(EQUIP_PART_NAMES)) == "全部"
        assert panel._format_parts(["环", "佩"]) == "环/佩"

    def test_parts_save_roundtrip(self, qtbot, tmp_attrs):
        panel = AffixCapsPanel()
        qtbot.addWidget(panel)
        # 隔离掉真实配置中已有的 affix_parts，验证单键往返
        panel._data.pop("affix_parts", None)
        panel._data.setdefault("affix_parts", {})["测试词条"] = ["环", "佩"]
        panel._save_data()
        assert _load_yaml(tmp_attrs)["affix_parts"]["测试词条"] == ["环", "佩"]
        assert panel._get_affix_parts("测试词条") == ["环", "佩"]
        # 移除配置：affix_parts 清空后顶层键一并移除，不落盘
        panel._drop_affix_parts("测试词条")
        panel._save_data()
        assert "affix_parts" not in _load_yaml(tmp_attrs)
        assert panel._get_affix_parts("测试词条") == list(EQUIP_PART_NAMES)

    def test_rename_alias_migrates_parts(self, qtbot, tmp_attrs, monkeypatch):
        panel = AffixCapsPanel()
        qtbot.addWidget(panel)
        # 选中含「会心率」的普通词组并配置部位
        rows = [panel._affix_list.item(i).text()
                for i in range(panel._affix_list.count())]
        panel._affix_list.setCurrentRow(rows.index("会心率"))
        panel._data.setdefault("affix_parts", {})["会心率"] = ["武器"]
        monkeypatch.setattr(
            affix_caps_panel.QInputDialog, "getText",
            staticmethod(lambda *a, **k: ("会心率X", True)),
        )
        panel._rename_alias("会心率")
        parts = _load_yaml(tmp_attrs)["affix_parts"]
        assert parts.get("会心率X") == ["武器"] and "会心率" not in parts

    def test_external_aliases_save_and_deduplicate(
        self, qtbot, tmp_attrs, monkeypatch,
    ):
        panel = AffixCapsPanel()
        qtbot.addWidget(panel)
        button = affix_caps_panel.QPushButton(panel)
        monkeypatch.setattr(
            affix_caps_panel.QInputDialog, "getMultiLineText",
            staticmethod(lambda *a, **k: ("剑增\n剑增\n主剑增", True)),
        )
        panel._edit_external_aliases("剑武学增伤", button)
        aliases = _load_yaml(tmp_attrs)["affix_aliases"]["剑武学增伤"]
        assert aliases == ["剑增", "主剑增"]

    def test_rename_alias_migrates_external_aliases(
        self, qtbot, tmp_attrs, monkeypatch,
    ):
        panel = AffixCapsPanel()
        qtbot.addWidget(panel)
        rows = [panel._affix_list.item(i).text()
                for i in range(panel._affix_list.count())]
        panel._affix_list.setCurrentRow(rows.index("会心率"))
        panel._data.setdefault("affix_aliases", {})["会心率"] = ["暴击"]
        monkeypatch.setattr(
            affix_caps_panel.QInputDialog, "getText",
            staticmethod(lambda *a, **k: ("会心率X", True)),
        )
        panel._rename_alias("会心率")
        mapping = _load_yaml(tmp_attrs)["affix_aliases"]
        assert mapping["会心率X"] == ["暴击"]
        assert "会心率" not in mapping


# ─── 流派配置行编辑往返 ────────────────────────────────────

class TestSchoolPanel:
    def test_list_prefilled(self, qtbot, tmp_attrs):
        panel = SchoolPanel()
        qtbot.addWidget(panel)
        assert panel._school_list.count() == 11
        names = [panel._school_list.item(i).text() for i in range(11)]
        assert "鸣金·虹" in names and "破竹·鸢" in names

    def test_form_follows_selection(self, qtbot, tmp_attrs):
        panel = SchoolPanel()
        qtbot.addWidget(panel)
        names = [panel._school_list.item(i).text() for i in range(11)]
        panel._school_list.setCurrentRow(names.index("裂石·钧"))
        assert panel._combo_attr.currentText() == "裂石"
        assert panel._combo_main_weapon.currentText() == "横刀"
        assert panel._edit_main_martial.text() == "斩雪刀法"
        assert panel._combo_sub_weapon.currentText() == "陌刀"
        assert panel._edit_sub_martial.text() == "十方破阵"
        assert panel._scheme_list.count() == 1
        assert panel._scheme_list.item(0).text() == "基础方案"
        assert panel._scheme_list.currentRow() == 0
        assert "方案：基础方案" in panel._value_source_label.text()
        assert panel._scheme_group.height() == panel._base_attrs_group.height()
        cards = {
            card.title(): card for card
            in panel._ps_scroll_widget.findChildren(QGroupBox)
        }
        assert cards["攻击属性"].layout().rowCount() == 6
        assert cards["判定属性"].layout().rowCount() == 3
        assert cards["增益效果"].layout().rowCount() == 6
        assert cards["伤害加成"].layout().rowCount() == 3
        labels = {
            label.text() for label
            in panel._ps_scroll_widget.findChildren(QLabel)
        }
        assert "陌刀武学增伤" in labels
        assert "十方破阵蓄力技增伤" in labels
        assert "陌刀增" not in labels
        assert "蓄力技定音" not in labels

    def test_switching_scheme_removes_old_value_panel_immediately(
        self, qtbot, tmp_attrs,
    ):
        panel = SchoolPanel()
        qtbot.addWidget(panel)
        assert panel._scheme_list.count() >= 1
        panel._on_scheme_selected(0)
        old_panel = panel._ps_scroll_layout.itemAt(0).widget()
        panel._on_scheme_selected(0)
        assert old_panel.parent() is None
        assert not old_panel.isVisible()
        # 当前面板 + DPS 校正区 + 末尾 stretch，不允许旧卡片残留。
        assert panel._ps_scroll_layout.count() == 3
        assert panel._scheme_baseline_edit is not None
        assert panel._scheme_baseline_edit.objectName() == (
            "graduationBaselineDpsEdit"
        )

    def test_external_navigation_selects_school_and_base_attr(
        self, qtbot, tmp_attrs, monkeypatch,
    ):
        import lvjiang.apps.yysls.config as game_config

        monkeypatch.setattr(
            game_config,
            "get_play_styles",
            lambda school: {"测试基础属性": {"min_outer": 1234}},
        )
        panel = SchoolPanel()
        qtbot.addWidget(panel)
        panel.select_school_base_attr("鸣金·虹", "测试基础属性")
        assert panel._current_school() == "鸣金·虹"
        assert panel._ps_list.currentItem().text() == "测试基础属性"
        assert panel._scheme_list.currentRow() == -1
        assert panel._ps_current_name == "测试基础属性"

        values = panel._ps_scroll_widget.findChildren(QLineEdit)
        assert values
        assert {edit.height() for edit in values} == {24}
        gain_card = next(
            card for card in panel._ps_scroll_widget.findChildren(QGroupBox)
            if card.title() == "增益效果"
        )
        assert gain_card.layout().rowCount() == 6
        empty_cells = [
            gain_card.layout().itemAtPosition(row, column).widget()
            for row in (1, 5) for column in (0, 1)
        ]
        assert all(cell is not None and cell.height() == 24 for cell in empty_cells)

    def test_field_edit_saves(self, qtbot, tmp_attrs):
        panel = SchoolPanel()
        qtbot.addWidget(panel)
        names = [panel._school_list.item(i).text() for i in range(11)]
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
        assert panel._school_list.count() == 12
        # 新增流派自动选中，表单置空
        assert panel._current_school() == "测试流派"
        assert panel._combo_attr.currentText() == ""

        # 配置属性 → 即时写盘
        panel._combo_attr.setCurrentText("鸣金")
        assert _load_yaml(tmp_attrs)["schools"]["测试流派"] == {"attr": "鸣金"}

        # 重命名（编辑列表项触发 itemChanged）
        row = [panel._school_list.item(i).text() for i in range(12)].index("测试流派")
        panel._school_list.item(row).setText("测试流派2")
        schools = _load_yaml(tmp_attrs)["schools"]
        assert "测试流派2" in schools and "测试流派" not in schools

        # 删除流派
        monkeypatch.setattr(
            school_panel.QMessageBox, "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
        )
        row = [panel._school_list.item(i).text() for i in range(12)].index("测试流派2")
        panel._school_list.setCurrentRow(row)
        panel._on_del_school()
        assert "测试流派2" not in _load_yaml(tmp_attrs)["schools"]
        assert panel._school_list.count() == 11
