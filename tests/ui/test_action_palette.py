"""指令目录 + 指令面板

- 目录（纯逻辑）：每条指令的默认渲染都能被 DSL 解析；槽位类型渲染规则；可选槽位；搜索
- 面板（qtbot）：选指令建表单、场景变了区域下拉跟着变、取画布填槽位、预览随输入刷新、
  插入发出渲染文本
- 对话框 insert_statement：光标行有内容时先换行，多行模板保持缩进
"""
from __future__ import annotations

import pytest

from lvjiang.ui import action_palette as ap
from lvjiang.workflows import action_catalog as cat
from lvjiang.workflows.grammar import parse_text

# ─── 目录 ───────────────────────────────────────────────


def _defaults_for(action: cat.Action) -> dict[str, str]:
    """给每个必填槽位一个类型合法的样例值"""
    sample = {
        "scene": "game_main_page", "region": "menu", "coord": "(0.5, 0.5)", "rect": "(0.1, 0.2, 0.3, 0.4)",
        "color": "#2ecc71", "delay": "page_refresh", "template": "icon", "text": "文字", "raw": "1",
        "var": "v", "choice": "",
    }
    out = {}
    for s in action.slots:
        if s.default:
            out[s.key] = s.default
        elif not s.optional:
            out[s.key] = sample[s.kind]
            if s.kind == "choice" and s.choices:
                out[s.key] = s.choices[0]
    return out


@pytest.mark.parametrize("action", cat.ACTIONS, ids=[a.key for a in cat.ACTIONS])
def test_every_action_renders_parseable_dsl(action):
    text = cat.render(action, _defaults_for(action))
    # 块模板里的空行是留给用户填的，解析器对空行无感
    parse_text(text + "\n")


def test_render_slot_rules():
    assert cat.render_slot(cat.Slot("s", "s", "scene"), " [lobby] ") == "[lobby]"
    assert cat.render_slot(cat.Slot("d", "d", "delay"), "page_refresh") == "@page_refresh"
    assert cat.render_slot(cat.Slot("d", "d", "delay"), "@x") == "@x"
    assert cat.render_slot(cat.Slot("v", "v", "var"), "$hit") == "$hit"
    assert cat.render_slot(cat.Slot("v", "v", "var"), "hit") == "$hit"
    assert cat.render_slot(cat.Slot("t", "t", "text"), '"a"') == '"a"'
    assert cat.render_slot(cat.Slot("t", "t", "text"), "a") == '"a"'
    assert cat.render_slot(cat.Slot("c", "c", "color"), "2ecc71") == '"#2ecc71"'
    assert cat.render_slot(cat.Slot("p", "p", "coord"), "0.5,0.25") == "(0.5, 0.25)"
    assert cat.render_slot(cat.Slot("r", "r", "rect"), "(1, 2, 3, 4)") == "(1, 2, 3, 4)"
    with pytest.raises(cat.RenderError):
        cat.render_slot(cat.Slot("p", "p", "coord"), "(1, 2, 3)")
    with pytest.raises(cat.RenderError):
        cat.render_slot(cat.Slot("c", "c", "color"), "#fff")
    with pytest.raises(cat.RenderError, match="未填写"):
        cat.render_slot(cat.Slot("s", "s", "scene"), "")


def test_optional_slot_wrap():
    a = cat.get_action("click_region")
    assert cat.render(a, {"scene": "s", "region": "r"}) == "click [s].[r]"
    assert cat.render(a, {"scene": "s", "region": "r", "wait": "page_refresh"}) == "click [s].[r] after wait @page_refresh"


def test_find_image_full():
    a = cat.get_action("find_image")
    out = cat.render(a, {"area": "[map].[canvas]", "var": "icon", "tpl": "extract_icon", "conf": "0.85"})
    assert out == 'find [map].[canvas] as $icon by image "extract_icon" where confidence >= 0.85'
    parse_text(out + "\n")


def test_search_and_categories():
    assert [a.key for a in cat.search("click")][:3] == ["click_region", "click_coord", "click_var"]
    assert cat.search("找图")[0].key == "find_image"
    assert len(cat.search("")) == len(cat.ACTIONS)
    assert cat.categories()[0] == cat.CAT_INTERACT
    with pytest.raises(ValueError):
        cat.Slot("x", "x", "nope")


# ─── 面板 ───────────────────────────────────────────────

def _providers(pick=None, rect=None):
    regions = {"lobby": [("start_btn", "开始 (区域)"), ("tab_bar", "页签 (区域)")], "map": [("canvas", "画布 (区域)")]}
    return ap.PaletteProviders(
        scenes=lambda: [("lobby", "大厅"), ("map", "地图")],
        regions=lambda s: regions.get(s, []),
        delays=lambda: [("page_refresh", "翻页"), ("step_interval", "步间")],
        templates=lambda: ["extract_icon"],
        last_coord=lambda: pick and f"({pick[0]:.4f}, {pick[1]:.4f})",
        last_rect=lambda: rect and "(%.4f, %.4f, %.4f, %.4f)" % rect,
        last_color=lambda: pick and '"#%02x%02x%02x"' % pick[2:],
    )


class TestPalette:
    def _palette(self, qtbot, providers=None):
        p = ap.ActionPalette(providers or _providers())
        qtbot.addWidget(p)
        return p

    def test_list_has_headers_and_actions(self, qtbot):
        p = self._palette(qtbot)
        texts = [p.list.item(i).text() for i in range(p.list.count())]
        assert texts[0].startswith("—") and any("点击区域" in t for t in texts)
        p.search.setText("找图")
        assert p.list.count() == 2 and "找图" in p.list.item(1).text()

    def test_click_region_form_and_region_follows_scene(self, qtbot):
        p = self._palette(qtbot)
        p.select_action("click_region")
        assert set(p._fields) == {"scene", "region", "wait"}
        p.set_value("scene", "lobby")
        region_cb = p._fields["region"]
        assert [region_cb.itemData(i) for i in range(region_cb.count())] == ["start_btn", "tab_bar"]
        p.set_value("region", "start_btn")
        assert p.rendered() == "click [lobby].[start_btn]"
        p.set_value("wait", "page_refresh")
        assert p.rendered() == "click [lobby].[start_btn] after wait @page_refresh"
        assert p.btn_insert.isEnabled()
        p.set_value("scene", "map")
        assert [region_cb.itemData(i) for i in range(region_cb.count())] == ["canvas"]

    def test_missing_required_disables_insert(self, qtbot):
        p = self._palette(qtbot)
        p.select_action("click_region")
        assert p.rendered() is None and not p.btn_insert.isEnabled()
        assert "未填写" in p.preview.text()

    def test_canvas_values_prefill_and_button(self, qtbot):
        p = self._palette(qtbot, _providers(pick=(0.25, 0.5, 46, 204, 113), rect=(0.1, 0.2, 0.3, 0.4)))
        p.select_action("color_ratio")
        assert p.rendered() == '$ratio = color_ratio((0.1000, 0.2000, 0.3000, 0.4000), "#2ecc71", 40)'
        p.select_action("pixel")
        assert p.rendered() == "$rgb = pixel((0.2500, 0.5000))"

    def test_take_canvas_button_fills_later(self, qtbot):
        state = {"pick": None}
        prov = _providers()
        prov.last_coord = lambda: state["pick"]
        p = self._palette(qtbot, prov)
        p.select_action("click_coord")
        assert p.rendered() is None
        state["pick"] = "(0.3000, 0.7000)"
        host = p._fields["coord"]
        host.findChild(type(p.btn_insert)).click()   # 「取画布」按钮
        assert p.rendered() == "click (0.3000, 0.7000)"

    def test_insert_emits_rendered(self, qtbot):
        p = self._palette(qtbot)
        p.select_action("wait_named")
        p.set_value("delay", "step_interval")
        with qtbot.waitSignal(p.insert_requested, timeout=1000) as blocker:
            p.btn_insert.click()
        assert blocker.args == ["wait @step_interval"]

    def test_block_template_renders_multiline(self, qtbot):
        p = self._palette(qtbot)
        p.select_action("if_else")
        p.set_value("cond", "$hit")
        assert p.rendered() == "if $hit\n    \nelse\n    \nend"


# ─── 对话框插入 ─────────────────────────────────────────

class TestInsertStatement:
    def _dialog(self, qtbot, monkeypatch, tmp_path):
        from PyQt6.QtWidgets import QMessageBox

        from lvjiang.core.config import resolver as cr
        from lvjiang.core.config.resolver import ConfigResolver
        from lvjiang.ui.script_editor_dialog import ScriptEditorDialog

        # 对话框带未保存修改关闭时会弹「放弃修改？」模态框——qtbot 收尾关窗会卡在那等人点，
        # 必须打桩（与 test_script_editor_dialog 同一做法）
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        (tmp_path / "s" / "workflows").mkdir(parents=True)
        (tmp_path / "l").mkdir()
        monkeypatch.setattr(cr, "_resolver", ConfigResolver(system_dir=tmp_path / "s", local_dir=tmp_path / "l", dev_mode=True))
        dlg = ScriptEditorDialog(None)
        qtbot.addWidget(dlg)
        return dlg

    def test_insert_on_empty_line(self, qtbot, monkeypatch, tmp_path):
        dlg = self._dialog(qtbot, monkeypatch, tmp_path)
        dlg.editor.setPlainText("")
        dlg.insert_statement("click [a].[b]")
        assert dlg.editor.toPlainText() == "click [a].[b]\n"

    def test_insert_mid_line_breaks_first_and_keeps_indent(self, qtbot, monkeypatch, tmp_path):
        dlg = self._dialog(qtbot, monkeypatch, tmp_path)
        dlg.editor.setPlainText("if $x\n    log \"a\"\nend\n")
        cursor = dlg.editor.textCursor()
        cursor.setPosition(len("if $x\n    log \"a\""))   # 第 2 行末尾
        dlg.editor.setTextCursor(cursor)
        dlg.insert_statement("loop 2\n    \nend")
        assert dlg.editor.toPlainText() == 'if $x\n    log "a"\n    loop 2\n        \n    end\nend\n'

    def test_palette_insert_goes_to_editor(self, qtbot, monkeypatch, tmp_path):
        dlg = self._dialog(qtbot, monkeypatch, tmp_path)
        dlg.editor.setPlainText("")
        dlg.palette.select_action("screenshot")
        dlg.palette.btn_insert.click()
        assert dlg.editor.toPlainText() == "screenshot\n"
