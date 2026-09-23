"""全局测试防线：严禁一切阻塞弹窗

原生 MessageBox 和 Qt 模态对话框在测试里都没人去点，一旦弹出就无限阻塞
pytest——不报错、不超时，CI 和无人值守只表现为「卡住了」。两条防线都把它们
换成立即抛错，需要驱动对话框的用例自行在测试内打桩。

- ``_no_native_dialogs``：平台原生 native_confirm/native_pause。走到
  confirm/pause 的路径必须自己打桩（stub _confirm_continue、注入
  _ui_callback 或 monkeypatch 内置函数）。native_notify 不阻塞但会在桌面弹
  真通知，替换为 no-op 消音。builtins.system 用 from-import 在加载时绑定了
  本地名，所以 platforms 与 system 两处都要补丁。
- ``_no_qt_modal_dialogs``：QMessageBox 等静态便捷函数与 QDialog.exec()。
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


#: Qt 的模态入口：静态便捷函数按类名分组，另加实例上的 exec()。
#: 只列真正阻塞的——open()/show() 不等待，测试里出现是正常的。
_BLOCKING_QT_DIALOGS = {
    "QMessageBox": (
        "warning", "critical", "information", "question", "about", "aboutQt"),
    "QInputDialog": (
        "getText", "getMultiLineText", "getItem", "getInt", "getDouble"),
    "QFileDialog": (
        "getOpenFileName", "getOpenFileNames", "getSaveFileName",
        "getExistingDirectory"),
    "QColorDialog": ("getColor",),
    "QFontDialog": ("getFont",),
}


@pytest.fixture(autouse=True)
def _no_qt_modal_dialogs(monkeypatch):
    """全局测试防线：严禁 Qt 模态对话框。

    离屏模式下没人去点确定，``QMessageBox.warning`` 和 ``QDialog.exec()``
    会无限等下去——整个套件挂死，不报错也不超时，只能靠人去猜是哪一条卡住。
    真实事故：UI 槽函数里一个 AttributeError 被 ``except`` 接住后弹
    ``QMessageBox.warning``，本该是一条清晰的失败，结果变成全量卡在 84% 二十
    多分钟。

    这里把它们换成立即抛错，把「无限挂起」变成「秒级可见的失败」。需要驱动
    对话框的用例自行在测试内打桩（``monkeypatch.setattr(QMessageBox,
    "question", ...)``），测试体里的打桩晚于本 fixture，自然覆盖它。
    """
    from PyQt6 import QtWidgets

    def _banned(api: str):
        def _raise(*args, **kwargs):
            # 标题和正文都带上：各 API 的参数位置不一样，而被 except 兜住的
            # 原始错误通常在正文里，只取第一个字符串会把它丢掉。
            texts = [a for a in args if isinstance(a, str) and a.strip()]
            detail = " | ".join(texts)
            raise AssertionError(
                f"测试中触发了 Qt 模态对话框 {api}"
                + (f"：{detail}" if detail else "")
                + "。它在离屏模式下会无限阻塞，必须在测试内打桩；"
                  "若这是被 except 兜住的意外错误，请先修那个错误。")
        return _raise

    for class_name, methods in _BLOCKING_QT_DIALOGS.items():
        cls = getattr(QtWidgets, class_name, None)
        if cls is None:
            continue
        for method in methods:
            if hasattr(cls, method):
                monkeypatch.setattr(cls, method, _banned(
                    f"{class_name}.{method}()"))
    # QMessageBox/QInputDialog 等都继承 QDialog，实例 exec() 一并封死。
    monkeypatch.setattr(
        QtWidgets.QDialog, "exec", _banned("QDialog.exec()"))


@pytest.fixture(autouse=True)
def _isolate_session_store(tmp_path, monkeypatch):
    """每个用例默认使用独立 session.json，禁止测试接触用户会话数据。"""
    from lvjiang import constants
    from lvjiang.core.config.session import reset_session_store

    monkeypatch.setattr(
        constants, "SESSION_PATH", tmp_path / "session" / "session.json",
    )
    monkeypatch.setattr(
        constants, "BATCH_CONFIG_PATH", tmp_path / "session" / "batch.json",
    )
    reset_session_store()
    yield
    reset_session_store()


@pytest.fixture(autouse=True)
def _isolate_profile_store(tmp_path, monkeypatch):
    """每个用例使用独立 Profile 定义与数据库，禁止读取用户真实数据。

    Profile 不经过 SessionStore，拥有独立的 profile.yaml/profile.db 路径与
    模块级单例。只隔离 session.json 仍会让工作流测试因已有业务周期定义而
    失败，或者在调用 profile_declare 时尝试修改真实配置。
    """
    from lvjiang.core.profile import repository, schema

    profile_dir = tmp_path / "session"
    monkeypatch.setattr(schema, "_PROFILE_PATH", profile_dir / "profile.yaml")
    monkeypatch.setattr(repository, "_DB_PATH", profile_dir / "profile.db")
    schema._config = None
    repository.reset_profile_db()
    yield
    schema._config = None
    repository.reset_profile_db()


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
