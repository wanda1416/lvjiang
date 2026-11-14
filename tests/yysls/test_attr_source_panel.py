"""心法编辑页使用领域分组与展示名称，避免把数据来源当成心法。"""

from lvjiang.apps.yysls.core.attr_model import AttrModelManager
from lvjiang.apps.yysls.ui.game_settings import attr_source_panel as module


def test_inner_way_group_and_summary_label_follow_domain_fields(
    qtbot, tmp_path, monkeypatch,
):
    (tmp_path / "inner_way.yaml").write_text(
        "kind: inner_way\nentries:\n"
        "  source-row-1:\n"
        "    group: 甲\n    tier: 6\n"
        "    label: 甲·六重（满重累计，重数归属待补）\n"
        "    stats: {min_outer: 40}\n",
        encoding="utf-8",
    )
    manager = AttrModelManager(tmp_path)
    monkeypatch.setattr(module, "get_attr_model_manager", lambda: manager)
    panel = module.AttrSourcePanel(("inner_way",))
    qtbot.addWidget(panel)

    assert panel._list.count() == 1
    assert panel._list.item(0).text().startswith("甲 ")
    assert panel._table.item(0, 0).text() == "六重（满重累计，重数归属待补）"
    panel._search.setText("甲")
    assert panel._list.count() == 1
