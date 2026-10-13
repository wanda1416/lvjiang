"""手机与无界面执行端必须能导入公共插件契约，不依赖 Qt。"""
import os
import subprocess
import sys
from pathlib import Path


def test_public_plugin_contracts_import_without_qt(tmp_path):
    environment = dict(os.environ, LVJIANG_ROOT=str(tmp_path))
    environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.modules['PyQt6'] = None; "
         "from lvjiang.core.app_events import AppEvent; "
         "from lvjiang.core.plugin_runtime import PluginRuntimePort; "
         "assert AppEvent('sample', 'changed').app_id == 'sample'"],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr
