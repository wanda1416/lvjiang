"""AI 设置页：独立保存草稿，后台测试可取消。"""
from __future__ import annotations

import asyncio
import threading

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.ai import AIError, AIReply, AIService, AISettings, AIStore
from ..i18n import tr
from .button_styles import apply_button_style


class _Signals(QObject):
    completed = pyqtSignal(object)


class _ConnectionTest(QRunnable):
    def __init__(self, service: AIService):
        super().__init__()
        self.service = service
        self.signals = _Signals()
        self.cancelled = threading.Event()

    async def _run(self) -> AIReply:
        async def watch_cancel():
            while not self.cancelled.is_set():
                await asyncio.sleep(0.05)

        request = asyncio.create_task(self.service.test_connection())
        cancel = asyncio.create_task(watch_cancel())
        try:
            done, _ = await asyncio.wait({request, cancel}, return_when=asyncio.FIRST_COMPLETED)
            if cancel in done:
                raise asyncio.CancelledError
            return await request
        finally:
            request.cancel()
            cancel.cancel()
            await asyncio.gather(request, cancel, return_exceptions=True)

    def run(self):
        try:
            result: AIReply | AIError = asyncio.run(self._run())
        except asyncio.CancelledError:
            result = AIError("cancelled", "连接测试已取消")
        except AIError as error:
            result = error
        except Exception:
            result = AIError("internal", "连接测试失败，请检查连接配置")
        self.signals.completed.emit(result)


class AISettingsPage(QWidget):
    def __init__(self, parent=None, *, store: AIStore | None = None):
        super().__init__(parent)
        self.store = store if store is not None else AIStore()
        self._worker: _ConnectionTest | None = None
        saved = self.store.settings()
        layout = QVBoxLayout(self)
        intro = QLabel(tr("配置 OpenAI 兼容接口，供后续 AI 功能使用。连接测试会向所选模型发送一条简短请求，可能产生少量费用。"))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self._form = QWidget()
        form = QFormLayout(self._form)
        self.url = QLineEdit(saved.base_url)
        self.url.setPlaceholderText("https://api.example.com/v1")
        self.model = QLineEdit(saved.model)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText(tr("无需鉴权的本地服务可留空"))
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 300)
        self.timeout.setSuffix(tr(" 秒"))
        self.timeout.setValue(int(saved.timeout) if 1 <= saved.timeout <= 300 else 30)
        form.addRow(tr("接口根地址"), self.url)
        form.addRow(tr("模型名称"), self.model)
        form.addRow("API Key", self.key)
        form.addRow(tr("请求超时"), self.timeout)
        layout.addWidget(self._form)
        note = QLabel(tr("接口根地址通常以 /v1 结尾，不包含 /chat/completions。API Key 保存到系统凭据库，不进入配置文件或手机同步数据。"))
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.save_button = QPushButton(tr("保存 AI 配置"))
        self.test_button = QPushButton(tr("测试连接"))
        self.cancel_button = QPushButton(tr("取消测试"))
        self.cancel_button.setEnabled(False)
        for button in (self.save_button, self.test_button, self.cancel_button):
            row.addWidget(button)
            apply_button_style(button, variant="neutral")
        row.addStretch()
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        if saved.base_url:
            try:
                self.key.setText(self.store.get_key(saved.base_url))
            except AIError as error:
                self.status.setText(tr(str(error)))
        # 地址变化必须显式重填 Key，避免把旧服务凭据发送到新服务。
        self.url.textChanged.connect(self._url_changed)
        for field in (self.model, self.key):
            field.textChanged.connect(self._edited)
        self.timeout.valueChanged.connect(self._edited)
        self.save_button.clicked.connect(self.save)
        self.test_button.clicked.connect(self.test)
        self.cancel_button.clicked.connect(self.cancel_test)
        self.destroyed.connect(lambda: self.cancel_test())
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.cancel_test)

    def _url_changed(self):
        self.key.clear()
        self._edited()

    def _edited(self):
        self.status.clear()

    def settings(self) -> AISettings:
        return AISettings(self.url.text(), self.model.text(), self.timeout.value()).validated()

    def save(self):
        try:
            self.store.save(self.settings(), self.key.text())
        except AIError as error:
            self.status.setText(tr(str(error)))
        except Exception:
            self.status.setText(tr("保存失败，请检查配置目录权限"))
        else:
            self.status.setText(tr("AI 配置已保存"))

    def test(self):
        if self._worker is not None:
            return
        try:
            service = AIService(self.settings(), self.key.text())
        except AIError as error:
            self.status.setText(tr(str(error)))
            return
        worker = _ConnectionTest(service)
        self._worker = worker
        worker.signals.completed.connect(self._completed)
        self._set_testing(True)
        self.status.setText(tr("正在测试连接…"))
        QThreadPool.globalInstance().start(worker)

    def cancel_test(self):
        if self._worker is not None:
            self._worker.cancelled.set()

    def _set_testing(self, testing: bool):
        self._form.setEnabled(not testing)
        self.save_button.setEnabled(not testing)
        self.test_button.setEnabled(not testing)
        self.cancel_button.setEnabled(testing)

    def _completed(self, result: AIReply | AIError):
        self._worker = None
        self._set_testing(False)
        if isinstance(result, AIError):
            self.status.setText(tr(str(result)))
        else:
            # 不展示服务返回的任意正文；成功表示实际获得非空文本回复。
            self.status.setText(tr("连接可用，模型已返回文本回复（耗时 {seconds} 秒）").format(seconds=f"{result.elapsed:.2f}"))
