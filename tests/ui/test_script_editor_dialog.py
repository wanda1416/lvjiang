"""脚本编辑对话框测试

1. 纯逻辑：id 校验、模板、语法检查、目录树合并视图、落盘校验
2. 对话框（qtbot，输入框/确认框打桩）：新建 → 文件落盘且进树；改文本保存；删除；
   出厂只读 + 复制到本地
resolver 指向 tmp 的 system/local 双层目录，不碰真实配置。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt

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
    (system / "workflows" / "subcall").mkdir()
    (system / "workflows" / "subcall" / "nav.wf").write_text("log \"nav\"\n", encoding="utf-8")
    return system, local


def _rels(dlg) -> list[str]:
    """树里的文件节点（rel_path），按树序"""
    out = []

    def walk(item):
        for i in range(item.childCount()):
            child = item.child(i)
            rel = child.data(0, Qt.ItemDataRole.UserRole)
            if rel:
                out.append(rel)
            walk(child)

    walk(dlg.tree.invisibleRootItem())
    return out


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
        assert sed.wf_rel_path("subcall/nav.wf") == "workflows/subcall/nav.wf"
        assert sed.join_rel("", "abc") == "abc.wf"
        assert sed.join_rel("subcall", "abc") == "subcall/abc.wf"


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
        assert [(e.rel_path, e.layer) for e in entries] == [
            ("_tmp.wf", "system"),
            ("factory.wf", "system"),
            ("mine.wf", "local"),
            ("subcall/nav.wf", "system"),
        ]

    def test_local_shadow_wins(self, user_resolver, dirs):
        (dirs[1] / "workflows" / "factory.wf").write_text("log \"shadow\"\n", encoding="utf-8")
        entries = sed.list_script_files()
        factory = [e for e in entries if e.rel_path == "factory.wf"]
        assert len(factory) == 1                      # 不重复出现
        assert factory[0].layer == "local" and factory[0].file.overrides_system

    def test_subdir_file_reachable(self, user_resolver):
        """子目录里的 .wf 也能被编辑器寻址——旧的扁平列表根本打不开它们"""
        rels = [e.rel_path for e in sed.list_script_files()]
        assert "subcall/nav.wf" in rels

    def test_no_filtering(self, user_resolver):
        """_ 前缀是发现层的事，树上照实展示"""
        assert "_tmp.wf" in [e.rel_path for e in sed.list_script_files()]


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

    def _select(self, dlg, rel: str):
        dlg.tree.setCurrentItem(dlg._file_items[rel])

    def test_tree_shows_everything_by_path(self, qtbot, dev_resolver):
        dlg = self._dialog(qtbot, pytest.MonkeyPatch())
        # 目录在前、文件在后，各自按路径排序
        assert _rels(dlg) == ["subcall/nav.wf", "_tmp.wf", "factory.wf"]
        # 默认选中 = 根目录下第一个文件，不钻进子目录；不按文件名做特殊跳过
        assert dlg._current.rel_path == "_tmp.wf"
        assert not dlg.btn_save.isEnabled()

    def test_tree_nests_directories_and_shows_filename_only(self, qtbot, dev_resolver):
        dlg = self._dialog(qtbot, pytest.MonkeyPatch())
        top = [dlg.tree.topLevelItem(i).text(0) for i in range(dlg.tree.topLevelItemCount())]
        assert "subcall" in top                          # 目录成节点
        nav = dlg._file_items["subcall/nav.wf"]
        assert nav.text(0) == "nav.wf"                   # 只写文件名，不带层标记
        assert nav.parent().text(0) == "subcall"

    def test_opens_subdirectory_file(self, qtbot, dev_resolver):
        dlg = self._dialog(qtbot, pytest.MonkeyPatch())
        self._select(dlg, "subcall/nav.wf")
        assert dlg._current.rel_path == "subcall/nav.wf"
        assert "nav" in dlg.editor.toPlainText()

    def test_directories_start_collapsed(self, qtbot, dev_resolver):
        dlg = self._dialog(qtbot, pytest.MonkeyPatch())
        assert not dlg._dir_items["subcall"].isExpanded()

    def test_selecting_subdir_file_expands_to_reveal_it(self, qtbot, dev_resolver, dirs, monkeypatch):
        """光标落在收起的目录里，用户看不到自己在编辑哪一个"""
        dlg = self._dialog(qtbot, monkeypatch)
        self._select(dlg, "subcall/nav.wf")
        dlg.editor.setPlainText('log "x"\n')
        dlg._on_save()                                  # 触发重建
        assert dlg._current.rel_path == "subcall/nav.wf"
        assert dlg._dir_items["subcall"].isExpanded()

    def test_reload_keeps_expansion_state(self, qtbot, dev_resolver, monkeypatch):
        dlg = self._dialog(qtbot, monkeypatch)
        dlg._dir_items["subcall"].setExpanded(True)
        dlg._reload_list()
        assert dlg._dir_items["subcall"].isExpanded()

    def test_directory_node_not_selectable(self, qtbot, dev_resolver):
        dlg = self._dialog(qtbot, pytest.MonkeyPatch())
        node = next(dlg.tree.topLevelItem(i) for i in range(dlg.tree.topLevelItemCount())
                    if dlg.tree.topLevelItem(i).text(0) == "subcall")
        assert not (node.flags() & Qt.ItemFlag.ItemIsSelectable)
        assert node.data(0, Qt.ItemDataRole.UserRole) is None

    def test_new_creates_file(self, qtbot, dev_resolver, dirs, monkeypatch):
        dlg = self._dialog(qtbot, monkeypatch)
        self._answers(monkeypatch, ("fresh", True), ("新脚本", True))
        dlg._on_new()
        path = dirs[0] / "workflows" / "fresh.wf"
        assert path.exists() and "#% name: 新脚本" in path.read_text(encoding="utf-8")
        assert dlg._current.rel_path == "fresh.wf"
        assert "fresh.wf" in _rels(dlg)
        # 新脚本由发现层自动扫到并默认展示，不需要写任何登记文件
        assert dlg.changed

    def test_new_lands_in_selected_directory(self, qtbot, dev_resolver, dirs, monkeypatch):
        """在子目录里新建，落在那个子目录，不会莫名跑回顶层"""
        dlg = self._dialog(qtbot, monkeypatch)
        self._select(dlg, "subcall/nav.wf")
        self._answers(monkeypatch, ("helper", True), ("助手", True))
        dlg._on_new()
        assert (dirs[0] / "workflows" / "subcall" / "helper.wf").exists()
        assert dlg._current.rel_path == "subcall/helper.wf"

    def test_new_rejects_underscore_then_accepts(self, qtbot, dev_resolver, dirs, monkeypatch):
        dlg = self._dialog(qtbot, monkeypatch)
        self._answers(monkeypatch, ("_bad", True), ("good", True), ("g", True))
        dlg._on_new()
        assert (dirs[0] / "workflows" / "good.wf").exists()
        assert not (dirs[0] / "workflows" / "_bad.wf").exists()

    def test_edit_save_writes_and_validates(self, qtbot, dev_resolver, dirs, monkeypatch):
        dlg = self._dialog(qtbot, monkeypatch)
        self._select(dlg, "factory.wf")
        dlg.editor.setPlainText('#% name: 出厂\nlog "changed"')
        assert dlg._dirty and dlg.btn_save.isEnabled()
        dlg._on_save()
        assert (dirs[0] / "workflows" / "factory.wf").read_text(encoding="utf-8") == '#% name: 出厂\nlog "changed"\n'
        assert not dlg._dirty
        assert "已保存" in dlg.lbl_status.text()

    def test_save_reports_syntax_problem(self, qtbot, dev_resolver, monkeypatch):
        dlg = self._dialog(qtbot, monkeypatch)
        self._select(dlg, "factory.wf")
        dlg.editor.setPlainText("click\n")
        dlg._on_save()
        assert "校验未通过" in dlg.lbl_status.text()

    # ─── 出厂只读 / 复制到本地 ───────────────────────

    def test_user_mode_factory_is_read_only(self, qtbot, user_resolver, monkeypatch):
        dlg = self._dialog(qtbot, monkeypatch)
        self._select(dlg, "factory.wf")
        assert dlg.editor.isReadOnly()
        assert not dlg.btn_delete.isEnabled()
        assert "只读" in dlg.lbl_status.text()

    def test_user_mode_save_on_factory_refused(self, qtbot, user_resolver, dirs, monkeypatch):
        """只读拦在保存这一步：不能悄悄给出厂文件生成 local 影子"""
        dlg = self._dialog(qtbot, monkeypatch)
        self._select(dlg, "factory.wf")
        dlg.editor.setReadOnly(False)      # 绕过 UI 置灰，直接打服务端那道闸
        dlg.editor.setPlainText('log "mine"')
        dlg._on_save()
        assert not (dirs[1] / "workflows" / "factory.wf").exists()
        assert "只读" in dlg.lbl_status.text()

    def test_copy_to_local_then_editable(self, qtbot, user_resolver, dirs, monkeypatch):
        dlg = self._dialog(qtbot, monkeypatch)
        self._select(dlg, "factory.wf")
        dlg._copy_to_local(dlg._current)
        assert (dirs[1] / "workflows" / "factory.wf").exists()
        assert dlg._current.layer == "local" and dlg._current.file.overrides_system
        assert not dlg.editor.isReadOnly()
        dlg.editor.setPlainText('#% name: 出厂\nlog "mine"')
        dlg._on_save()
        assert 'log "mine"' in (dirs[1] / "workflows" / "factory.wf").read_text(encoding="utf-8")
        assert (dirs[0] / "workflows" / "factory.wf").read_text(encoding="utf-8") == \
            '#% name: 出厂\nlog "hi"\n'                 # system 原样未动

    def test_copy_to_local_works_in_subdir(self, qtbot, user_resolver, dirs, monkeypatch):
        dlg = self._dialog(qtbot, monkeypatch)
        self._select(dlg, "subcall/nav.wf")
        dlg._copy_to_local(dlg._current)
        assert (dirs[1] / "workflows" / "subcall" / "nav.wf").exists()
        assert _rels(dlg).count("subcall/nav.wf") == 1   # 合并视图仍只有一个节点

    def test_shadow_cannot_be_deleted(self, qtbot, user_resolver, dirs, monkeypatch):
        """出厂脚本的本地副本删不掉——删了这个实体就没了，走还原那条路"""
        dlg = self._dialog(qtbot, monkeypatch)
        self._select(dlg, "factory.wf")
        dlg._copy_to_local(dlg._current)
        assert not dlg.btn_delete.isEnabled()
        assert "还原为出厂" in dlg.btn_delete.toolTip()
        dlg._on_delete()
        assert (dirs[1] / "workflows" / "factory.wf").exists()   # 未被删

    def test_revert_to_system_drops_shadow(self, qtbot, user_resolver, dirs, monkeypatch):
        """「复制到本地」必须有回头路，否则改坏了既删不掉也回不去"""
        dlg = self._dialog(qtbot, monkeypatch)
        self._select(dlg, "factory.wf")
        dlg._copy_to_local(dlg._current)
        dlg.editor.setPlainText('#% name: 出厂\nlog "broken"')
        dlg._on_save()
        dlg._revert_to_system(dlg._current)
        assert not (dirs[1] / "workflows" / "factory.wf").exists()
        assert dlg._current.layer == "system" and dlg.editor.isReadOnly()
        assert 'log "hi"' in dlg.editor.toPlainText()             # 读回出厂内容
        assert "factory.wf" in _rels(dlg)

    def test_local_only_script_is_deletable(self, qtbot, user_resolver, dirs, monkeypatch):
        """纯本地脚本没有出厂版本，正常可删"""
        (dirs[1] / "workflows" / "mine.wf").write_text('log "m"\n', encoding="utf-8")
        dlg = self._dialog(qtbot, monkeypatch)
        self._select(dlg, "mine.wf")
        assert dlg.btn_delete.isEnabled()
        dlg._on_delete()
        assert not (dirs[1] / "workflows" / "mine.wf").exists()
        assert "mine.wf" not in _rels(dlg)

    def test_origin_label_follows_layer_not_writability(self, qtbot, dev_resolver):
        """开发模式下 system 可写，但它仍是出厂内容，标签不能写成「本地」"""
        dlg = self._dialog(qtbot, pytest.MonkeyPatch())
        self._select(dlg, "factory.wf")
        assert dlg.lbl_layer.text().startswith("出厂")
        assert not dlg.editor.isReadOnly()          # 开发模式确实可写

    def test_unlock_keeps_factory_read_only(self, qtbot, user_resolver, monkeypatch):
        """调试跑完解锁编辑区，不能顺手把只读的出厂脚本也放开"""
        dlg = self._dialog(qtbot, monkeypatch)
        self._select(dlg, "factory.wf")
        dlg.set_locked(True)
        dlg.set_locked(False)
        assert dlg.editor.isReadOnly()
        assert dlg.tree.isEnabled()

    def test_check_button_only_syntax(self, qtbot, dev_resolver, monkeypatch):
        dlg = self._dialog(qtbot, monkeypatch)
        self._select(dlg, "factory.wf")
        dlg.editor.setPlainText("click [nope].[btn]\n")   # 引用未绑定，但语法对
        dlg._on_check()
        assert "语法检查通过" in dlg.lbl_status.text()
