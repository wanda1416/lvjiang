"""设备端 Python 入口不得依赖 APK 未分发的桌面 Qt 运行库。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from tests.fake_plugin import write_fake_app_package


def test_ondevice_entrypoints_import_without_pyqt6(tmp_path) -> None:
    shutil.copytree(Path(__file__).parents[3] / "config/system", tmp_path / "config/system")
    plugin = write_fake_app_package(tmp_path / "plugins", body="""
        HOOKS_MODULE = __name__
        hooks = AppHooks(
            id="demo_app", name="demo_app",
            workflow_implementations={"demo_task": f"{HOOKS_MODULE}.workflow.DemoWorkflow"},
        )
    """)
    (plugin / "workflow.py").write_text(
        "class DemoWorkflow:\n"
        "    DEVICE_VISIBLE = True\n"
        "    DEVICE_SETTINGS_MODULE = __name__.rsplit('.', 1)[0] + '.settings'\n",
        encoding="utf-8")
    (plugin / "settings.py").write_text(
        "def get_settings(*args):\n    return '{}'\n\n"
        "def save_settings(*args):\n    return '{}'\n",
        encoding="utf-8")
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
        from lvjiang.core.ondevice import offline, workflow_runner, plugins, task_settings, bootstrap, user_settings
        from lvjiang.core.ondevice import workbench, batch
        assert callable(batch.prepare) and callable(batch.editor)
        assert __import__("json").loads(batch.editor())["ok"]
        assert not __import__("json").loads(batch.view())["ok"]
        from lvjiang.core import version_info
        from lvjiang.core import about
        from lvjiang.core.qrcode import group as group_qrcode
        from lvjiang.core.updates import announcement
        assert about.about_content()["version"] == version_info.get_version()
        assert callable(workbench.public_content)
        assert callable(announcement.fetch_announcement_manifest)
        assert callable(group_qrcode.fetch_qrcode_bytes)
        assert version_info.get_version()
        from lvjiang.core.profile import service

        # 设备构建组合根按名装配插件：用与真实插件无关的假插件验证这条链路。
        plugins.configure_apps(DEMO_SPEC)
        plugins.ensure_loaded()
        from lvjiang.core.plugins import get_registered_app_ids
        assert "demo_app" in get_registered_app_ids()
        from lvjiang.workflows.implementations import get_workflow_class
        import importlib
        workflow = get_workflow_class("demo_task")
        adapter = importlib.import_module(workflow.DEVICE_SETTINGS_MODULE)
        assert callable(adapter.get_settings) and callable(adapter.save_settings)
        assert task_runner.STATE_IDLE == "idle"
        assert callable(workflow_runner.create_engine)
        assert callable(offline.apply_sync)
        assert callable(service.profile_action)
        assert callable(task_settings.get_settings)
        assert callable(bootstrap.initialize) and callable(user_settings.create_user)
        """
    ).replace("DEMO_SPEC", repr(f"demo_app@{plugin}"))

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
