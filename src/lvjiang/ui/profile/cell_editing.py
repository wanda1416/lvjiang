"""档案总览单元格编辑与数据写入 Mix-in

ProfileCellEditingMixin: 双击编辑、右键菜单增减、自定义增减、覆写、
note 编辑、历史记录、值解析。
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING

from loguru import logger
from PyQt6.QtWidgets import (
    QInputDialog,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
)

from ...core.profile.models import (
    MODEL_NOTE,
    MODEL_QUOTA,
    MODEL_REGEN,
    MODEL_STOCK,
    KeyDef,
    QuotaKeyDef,
    RegenKeyDef,
    StepDef,
    StockKeyDef,
)
from ...core.profile.regen import (
    compute_regen_entry,
    is_realtime_regen,
)
from ...core.profile.repository import db_read_all
from ...core.profile.schema import (
    get_profile_config,
    save_profile_config,
)
from ...core.profile.store import get_groups
from ...i18n import tr
from .cell_formatting import (
    apply_cell_style,
    format_profile_cell,
)
from .dialogs import HistoryDialog, ask_value_dialog

if TYPE_CHECKING:
    from .tab import ProfileTab

# 哨兵值：表示解析失败
_PARSE_ERROR = object()


def _is_continuous_regen(kd) -> bool:
    return isinstance(kd, RegenKeyDef) and is_realtime_regen(kd)


def _current_regen_value(entry: dict, kd: RegenKeyDef) -> float:
    return compute_regen_entry(entry, kd).value


class ProfileCellEditingMixin:
    """档案总览单元格编辑与数据写入 Mix-in。

    依赖主类的 ``self._tables`` / ``self._loading`` / ``self._editing_cap_cell``
    属性以及 ``_refresh_group`` / ``_get_current_group_name`` 方法。
    """

    def _displayed_column_keys(self: ProfileTab, group_name: str) -> list[str]:  # type: ignore[misc]
        """返回当前表格列顺序，兼容不含列管理 mix-in 的轻量宿主。"""
        visible = getattr(self, "_visible_column_keys", None)
        if isinstance(visible, dict) and group_name in visible:
            return list(visible[group_name])
        return list(get_groups().get(group_name, {}).get("columns", []))

    # ─── 单元格事件与编辑 ──────────────────────────────────────

    def _on_cell_double_clicked(self: ProfileTab, row: int, col: int, group_name: str):  # type: ignore[misc]
        """单元格双击：把 /cap 后缀剥掉再进入编辑，让用户编辑的是值本身

        取"纯值"的办法是拿同一个 formatter 再跑一遍、只把 show_cap 关掉，
        而不是对显示文本做 ``split("/")``：

        - 按 split 只能靠"文本里有没有 /"来猜，note 存的是自由文本，
          一条「甲/乙」备注会被截成「甲」，编辑一下就把内容改没了；
        - formatter 是显示文本的唯一来源，关掉 show_cap 得到的就是它
          去掉后缀后的样子，两者天然对齐，不会漏也不会多剥。

        原来的实现还只认 regen / quota 两种模型，stock 和 note 同样会显示
        X/Y 却没被剥离——note 因为写入侧不做数值解析，直接把「2/7」整个当
        文本存了回去，值就此损坏（本次修复的起因）。
        """
        if self._loading:
            return

        from ...core.profile import get_profile_config
        config = get_profile_config()
        column_keys = self._displayed_column_keys(group_name)
        # 第 0 列是用户名，数据列从 1 开始——与 _on_item_changed 保持同一套
        # 下标换算。这里原本写的是 column_keys[col]，取到的是右边一列的
        # KeyDef，剥离与否按错误的列判断（第 0 列还会拿第一个 key 去套
        # 用户名那格）。
        if col < 1 or col - 1 >= len(column_keys):
            return

        kd = config.get_key(column_keys[col - 1])
        if not kd:
            return
        if not (kd.show_cap and kd.cap is not None):
            return  # 不会显示 /cap，没有要剥的东西

        model_type = config.get_model_type(column_keys[col - 1]) or ""

        table = self._tables.get(group_name)
        if not table:
            return
        item = table.item(row, col)
        if not item:
            return
        name_item = table.item(row, 0)
        if not name_item:
            return

        user_data = self._load_user_data(name_item.text())
        plain, _style = format_profile_cell(
            replace(kd, show_cap=False), model_type, user_data)
        if item.text() == plain:
            return
        self._editing_cap_cell = True
        item.setText(plain)
        self._editing_cap_cell = False

    def _on_item_changed(self: ProfileTab, item: QTableWidgetItem, group_name: str):  # type: ignore[misc]
        """单元格编辑完成后回写到 profile 节点"""
        if self._loading or self._editing_cap_cell:
            return

        row = item.row()
        col = item.column()
        table = item.tableWidget()
        if not table:
            return

        from ...core.profile import get_profile_config
        config = get_profile_config()

        column_keys = self._displayed_column_keys(group_name)
        # 第 0 列是用户名，数据列从 1 开始
        if col < 1 or col - 1 >= len(column_keys):
            return

        key_str = column_keys[col - 1]
        kd = config.get_key(key_str)
        if not kd:
            return

        model_type = config.get_model_type(key_str) or ""

        name_item = table.item(row, 0)
        if not name_item:
            return
        user_name = name_item.text()

        raw_value = item.text()

        # ── note 模型：直接写入文本，不走数值管线 ──
        if model_type == MODEL_NOTE:
            from ...core.profile.service import profile_action
            try:
                profile_action(
                    user_name, key_str,
                    model_type=model_type,
                    set_value=raw_value,
                    source="",
                )
            except Exception as e:
                logger.error(f"note 写入失败: {e}")
                QMessageBox.warning(self, tr("保存失败"), tr("回写用户数据失败:\n{e}").format(e=e))
            return

        parsed_value = _parse_value(raw_value, model_type, kd)
        if parsed_value is _PARSE_ERROR:
            self._loading = True
            user_data = self._load_user_data(user_name)
            text, style = format_profile_cell(kd, model_type, user_data)
            item.setText(text)
            apply_cell_style(item, style)
            self._loading = False
            return

        # 读取当前值，计算 delta，走 action 路径（触发 sync）
        profile_data = db_read_all(user_name)
        entry = profile_data.get(model_type, {}).get(key_str, {})
        current_value = entry.get("value", 0) or 0
        if model_type == MODEL_REGEN and isinstance(kd, RegenKeyDef):
            current_value = _current_regen_value(entry, kd)

        delta = parsed_value - current_value
        force_target_write = (
            model_type == MODEL_REGEN
            and _is_continuous_regen(kd)
            and abs(parsed_value - math.floor(parsed_value)) > 1e-9
        )
        if delta == 0 and not force_target_write:
            return

        # Cell 编辑路径根据变动方向选择对应词表（增加→来源，减少→用途）
        if delta > 0:
            cell_source = kd.sources[0] if kd.sources else ""
        else:
            cell_source = kd.uses[0] if kd.uses else ""

        self._adjust_value(
            user_name, model_type, key_str, kd, current_value, delta,
            is_action=True, source=cell_source, expected_entry=dict(entry),
            regen_progress_source="target",
            force_write=force_target_write,
        )

    def _on_cell_context_menu(self: ProfileTab, pos, group_name: str, table: QTableWidget):  # type: ignore[misc]
        """右键菜单：快速增减数值"""
        from PyQt6.QtWidgets import QMenu

        item = table.itemAt(pos)
        if not item:
            return

        row = item.row()
        col = item.column()

        from ...core.profile import get_profile_config
        config = get_profile_config()

        column_keys = self._displayed_column_keys(group_name)
        # 第 0 列是用户名，数据列从 1 开始
        if col < 1 or col - 1 >= len(column_keys):
            return

        key_str = column_keys[col - 1]
        kd = config.get_key(key_str)
        if not kd:
            return

        model_type = config.get_model_type(key_str) or ""

        name_item = table.item(row, 0)
        if not name_item:
            return
        user_name = name_item.text()

        # 获取当前值（从 DB 读取）
        profile_data = db_read_all(user_name)
        entry = profile_data.get(model_type, {}).get(key_str, {})
        current_value = entry.get("value", 0)
        if current_value is None:
            current_value = 0
        if model_type == MODEL_REGEN and isinstance(kd, RegenKeyDef):
            current_value = _current_regen_value(entry, kd)
        expected_entry = dict(entry)

        # 构建菜单
        menu = QMenu(self)
        menu.setTitle(f"{kd.label} ({user_name})")

        # note 模型：提供文本编辑选项
        if model_type == MODEL_NOTE:
            action_edit = menu.addAction(tr("编辑文本..."))
            if action_edit:
                current_text = expected_entry.get("value_text", "")
                action_edit.triggered.connect(
                    lambda: self._edit_note_text(
                        user_name, model_type, key_str, kd, current_text,
                        group_name, table,
                    )
                )
            # note 历史记录
            action_history = menu.addAction(tr("查看历史记录"))
            if action_history:
                action_history.triggered.connect(
                    lambda: self._show_history_dialog(user_name, model_type, key_str, kd.label)
                )
            viewport = table.viewport()
            if viewport:
                menu.exec(viewport.mapToGlobal(pos))
            return

        # 获取该字段的自定义 steps（Quota、Regen 和 Stock 模型支持）
        kd_steps: list[StepDef] = []
        kd_increment_only = False
        if model_type == MODEL_QUOTA and isinstance(kd, QuotaKeyDef):
            kd_steps = kd.steps
            kd_increment_only = kd.increment_only
        elif model_type == MODEL_REGEN and isinstance(kd, RegenKeyDef):
            kd_steps = kd.steps
        elif model_type == MODEL_STOCK and isinstance(kd, StockKeyDef):
            kd_steps = kd.steps

        if kd_steps:
            # 有自定义 steps：只展示用户定义的幅度，标签优先显示来源
            for step in kd_steps:
                if step.value > 0:
                    val_label = f"+{step.value}"
                elif step.value < 0:
                    val_label = str(step.value)
                else:
                    continue
                label = f"{step.source}({val_label})" if step.source else val_label
                action = menu.addAction(label)
                if action:
                    action.triggered.connect(
                        lambda checked, s=step: self._adjust_value(
                            user_name, model_type, key_str, kd, current_value, s.value,
                            is_action=True, source=s.source, expected_entry=expected_entry,
                        )
                    )
            menu.addSeparator()
            # 始终提供自定义输入入口
            action_inc = menu.addAction(tr("增加..."))
            if action_inc:
                action_inc.triggered.connect(
                    lambda: self._adjust_value_custom(
                        user_name, model_type, key_str, kd, current_value, direction=1,
                        expected_entry=expected_entry,
                    )
                )
            # 单向增加模式下不提供减少
            if not kd_increment_only:
                action_dec = menu.addAction(tr("减少..."))
                if action_dec:
                    action_dec.triggered.connect(
                        lambda: self._adjust_value_custom(
                            user_name, model_type, key_str, kd, current_value, direction=-1,
                            expected_entry=expected_entry,
                        )
                    )
        else:
            # 无自定义 steps：只提供自定义输入
            action_inc = menu.addAction(tr("增加..."))
            if action_inc:
                action_inc.triggered.connect(
                    lambda: self._adjust_value_custom(
                        user_name, model_type, key_str, kd, current_value, direction=1,
                        expected_entry=expected_entry,
                    )
                )
            # 单向增加模式下不提供减少
            if not kd_increment_only:
                action_dec = menu.addAction(tr("减少..."))
                if action_dec:
                    action_dec.triggered.connect(
                        lambda: self._adjust_value_custom(
                            user_name, model_type, key_str, kd, current_value, direction=-1,
                            expected_entry=expected_entry,
                        )
                    )

        # 覆写：直接设定值，不走 sync
        action_override = menu.addAction(tr("覆写..."))
        if action_override:
            action_override.triggered.connect(
                lambda: self._override_value_custom(
                    user_name, model_type, key_str, kd, current_value
                )
            )

        # 历史记录
        menu.addSeparator()
        action_history = menu.addAction(tr("查看历史记录"))
        if action_history:
            action_history.triggered.connect(
                lambda: self._show_history_dialog(user_name, model_type, key_str, kd.label)
            )

        viewport = table.viewport()
        if viewport:
            menu.exec(viewport.mapToGlobal(pos))

    # ─── 数据写入与增减 ──────────────────────────────────────

    def _adjust_value(  # type: ignore[misc]
        self: ProfileTab,
        user_name: str,
        model_type: str,
        key: str,
        kd,
        current_value,
        delta: int | float,
        is_action: bool = True,
        source: str = "",
        expected_entry: dict | None = None,
        use_cas: bool = True,
        regen_progress_source: str = "current",
        force_write: bool = False,
    ):
        """增减数值并写回。

        UI 只负责收集上下文、处理提示和刷新；写入语义统一委托 profile.service.profile_action。
        """
        from ...core.profile.service import (
            ProfileWriteConflict,
            profile_action,
        )
        try:
            profile_action(
                user_name, key,
                model_type=model_type,
                delta=delta,
                source=source,
                current_value=current_value,
                expected_entry=expected_entry,
                is_action=is_action,
                use_cas=use_cas,
                regen_progress_source=regen_progress_source,
                force_write=force_write,
            )
        except ProfileWriteConflict:
            logger.warning(f"{user_name} {model_type}.{key} CAS 失败，本次增减未写入")
            QMessageBox.warning(
                self, tr("写入冲突"),
                tr("该数值已被其他进程更新，本次增减未写入。请刷新后重试。"),
            )
            current_group = self._get_current_group_name()
            table = self._tables.get(current_group)
            if table:
                self._refresh_group(current_group, table)
            return
        except Exception as e:
            logger.error(f"回写失败: {e}")
            QMessageBox.warning(self, tr("保存失败"), tr("回写用户数据失败:\n{e}").format(e=e))
            return

        # 刷新表格
        current_group = self._get_current_group_name()
        table = self._tables.get(current_group)
        if table:
            self._refresh_group(current_group, table)

    def _register_new_source(self: ProfileTab, kd: KeyDef, source: str, vocab: list[str]) -> None:  # type: ignore[misc]
        """新词条自动追加到对应词表（来源/用途）并持久化到 profile.yaml

        保存与内存修改原子化：save 失败时回滚内存词表，避免"会话内可见、重启后丢失"。
        """
        if not source or source in vocab:
            return
        vocab.append(source)
        try:
            save_profile_config(get_profile_config())
        except Exception as e:
            try:
                vocab.remove(source)
            except ValueError:
                pass
            logger.warning(f"持久化新词条 '{source}' 失败: {e}")

    def _adjust_value_custom(  # type: ignore[misc]
        self: ProfileTab,
        user_name: str,
        model_type: str,
        key: str,
        kd,
        current_value,
        direction: int = 0,
        expected_entry: dict | None = None,
    ):
        """自定义增减数值（带来源/用途选择）

        direction: 1=增加（展示来源词表），-1=减少（展示用途词表），
            0=双向（两类叠加，来源在上）。
        """
        # 根据 direction 选择词表与标签
        if direction > 0:
            min_val = 0
            prompt = tr("增加量:")
            vocab = kd.sources
            vocab_label = tr("来源")
        elif direction < 0:
            min_val = 0
            prompt = tr("减少量:")
            vocab = kd.uses
            vocab_label = tr("用途")
        else:
            min_val = -999999
            prompt = tr("增减量（正增负减）:")
            vocab = kd.sources + [u for u in kd.uses if u not in kd.sources]
            vocab_label = tr("来源")

        if kd.decimal:
            current_text = f"{current_value:.4f}".rstrip("0").rstrip(".")
            is_float = True
        else:
            current_text = str(int(current_value))
            is_float = False

        value, source, _sync, ok = ask_value_dialog(
            self,
            title=tr("自定义增减 - {label}").format(label=kd.label),
            hint=tr("当前值: {val}").format(val=current_text),
            prompt=prompt,
            is_float=is_float,
            min_val=min_val,
            sources=vocab,
            source_label=vocab_label,
        )
        if not ok:
            return

        delta = float(value) if is_float else int(value)

        # 根据 direction 调整 delta 符号
        if direction > 0:
            delta = abs(delta)
        elif direction < 0:
            delta = -abs(delta)

        # 检查减少后是否小于 0
        new_value = current_value + delta
        if new_value < 0:
            QMessageBox.warning(
                None, tr("数值无效"),
                tr("减少后数值不能小于 0（当前值: {cur}，输入: {inp}）").format(cur=int(current_value), inp=int(abs(delta)))
            )
            return

        # 新词条归入实际变动方向对应的词表：增加→来源，减少→用途
        if delta > 0:
            self._register_new_source(kd, source, kd.sources)
        elif delta < 0:
            self._register_new_source(kd, source, kd.uses)

        # 自定义增减属于 action，触发 sync_targets 同步
        self._adjust_value(
            user_name, model_type, key, kd, current_value, delta,
            is_action=True, source=source, expected_entry=expected_entry,
        )

    def _override_value_custom(  # type: ignore[misc]
        self: ProfileTab,
        user_name: str,
        model_type: str,
        key: str,
        kd,
        current_value,
    ):
        """覆写（编辑语义）：输入目标值，计算 delta 走 CAS 写入。

        默认勾选「同步变更依赖方」→ 走 action 路径（触发 sync_targets 同步）。
        取消勾选 → 纯覆写语义（仅写本 key，不触发任何同步）。
        """
        if kd.decimal:
            current_text = f"{current_value:.4f}".rstrip("0").rstrip(".")
            is_float = True
        else:
            current_text = str(int(current_value))
            is_float = False

        value, source, sync_checked, ok = ask_value_dialog(
            self,
            title=tr("覆写 - {label}").format(label=kd.label),
            hint=tr("当前值: {val}").format(val=current_text),
            prompt=tr("新值:"),
            is_float=is_float,
            min_val=0,
            sources=kd.sources + [u for u in kd.uses if u not in kd.sources],
            initial_value=current_value,
            sync_checkbox=True,
            sync_default=True,
            source_label=tr("来源/用途"),
        )
        if not ok:
            return

        new_value = value
        delta = new_value - current_value
        force_target_write = (
            model_type == MODEL_REGEN
            and _is_continuous_regen(kd)
            and abs(new_value - math.floor(new_value)) > 1e-9
        )
        if delta == 0 and not force_target_write:
            return

        # 新词条归入实际变动方向对应的词表：增加→来源，减少→用途
        if delta > 0:
            self._register_new_source(kd, source, kd.sources)
        elif delta < 0:
            self._register_new_source(kd, source, kd.uses)

        # sync_checked=True: 走 action 路径（触发 sync_targets 同步）
        # sync_checked=False: 纯覆写语义（change_type="override"，不触发同步）
        self._adjust_value(
            user_name, model_type, key, kd, current_value, delta,
            is_action=sync_checked, source=source, use_cas=False,
            regen_progress_source="target",
            force_write=force_target_write,
        )

    def _edit_note_text(  # type: ignore[misc]
        self: ProfileTab,
        user_name: str,
        model_type: str,
        key: str,
        kd,
        current_text: str,
        group_name: str = "",
        table: QTableWidget | None = None,
    ) -> None:
        """弹出多行文本输入框编辑 note 文本"""
        from ...core.profile.service import profile_action

        text, ok = QInputDialog.getMultiLineText(
            self,
            tr("编辑备注 - {label}").format(label=kd.label),
            tr("备注内容:"),
            current_text,
        )
        if not ok:
            return

        try:
            profile_action(
                user_name, key,
                model_type=model_type,
                set_value=text,
                source="",
            )
        except Exception as e:
            logger.error(f"note 写入失败: {e}")
            QMessageBox.warning(self, tr("保存失败"), tr("回写用户数据失败:\n{e}").format(e=e))
            return

        if not group_name:
            group_name = self._get_current_group_name()
            table = self._tables.get(group_name)
        if table:
            self._refresh_group(group_name, table)

    def _show_history_dialog(  # type: ignore[misc]
        self: ProfileTab,
        user_name: str, model_type: str, key: str, key_label: str,
    ) -> None:
        """打开历史记录查看器，展示指定 key 的最近变更记录"""
        dialog = HistoryDialog(user_name, model_type, key, key_label, self)
        dialog.exec()


# ─── 值解析（模块级） ────────────────────────────────────────────


def _parse_value(raw: str, model_type: str, kd: KeyDef):
    """解析用户输入值，返回解析后的值或 _PARSE_ERROR"""
    # decimal 类型的 key 统一走 float 解析
    if kd.decimal:
        try:
            return float(raw) if raw else 0.0
        except ValueError:
            QMessageBox.warning(None, tr("输入错误"), f"{kd.label} 必须是数字")
            return _PARSE_ERROR

    if model_type == MODEL_QUOTA:
        # quota 可以是 int 或 bool（如 shop_of_week）
        if isinstance(kd, QuotaKeyDef) and kd.cap is not None:
            try:
                return int(raw) if raw else 0
            except ValueError:
                QMessageBox.warning(None, tr("输入错误"), tr("{label} 必须是整数").format(label=kd.label))
                return _PARSE_ERROR
        # 无 cap 的 quota 可能是 bool
        upper = raw.upper()
        if upper in ("Y", "TRUE", "1", tr("是"), "YES"):
            return True
        if upper in ("", "N", "FALSE", "0", tr("否"), "NO"):
            return False
        try:
            return int(raw)
        except ValueError:
            return raw

    if model_type == MODEL_REGEN:
        try:
            return int(raw) if raw else 0
        except ValueError:
            QMessageBox.warning(None, tr("输入错误"), tr("{label} 必须是整数").format(label=kd.label))
            return _PARSE_ERROR

    if model_type == MODEL_STOCK:
        try:
            return int(raw) if raw else 0
        except ValueError:
            QMessageBox.warning(None, tr("输入错误"), tr("{label} 必须是整数").format(label=kd.label))
            return _PARSE_ERROR

    return raw
