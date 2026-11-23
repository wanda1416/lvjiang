"""Development-only, process-local task diagnostics controls."""

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.task_diagnostics import DiagnosticOptions, TaskDiagnostics
from ..i18n import tr


class TaskDiagnosticsDialog(QDialog):
    def __init__(self, diagnostics: TaskDiagnostics, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("任务诊断"))
        self.resize(580, 380)
        self._diagnostics = diagnostics
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        debug = QWidget()
        form = QFormLayout(debug)
        preview = QLabel(
            tr(
                "开发预览：默认关闭，重启程序后恢复关闭。修改仅对下次启动的任务或批次生效；不改变正在运行的任务。"
            )
        )
        preview.setWordWrap(True)
        form.addRow(preview)
        self.enabled_check = QCheckBox(tr("启用开发诊断模式"))
        self.enabled_check.setChecked(diagnostics.options.enabled)
        form.addRow(self.enabled_check)
        self.frames_spin = QSpinBox()
        self.frames_spin.setRange(1, 10_000)
        self.frames_spin.setValue(diagnostics.options.max_frames)
        self.frames_spin.setSuffix(" " + tr("帧"))
        form.addRow(tr("最近帧上限"), self.frames_spin)
        self.capacity_spin = QSpinBox()
        self.capacity_spin.setRange(1, 1_000)
        self.capacity_spin.setValue(max(1, diagnostics.options.max_bytes // 1_000_000))
        self.capacity_spin.setSuffix(" MB")
        form.addRow(tr("压缩缓存上限"), self.capacity_spin)
        hint = QLabel(
            tr(
                "按任务保留实际识别用帧（WebP），超出任一上限时淘汰最早帧，直到同时不超限；恰好等于上限时不淘汰。1 MB = 1,000,000 字节。限额包含压缩图与帧元数据，不是进程总内存；原图与编码队列另有界。并行任务各自占用缓存，结束后只保留最近启动任务的记录。仅内存保存，退出即清空，不自动上传。"
            )
        )
        hint.setWordWrap(True)
        form.addRow(hint)
        self.tabs.addTab(debug, tr("诊断调试"))
        self.tabs.addTab(
            QWidget(), tr("模拟运行")
        )  # Deliberately blank until a real GUI exists.
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self._sync_limits_enabled()
        self.enabled_check.toggled.connect(self._changed)
        self.frames_spin.valueChanged.connect(self._changed)
        self.capacity_spin.valueChanged.connect(self._changed)

    def _sync_limits_enabled(self) -> None:
        enabled = self.enabled_check.isChecked()
        self.frames_spin.setEnabled(enabled)
        self.capacity_spin.setEnabled(enabled)

    def _changed(self, *_args) -> None:
        # 只在用户真的改动时回写：打开对话框不应把取整后的上限覆盖回去。
        self._sync_limits_enabled()
        self._diagnostics.options = DiagnosticOptions(
            self.enabled_check.isChecked(),
            self.frames_spin.value(),
            self.capacity_spin.value() * 1_000_000,
        )
