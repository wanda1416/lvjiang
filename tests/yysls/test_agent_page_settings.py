"""智能调律页的 Agent 选择、导出路径覆盖与下载不改动全局 Agent 默认值。

全局 Agent 设置页（宿主）见 ``tests/ui/settings/test_agent_settings.py``。
"""
import json
from types import SimpleNamespace

from PyQt6.QtWidgets import QWidget

from lvjiang.core.ai import agent_connection
from lvjiang.ui.main.plugin_runtime import PluginRuntime


def test_instance_selection_override_and_download_leave_global_defaults_unchanged(qtbot, tmp_path, monkeypatch):
    from lvjiang import constants
    from lvjiang.apps.yysls.ui.tuning import agent_page
    from lvjiang.core.ai.agent_settings import AgentSettings, AgentTarget

    settings = AgentSettings((AgentTarget("first", "Agent 甲", str(tmp_path / "first.json")),
                              AgentTarget("second", "Agent 乙", str(tmp_path / "second.json"))),
                             "https://example.invalid/download")
    monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(agent_page, "load_agent_settings", lambda: settings)
    monkeypatch.setattr(agent_connection, "settings_path", lambda _root: tmp_path / "connection.json")
    opened = []
    monkeypatch.setattr(agent_page.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    host = QWidget()
    host.plugin_runtime = PluginRuntime(host)
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
        assert set(doc["mcpServers"]) == {"律匠(lvjiang)"}
        assert list(doc["mcpServers"].values())[0]["type"] == "streamable-http"
        assert not (tmp_path / "first.json").exists()
        page.reload_agents()
        assert page.export_path.text() == override
    finally:
        page._shutdown()
