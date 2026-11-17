"""模板图管理页与「新增图像」的目标切换：所有权决定哪些动作可用。"""

from __future__ import annotations

import pytest

from lvjiang.core.templates import ROLE_SAMPLE, TemplateBinding, TemplateInfo
from lvjiang.ui.reference import template_panel as tp


@pytest.fixture
def panel(qtbot, monkeypatch):
    infos = [
        TemplateInfo("android/demo/back", "android",
                     bindings=(TemplateBinding("android", "demo", "back"),)),
        TemplateInfo("common/plain", "common", record_w=640, record_h=360),
        TemplateInfo("common/locked", "common", role=ROLE_SAMPLE),
    ]
    monkeypatch.setattr(tp.tlib, "templates", lambda **_: list(infos))
    widget = tp.TemplatePanel()
    qtbot.addWidget(widget)
    return widget


def _select(panel, name):
    panel._select(name)


def _select_group(panel, group):
    """列表现在只显示当前分组的模板，切组要显式切过滤器。"""
    panel._group_filter.setCurrentIndex(panel._group_filter.findData(group))


def test_layout_group_templates_are_read_only(panel, qtbot):
    _select_group(panel, "android")
    _select(panel, "android/demo/back")
    # 名字与分组就地只读，保存按钮不出现，删除禁用
    assert panel._name_edit.isReadOnly() is True
    assert panel._group_combo.isEnabled() is False
    assert panel._save_btn.isHidden() is True
    assert panel._delete_btn.isEnabled() is False
    assert panel._copy_name_btn.isEnabled() is True
    assert "场景管理" in panel._hint_label.text()
    # 布局分组给 scan 用法
    assert panel._usage_edit.text().startswith("scan [demo].[back]")


def test_plain_group_templates_are_editable(panel):
    _select(panel, "common/plain")
    assert panel._name_edit.isReadOnly() is False
    assert panel._group_combo.isEnabled() is True
    assert panel._delete_btn.isEnabled() is True
    # 没改动时保存不可点
    assert panel._save_btn.isEnabled() is False
    assert panel._usage_edit.text() == 'find as $v by image "common/plain"'
    assert panel._record_label.text() == "640×360"


def test_sample_templates_cannot_be_renamed_or_deleted(panel):
    _select(panel, "common/locked")
    assert panel._name_edit.isReadOnly() is True
    assert panel._group_combo.isEnabled() is False
    assert panel._save_btn.isHidden() is True
    assert panel._delete_btn.isEnabled() is False
    assert panel._copy_usage_btn.isEnabled() is False
    assert "标定样本" in panel._hint_label.text()


def test_group_filter_defaults_to_shared_group_and_has_no_all(panel):
    """分组过滤不提供「全部」；共享分组排在布局分组之前，默认就是 common。"""
    texts = [panel._group_filter.itemText(i)
             for i in range(panel._group_filter.count())]
    assert panel._group_filter.count() == 2
    assert panel._group_filter.currentData() == "common"
    assert texts[0].startswith("common")
    assert "android" in texts[1]
    assert all("全部" not in text for text in texts)


def test_list_shows_stem_only(panel):
    """列表只显示名字本身，分组已经由过滤器给定，不再重复占位。"""
    texts = [panel._list.item(i).text() for i in range(panel._list.count())]
    assert texts == ["locked", "plain"]
    assert panel._list.item(0).data(
        __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ItemDataRole.UserRole,
    ) == "common/locked"


def test_dialog_target_toggle_switches_tools_and_space_controls(qtbot):
    """新增图像切到模板图：隐藏网格工具、显示模板表单，并禁用图库空间控件。"""
    from lvjiang.ui.reference.dialog import ReferenceManagerDialog

    dialog = ReferenceManagerDialog(persist_ui_state=False)
    qtbot.addWidget(dialog)

    titles = [dialog._tabs.tabText(i) for i in range(dialog._tabs.count())]
    assert titles == ["新增图像", "参考图管理", "参考图元数据", "模板图管理"]

    # 写入目标是互斥的切换按钮（不是下拉）
    assert dialog._target_buttons["reference"].isCheckable()
    assert dialog._target_buttons["reference"].isChecked()
    assert dialog._target_buttons["template"].isChecked() is False

    # 默认：参考图模式，网格工具可见、空间控件可用
    assert dialog._is_template_target() is False
    assert dialog._grid_panel.isVisibleTo(dialog) is True
    assert dialog._space_combo.isEnabled() is True

    dialog.set_write_target("template")
    assert dialog._is_template_target() is True
    assert dialog._template_form.isVisibleTo(dialog) is True
    assert dialog._grid_panel.isVisibleTo(dialog) is False
    assert dialog._space_combo.isEnabled() is False
    assert "图库空间" in dialog._btn_edit_space.toolTip()

    dialog._tabs.setCurrentIndex(3)
    assert dialog._space_combo.isEnabled() is False
    dialog._tabs.setCurrentIndex(1)
    dialog.set_write_target("reference")
    dialog._tabs.setCurrentIndex(1)
    assert dialog._space_combo.isEnabled() is True


def test_write_target_switch_keeps_right_pane_allocation(qtbot):
    """切模式只换页，不重新分配宽度——否则右侧会跳一下（闪烁）。"""
    from lvjiang.ui.reference.dialog import ReferenceManagerDialog

    dialog = ReferenceManagerDialog(persist_ui_state=False)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)

    before = dialog._add_splitter.sizes()
    assert dialog._right_stack.currentWidget() is dialog._grid_panel

    dialog.set_write_target("template")
    assert dialog._right_stack.currentWidget() is dialog._template_form
    assert dialog._add_splitter.sizes() == before
    # 两块共用同一个容器，宽度必须完全一致
    assert dialog._template_form.width() == dialog._grid_panel.width()

    dialog.set_write_target("reference")
    assert dialog._right_stack.currentWidget() is dialog._grid_panel
    assert dialog._add_splitter.sizes() == before


def test_inline_rename_and_regroup(panel, monkeypatch):
    """改名与换分组都在详情里完成：保存时走核心层的 rename，目标带上新分组。"""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(tp.tlib, "rename_template",
                        lambda old, new: calls.append((old, new)))

    _select(panel, "common/plain")
    panel._group_combo.setEditText("misc")
    panel._name_edit.setText("renamed")
    assert panel._save_btn.isEnabled() is True

    panel._on_save()
    assert calls == [("common/plain", "misc/renamed")]


def test_inline_rename_rejects_path_separator(panel, monkeypatch, qtbot):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(tp.tlib, "rename_template",
                        lambda old, new: calls.append((old, new)))
    warnings: list[str] = []
    monkeypatch.setattr(tp.QMessageBox, "warning",
                        lambda *args, **kwargs: warnings.append(args[2]))

    _select(panel, "common/plain")
    panel._name_edit.setText("bad/name")
    panel._on_save()

    assert calls == []
    assert warnings and "分隔符" in warnings[0]


def test_preview_dialog_does_not_write_ui_state(qtbot, monkeypatch):
    """一次性实例（预览/测试）不得改写真实 session 的窗口尺寸与网格默认值。"""
    import lvjiang.core.config as core_config

    sizes: list[tuple] = []
    grids: list[dict] = []
    monkeypatch.setattr(core_config, "update_ui_page_state",
                        lambda *args, **kwargs: sizes.append(args))
    monkeypatch.setattr(core_config, "save_reference_grid",
                        lambda *args, **kwargs: grids.append(args))

    from lvjiang.ui.reference.dialog import ReferenceManagerDialog

    preview = ReferenceManagerDialog(persist_ui_state=False)
    qtbot.addWidget(preview)
    preview.show()
    qtbot.waitExposed(preview)
    preview._on_grid_defaults_changed({"rows": 3, "cols": 6, "gap": 2,
                                       "height": 122, "width": 122})
    preview.close()

    assert sizes == [] and grids == []

    # 开关是活的：默认实例仍会保存（此处是替身，不落盘）
    normal = ReferenceManagerDialog()
    qtbot.addWidget(normal)
    normal.show()
    qtbot.waitExposed(normal)
    normal.close()
    assert len(sizes) == 1 and sizes[0][0] == "reference_manager"
