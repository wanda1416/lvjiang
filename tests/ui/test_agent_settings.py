"""全局 Agent 草稿、安装级选择和导出彼此隔离。"""
import json
from types import SimpleNamespace

import yaml
from PyQt6.QtWidgets import QWidget

from lvjiang.core import agent_connection
from lvjiang.core.agent_settings import load_agent_settings
from lvjiang.core.config import resolver
from lvjiang.ui.agent_settings import AgentSettingsPage


def test_global_agent_drafts_only_save_agents_node(qtbot, tmp_path, monkeypatch):
    system = tmp_path / "system"
    system.mkdir()
    source = {"other": {"keep": True}, "agents": {"download_agent": {"url": "https://example.invalid/download"},
              "items": [{"key": "example", "name": "Agent 示例", "export_path": str(tmp_path / "mcp.json")}]}}
    (system / "app.yaml").write_text(yaml.safe_dump(source, allow_unicode=True), encoding="utf-8")
    store = resolver.ConfigResolver(system, tmp_path / "local", dev_mode=False)
    monkeypatch.setattr(resolver, "_resolver", store)
    page = AgentSettingsPage()
    qtbot.addWidget(page)
    page.table.item(0, 0).setText("修改名称")
    page.add_row(name="第二个 Agent", path=str(tmp_path / "second.json"))
    assert load_agent_settings().items[0].name == "Agent 示例"
    assert not (tmp_path / "local/app.yaml").exists()
    # 保存时读最新配置，不回滚编辑窗口打开后变化的其他设置。
    resolver.save_app_config_node("other", {"keep": "updated"})
    page.save()
    settings = load_agent_settings()
    assert [item.name for item in settings.items] == ["修改名称", "第二个 Agent"]
    assert settings.items[0].key == "example"
    assert resolver.load_app_config()["other"] == {"keep": "updated"}
    assert settings.download_url == "https://example.invalid/download"


def test_instance_selection_override_and_download_leave_global_defaults_unchanged(qtbot, tmp_path, monkeypatch):
    from lvjiang import constants
    from lvjiang.apps.yysls.ui.tuning import agent_page
    from lvjiang.core.agent_settings import AgentSettings, AgentTarget

    settings = AgentSettings((AgentTarget("first", "Agent 甲", str(tmp_path / "first.json")),
                              AgentTarget("second", "Agent 乙", str(tmp_path / "second.json"))),
                             "https://example.invalid/download")
    monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(agent_page, "load_agent_settings", lambda: settings)
    monkeypatch.setattr(agent_connection, "settings_path", lambda _root: tmp_path / "connection.json")
    opened = []
    monkeypatch.setattr(agent_page.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    host = QWidget()
    qtbot.addWidget(host)
    page = agent_page.AgentTuningPage(host)
    qtbot.addWidget(page)
    try:
        assert not (tmp_path / "connection.json").exists()  # 初始展示不保存选择。
        page.agent.setCurrentIndex(1)
        override = str(tmp_path / "custom.json")
        page.export_path.setText(override)
        page._save_export_path()
        assert page.connection.agent_key == "second"
        assert page.connection.export_paths == {"second": override}
        assert settings.items[1].export_path == str(tmp_path / "second.json")
        page._download_agent()
        assert opened == [settings.download_url]
        page.server = SimpleNamespace(connection_config=lambda: {"mcpServers": {"lvjiang": {
            "url": "http://127.0.0.1:18765/mcp", "headers": {"Authorization": "Bearer test-only"}}}},
                                      running=False, stop=lambda: None)
        page._export()
        doc = json.loads((tmp_path / "custom.json").read_text(encoding="utf-8"))
        assert list(doc["mcpServers"].values())[0]["type"] == "streamable-http"
        assert not (tmp_path / "first.json").exists()
        page.reload_agents()
        assert page.export_path.text() == override
    finally:
        page._shutdown()
