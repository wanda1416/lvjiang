"""用户总览「上传页面结构 / 下载页面结构」的界面流程。

这两个动作**不进常规同步**：只有用户点按钮才联网。上传在「远端存在且不是本
实例写的」时必须先问是否覆盖；下载固定两次确认——第一次看来源，第二次确认
本机将被替换并告知备份路径。

界面文案集中在这里，方便与用户指南逐句对照。
"""

from __future__ import annotations

import sys

from loguru import logger
from PyQt6.QtWidgets import QMessageBox, QWidget

from ...core.profile import layout
from ...core.sync import layout_sync
from ...core.sync.client import SyncError
from ...i18n import tr

#: 复用同一个会话，避免每点一次按钮就 refresh 一次令牌。
_SESSION = None


def is_android() -> bool:
    """Android 只允许下载：页面结构以 PC 为准。"""
    return hasattr(sys, "getandroidapilevel")


def access_token() -> str:
    """当前登录的 access 令牌；未登录或换不到时返回空串。"""
    global _SESSION
    from ...core.account.service import AccountError, AccountSession

    if _SESSION is None:
        _SESSION = AccountSession()
    session = _SESSION
    if session.access_token:
        return session.access_token
    if not session.has_stored_session():
        return ""
    try:
        if session.restore(fetch_summary=False) is None:
            return ""
    except AccountError:
        return ""
    return session.access_token or ""


def _summary(doc: layout.ProfileLayout) -> str:
    groups = doc.overview.groups
    columns = sum(len(group.columns) for group in groups.values())
    return tr("{} 个分组 / {} 列").format(len(groups), columns)


def _local_summary() -> str:
    return _summary(layout.load())


def _remote_summary(remote: layout_sync.RemoteLayout) -> str:
    try:
        return _summary(layout.parse_bytes(remote.decode()))
    except Exception:  # noqa: BLE001 - 远端内容坏了不该挡住「是否下载」的判断
        return tr("无法解析")


def _ask_overwrite(parent: QWidget, remote: layout_sync.RemoteLayout) -> bool:
    """远端由别的设备写入时的覆盖确认；默认不覆盖。"""
    text = tr(
        "远端已有页面结构，且不是本机写入的：\n\n"
        "{remote}\n"
        "远端结构：{remote_size}\n"
        "本机结构：{local_size}\n\n"
        "覆盖后，其它设备下载将得到本机结构。"
    ).format(
        remote=remote.describe(),
        remote_size=_remote_summary(remote),
        local_size=_local_summary(),
    )
    answer = QMessageBox.warning(
        parent,
        tr("覆盖远端页面结构？"),
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def upload_page_structure(parent: QWidget) -> bool:
    """上传本机页面结构；返回是否需要刷新界面（上传不改本机，故恒为 False）。"""
    if is_android():
        QMessageBox.information(
            parent, tr("上传页面结构"),
            tr("页面结构以 PC 为准，手机端只支持下载。"))
        return False
    token = access_token()
    if not token:
        QMessageBox.information(
            parent, tr("上传页面结构"), tr("请先在「账号」里登录后再上传页面结构。"))
        return False
    try:
        try:
            outcome = layout_sync.upload(token)
        except layout_sync.LayoutConflict as conflict:
            remote = conflict.remote
            if remote is None:
                QMessageBox.information(
                    parent, tr("上传页面结构"),
                    tr("云端页面结构在你确认期间被其它设备改写，请先下载或稍后重试。"))
                return False
            if not _ask_overwrite(parent, remote):
                return False
            try:
                outcome = layout_sync.upload(token, force=True)
            except layout_sync.LayoutConflict:
                QMessageBox.information(
                    parent, tr("上传页面结构"),
                    tr("云端页面结构在你确认期间被其它设备改写，请先下载或稍后重试。"))
                return False
    except SyncError as error:
        QMessageBox.warning(parent, tr("上传页面结构"), str(error))
        return False
    except OSError as error:
        QMessageBox.warning(
            parent, tr("上传页面结构"), tr("读取本机页面结构失败：{error}").format(error=error))
        return False

    if outcome.status == "unchanged":
        QMessageBox.information(
            parent, tr("上传页面结构"), tr("云端与本机一致，无需上传。"))
    else:
        detail = outcome.remote.describe() if outcome.remote else ""
        QMessageBox.information(
            parent, tr("上传页面结构"),
            tr("已上传页面结构。\n{detail}").format(detail=detail))
    return False


def download_page_structure(parent: QWidget) -> bool:
    """下载云端页面结构；返回 True 表示本机结构已变，调用方需要重建界面。"""
    token = access_token()
    if not token:
        QMessageBox.information(
            parent, tr("下载页面结构"), tr("请先在「账号」里登录后再下载页面结构。"))
        return False
    try:
        remote = layout_sync.fetch_remote(token)
    except SyncError as error:
        QMessageBox.warning(parent, tr("下载页面结构"), str(error))
        return False
    if remote is None:
        QMessageBox.information(
            parent, tr("下载页面结构"),
            tr("云端还没有页面结构，先在编辑端点一次「上传页面结构」。"))
        return False

    # 第一次确认：看清楚来源再决定。
    first = QMessageBox.question(
        parent,
        tr("下载页面结构"),
        tr(
            "将使用云端的页面结构：\n\n{remote}\n云端结构：{size}\n本机结构：{local}\n\n"
            "继续后还需再确认一次才会替换本机结构。"
        ).format(
            remote=remote.describe(),
            size=_remote_summary(remote),
            local=_local_summary(),
        ),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if first != QMessageBox.StandardButton.Yes:
        return False

    # 第二次确认：明确「本机将被替换」并说明备份位置。
    second = QMessageBox.warning(
        parent,
        tr("确认替换本机结构"),
        tr(
            "本机当前的页面结构将被云端结构替换。\n"
            "现有结构会先备份到 config/session 下的 _backup-<时间戳> 目录，"
            "需要时手工恢复即可。\n\n确认替换？"
        ),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if second != QMessageBox.StandardButton.Yes:
        return False

    try:
        remote, backup = layout_sync.download(token, remote=remote)
    except (ValueError, SyncError) as error:
        QMessageBox.warning(
            parent, tr("下载页面结构"),
            tr("下载失败，本机结构未改变：{error}").format(error=error))
        return False
    except OSError as error:
        logger.error("页面结构下载写盘失败: {}", error)
        QMessageBox.warning(
            parent, tr("下载页面结构"),
            tr("写入本机页面结构失败：{error}").format(error=error))
        return False

    QMessageBox.information(
        parent,
        tr("下载页面结构"),
        tr("已下载页面结构。\n{detail}\n备份：{backup}").format(
            detail=remote.describe(), backup=str(backup) if backup else tr("无（原结构不存在）")),
    )
    return True
