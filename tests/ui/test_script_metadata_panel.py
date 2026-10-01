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
    assert [widget.code_tabs.tabText(i) for i in range(widget.code_tabs.count())] == [
        "代码", "元数据", "录制",
    ]
    assert widget.code_tabs.indexOf(widget.record) == 2
    assert widget.debug.canvas._placeholder.splitlines() == [
        "点「刷新截图」抓一帧",
        "左键点：取点/取色；左键拖：取区域",
        "右键拖：平移；滚轮：缩放",
    ]
    assert widget.side_tabs.indexOf(widget.record) == -1
    assert [widget.side_tabs.tabText(i) for i in range(widget.side_tabs.count())] == [
        "指令", "调试"]

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


def test_metadata_panel_preserves_batch_check(qtbot):
    from lvjiang.ui.scripts.metadata_panel import MetadataPanel

    panel = MetadataPanel()
    qtbot.addWidget(panel)
    panel.load_text(
        "#% runnable: true\n#% batchable: true\n"
        "#% batch_check: check_batch\n\n"
        "def check_batch($params)\n"
        "    return {\"status\": \"success\"}\n"
        "end\n",
        editable=True,
    )
    applied: list[str] = []
    panel.text_applied.connect(applied.append)

    panel._apply()

    assert parse_metadata(applied[0])["batch_check"] == "check_batch"


def test_metadata_buttons_and_expansion_keep_stable_geometry(qtbot):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtWidgets import QApplication, QScrollArea

    from lvjiang.ui.scripts.metadata_panel import MetadataPanel

    panel = MetadataPanel()
    qtbot.addWidget(panel)
    panel.load_text("#% name: 示例\n#% note: 第一行\n", editable=True)
    panel.resize(640, 440)
    panel.show()
    qtbot.waitExposed(panel)
    scroll = panel.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn

    toggle = panel.edit_note.toggle
    viewport_width = scroll.viewport().width()
    before = toggle.mapTo(panel, QPoint(0, 0))
    toggle.click()
    QApplication.processEvents()
    after_expand = toggle.mapTo(panel, QPoint(0, 0))
    assert after_expand == before
    assert scroll.viewport().width() == viewport_width
    toggle.click()
    QApplication.processEvents()
    assert toggle.mapTo(panel, QPoint(0, 0)) == before
    assert scroll.viewport().width() == viewport_width


def test_metadata_parameter_rows_align_with_outer_form(qtbot):
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QApplication

    from lvjiang.ui.scripts.metadata_panel import MetadataPanel

    panel = MetadataPanel()
    qtbot.addWidget(panel)
    panel.load_text(
        "#% name: demo\n#% parameters:\n"
        "#%   - {name: option, type: bool, default: true}\n",
        editable=True,
    )
    for width in (480, 700):
        panel.resize(width, 700)
        panel.show()
        QApplication.processEvents()
        input_x = panel.edit_name.mapTo(panel, QPoint(0, 0)).x()
        assert panel.parameters.list.mapTo(panel, QPoint(0, 0)).x() == input_x
        assert panel.parameters.name.mapTo(panel, QPoint(0, 0)).x() == input_x
        outer_form = panel.edit_name.parentWidget().layout()
        outer_label = outer_form.labelForField(panel.edit_name)
        inner_label = panel.parameters._form.labelForField(panel.parameters.name)
        assert outer_label.mapTo(panel, QPoint(0, 0)).x() == (
            inner_label.mapTo(panel, QPoint(0, 0)).x())


def test_metadata_panel_edits_all_declared_fields_without_yaml_input(qtbot):
    from lvjiang.ui.scripts.metadata_panel import MetadataPanel, read_front_matter

    source = (
        "#% id: example\n"
        "#% name: 示例\n"
        "#% note: |-\n"
        "#%   第一行\n"
        "#%   第二行\n"
        "#% env: [desktop]\n"
        "#% requires: [device_gesture]\n"
        "#% scope: dedicated\n"
        "#% runnable: true\n"
        "#% batchable: true\n"
        "#% hidden: true\n"
        "#% batch_check: check_batch\n"
        "#% batch_unit_prepare: true\n"
        "#% parameters:\n"
        "#%   - {name: enabled, type: bool, default: false}\n"
        "#%   - name: target\n"
        "#%     type: select\n"
        "#%     default: first\n"
        "#%     options: [first, {value: second, label: 第二项}]\n"
        "#%   - {name: count, type: number, min: 1, max: 9, default: 3}\n"
        "#%   - name: message\n"
        "#%     type: text\n"
        "#%     default: \"两行\\n内容\"\n"
        "#%     placeholder: 提示\n"
        "#%     multiline: true\n"
        "#%     require: [\"$enabled\", \"$count > 0\"]\n"
        "#%   - name: flags\n"
        "#%     type: checkgroup\n"
        "#%     options: [a, b]\n"
        "#%     default: {a: false}\n"
        "\nlog \"body\"\n"
    )
    panel = MetadataPanel()
    qtbot.addWidget(panel)
    panel.load_text(source, editable=True)
    assert not panel.has_draft()
    assert panel.scope_radios["dedicated"].isChecked()
    form = panel.edit_batch_check.parentWidget().layout()
    assert form.labelForField(panel.edit_batch_check).text() == "批量检查"
    assert panel.edit_note.text() == "第一行\n第二行"
    panel.edit_note.toggle.click()
    assert panel.edit_note.text() == "第一行\n第二行"
    panel.edit_note.toggle.click()
    assert panel.check_batch_unit_prepare.isChecked()
    assert panel.parameters.list.count() == 5
    applied: list[str] = []
    panel.text_applied.connect(applied.append)
    panel._apply()
    assert applied == [source]

    panel.edit_note.toggle.click()
    panel.edit_note.multi.setPlainText("更新\n说明")
    panel.scope_radios["daily"].setChecked(True)
    panel.parameters.list.setCurrentRow(3)
    assert panel.parameters.require_list.isChecked()
    assert panel.parameters.require.text() == "$enabled\n$count > 0"
    panel.parameters.list.setCurrentRow(4)
    assert panel.parameters.values()[3]["default"] == "两行\n内容"
    panel._apply()
    result = read_front_matter(applied[-1])
    assert result["note"] == "更新\n说明"
    assert result["scope"] == "daily"
    assert result["batch_unit_prepare"] is True
    assert result["requires"] == ["device_gesture"]
    assert result["parameters"][4]["default"] == {"a": False}
    assert applied[-1].endswith('log "body"\n')


def test_unknown_parameter_type_is_visible_and_can_be_corrected(qtbot):
    from lvjiang.ui.scripts.metadata_panel import MetadataPanel

    source = (
        "#% parameters:\n"
        "#%   - {name: retries, type: int, default: 12}\n"
        "\nlog \"ok\"\n"
    )
    panel = MetadataPanel()
    qtbot.addWidget(panel)
    panel.load_text(source, editable=True)
    assert panel.btn_apply.isEnabled()
    assert panel.parameters.kind.currentData() == "int"
    assert not panel.has_draft()

    panel.parameters.kind.setCurrentIndex(panel.parameters.kind.findData("number"))
    applied: list[str] = []
    panel.text_applied.connect(applied.append)
    panel._apply()
    assert parse_metadata(applied[-1])["parameters"][0]["type"] == "number"
    assert parse_metadata(applied[-1])["parameters"][0]["default"] == 12


def test_developer_save_respects_layer_and_migrates(qtbot, tmp_path, monkeypatch):
    from lvjiang.core.config import resolver as resolver_module
    from lvjiang.ui.scripts.editor_dialog import ScriptEditorDialog

    resolver = resolver_module.ConfigResolver(
        tmp_path / "system", tmp_path / "local", dev_mode=True,
    )
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    resolver.write_entity("workflows/demo.wf", "#% runnable: true\n\nlog \"ok\"\n")
    widget = ScriptEditorDialog()
    qtbot.addWidget(widget)
    widget._load_entry(widget._entry("demo.wf"))
    widget.metadata_panel.location_radios["local"].setChecked(True)
    assert widget.btn_save.isEnabled()
    widget._on_save()
    assert not (tmp_path / "system/workflows/demo.wf").exists()
    assert (tmp_path / "local/workflows/demo.wf").exists()
    assert widget._current.layer == "local"

    widget.editor.insertPlainText("\n# 本地修改")
    widget._on_save()
    assert "本地修改" in (tmp_path / "local/workflows/demo.wf").read_text()
    assert not (tmp_path / "system/workflows/demo.wf").exists()
    widget._dirty = False


def test_layer_migration_copies_referenced_input_trace(tmp_path):
    from lvjiang.ui.scripts.editor_dialog import copy_trace_dependencies

    source_root = tmp_path / "system/workflows"
    target_root = tmp_path / "local/workflows"
    source = source_root / "nested/demo.wf"
    target = target_root / "nested/demo.wf"
    old_trace = source_root / "lvtrace/example.lvtrace"
    old_trace.parent.mkdir(parents=True)
    old_trace.write_bytes(b"recorded-input")
    text = 'replay input_trace "../lvtrace/example.lvtrace"\n'

    copy_trace_dependencies(text, source, target, source_root, target_root)

    assert (target_root / "lvtrace/example.lvtrace").read_bytes() == b"recorded-input"


def test_regular_user_sees_system_read_only_and_local_location(qtbot, tmp_path, monkeypatch):
    from lvjiang.core.config import resolver as resolver_module
    from lvjiang.ui.scripts.editor_dialog import ScriptEditorDialog

    system = tmp_path / "system/workflows/system_script.wf"
    system.parent.mkdir(parents=True)
    system.write_text("#% runnable: true\n\nlog \"system\"\n")
    local = tmp_path / "local/workflows/local_script.wf"
    local.parent.mkdir(parents=True)
    local.write_text("#% runnable: true\n\nlog \"local\"\n")
    resolver = resolver_module.ConfigResolver(
        tmp_path / "system", tmp_path / "local", dev_mode=False,
    )
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    widget = ScriptEditorDialog()
    qtbot.addWidget(widget)

    widget._load_entry(widget._entry("system_script.wf"))
    assert widget.editor.isReadOnly()
    assert widget.metadata_panel.location() == "system"
    assert not widget.metadata_panel.location_row.isEnabled()

    widget._load_entry(widget._entry("local_script.wf"))
    assert not widget.editor.isReadOnly()
    assert widget.metadata_panel.location() == "local"
    assert not widget.metadata_panel.location_row.isEnabled()


def test_metadata_draft_survives_tab_switch(qtbot, tmp_path, monkeypatch):
    from lvjiang.core.config import resolver as resolver_module
    from lvjiang.ui.scripts.editor_dialog import ScriptEditorDialog

    resolver = resolver_module.ConfigResolver(
        tmp_path / "system", tmp_path / "local", dev_mode=True,
    )
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    resolver.write_entity("workflows/demo.wf", "#% name: 原名\n\nlog \"ok\"\n")
    widget = ScriptEditorDialog()
    qtbot.addWidget(widget)
    widget._load_entry(widget._entry("demo.wf"))
    widget.code_tabs.setCurrentWidget(widget.metadata_panel)
    widget.metadata_panel.edit_name.setText("草稿")

    widget.code_tabs.setCurrentWidget(widget.editor)
    widget.code_tabs.setCurrentWidget(widget.metadata_panel)

    assert widget.metadata_panel.edit_name.text() == "草稿"
    widget.metadata_panel._reload()


def test_save_does_not_discard_unapplied_metadata(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from lvjiang.core.config import resolver as resolver_module
    from lvjiang.ui.scripts.editor_dialog import ScriptEditorDialog

    resolver = resolver_module.ConfigResolver(
        tmp_path / "system", tmp_path / "local", dev_mode=True,
    )
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    resolver.write_entity("workflows/demo.wf", "#% name: 原名\n\nlog \"ok\"\n")
    widget = ScriptEditorDialog()
    qtbot.addWidget(widget)
    widget._load_entry(widget._entry("demo.wf"))
    widget.metadata_panel.edit_name.setText("未应用")
    widget.metadata_panel.location_radios["local"].setChecked(True)
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, _body: warnings.append(title))

    widget._on_save()

    assert warnings == ["元数据尚未应用"]
    assert (tmp_path / "system/workflows/demo.wf").exists()
    assert not (tmp_path / "local/workflows/demo.wf").exists()
    widget.metadata_panel._reload()
    widget.metadata_panel.set_location("system", developer=True, editable=True)
    widget._dirty = False


def test_recording_lands_in_result_pane_until_written(qtbot, tmp_path, monkeypatch):
    """录制占用中央页签；结果不自动改代码，写入后回到代码页。"""
    from lvjiang.core.config import resolver as resolver_module
    from lvjiang.ui.scripts.editor_dialog import ScriptEditorDialog

    resolver = resolver_module.ConfigResolver(
        tmp_path / "system", tmp_path / "local", dev_mode=True,
    )
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    resolver.write_entity("workflows/demo.wf", "#% runnable: true\n\nlog \"demo\"\n")
    widget = ScriptEditorDialog()
    qtbot.addWidget(widget)
    record = widget.record
    assert not record.btn_insert.isHidden() and record.btn_save.isHidden()
    assert record.btn_copy.text() == "复制录制结果"
    before = widget.editor.toPlainText()

    class _FakeRecorder:
        precision = "low"

        def stop(self):
            return 'click (0.5, 0.5)\nwait 0.3'

    record._recorder = _FakeRecorder()
    widget.begin_recording()
    assert widget.code_tabs.currentWidget() is record
    assert not widget.code_tabs.isTabEnabled(widget.code_tabs.indexOf(widget.editor))
    assert not widget.code_tabs.isTabEnabled(widget.code_tabs.indexOf(widget.metadata_panel))
    record._stop_recording()

    assert widget.editor.toPlainText() == before          # 代码区未被动过
    assert record.text_edit.toPlainText() == 'click (0.5, 0.5)\nwait 0.3'
    assert not record.text_edit.isReadOnly()              # 可先修改
    assert record.btn_insert.isEnabled()
    assert widget.code_tabs.isTabEnabled(widget.code_tabs.indexOf(widget.editor))
    assert widget.code_tabs.isTabEnabled(widget.code_tabs.indexOf(widget.metadata_panel))

    record.text_edit.setPlainText('click (0.5, 0.5)\nwait 0.5')
    record._on_insert()

    assert 'wait 0.5' in widget.editor.toPlainText()
    assert widget._dirty
    assert widget.code_tabs.currentWidget() is widget.editor
    assert record.text_edit.isReadOnly()
    assert not record.btn_insert.isEnabled()
    assert "已写入编辑区域" in record.lbl_status.text()
    inserted = widget.editor.toPlainText()
    record._on_insert()
    assert widget.editor.toPlainText() == inserted         # 不允许重复写入
    record._on_clear()
    widget._dirty = False


def test_pending_recording_result_guards_script_switch(qtbot, tmp_path, monkeypatch):
    """录制结果与开始时的脚本绑定，不能静默转移到另一份脚本。"""
    from PyQt6.QtWidgets import QMessageBox

    from lvjiang.core.config import resolver as resolver_module
    from lvjiang.ui.scripts.editor_dialog import ScriptEditorDialog

    resolver = resolver_module.ConfigResolver(
        tmp_path / "system", tmp_path / "local", dev_mode=True,
    )
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    for name in ("a", "b"):
        resolver.write_entity(
            f"workflows/{name}.wf",
            f"#% runnable: true\n\nlog \"{name}\"\n",
        )
    widget = ScriptEditorDialog()
    qtbot.addWidget(widget)
    assert widget._current is not None and widget._current.rel_path == "a.wf"
    widget.record._target_id = "a.wf"
    widget.record.text_edit.setPlainText('press "Q"')

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.No,
    )
    widget.tree.setCurrentItem(widget._file_items["b.wf"])
    assert widget._current is not None and widget._current.rel_path == "a.wf"
    assert widget.tree.currentItem() is widget._file_items["a.wf"]
    assert widget.record.has_pending_result

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    widget.tree.setCurrentItem(widget._file_items["b.wf"])
    assert widget._current is not None and widget._current.rel_path == "b.wf"
    assert not widget.record.text_edit.toPlainText()
    assert not widget.record.has_pending_result


def test_new_button_recovers_after_lock_and_recording(qtbot, tmp_path, monkeypatch):
    """运行/录制结束后“新建”必须恢复可用，而不是永远灰着。"""
    from lvjiang.core.config import resolver as resolver_module
    from lvjiang.ui.scripts.editor_dialog import ScriptEditorDialog

    resolver = resolver_module.ConfigResolver(
        tmp_path / "system", tmp_path / "local", dev_mode=True,
    )
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    resolver.write_entity("workflows/demo.wf", "#% runnable: true\n\nlog \"demo\"\n")
    widget = ScriptEditorDialog()
    qtbot.addWidget(widget)
    assert widget.btn_new.isEnabled()

    widget.set_locked(True)
    assert not widget.btn_new.isEnabled()
    widget.set_locked(False)
    assert widget.btn_new.isEnabled()

    widget.begin_recording()
    assert not widget.btn_new.isEnabled()
    widget.end_recording()
    assert widget.btn_new.isEnabled()
