"""全局 Agent 草稿、安装级选择和导出彼此隔离。"""

import yaml

from lvjiang.core.ai.agent_settings import load_agent_settings
from lvjiang.core.config import resolver
from lvjiang.ui.settings.agent import AgentSettingsPage


def test_global_agent_drafts_only_save_agents_node(qtbot, tmp_path, monkeypatch):
    system = tmp_path / "system"
    system.mkdir()
    source = {"other": {"keep": True}, "agents": {"download_agent": {"url": "https://example.invalid/download"},
              "items": [{"key": "example", "name": "Agent 示例", "export_path": str(tmp_path / "mcp.json"),
                         "transport": "streamableHttp"}]}}
    (system / "app.yaml").write_text(yaml.safe_dump(source, allow_unicode=True), encoding="utf-8")
    store = resolver.ConfigResolver(system, tmp_path / "local", dev_mode=False)
    monkeypatch.setattr(resolver, "_resolver", store)
    page = AgentSettingsPage()
    qtbot.addWidget(page)
    assert page.server_name.text() == "律匠(lvjiang)"
    page.server_name.setText("律匠二号(lvjiang-2)")
    page.table.item(0, 0).setText("修改名称")
    page.add_row(name="第二个 Agent", path=str(tmp_path / "second.json"))
    assert load_agent_settings().items[0].name == "Agent 示例"
    assert load_agent_settings().items[0].transport == "streamable-http"
    assert not (tmp_path / "local/app.yaml").exists()
    # 保存时读最新配置，不回滚编辑窗口打开后变化的其他设置。
    resolver.save_app_config_node("other", {"keep": "updated"})
    page.save()
    settings = load_agent_settings()
    assert [item.name for item in settings.items] == ["修改名称", "第二个 Agent"]
    assert settings.server_name == "律匠二号(lvjiang-2)"
    assert settings.items[0].key == "example"
    assert settings.to_dict()["items"][0]["transport"] == "streamable-http"
    assert resolver.load_app_config()["other"] == {"keep": "updated"}
    assert settings.download_url == "https://example.invalid/download"
