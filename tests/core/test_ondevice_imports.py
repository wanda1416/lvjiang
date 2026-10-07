"""设备端 Python 入口不得依赖 APK 未分发的桌面 Qt 运行库。"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_ondevice_entrypoints_import_without_pyqt6() -> None:
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

        from lvjiang.core.ondevice import task_runner
        from lvjiang.core.ondevice import offline, workflow_runner, plugins, task_settings
        from lvjiang.core.profile import service
        from lvjiang.apps.yysls.ondevice import tuning_config

        plugins.configure_apps("yysls")
        plugins.ensure_loaded()
        assert task_runner.STATE_IDLE == "idle"
        assert callable(workflow_runner.create_engine)
        assert callable(offline.apply_sync)
        assert callable(service.profile_action)
        assert callable(tuning_config.get_tuning_config)
        assert callable(task_settings.get_settings)
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
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
