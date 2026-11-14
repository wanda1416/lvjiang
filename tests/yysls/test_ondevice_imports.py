"""yysls 的设备端入口不得依赖 APK 未分发的桌面 Qt 运行库。

宿主设备端入口与按名装配插件的链路由 ``tests/core/ondevice/test_ondevice_imports.py``
覆盖；这里验证本插件的设备端模块与设备设置适配器。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path


def test_yysls_ondevice_modules_import_without_pyqt6(tmp_path) -> None:
    shutil.copytree(Path(__file__).parents[2] / "config/system", tmp_path / "config/system")
    script = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class BlockPyQt6(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "PyQt6" or fullname.startswith("PyQt6."):
                    raise ModuleNotFoundError(
                        "No module named 'PyQt6'", name="PyQt6"
                    )
                return None

        sys.meta_path.insert(0, BlockPyQt6())

        from lvjiang.i18n import init_i18n

        init_i18n("zh_CN")

        from lvjiang.core.ondevice import plugins
        from lvjiang.apps.yysls.ondevice import tuning_config
        from lvjiang.apps.yysls.ondevice import loadout_view
        assert callable(loadout_view.plan_view)
        assert not __import__('json').loads(loadout_view.plan_view("missing", "missing"))["ok"]

        plugins.configure_apps("yysls")
        plugins.ensure_loaded()
        assert callable(tuning_config.get_tuning_config)
        from lvjiang.apps.yysls.workflows.implementations.auto_tuning import AutoTuningWorkflow
        import importlib
        adapter = importlib.import_module(AutoTuningWorkflow.DEVICE_SETTINGS_MODULE)
        assert callable(adapter.get_settings) and callable(adapter.save_settings)
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "LVJIANG_ROOT": str(tmp_path)},
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
