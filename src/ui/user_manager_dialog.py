"""用户管理对话框"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem,
    QInputDialog, QMessageBox, QGroupBox, QFormLayout,
)
from PyQt6.QtCore import Qt
from loguru import logger

from src.core.user_config import UserConfigManager


class UserManagerDialog(QDialog):
    """用户管理对话框"""

    def __init__(self, user_manager: UserConfigManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("用户管理")
        self.setMinimumSize(500, 400)

        self._user_manager = user_manager
        self._setup_ui()
        self._refresh_user_list()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ─── 用户列表区 ───
        list_group = QGroupBox("用户列表")
        list_layout = QVBoxLayout(list_group)

        self._user_list = QListWidget()
        self._user_list.currentRowChanged.connect(self._on_user_selected)
        list_layout.addWidget(self._user_list)

        # 按钮行
        btn_row = QHBoxLayout()
        self._btn_new = QPushButton("新建用户")
        self._btn_new.clicked.connect(self._on_create_user)
        btn_row.addWidget(self._btn_new)

        self._btn_delete = QPushButton("删除用户")
        self._btn_delete.clicked.connect(self._on_delete_user)
        self._btn_delete.setEnabled(False)
        btn_row.addWidget(self._btn_delete)

        btn_row.addStretch()
        list_layout.addLayout(btn_row)

        layout.addWidget(list_group)

        # ─── 用户详情区（预留） ───
        detail_group = QGroupBox("用户信息")
        detail_layout = QFormLayout(detail_group)

        self._lbl_name = QLabel("-")
        detail_layout.addRow("用户名:", self._lbl_name)

        self._lbl_created = QLabel("-")
        detail_layout.addRow("创建时间:", self._lbl_created)

        # 预留数据展示区
        self._lbl_stats = QLabel("（装备数据展示功能开发中...）")
        self._lbl_stats.setStyleSheet("color: gray; font-style: italic;")
        detail_layout.addRow("装备统计:", self._lbl_stats)

        layout.addWidget(detail_group)

        # ─── 底部按钮 ───
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        bottom_row.addWidget(btn_close)
        layout.addLayout(bottom_row)

    def _refresh_user_list(self):
        """刷新用户列表"""
        self._user_list.blockSignals(True)
        self._user_list.clear()
        active_user = self._user_manager.get_active_user_name()

        for name in self._user_manager.list_users():
            suffix = " [当前]" if name == active_user else ""
            item = QListWidgetItem(f"{name}{suffix}")
            self._user_list.addItem(item)

        self._user_list.blockSignals(False)

        # 选中第一个（如果没有选中的）
        if self._user_list.count() > 0 and self._user_list.currentRow() < 0:
            self._user_list.setCurrentRow(0)

    def _on_user_selected(self, row: int):
        """用户列表选中项变化"""
        if row < 0:
            self._btn_delete.setEnabled(False)
            self._clear_detail()
            return

        name = self._user_manager.list_users()[row]
        user = self._user_manager.get_user(name)
        if user is None:
            return

        # 激活用户不可删除
        is_active = (name == self._user_manager.get_active_user_name())
        self._btn_delete.setEnabled(not is_active)

        # 显示详情
        self._lbl_name.setText(user.name)
        self._lbl_created.setText(user.created_at[:19] if user.created_at else "-")

    def _clear_detail(self):
        """清空详情显示"""
        self._lbl_name.setText("-")
        self._lbl_created.setText("-")

    def _on_create_user(self):
        """新建用户"""
        name, ok = QInputDialog.getText(
            self, "新建用户", "请输入用户名：",
        )
        if not ok or not name:
            return
        name = name.strip()
        if not name:
            return

        if self._user_manager.create_user(name):
            self._refresh_user_list()
            logger.info(f"用户已创建: {name}")
            QMessageBox.information(self, "成功", f"用户「{name}」已创建")
        else:
            QMessageBox.warning(self, "失败", "用户名已存在或为空")

    def _on_delete_user(self):
        """删除用户"""
        row = self._user_list.currentRow()
        if row < 0:
            return
        name = self._user_manager.list_users()[row]

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除用户「{name}」吗？\n该用户的所有数据将被清除，此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self._user_manager.delete_user(name):
            self._refresh_user_list()
            logger.info(f"用户已删除: {name}")
        else:
            QMessageBox.warning(self, "失败", "无法删除该用户")

