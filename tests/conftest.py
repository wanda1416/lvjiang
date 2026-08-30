"""全局测试防线：严禁原生阻塞弹窗

native_confirm/native_pause 是平台原生 MessageBox，一旦在测试里被
触发会无限阻塞 pytest（CI/无人值守直接卡死）。此处全局替换为立即
抛错：任何走到 confirm/pause 的路径必须在测试内自行打桩（如 stub
_confirm_continue、注入 _ui_callback 或 monkeypatch 内置函数）。
native_notify 非阻塞但会在桌面弹真通知，替换为 no-op 消音。

builtins.system 用 from-import 在加载时绑定了本地名，所以
platforms 与 system 两处都要补丁。
"""

import os
from pathlib import Path

import pytest

from tests.config_write_guard import install_project_config_write_guard

# 在收集测试模块前就封死真实 config；不能只依赖各用例自觉使用 tmp_path。
install_project_config_write_guard(Path(__file__).parents[1] / "config")


def pytest_runtest_logreport(report):
    """把 Actions 中的失败暴露为 check annotation，便于无日志权限时定位。"""
    if os.environ.get("GITHUB_ACTIONS") != "true" or not report.failed:
        return

    path, line, _ = report.location
    details = report.longreprtext[-4000:]
    message = f"{report.nodeid}\n{details}"
    # GitHub workflow command 的数据区需要转义，否则换行会截断注解。
    message = (message.replace("%", "%25")
               .replace("\r", "%0D")
               .replace("\n", "%0A"))
    print(
        f"::error file={path},line={line + 1},title=pytest failure::{message}",
        flush=True,
    )


@pytest.fixture(autouse=True)
def _no_native_dialogs(monkeypatch):
    def _banned(text: str, *args, **kwargs):
        raise AssertionError(
            f"测试中触发了原生阻塞弹窗（调用点必须在测试内打桩）: {text!r}")

    for mod in ("lvjiang.core.platforms",
                "lvjiang.workflows.builtins.system"):
        monkeypatch.setattr(f"{mod}.native_confirm", _banned)
        monkeypatch.setattr(f"{mod}.native_pause", _banned)
        monkeypatch.setattr(f"{mod}.native_notify", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _isolate_session_store(tmp_path, monkeypatch):
    """每个用例默认使用独立 session.json，禁止测试接触用户会话数据。"""
    from lvjiang import constants
    from lvjiang.core.config.session import reset_session_store

    monkeypatch.setattr(
        constants, "SESSION_PATH", tmp_path / "session" / "session.json",
    )
    reset_session_store()
    yield
    reset_session_store()


@pytest.fixture(autouse=True)
def _reset_game_config():
    """每个用例前重置 GameConfigManager 单例

    并行执行时同一 worker 内多个测试共享单例，前一个测试的 monkeypatch
    或配置修改会污染后续测试。生产环境不受影响。
    """
    from lvjiang.apps.yysls.config.manager import reset_game_config
    reset_game_config()
    yield
    reset_game_config()
