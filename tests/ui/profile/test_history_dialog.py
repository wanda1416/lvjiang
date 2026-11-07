"""变更记录对话框的交互契约。

保护的是用户能看到、能操作的行为：删除只删这一条、单用户视图收起用户列但
条件可以再放宽、改筛选后回到第一页、页码可任意跳转、聚合候选默认只列前 10
项且可以搜索。列号一律按列 key 定位，不再依赖手写偏移量。
"""

from PyQt6.QtWidgets import QMessageBox, QPushButton, QWidget

from lvjiang.ui.profile.history_dialog import HistoryDialog, HistoryScope
from lvjiang.ui.profile.history_filters import FACET_VISIBLE_LIMIT


def _seed(count: int, sources: tuple[str, ...] = ("日常", "限流")) -> None:
    from lvjiang.core.profile import repository

    for index in range(count):
        repository.db_upsert(
            "tester", "stock", "jinbi", index + 1,
            change_type="delta", delta_value=1,
            source=sources[index % len(sources)],
        )


def _actions_cell(dialog: HistoryDialog, row: int) -> QWidget | None:
    index = dialog._column_index("actions")
    assert index is not None
    return dialog._table.cellWidget(row, index)


def test_history_delete_requires_confirmation_and_refreshes_without_value_change(
    qapp, monkeypatch,
):
    from lvjiang.core.profile import repository

    # 真实临时 DB 验证按钮只删除这一行，不写余额或删除审计。
    repository.db_upsert('tester', 'stock', 'item', 2, change_type='delta',
                         delta_value=2, source='example')
    repository.db_upsert('tester', 'stock', 'item', 3, change_type='delta',
                         delta_value=1, source='example')
    # 未定义 key 不影响历史删除，按钮不复用撤销的限制。
    dialog = HistoryDialog(
        HistoryScope(username='tester', model_type='stock', key='item',
                     key_label='示例'))
    table = dialog._table
    first = repository.db_get_history('tester', 'stock', 'item')[0]['id']
    monkeypatch.setattr(
        QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.No)
    next(b for b in _actions_cell(dialog, 1).findChildren(QPushButton)
         if b.text() == '删除').click()
    assert repository.db_count_history('tester', 'stock', 'item') == 2
    monkeypatch.setattr(
        QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    next(b for b in _actions_cell(dialog, 1).findChildren(QPushButton)
         if b.text() == '删除').click()
    assert not repository.db_get_history_record(first)
    assert repository.db_count_history('tester', 'stock', 'item') == 1
    assert repository.db_read_entry('tester', 'stock', 'item')['value'] == 3
    assert table.rowCount() == 2 and not dialog.values_changed
    dialog.close()


def test_columns_stay_visible_in_single_user_scope(qapp):
    """入口的预设只体现在筛选条件与条件标签上，不收起任何列。

    收列会让表格每次筛选都重排列宽、连带对话框一起变宽变窄，用户看到的是
    一次次闪动；列固定之后换筛选只换内容。
    """
    _seed(5)
    dialog = HistoryDialog(
        HistoryScope(username='tester', model_type='stock', key='jinbi',
                     key_label='金币'))
    table = dialog._table
    for index in range(table.columnCount()):
        assert not table.isColumnHidden(index)
    assert [chip[0] for chip in dialog._condition_chips()] == ["username", "key"]

    # 预设不锁定：取消用户条件后其它条件与列都不受影响。
    dialog._drop_condition("username")
    assert dialog._shown == 5
    for index in range(table.columnCount()):
        assert not table.isColumnHidden(index)
    dialog.close()


def test_filter_changes_keep_dialog_minimum_size(qapp):
    """筛选与翻页都不能改变对话框的最小尺寸，否则窗口会自己跳。

    合计金额、页码位数、条件标签都是随数据变长的文字；它们一旦参与最小宽度
    计算，窗口就会在筛选时变宽或变窄。
    """
    _seed(120, sources=("日常", "限流", "活动奖励"))
    dialog = HistoryDialog(HistoryScope())
    baseline = dialog.minimumSizeHint()
    assert baseline.width() > 0

    dialog._filter_bar.set_keyword("一个很长很长的关键词用来撑开条件标签")
    dialog._apply_filters()
    assert dialog.minimumSizeHint() == baseline

    button = dialog._facet_buttons["source"]
    button.set_values(("日常", "限流", "活动奖励"))
    button.values_changed.emit(("日常", "限流", "活动奖励"))
    assert dialog.minimumSizeHint() == baseline

    dialog._delta_combo.setCurrentIndex(1)
    dialog._page_size_combo.setCurrentIndex(
        dialog._page_size_combo.findData(200))
    assert dialog.minimumSizeHint() == baseline

    dialog._filter_bar.set_keyword("绝对不存在")
    dialog._apply_filters()
    assert dialog._shown == 0
    assert dialog.minimumSizeHint() == baseline

    dialog._clear_all_filters()
    assert dialog.minimumSizeHint() == baseline
    dialog.close()


def test_filter_change_returns_to_first_page(qapp):
    _seed(120)
    dialog = HistoryDialog(HistoryScope(username='tester'))
    assert dialog._page_count == 2
    dialog._go_to_page(2)
    assert dialog._page == 2

    button = dialog._facet_buttons["source"]
    button.set_values(("日常",))
    button.values_changed.emit(("日常",))
    assert dialog._page == 1
    assert dialog._total == 60
    dialog.close()


def test_jump_to_page_clamps_to_last_page(qapp):
    _seed(120)
    dialog = HistoryDialog(HistoryScope())
    dialog._page_spin.setValue(2)
    dialog._jump_to_page()
    assert dialog._page == 2
    dialog._page_spin.setValue(999)
    dialog._jump_to_page()
    assert dialog._page == dialog._page_count
    dialog._go_to_page(-5)
    assert dialog._page == 1
    dialog.close()


def test_facet_candidates_show_top_ten_and_can_be_searched(qapp):
    sources = tuple(f"来源{index:02d}" for index in range(14))
    _seed(28, sources=sources)
    dialog = HistoryDialog(HistoryScope())
    button = dialog._facet_buttons["source"]
    assert len(button.candidates) == FACET_VISIBLE_LIMIT
    assert button.candidate_total == len(sources)

    dialog._on_facet_search("source", "来源13")
    assert [raw for raw, _count in button.candidates] == ["来源13"]
    dialog.close()


def test_picking_facet_value_filters_rows(qapp):
    from PyQt6.QtCore import Qt

    _seed(6)
    dialog = HistoryDialog(HistoryScope())
    button = dialog._facet_buttons["source"]
    button.open_panel()
    listing = button._list
    assert listing is not None and listing.count() == 2
    for index in range(listing.count()):
        item = listing.item(index)
        if item is not None and item.data(Qt.ItemDataRole.UserRole) == "日常":
            item.setCheckState(Qt.CheckState.Checked)
            break
    else:  # pragma: no cover - 候选里必然有「日常」
        raise AssertionError("候选列表缺少预期取值")
    assert dialog._query.sources == ("日常",)
    assert dialog._total == 3
    dialog.close()


def test_empty_result_offers_clear_filters(qapp):
    _seed(4)
    dialog = HistoryDialog(HistoryScope())
    dialog._filter_bar.set_keyword("绝对不存在的关键词")
    dialog._apply_filters()
    assert dialog._shown == 0
    assert dialog._empty_panel is not None
    dialog._clear_all_filters()
    assert dialog._shown == 4
    dialog.close()
