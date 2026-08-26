"""脚本编辑对话框测试

1. 纯逻辑：id 校验、模板、语法检查、列表合并视图、自动暴露、落盘校验
2. 对话框（qtbot，输入框/确认框打桩）：新建 → 文件落盘且进列表；改文本保存；删除
resolver 指向 tmp 的 system/local 双层目录，不碰真实配置。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lvjiang.core.config import resolver as cr
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.ui.scripts import editor_dialog as sed
from lvjiang.workflows.metadata import parse_metadata


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    system = tmp_path / "system"
    local = tmp_path / "local"
    (system / "workflows").mkdir(parents=True)
    (local / "workflows").mkdir(parents=True)
    (system / "workflows" / "factory.wf").write_text("#% name: 出厂\nlog \"hi\"\n", encoding="utf-8")
    (system / "workflows" / "_tmp.wf").write_text("log \"skip\"\n", encoding="utf-8")
    return system, local


@pytest.fixture
def dev_resolver(dirs, monkeypatch):
    r = ConfigResolver(system_dir=dirs[0], local_dir=dirs[1], dev_mode=True)
    monkeypatch.setattr(cr, "_resolver", r)
    return r


@pytest.fixture
def user_resolver(dirs, monkeypatch):
    r = ConfigResolver(system_dir=dirs[0], local_dir=dirs[1], dev_mode=False)
    monkeypatch.setattr(cr, "_resolver", r)
    return r


# ─── 纯逻辑 ─────────────────────────────────────────────

class TestScriptId:
    def test_valid(self):
        assert sed.validate_script_id("daily_x1") is None

    @pytest.mark.parametrize("bad", ["", "  ", "_tmp", "1abc", "a-b", "中文", "a.wf"])
    def test_invalid(self, bad):
        assert sed.validate_script_id(bad)

    def test_rel_path(self):
        assert sed.script_rel_path("abc") == "workflows/abc.wf"


class TestTemplate:
    def test_metadata_and_syntax(self):
        text = sed.new_script_text("我的脚本")
        meta = parse_metadata(text)
        assert meta["name"] == "我的脚本"
        assert meta["env"] == ["android", "desktop"]
        assert sed.check_syntax(text) == []


class TestCheckSyntax:
    def test_ok(self):
        assert sed.check_syntax('log "a"\nif $x\n    click $x\nend\n') == []

    def test_reports_line(self):
        problems = sed.check_syntax('log "a"\nclick\n')
        assert len(problems) == 1
        assert "第 2 行" in problems[0]

    def test_transformer_error_unwrapped(self):
        problems = sed.check_syntax('scan [s].[a] as $v by image "x"\n')
        assert problems and "by image" in problems[0] and "VisitError" not in problems[0]


class TestListScripts:
    def test_union_and_layer(self, user_resolver, dirs):
        (dirs[1] / "workflows" / "mine.wf").write_text("log \"m\"\n", encoding="utf-8")
        entries = sed.list_script_files()
        assert [(e.id, e.layer) for e in entries] == [("factory", "system"), ("mine", "local")]

    def test_local_shadow_wins(self, user_resolver, dirs):
        (dirs[1] / "workflows" / "factory.wf").write_text("log \"shadow\"\n", encoding="utf-8")
        entries = sed.list_script_files()
        assert len(entries) == 1 and entries[0].layer == "local"


def _layout(regions=()):
    layout = MagicMock()
    layout.get_canvas.return_value = MagicMock(x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1)
    layout.get_scene_regions.return_value = list(regions)
    layout.get_scene_points.return_value = []
    layout.get_scene_panels.return_value = []
    layout.get_scene_arrows.return_value = []
    return layout


class TestValidateWithLayout:
    def test_trivial_passes(self, dev_resolver, dirs):
        p = dirs[0] / "workflows" / "factory.wf"
        assert sed.validate_with_layout(p, _layout(), {}) == []

    def test_unbound_ref_reported(self, dev_resolver, dirs):
        p = dirs[0] / "workflows" / "bad.wf"
        p.write_text("click [nope].[btn]\n", encoding="utf-8")
        problems = sed.validate_with_layout(p, _layout(), {})
        assert problems and "nope" in problems[0]

    def test_undefined_named_wait_reported(self, dev_resolver, dirs):
        p = dirs[0] / "workflows" / "w.wf"
        p.write_text("wait @no_such\n", encoding="utf-8")
        problems = sed.validate_with_layout(p, _layout(), {})
        assert problems and "no_such" in problems[0]


# ─── 对话框 ─────────────────────────────────────────────

class TestDialog:
    def _dialog(self, qtbot, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
        dlg = sed.ScriptEditorDialog(None)
        qtbot.addWidget(dlg)
        return dlg

    def _answers(self, monkeypatch, *answers):
        from PyQt6.QtWidgets import QInputDialog

        it = iter(answers)
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: next(it))

    def test_lists_and_loads_first(self, qtbot, dev_resolver):
        dlg = self._dialog(qtbot, pytest.MonkeyPatch())
        assert dlg.list.count() == 1
        assert dlg._current.id == "factory"
        assert "出厂" in dlg.editor.toPlainText()
        assert not dlg.btn_save.isEnabled()

    def test_new_creates_file(self, qtbot, dev_resolver, dirs, monkeypatch):
        dlg = self._dialog(qtbot, monkeypatch)
        self._answers(monkeypatch, ("fresh", True), ("新脚本", True))
        dlg._on_new()
        path = dirs[0] / "workflows" / "fresh.wf"
        assert path.exists() and "#% name: 新脚本" in path.read_text(encoding="utf-8")
        assert dlg._current.id == "fresh"
        assert dlg.list.count() == 2
        # 新脚本由发现层自动扫到并默认展示，不需要写任何登记文件
        assert dlg.changed

    def test_new_rejects_underscore_then_accepts(self, qtbot, dev_resolver, dirs, monkeypatch):
        dlg = self._dialog(qtbot, monkeypatch)
        self._answers(monkeypatch, ("_bad", True), ("good", True), ("g", True))
        dlg._on_new()
        assert (dirs[0] / "workflows" / "good.wf").exists()
        assert not (dirs[0] / "workflows" / "_bad.wf").exists()

    def test_edit_save_writes_and_validates(self, qtbot, dev_resolver, dirs, monkeypatch):
        dlg = self._dialog(qtbot, monkeypatch)
        dlg.editor.setPlainText('#% name: 出厂\nlog "changed"')
        assert dlg._dirty and dlg.btn_save.isEnabled()
        dlg._on_save()
        assert (dirs[0] / "workflows" / "factory.wf").read_text(encoding="utf-8") == '#% name: 出厂\nlog "changed"\n'
        assert not dlg._dirty
        assert "已保存" in dlg.lbl_status.text()

    def test_save_reports_syntax_problem(self, qtbot, dev_resolver, monkeypatch):
        dlg = self._dialog(qtbot, monkeypatch)
        dlg.editor.setPlainText("click\n")
        dlg._on_save()
        assert "校验未通过" in dlg.lbl_status.text()

    def test_user_mode_writes_local_and_cannot_delete_factory(self, qtbot, user_resolver, dirs, monkeypatch):
        dlg = self._dialog(qtbot, monkeypatch)
        dlg.editor.setPlainText('log "mine"')
        dlg._on_save()
        assert (dirs[1] / "workflows" / "factory.wf").exists()
        assert dlg._current.layer == "local"
        # 出厂脚本不可删除：只清掉自己的 local 影子，system 原样保留
        dlg._on_delete()
        assert (dirs[1] / "workflows" / "factory.wf").exists()  # 仍在，未被删
        assert sed.list_script_files() != []

    def test_check_button_only_syntax(self, qtbot, dev_resolver, monkeypatch):
        dlg = self._dialog(qtbot, monkeypatch)
        dlg.editor.setPlainText("click [nope].[btn]\n")   # 引用未绑定，但语法对
        dlg._on_check()
        assert "语法检查通过" in dlg.lbl_status.text()
