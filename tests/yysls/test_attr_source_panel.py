"""属性来源面板：填写、分组与进度。

补数据是这块的瓶颈——心法 37 门 × 6 重 222 行。面板的价值在于把常见
情形压到两次点击，所以这里验的是「选完取值方式就已经落盘」，而不是
控件长什么样。
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QComboBox

from lvjiang.apps.yysls.core.attr_model import (
    AttrModelManager,
    get_attr_model_manager,
)
from lvjiang.apps.yysls.ui.game_settings.attr_source_panel import (
    _COLUMNS,
    MODE_FULL_AFFIX,
    MODE_NO_EFFECT,
    AttrSourcePanel,
)

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture
def panel(tmp_path, monkeypatch):
    """把来源目录指到 tmp_path，避免测试写坏仓库里的配置。"""
    (tmp_path / "inner_way.yaml").write_text(
        "kind: inner_way\n"
        "entries:\n"
        "  易水歌·一重:\n    modeled: false\n"
        "  易水歌·二重:\n    modeled: false\n"
        "  泣血婆娑·一重:\n    modeled: false\n",
        encoding="utf-8",
    )
    manager = AttrModelManager(tmp_path)
    import lvjiang.apps.yysls.ui.game_settings.attr_source_panel as module

    monkeypatch.setattr(module, "get_attr_model_manager", lambda: manager)
    monkeypatch.setattr(module, "invalidate_attr_model_cache", lambda: None)
    widget = AttrSourcePanel()
    return widget, manager


def test_entries_are_grouped_by_the_name_before_the_separator(panel) -> None:
    """一屏只显示一门心法的几重，否则 222 行一次铺开没法填。"""
    widget, _ = panel

    groups = [widget._list.item(i).text() for i in range(widget._list.count())]

    assert groups == ["易水歌", "泣血婆娑"]


def test_choosing_full_affix_persists_immediately(panel) -> None:
    """选完取值方式就落盘，不需要再点保存。"""
    widget, manager = panel
    widget._list.setCurrentRow(0)

    mode = widget._table.cellWidget(0, 1)
    assert isinstance(mode, QComboBox)
    mode.setCurrentIndex(_COLUMNS.index(MODE_FULL_AFFIX))

    saved = manager.raw_entry("易水歌·一重")
    assert "full_affix" in saved
    assert manager.progress("inner_way") == (1, 3)


def test_marking_no_effect_advances_progress(panel) -> None:
    """心法六重里大量是触发类效果；确认无贡献要能推进进度。"""
    widget, manager = panel
    widget._list.setCurrentRow(0)

    mode = widget._table.cellWidget(1, 1)
    mode.setCurrentIndex(_COLUMNS.index(MODE_NO_EFFECT))

    assert manager.raw_entry("易水歌·二重").get("no_effect") is True
    assert manager.progress("inner_way") == (1, 3)


def test_search_narrows_the_group_list(panel) -> None:
    widget, _ = panel

    widget._search.setText("泣血")

    groups = [widget._list.item(i).text() for i in range(widget._list.count())]
    assert groups == ["泣血婆娑"]


def test_progress_label_reports_both_kind_and_total(panel) -> None:
    widget, _ = panel

    assert "0/3" in widget._progress.text()


def test_creating_and_deleting_a_group(panel) -> None:
    widget, manager = panel

    manager.create_entry("inner_way", "长生无相·一重")
    widget._refresh_groups()
    groups = [widget._list.item(i).text() for i in range(widget._list.count())]
    assert "长生无相" in groups

    manager.delete_entry("长生无相·一重")
    widget._refresh_groups()
    groups = [widget._list.item(i).text() for i in range(widget._list.count())]
    assert "长生无相" not in groups


def test_the_real_manager_is_untouched_by_the_panel_fixture() -> None:
    """夹具替换的是模块级取用点；真单例不该被测试改到。"""
    assert get_attr_model_manager().errors() == {}


# ── 推导对话框 ────────────────────────────────────────────

@pytest.fixture
def derive(tmp_path, monkeypatch, session_dir):
    """来源目录与基础属性存储都指到临时目录。"""
    (tmp_path / "inner_way.yaml").write_text(
        "kind: inner_way\n"
        "entries:\n"
        "  易水歌·二重:\n    full_affix: 外功攻击\n"
        "  待填·一重:\n    modeled: false\n",
        encoding="utf-8",
    )
    manager = AttrModelManager(tmp_path)
    import lvjiang.apps.yysls.ui.game_settings.attr_derive_dialog as module

    monkeypatch.setattr(module, "get_attr_model_manager", lambda: manager)
    from lvjiang.apps.yysls.ui.game_settings.attr_derive_dialog import (
        AttrDeriveDialog,
    )
    return AttrDeriveDialog(), manager


@pytest.fixture
def session_dir(tmp_path, monkeypatch):
    import lvjiang.constants as constants
    import lvjiang.core.config.session as session_mod
    from lvjiang.apps.yysls.config import session_node

    root = tmp_path / "session"
    root.mkdir()
    monkeypatch.setattr(constants, "SESSION_CONFIG_DIR", root)
    monkeypatch.setattr(constants, "SESSION_PATH", root / "session.json")
    monkeypatch.setattr(session_mod, "_store", None)
    monkeypatch.setattr(session_node, "LEGACY_PATH", root / "yysls.json")
    return root


def test_only_filled_sources_are_offered(derive) -> None:
    """未填的条目贡献 0，列出来只会让人以为漏勾了。"""
    dialog, _ = derive

    labels = [
        dialog._list_sources.item(i).text()
        for i in range(dialog._list_sources.count())
    ]
    assert any("易水歌·二重" in text for text in labels)
    assert not any("待填·一重" in text for text in labels)


def test_derived_value_comes_from_the_affix_caps(derive) -> None:
    """一整条外功攻击按 1:2 拆开，两者之和回到当前等级的词条满值。"""
    dialog, _ = derive
    dialog._combo_level.setCurrentIndex(0)

    rows = {
        dialog._table.item(r, 0).text(): dialog._table.item(r, 1).text()
        for r in range(dialog._table.rowCount())
    }

    assert "最小外功攻击" in rows and "最大外功攻击" in rows


def test_saving_writes_a_base_attribute_set(derive) -> None:
    """推导结果存进现有的基础属性存储，毕业率链路照旧读它。"""
    from lvjiang.apps.yysls.config import get_play_styles

    dialog, _ = derive
    school = dialog._combo_school.currentText()
    dialog._combo_level.setCurrentIndex(0)

    result = dialog._manager().resolve(
        level=dialog._combo_level.get_level(),
        school_attr=dialog._school_attr(),
        selected=dialog._selected_ids(),
    )
    from lvjiang.apps.yysls.config import save_play_style
    save_play_style(school, "推导结果", result.combat_attrs.to_dict())

    stored = get_play_styles(school)["推导结果"]
    assert stored["min_outer"] > 0


# ── 修复回归 ──────────────────────────────────────────────

def test_switching_to_custom_value_stays_in_custom_value(panel) -> None:
    """切到「自定义数值」要给出可编辑的默认值。

    此前这里回落到 modeled: false，界面立刻弹回「未填」，这个模式
    根本进不去——而它正是填数据最主要的路径。
    """
    from lvjiang.apps.yysls.ui.game_settings.attr_source_panel import MODE_VALUE

    widget, manager = panel
    widget._list.setCurrentRow(0)

    widget._table.cellWidget(0, 1).setCurrentIndex(_COLUMNS.index(MODE_VALUE))

    assert "stats" in manager.raw_entry("易水歌·一重")
    assert widget._table.cellWidget(0, 1).currentText() == MODE_VALUE


def test_percent_fields_are_entered_as_percentages_and_stored_as_decimals(
    panel,
) -> None:
    """内部按小数存（0.046 = 4.6%）。界面直接存输入值的话，
    用户照着游戏面板填 4.6 会被当成 460% 用。"""
    from PyQt6.QtWidgets import QDoubleSpinBox
    from lvjiang.apps.yysls.ui.game_settings.attr_source_panel import MODE_VALUE

    widget, manager = panel
    widget._list.setCurrentRow(0)
    widget._table.cellWidget(0, 1).setCurrentIndex(_COLUMNS.index(MODE_VALUE))

    field = widget._table.cellWidget(0, 2).findChild(QComboBox)
    field.setCurrentIndex(field.findData("crit_rate"))
    spin = widget._table.cellWidget(0, 2).findChild(QDoubleSpinBox)
    assert spin.suffix().strip() == "%"
    spin.setValue(4.6)

    assert manager.raw_entry("易水歌·一重")["stats"]["crit_rate"] == pytest.approx(0.046)
    # 回读要还原成 4.6，不能显示成 0.046
    assert widget._table.cellWidget(0, 2).findChild(
        QDoubleSpinBox).value() == pytest.approx(4.6)


def test_non_percent_fields_are_stored_verbatim(panel) -> None:
    from PyQt6.QtWidgets import QDoubleSpinBox
    from lvjiang.apps.yysls.ui.game_settings.attr_source_panel import MODE_VALUE

    widget, manager = panel
    widget._list.setCurrentRow(0)
    widget._table.cellWidget(0, 1).setCurrentIndex(_COLUMNS.index(MODE_VALUE))

    field = widget._table.cellWidget(0, 2).findChild(QComboBox)
    field.setCurrentIndex(field.findData("min_outer"))
    widget._table.cellWidget(0, 2).findChild(QDoubleSpinBox).setValue(40.5)

    assert manager.raw_entry("易水歌·一重")["stats"]["min_outer"] == pytest.approx(40.5)


def test_full_affix_dropdown_lists_only_computable_categories(panel) -> None:
    """列出求值器算不了的类别，会造成「可选、可存、推导时才报错」，
    而一个条目报错整次求值全废。"""
    from lvjiang.apps.yysls.core.attr_model import (
        SUPPORTED_FULL_AFFIX_CATEGORIES,
    )
    from lvjiang.apps.yysls.ui.game_settings.attr_source_panel import (
        MODE_FULL_AFFIX,
    )

    widget, _ = panel
    widget._list.setCurrentRow(0)
    widget._table.cellWidget(0, 1).setCurrentIndex(
        _COLUMNS.index(MODE_FULL_AFFIX))

    combo = widget._table.cellWidget(0, 2)
    listed = {combo.itemText(i) for i in range(combo.count())}
    assert listed == set(SUPPORTED_FULL_AFFIX_CATEGORIES)
