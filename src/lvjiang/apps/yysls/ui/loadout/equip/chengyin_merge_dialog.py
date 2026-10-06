"""承音装备疑似同件快照的人工确认对话框。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.config.models import LevelConfig
from lvjiang.apps.yysls.core.loadout import (
    ChengyinMergeCandidate,
    LoadoutRepository,
    find_chengyin_merge_candidates,
)
from lvjiang.i18n import tr
from lvjiang.ui.button_styles import apply_button_style

from .cards import _CompactEquipCard


@dataclass(frozen=True)
class UserChengyinMergeCandidate:
    """带用户归属的一组承音装备历史快照。"""

    username: str
    candidate: ChengyinMergeCandidate


def load_user_chengyin_candidates(
    usernames: list[str],
    level_configs: list[LevelConfig],
    users_dir: Path | None = None,
) -> list[UserChengyinMergeCandidate]:
    """按用户顺序汇总全部承音装备合并候选。"""
    result: list[UserChengyinMergeCandidate] = []
    for username in usernames:
        try:
            repo = LoadoutRepository(username, users_dir)
            if not repo.path.exists():
                continue
            candidates = find_chengyin_merge_candidates(
                repo.load().equipment_items, level_configs)
            result.extend(
                UserChengyinMergeCandidate(username, candidate)
                for candidate in candidates
            )
        except Exception:
            logger.exception(f"读取用户 {username} 的承音装备候选失败")
    return result


class _CandidatePair(QFrame):
    def __init__(
        self,
        entries: list[UserChengyinMergeCandidate],
        display_params: dict,
        parent=None,
    ):
        super().__init__(parent)
        self.entries = entries
        self.entry = entries[0]
        self.old_fps = {entry.candidate.old_fp for entry in entries}
        self.requires_review = any(entry.candidate.requires_review for entry in entries)
        candidate = self.entry.candidate
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "_CandidatePair {border:1px solid palette(midlight);"
            "border-radius:6px;background:palette(window);}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        username = QLabel(f"{tr('用户名')}：{self.entry.username}")
        username.setStyleSheet("font-weight:600;")
        layout.addWidget(username)
        self.review_label = QLabel(tr("存在多个可能版本或兼容性分歧，请人工核对；一键勾选不选择本组。"))
        self.review_label.setWordWrap(True)
        self.review_label.setStyleSheet("color:#c57f17;")
        self.review_label.setVisible(self.requires_review)
        layout.addWidget(self.review_label)

        comparison = QHBoxLayout()
        comparison.setSpacing(8)
        old_versions = QWidget()
        old_layout = QVBoxLayout(old_versions)
        old_layout.setContentsMargins(0, 0, 0, 0)
        for index, entry in enumerate(entries, 1):
            title = (tr("旧版本") if len(entries) == 1
                     else tr("旧版本 {index}").format(index=index))
            old_layout.addWidget(self._card_column(
                title, entry.candidate.old, display_params))
        comparison.addWidget(old_versions, 1)
        arrow = QLabel("→")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow.setStyleSheet("font-size:20px;color:palette(mid);")
        comparison.addWidget(arrow)
        comparison.addWidget(self._card_column(
            tr("保留版本"), candidate.new, display_params), 1)
        self.checkbox = QCheckBox(tr("合并本组"))
        self.checkbox.setToolTip(tr("删除左侧全部旧版本，并把备战方案引用迁移到右侧版本；冲突组互斥选择"))
        comparison.addWidget(
            self.checkbox, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(comparison)

    @staticmethod
    def _card_column(title: str, equip: dict, display_params: dict) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-weight:600;color:palette(mid);")
        layout.addWidget(label)
        card = _CompactEquipCard(display_params)
        card.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        card.setCursor(Qt.CursorShape.ArrowCursor)
        card.setMinimumWidth(190)
        card.set_equip(equip, str(equip.get("type") or tr("未知")))
        layout.addWidget(card)
        return wrapper


class ChengyinMergeDialog(QDialog):
    """合并在窗口内执行，只有取消或关闭窗口才结束对话框。"""

    merge_requested = pyqtSignal()

    def __init__(
        self,
        candidates: list[UserChengyinMergeCandidate],
        display_params: dict,
        parent=None,
    ):
        super().__init__(parent)
        self._pairs: list[_CandidatePair] = []
        self._display_params = display_params
        self.setWindowTitle(tr("承音装备"))
        self.resize(1500, 760)
        self.setMinimumSize(1000, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-size:14px;font-weight:600;")
        root.addWidget(self.summary)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self._scroll, 1)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        root.addWidget(self.result_label)

        footer = QHBoxLayout()
        footer.addStretch()
        # 歧义组必须人工决定，不能用全选把疑似关系当成已确认的同件关系。
        self.check_all_button = QPushButton(tr("一键勾选"))
        self.check_all_button.setToolTip(tr("仅勾选无歧义候选；有多个可能版本的组请人工核对"))
        self.check_all_button.clicked.connect(self._check_all)
        self.merge_button = QPushButton(tr("合并选中项"))
        self.merge_button.setEnabled(False)
        self.merge_button.clicked.connect(self.merge_requested.emit)
        self.cancel_button = QPushButton(tr("取消合并"))
        self.cancel_button.clicked.connect(self.reject)
        apply_button_style(self.check_all_button, variant="neutral")
        apply_button_style(self.merge_button, variant="action")
        apply_button_style(self.cancel_button, variant="neutral")
        footer.addWidget(self.check_all_button)
        footer.addWidget(self.merge_button)
        footer.addWidget(self.cancel_button)
        root.addLayout(footer)
        self.set_candidates(candidates)

    def set_candidates(self, candidates: list[UserChengyinMergeCandidate]) -> None:
        """按最新装备池重建候选，清除旧勾选，不关闭窗口。"""
        grouped: dict[tuple[str, str], list[UserChengyinMergeCandidate]] = {}
        for entry in candidates:
            grouped.setdefault((entry.username, entry.candidate.new_fp), []).append(entry)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        self._pairs = []
        for index, entries in enumerate(grouped.values()):
            pair = _CandidatePair(entries, self._display_params)
            pair.checkbox.toggled.connect(
                lambda checked, pair=pair: self._on_group_toggled(pair, checked))
            self._pairs.append(pair)
            grid.addWidget(pair, index // 2, index % 2)
        for column in range(2):
            grid.setColumnStretch(column, 1)
        if not candidates:
            empty = QLabel(tr("全部用户中没有找到符合条件的疑似重复装备"))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color:palette(mid);font-size:14px;padding:48px;")
            grid.addWidget(empty, 0, 0, 1, 2)
        for pair in self._pairs:
            pair.requires_review |= any(
                self._groups_conflict(pair, other)
                for other in self._pairs if other is not pair)
            pair.review_label.setVisible(pair.requires_review)
        self.summary.setText(tr(
            "识别出 {count} 组合并候选，其中 {review_count} 组需要人工核对。"
            "左侧全部旧版本合并到右侧保留版本；有分歧的组不自动勾选。"
        ).format(count=len(self._pairs), review_count=sum(p.requires_review for p in self._pairs)))
        grid.setRowStretch((len(self._pairs) + 1) // 2, 1)
        scrollbar = self._scroll.verticalScrollBar()
        position = scrollbar.value() if scrollbar is not None else 0
        self._scroll.setWidget(content)
        if scrollbar is not None:
            scrollbar.setValue(position)
        self.check_all_button.setEnabled(any(not p.requires_review for p in self._pairs))
        self._update_merge_enabled()

    def _check_all(self) -> None:
        """一次勾选无歧义候选，保留用户已人工选择的歧义组。"""
        for pair in self._pairs:
            if not pair.requires_review:
                pair.checkbox.setChecked(True)

    @staticmethod
    def _groups_conflict(left: _CandidatePair, right: _CandidatePair) -> bool:
        if left.entry.username != right.entry.username:
            return False
        return bool(
            left.old_fps & right.old_fps
            or left.entry.candidate.new_fp in right.old_fps
            or right.entry.candidate.new_fp in left.old_fps)

    def _on_group_toggled(self, pair: _CandidatePair, checked: bool) -> None:
        if checked:
            for other in self._pairs:
                if other is not pair and self._groups_conflict(pair, other):
                    other.checkbox.setChecked(False)
        self._update_merge_enabled()

    def _update_merge_enabled(self) -> None:
        self.merge_button.setEnabled(any(
            pair.checkbox.isChecked() for pair in self._pairs))

    def selected_candidates(self) -> list[UserChengyinMergeCandidate]:
        return [
            entry for pair in self._pairs
            if pair.checkbox.isChecked()
            for entry in pair.entries
        ]


__all__ = [
    "ChengyinMergeDialog",
    "UserChengyinMergeCandidate",
    "load_user_chengyin_candidates",
]
