"""词组配置右侧子 Tab、整页滚动契约与升级规则列宽。

- 右侧拆「基础属性」「等级属性」两个子 Tab：基础属性承载词组分类 → 词条名称
  映射，等级属性承载等级/数值表与升级规则。
- 基础属性页内容全局顺序平铺，装不下时**整页**滚动——不再给词条列表单独套
  滚动条（局部滚动与页内空白并存会把映射区割裂成两块）。
- 升级规则的「原词条」「目标词条」平分剩余宽度。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHeaderView

from lvjiang.apps.yysls.ui.game_settings.affix_caps_panel import AffixCapsPanel
from lvjiang.i18n import tr


def _many_aliases(count: int) -> list[str]:
    return [f"词条{i}" for i in range(count)]


def test_right_side_splits_into_base_and_level_tabs(qtbot) -> None:
    data = {"affix_caps": {"外功攻击": {"_aliases": ["最大外功攻击"], "110": {"cap": 10}}}}
    panel = AffixCapsPanel(data=data)
    qtbot.addWidget(panel)

    assert [panel._sub_tabs.tabText(i) for i in range(panel._sub_tabs.count())] == [
        tr("基础属性"), tr("等级属性")]
    # 等级表归「等级属性」页，词条映射归「基础属性」页
    assert panel._table.parent() is panel._level_page
    assert panel._alias_frame.parent() is panel._base_page
    # 基础属性页挂在一个整页滚动区里
    assert panel._sub_tabs.widget(0) is panel._base_scroll


def test_alias_list_lies_flat_without_nested_scroll(qtbot) -> None:
    """词条列表直接平铺在词条名称区里，不再被单独套滚动条。"""
    data = {"affix_caps": {"外功攻击": {"_aliases": _many_aliases(20), "110": {"cap": 10}}}}
    panel = AffixCapsPanel(data=data)
    qtbot.addWidget(panel)
    panel._affix_list.setCurrentRow(0)

    assert panel._alias_tags_widget.parent() is panel._alias_frame
    assert panel._alias_group_tabs.parent() is panel._alias_frame
    # 整页滚动区按需出现滚动条，不设人为高度上限
    assert panel._base_scroll.widget() is panel._base_page
    assert panel._base_scroll.verticalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAsNeeded)


def test_base_page_shows_all_without_scrollbar_when_it_fits(qapp, qtbot) -> None:
    data = {"affix_caps": {"外功攻击": {"_aliases": _many_aliases(6), "110": {"cap": 10}}}}
    panel = AffixCapsPanel(data=data)
    qtbot.addWidget(panel)
    panel.resize(1000, 900)
    panel.show()
    qapp.processEvents()

    assert not panel._base_scroll.verticalScrollBar().isVisible()


def test_base_page_scrolls_as_a_whole_when_content_exceeds(qapp, qtbot) -> None:
    data = {"affix_caps": {"外功攻击": {"_aliases": _many_aliases(60), "110": {"cap": 10}}}}
    panel = AffixCapsPanel(data=data)
    qtbot.addWidget(panel)
    panel.resize(1000, 500)
    panel.show()
    qapp.processEvents()

    assert panel._base_scroll.verticalScrollBar().isVisible()


def test_base_page_packs_cards_in_sequence_without_spreading(qapp, qtbot) -> None:
    """多出来的高度必须沉到页面底部，不能均摊进各行间距。

    没有尾 stretch 时，QVBoxLayout 会把剩余空间摊给每两个卡片之间，
    把「词组分类 / 词组单位 / 同名叠加」拉成相隔很远的一串。
    """
    data = {"affix_caps": {"外功攻击": {"_aliases": _many_aliases(3), "110": {"cap": 10}}}}
    panel = AffixCapsPanel(data=data)
    qtbot.addWidget(panel)
    panel.resize(1000, 900)
    panel.show()
    qapp.processEvents()

    pool = panel._base_page.findChild(QFrame, "poolFrame")
    unit = panel._base_page.findChild(QFrame, "unitFrame")
    assert pool is not None and unit is not None
    gap = unit.geometry().top() - pool.geometry().bottom()
    # 只允许布局默认间距级别的缝隙，不允许被拉伸成上百像素
    assert 0 <= gap < 30


def test_upgrade_rule_splits_remaining_width_between_two_affix_columns(qtbot) -> None:
    """原词条与目标词条平分剩余宽度；等级列贴内容。"""
    data = {"affix_caps": {"外功攻击": {"_aliases": ["最大外功攻击"], "110": {"cap": 10}}}}
    panel = AffixCapsPanel(data=data)
    qtbot.addWidget(panel)

    header = panel._upgrade_table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.ResizeToContents
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.ResizeToContents
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Stretch
    assert header.sectionResizeMode(3) == QHeaderView.ResizeMode.Stretch
