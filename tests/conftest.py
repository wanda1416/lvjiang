"""全局测试防线：严禁原生阻塞弹窗

native_confirm/native_pause 是平台原生 MessageBox，一旦在测试里被
触发会无限阻塞 pytest（CI/无人值守直接卡死）。此处全局替换为立即
抛错：任何走到 confirm/pause 的路径必须在测试内自行打桩（如 stub
_confirm_continue、注入 _ui_callback 或 monkeypatch 内置函数）。
native_notify 非阻塞但会在桌面弹真通知，替换为 no-op 消音。

builtins.system 用 from-import 在加载时绑定了本地名，所以
platforms 与 system 两处都要补丁。
"""

import pytest


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
