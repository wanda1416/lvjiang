"""宏录制结果对话框 - 展示生成的 DSL 文本，支持复制/保存"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QFileDialog, QMessageBox, QApplication,
)
from loguru import logger

from src.constants import SYSTEM_WORKFLOWS_DIR


class MacroResultDialog(QDialog):
    """展示录制生成的 DSL 语句，提供复制到剪贴板 / 保存为 .wf"""

    def __init__(self, dsl_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("录制结果 - DSL 语句")
        self.setMinimumSize(560, 480)
        self._dsl_text = dsl_text
        self._preserved = False   # 是否已复制或保存（防止误关丢失）
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        tip = QLabel(
            "以下是录制生成的 DSL 语句（画布归一化坐标，可直接剪切到任意 .wf 复用）："
        )
        tip.setStyleSheet("color: #555;")
        layout.addWidget(tip)

        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(self._dsl_text)
        self.text_edit.setStyleSheet("font-family: Consolas, monospace; font-size: 13px;")
        self.text_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.text_edit)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_copy = QPushButton("复制到剪贴板")
        btn_copy.clicked.connect(self._on_copy)
        btn_row.addWidget(btn_copy)

        btn_save = QPushButton("保存为 .wf")
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self._on_close)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)

    # ─── 关闭保护 ─────────────────────────────────────────

    def _on_text_changed(self):
        """文本被编辑后，之前的复制/保存视为失效"""
        self._preserved = False

    def _confirm_discard(self) -> bool:
        """未复制/保存且有内容时弹确认，返回是否允许关闭"""
        if self._preserved or not self.text_edit.toPlainText().strip():
            return True
        reply = QMessageBox.question(
            self, "未保存",
            "录制内容尚未复制到剪贴板或保存为文件，确定要关闭吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _on_close(self):
        if self._confirm_discard():
            self.accept()

    def closeEvent(self, event):
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()

    def _on_copy(self):
        """复制当前文本到系统剪贴板"""
        QApplication.clipboard().setText(self.text_edit.toPlainText())
        self._preserved = True
        logger.info("录制 DSL 已复制到剪贴板")
        QMessageBox.information(self, "已复制", "DSL 语句已复制到剪贴板")

    def _on_save(self):
        """保存当前文本为 .wf 文件（默认目录为系统工作流目录）"""
        default_path = str(SYSTEM_WORKFLOWS_DIR / "recorded.wf")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存为工作流文件", default_path, "工作流文件 (*.wf);;所有文件 (*)"
        )
        if not path:
            return
        try:
            Path(path).write_text(self.text_edit.toPlainText(), encoding="utf-8")
            self._preserved = True
            logger.info(f"录制 DSL 已保存: {path}")
            QMessageBox.information(self, "已保存", f"已保存到:\n{path}")
        except Exception as e:
            logger.error(f"保存录制 DSL 失败: {e}")
            QMessageBox.warning(self, "保存失败", str(e))
