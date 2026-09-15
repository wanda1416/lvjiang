from lvjiang.ui.scripts.editor_dialog import validate_script_id
from lvjiang.ui.scripts.metadata_panel import replace_front_matter
from lvjiang.workflows.metadata import parse_metadata


def test_replace_front_matter_preserves_workflow_body():
    original = (
        "#% name: 旧名称\n"
        "#% runnable: true\n"
        "\n"
        "# 正文注释必须保留\n"
        'log "开始"\n'
    )

    result = replace_front_matter(original, {
        "id": "demo",
        "name": "新名称",
        "env": ["android", "desktop"],
        "runnable": True,
        "batchable": False,
    })

    assert parse_metadata(result)["name"] == "新名称"
    assert result.endswith('\n# 正文注释必须保留\nlog "开始"\n')


def test_replace_front_matter_adds_header_without_losing_first_statement():
    result = replace_front_matter('log "开始"\n', {
        "id": "demo",
        "runnable": True,
    })

    assert parse_metadata(result)["id"] == "demo"
    assert result.endswith('\nlog "开始"\n')


def test_editor_accepts_unicode_script_id():
    assert validate_script_id("好友送礼") is None
    assert validate_script_id("Équipement_2") is None
    assert validate_script_id("2好友") is not None


def test_workbench_actions_belong_to_editor_tab(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QSizePolicy

    from lvjiang.core.config import resolver as resolver_module
    from lvjiang.ui.scripts.editor_dialog import ScriptEditorDialog

    resolver = resolver_module.ConfigResolver(
        tmp_path / "system", tmp_path / "local", dev_mode=True,
    )
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    resolver.write_entity(
        "workflows/demo.wf", "#% runnable: true\n\nlog \"demo\"\n",
    )
    widget = ScriptEditorDialog()
    qtbot.addWidget(widget)

    editor_page = widget.workspace_tabs.widget(0)
    config_page = widget.workspace_tabs.widget(1)
    assert editor_page.isAncestorOf(widget.editor_toolbar)
    assert not config_page.isAncestorOf(widget.editor_toolbar)
    assert widget.record.radio_precision_low.text() == "低精度"
    assert widget.record.radio_precision_high.text() == "高精度"
    assert widget.record.check_mouse_movement.text() == ""
    assert "100ms" in widget.record.radio_precision_low.toolTip()
    assert "lvtrace" in widget.record.radio_precision_high.toolTip()
    assert widget.record.minimumWidth() == 0

    toolbar_layout = widget.editor_toolbar.layout()
    assert toolbar_layout.indexOf(widget.lbl_layer) == (
        toolbar_layout.indexOf(widget.btn_check) + 1)
    assert widget.lbl_layer.sizePolicy().horizontalPolicy() == (
        QSizePolicy.Policy.Ignored)

    widget.resize(1200, 700)
    widget.show()
    qtbot.waitExposed(widget)
    check_x = widget.btn_check.x()
    label_x = widget.lbl_layer.x()
    widget.lbl_layer.setText("system · /" + "very-long-path/" * 40 + "demo.wf")
    qtbot.wait(10)
    assert widget.btn_check.x() == check_x
    assert widget.lbl_layer.x() == label_x
    tree_width, editor_width, tools_width = widget.main_splitter.sizes()
    assert tree_width >= 220
    assert editor_width > tools_width
    assert tools_width <= 340

    widget.main_splitter.setSizes([220, 979, 1])
    qtbot.wait(10)
    tools_width = widget.main_splitter.sizes()[2]
    assert 0 < tools_width <= 10


def test_editor_marks_all_duplicate_id_files_with_error_icon(
        qtbot, tmp_path, monkeypatch):
    from PyQt6.QtGui import QColor

    from lvjiang.core.config import resolver as resolver_module
    from lvjiang.ui.scripts.editor_dialog import ScriptEditorDialog
    from lvjiang.ui.theme import get_theme_manager

    resolver = resolver_module.ConfigResolver(
        tmp_path / "system", tmp_path / "local", dev_mode=True,
    )
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    for rel in ("a.wf", "nested/b.wf"):
        resolver.write_entity(
            f"workflows/{rel}",
            "#% id: duplicate\n#% runnable: true\n\nlog \"demo\"\n",
        )

    widget = ScriptEditorDialog()
    qtbot.addWidget(widget)

    expected_color = QColor(get_theme_manager().tokens.danger)
    for rel in ("a.wf", "nested/b.wf"):
        item = widget._file_items[rel]
        assert not item.icon(0).isNull()
        assert item.icon(0).cacheKey() == widget._icon_id_conflict.cacheKey()
        assert item.foreground(0).color() == expected_color
        assert "脚本 id 'duplicate' 冲突" in item.toolTip(0)


def test_editor_defers_metadata_parse_until_focus_out(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QFocusEvent
    from PyQt6.QtWidgets import QApplication

    from lvjiang.core.config import resolver as resolver_module
    from lvjiang.ui.scripts import editor_dialog as editor_module

    resolver = resolver_module.ConfigResolver(
        tmp_path / "system", tmp_path / "local", dev_mode=True,
    )
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    resolver.write_entity(
        "workflows/demo.wf", "#% runnable: true\n\nlog \"demo\"\n",
    )
    widget = editor_module.ScriptEditorDialog()
    qtbot.addWidget(widget)

    parsed: list[str] = []
    monkeypatch.setattr(
        editor_module, "metadata_error", lambda text: parsed.append(text) or "",
    )

    widget.editor.insertPlainText("#")
    QApplication.processEvents()
    assert parsed == []

    QApplication.sendEvent(
        widget.editor, QFocusEvent(QEvent.Type.FocusOut),
    )
    assert parsed == [widget.editor.toPlainText()]

    QApplication.sendEvent(
        widget.editor, QFocusEvent(QEvent.Type.FocusOut),
    )
    assert parsed == [widget.editor.toPlainText()]
    # 避免 qtbot 回收对话框时因本用例制造的未保存状态弹确认框。
    widget._dirty = False


def test_metadata_panel_env_choices_come_from_app_config(qtbot, monkeypatch):
    """运行环境不是写死的 Android/Windows，而是系统参数 app.yaml 的 envs。"""
    from lvjiang.ui.scripts import metadata_panel as panel_module

    monkeypatch.setattr(
        panel_module, "load_available_envs",
        lambda: [("android", "安卓"), ("desktop", "桌面"), ("ios", "iOS")],
    )
    monkeypatch.setattr(
        "lvjiang.workflows.metadata.known_envs",
        lambda: ["android", "desktop", "ios"],
    )
    panel = panel_module.MetadataPanel()
    qtbot.addWidget(panel)

    assert [c.text() for c in panel._env_checks.values()] == ["安卓", "桌面", "iOS"]
    assert [
        panel._env_row.itemAt(index).widget().text()
        for index in range(len(panel._env_checks))
    ] == ["安卓", "桌面", "iOS"]

    panel.load_text("#% runnable: true\n#% env: [ios, android]\n", editable=True)
    assert panel._selected_env() == ["android", "ios"]

    panel._env_checks["desktop"].setChecked(True)
    panel._env_checks["android"].setChecked(False)
    applied: list[str] = []
    panel.text_applied.connect(applied.append)
    panel._apply()

    assert parse_metadata(applied[0])["env"] == ["desktop", "ios"]


def test_metadata_panel_keeps_env_missing_from_app_config(qtbot, monkeypatch):
    """脚本声明了系统参数里没有的环境：要显示出来，不能在“应用”时悄悄丢掉。"""
    from lvjiang.ui.scripts import metadata_panel as panel_module

    monkeypatch.setattr(
        panel_module, "load_available_envs", lambda: [("desktop", "桌面")])
    monkeypatch.setattr("lvjiang.workflows.metadata.known_envs", lambda: [])
    panel = panel_module.MetadataPanel()
    qtbot.addWidget(panel)

    panel.load_text("#% runnable: true\n#% env: [android]\n", editable=True)

    assert set(panel._env_checks) == {"desktop", "android"}
    assert "系统参数中未定义" in panel._env_checks["android"].text()
    applied: list[str] = []
    panel.text_applied.connect(applied.append)
    panel._apply()
    assert parse_metadata(applied[0])["env"] == ["android"]
