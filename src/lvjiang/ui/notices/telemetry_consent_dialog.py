"""匿名使用数据收集——首启一次性同意提示。

新版本首次启动时弹出（``needs_prompt()`` 判定，见
core/telemetry/consent.py）。两个按钮视觉等权，不做暗黑模式；无论选
哪个都不再重复弹，只能去「配置管理 → 网络与隐私」改主意。

「查看示例数据」直接调用各 schema 的 ``example()``，保证展示的就是
真会发的字段集合，不是写死之后再也不更新的模板文案。
"""
from __future__ import annotations

import json

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from ...i18n import tr
from ..button_styles import apply_button_style


class TelemetryConsentDialog(QDialog):
    """返回值经 :meth:`granted` 读取；无论用户点哪个按钮都算「已经问过」。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("匿名使用数据收集"))
        self.setMinimumSize(560, 420)
        self._granted = False
        self._setup_ui()

    def granted(self) -> bool:
        return self._granted

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        title = QLabel(tr("愿意贡献匿名使用数据，帮助改进律匠吗？"))
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        title.setWordWrap(True)
        layout.addWidget(title)

        body = QTextBrowser()
        body.setOpenExternalLinks(False)
        body.setMarkdown(self._body_markdown())
        layout.addWidget(body, stretch=1)

        btn_row = QHBoxLayout()
        self._btn_decline = QPushButton(tr("不同意"))
        self._btn_decline.clicked.connect(self._on_decline)
        self._btn_agree = QPushButton(tr("同意"))
        self._btn_agree.clicked.connect(self._on_agree)
        # 两个按钮视觉等权：都走 neutral 变体，不做「同意」高亮的暗黑模式
        apply_button_style(self._btn_decline, self._btn_agree, variant="neutral")
        btn_row.addWidget(self._btn_decline)
        btn_row.addWidget(self._btn_agree)
        layout.addLayout(btn_row)

    def _body_markdown(self) -> str:
        example = self._example_payload_text()
        heartbeat_line = tr("每天一次的启动记录：律匠版本、系统类型、运行环境、一个随机标识")
        id_line = tr(
            "这个标识是本机随机生成的一串字符，不含你的账号或硬件信息，"
            "但确实能让我们知道同一台电脑的多次记录，你可以随时在设置里重置它。")
        return (
            f"{tr('收集什么')}：\n\n"
            f"- {heartbeat_line}\n"
            f"{self._disclosure_markdown()}"
            f"{tr('不收集什么')}：\n\n"
            f"- {tr('账号、姓名或其他能认出你是谁的信息')}\n"
            f"- {tr('截图、日志、配置文件或其他未在上方列明的本地内容')}\n\n"
            f"{tr('用来做什么')}：{tr('仅用于所列功能改进，不公开发布原始数据')}。\n\n"
            f"{id_line}\n\n"
            f"{tr('同意后随时可在「配置管理 → 网络与隐私」关闭。')}\n\n"
            f"**{tr('实际会发送的数据长这样')}：**\n\n```json\n{example}\n```"
        )

    @staticmethod
    def _disclosure_markdown() -> str:
        from ...apps import get_registry
        lines: list[str] = []
        for item in get_registry().get("telemetry_disclosures", ()):
            lines.append(f"- **{item.title}**：{item.purpose}")
            lines.extend(f"  - {text}" for text in item.collected)
            lines.extend(f"  - {tr('不收集')}：{text}" for text in item.excluded)
            if vars(item).get("historical_upload_days"):
                lines.append("  - " + tr(
                    "开启后会补传最近 {days} 天内尚未上报的相关历史数据；关闭期间的记录仍保存在本地。"
                ).format(days=vars(item).get("historical_upload_days")))
        return "\n".join(lines) + ("\n\n" if lines else "\n")

    @staticmethod
    def _example_payload_text() -> str:
        from ...core.telemetry.heartbeat import HEARTBEAT_SCHEMA
        from ...core.telemetry.registry import all_schemas
        payloads = [HEARTBEAT_SCHEMA.example()]
        payloads.extend(
            schema.example() for schema in all_schemas()
            if schema.name != HEARTBEAT_SCHEMA.name
        )
        return json.dumps(payloads, ensure_ascii=False, indent=2)

    def _on_agree(self) -> None:
        self._granted = True
        self.accept()

    def _on_decline(self) -> None:
        self._granted = False
        self.accept()


def maybe_prompt_and_record(parent) -> None:
    """首启入口：需要问就问，问完立即落地选择。供 main_window 调用。"""
    from ...core.telemetry.consent import needs_prompt, record_consent_choice

    if not needs_prompt():
        return
    dialog = TelemetryConsentDialog(parent)
    dialog.exec()
    record_consent_choice(dialog.granted())
    if not dialog.granted():
        QMessageBox.information(
            parent, tr("好的"),
            tr("不会上传任何数据。如果之后想参与，"
               "可以在「配置管理 → 网络与隐私」里随时打开。"))
