"""双击带上限的单元格时，编辑的必须是值本身而不是 "X/Y" 显示串。

线上现象：note 列显示 2/7，双击直接在 "2/7" 上编辑，回车后整串被当成文本
存了回去，值变成 "2/7"。note 的写入侧不做数值解析，所以损坏是静默的。

根因有两个，都在 _on_cell_double_clicked：
1. 剥离 /cap 的前置判断只认 regen / quota，stock 和 note 同样会显示 X/Y
   却被漏掉；
2. 取 KeyDef 时用了 column_keys[col]，但第 0 列是角色名、数据列从 1 开始，
   实际取到的是右边一列的定义（_on_item_changed 用的是 col-1）。

修法是改用 formatter 自身（关掉 show_cap 再跑一遍）求"纯值"，而不是对显示
文本 split("/")——note 存自由文本，"甲/乙" 这种备注按 split 会被截成 "甲"。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem

import lvjiang.apps.yysls.ui.profile.cell_editing as cell_editing
from lvjiang.apps.yysls.config.profile_models import (
    MODEL_NOTE,
    MODEL_QUOTA,
    MODEL_STOCK,
)
from lvjiang.apps.yysls.ui.profile.cell_editing import ProfileCellEditingMixin

_GROUP = "G"
_COLUMNS = ["n", "s", "q"]


@pytest.fixture
def cap_env(tmp_path, monkeypatch):
    """三种会显示 X/Y 的模型各一列，cap 都是 7。"""
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    import lvjiang.apps.yysls.config.user_profile as profile_config
    import lvjiang.apps.yysls.core.profile_engine.profile_db as profile_db

    profile_config._config = None
    profile_config._PROFILE_PATH = session_dir / "profile.yaml"
    profile_config._PROFILE_PATH.write_text(
        yaml.dump(
            {
                "note": [{"key": "n", "label": "备注", "cap": 7, "show_cap": True}],
                "stock": [{"key": "s", "label": "存量", "cap": 7, "show_cap": True}],
                "quota": [{"key": "q", "label": "配额", "cap": 7,
                           "show_cap": True, "period": "week"}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    profile_db._db = None
    profile_db._DB_PATH = session_dir / "profile.db"

    monkeypatch.setattr(cell_editing, "get_groups",
                        lambda: {_GROUP: {"columns": list(_COLUMNS)}})

    yield SimpleNamespace(username="u")

    profile_config._config = None
    profile_db._db = None


class _Host(ProfileCellEditingMixin):
    """只带双击/回写两条路径依赖的最小宿主，避免构造整棵控件树。"""

    def __init__(self, table: QTableWidget):
        self._tables = {_GROUP: table}
        self._loading = False
        self._editing_cap_cell = False

    def _load_user_data(self, user_name: str) -> dict:
        from lvjiang.apps.yysls.core.profile_engine.profile_db import db_read_all
        return db_read_all(user_name)


def _seed(env) -> None:
    from lvjiang.apps.yysls.core.profile_engine.profile_ops import profile_action

    profile_action(env.username, "n", model_type=MODEL_NOTE, set_value="2", source="")
    profile_action(env.username, "s", model_type=MODEL_STOCK, set_value=2, source="")
    profile_action(env.username, "q", model_type=MODEL_QUOTA, set_value=2, source="")


def _build_table(env, qtbot) -> QTableWidget:
    """按总览的真实列布局建表：第 0 列角色名，之后依次是三列数据。"""
    from lvjiang.apps.yysls.config.user_profile import get_profile_config
    from lvjiang.apps.yysls.core.profile_engine.profile_db import db_read_all
    from lvjiang.apps.yysls.ui.profile.cell_formatting import format_profile_cell

    config = get_profile_config()
    data = db_read_all(env.username)

    table = QTableWidget(1, len(_COLUMNS) + 1)
    qtbot.addWidget(table)
    table.setItem(0, 0, QTableWidgetItem(env.username))
    for i, key in enumerate(_COLUMNS):
        model_type = config.get_model_type(key)
        kd = config.get_key(key, model_type=model_type)
        text, _style = format_profile_cell(kd, model_type, data)
        table.setItem(0, i + 1, QTableWidgetItem(text))
    return table


def _stored_note(env) -> str:
    from lvjiang.apps.yysls.core.profile_engine.profile_db import db_read_all
    return db_read_all(env.username)[MODEL_NOTE]["n"].get("value_text")


class TestDoubleClickStripsCapSuffix:
    def test_all_capped_models_show_suffix_first(self, cap_env, qtbot):
        _seed(cap_env)
        table = _build_table(cap_env, qtbot)
        assert [table.item(0, c).text() for c in (1, 2, 3)] == ["2/7", "2/7", "2/7"]

    @pytest.mark.parametrize("col", [1, 2, 3])
    def test_double_click_leaves_plain_value(self, cap_env, qtbot, col):
        """note / stock / quota 三列双击后编辑框里都应该只剩 2。"""
        _seed(cap_env)
        table = _build_table(cap_env, qtbot)
        host = _Host(table)

        host._on_cell_double_clicked(0, col, _GROUP)

        assert table.item(0, col).text() == "2", "双击后应剥掉 /7 再进入编辑"

    def test_name_column_untouched(self, cap_env, qtbot):
        """第 0 列是角色名，不该被当成数据列去剥离。"""
        _seed(cap_env)
        table = _build_table(cap_env, qtbot)
        host = _Host(table)

        host._on_cell_double_clicked(0, 0, _GROUP)

        assert table.item(0, 0).text() == cap_env.username


class TestNoteRoundTripNotCorrupted:
    """本次 bug 的正题：双击后原样回车，值不能变成 "2/7"。"""

    def test_commit_without_edit_keeps_value(self, cap_env, qtbot):
        _seed(cap_env)
        table = _build_table(cap_env, qtbot)
        host = _Host(table)

        host._on_cell_double_clicked(0, 1, _GROUP)
        host._on_item_changed(table.item(0, 1), _GROUP)

        assert _stored_note(cap_env) == "2", "原样提交不该把显示串写回去"

    def test_editing_to_new_number_stores_number(self, cap_env, qtbot):
        _seed(cap_env)
        table = _build_table(cap_env, qtbot)
        host = _Host(table)

        host._on_cell_double_clicked(0, 1, _GROUP)
        table.item(0, 1).setText("5")
        host._on_item_changed(table.item(0, 1), _GROUP)

        assert _stored_note(cap_env) == "5"


class TestNoteTextContainingSlash:
    """note 是自由文本，内容里本来就可能有 /，不能被当成 cap 后缀剥掉。"""

    def test_slash_text_survives_double_click(self, cap_env, qtbot):
        from lvjiang.apps.yysls.core.profile_engine.profile_ops import profile_action

        _seed(cap_env)
        profile_action(cap_env.username, "n", model_type=MODEL_NOTE,
                       set_value="甲/乙", source="")
        table = _build_table(cap_env, qtbot)
        host = _Host(table)

        assert table.item(0, 1).text() == "甲/乙", "非数字不显示上限"
        host._on_cell_double_clicked(0, 1, _GROUP)
        assert table.item(0, 1).text() == "甲/乙", "内容里的 / 不该被剥掉"

        host._on_item_changed(table.item(0, 1), _GROUP)
        assert _stored_note(cap_env) == "甲/乙"
