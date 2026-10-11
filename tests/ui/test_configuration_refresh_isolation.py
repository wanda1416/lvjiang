"""官方文件已安装后，一个页面刷新失败不妨碍其它页面刷新或误报安装失败。"""

from types import SimpleNamespace

from lvjiang.ui.main.startup_ops import StartupOpsMixin


def test_failed_workflow_refresh_does_not_block_other_views():
    refreshed = []
    messages = []
    def broken():
        raise ValueError("一个任务配置不可读取")
    host = SimpleNamespace(
        _load_workflow_configs=broken,
        _refresh_reference_space_combo=lambda: refreshed.append("references"),
        _refresh_layout_combo=lambda key: refreshed.append(key),
        layout_combo=SimpleNamespace(currentData=lambda: "android"),
        statusBar=lambda: SimpleNamespace(showMessage=lambda message, timeout: messages.append(message)),
    )
    StartupOpsMixin._refresh_installed_configuration(host, ("workflows/broken.wf", "references/new.png", "layouts.yaml"))
    assert refreshed == ["references", "android"]
    assert "已更新" in messages[0] and "刷新失败" in messages[0]
