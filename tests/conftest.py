"""全局测试防线：严禁原生阻塞弹窗

native_confirm/native_pause 是平台原生 MessageBox，一旦在测试里被
触发会无限阻塞 pytest（CI/无人值守直接卡死）。此处全局替换为立即
抛错：任何走到 confirm/pause 的路径必须在测试内自行打桩（如 stub
_confirm_continue、注入 _ui_callback 或 monkeypatch 内置函数）。
native_notify 非阻塞但会在桌面弹真通知，替换为 no-op 消音。

builtins.system 用 from-import 在加载时绑定了本地名，所以
platforms 与 system 两处都要补丁。
"""

from pathlib import Path

import pytest

from tests.config_write_guard import install_project_config_write_guard

# 在收集测试模块前就封死真实 config；不能只依赖各用例自觉使用 tmp_path。
install_project_config_write_guard(Path(__file__).parents[1] / "config")


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


class _FakeClipboard:
    """进程内假剪贴板，只实现生产代码用到的 QClipboard 接口。

    QMimeData 兼作存储，这样 mimeData().hasImage() / hasUrls() 等判断
    与真实剪贴板行为一致。QClipboard 各方法都可带 mode 参数，一律用
    *args 吞掉。
    """

    def __init__(self):
        from PyQt6.QtCore import QMimeData
        self._mime = QMimeData()

    def text(self, *args):
        return self._mime.text()

    def setText(self, text, *args):  # noqa: N802
        self._mime.setText(text)

    def image(self, *args):
        from PyQt6.QtGui import QImage
        data = self._mime.imageData()
        return data if isinstance(data, QImage) else QImage()

    def setImage(self, image, *args):  # noqa: N802
        self._mime.setImageData(image)

    def mimeData(self, *args):  # noqa: N802
        return self._mime

    def setMimeData(self, mime, *args):  # noqa: N802
        self._mime = mime

    def clear(self, *args):
        self._mime.clear()


@pytest.fixture(autouse=True)
def _isolate_clipboard(monkeypatch):
    """每个用例使用独立的进程内剪贴板，禁止测试接触系统剪贴板。

    系统剪贴板是跨进程的全局资源：xdist 并行时多个 worker 抢同一块
    剪贴板，Windows 上表现为 OleSetClipboard 拿不到锁（COM 0x800401d0），
    写入静默失败，随后读到的是别的 worker 刚写进去的内容，断言随机失败。
    顺带避免跑测试时覆盖开发者自己的剪贴板。
    """
    from PyQt6.QtWidgets import QApplication

    clipboard = _FakeClipboard()
    monkeypatch.setattr(QApplication, "clipboard", lambda *args: clipboard)


@pytest.fixture(autouse=True)
def distributed_plans_store(monkeypatch):
    """把 app.yaml 的 plans 键换成内存里的一份，与 session 层对称隔离。

    方案存两层：session.json（已由 _isolate_session_store 隔离）和
    config/system|local 的 app.yaml 顶层 plans。后者是入库文件，自带三条
    随包分发的预置方案，于是：

    * load_plans() 把预置方案排在本机方案前面，任何「列表里有几条」的断言
      都会偏移；
    * save_plans() 恒写两层，入参与真实分发层不一致就真去写 app.yaml；
      dev 模式落的是 system 层，被 config_write_guard 拦成失败。

    所以不是「distributed=True 的用例才需要」，凡碰方案的用例都需要——
    改成 autouse，免得每加一个测试文件就重新踩一次。需要检视内存内容的
    用例照常按名字请求，拿到的是同一份。
    """
    from lvjiang.core.config import plans as plans_mod

    store: dict = {}
    monkeypatch.setattr(
        plans_mod, "_load_distributed_raw", lambda: store.get("plans", []))
    monkeypatch.setattr(
        plans_mod, "_save_distributed_raw",
        lambda items: store.__setitem__("plans", items))
    return store
