
"""主窗口运行控制的工作流配置加载与选择器（用户/图库空间/布局/方案）。"""

from pathlib import Path
from typing import Any

from loguru import logger
from PyQt6.QtCore import Qt

from ...core.config.resolver import get_resolver
from ...i18n import tr

# 顶部上下文选择器的锁定原因。定义在这里而不是 window.py：window 已经
# import 本模块，反向 import 会成环。
LOCK_REASON_BATCH = "batch"
LOCK_REASON_PLAN = "plan"
LOCK_REASON_RUNNING = "running"

# 方案下拉的「不使用方案」项，userData 为空串。
PLAN_CUSTOM_LABEL = "-" + tr('自定义') + " -"

# automation_state_changed 的非常规状态。订阅方必须显式处理——
# 它们的 else 分支都会把未知状态当成「就绪」。
STATE_PLAN_UNSUPPORTED = "plan_unsupported"
#: 目标空闲但并发门禁不允许再起一个任务（当前仅 Lv1 未激活会命中）
STATE_START_DENIED = "start_denied"
STATE_PAUSING = "pausing"
STATE_STOPPING = "stopping"


class _RunSelectorsMixin:
    _custom_context: tuple[str, Any, str] | None

    # ─── 工作流配置加载 ──────────────────────────────────

    def _selected_run_env(self) -> str:
        """Return the environment currently held by the main-window UI."""
        combo = self._env_combo
        value = combo.currentData()
        return str(value) if value is not None else ""

    def _load_workflow_configs(self, preferred_workflow_id: str | None = None):
        """发现全部脚本，按作者声明与用户偏好过滤排序后填充下拉。

        脚本本体（.wf + 内置类）由发现层自动扫描；暴露层只决定日常页
        暴露哪些脚本、顺序、以及可选的显示名覆盖。暴露层逻辑与设备端
        悬浮面板共用 ``list_exposed_scripts()``。

        ``preferred_workflow_id`` 由「按某个执行目标的草稿重建」传入：
        那一刻下拉里还是上一个目标环境的条目，不能拿它当新选中项。
        """
        from ...workflows.discovery import (
            last_discovery_problems,
            list_exposed_scripts,
        )

        # 环境切换只会改变“不支持”提示，不应把用户选中的日常任务重置
        # 为第一项。清空 combo 前先按稳定 id 留住当前选择。
        selected_workflow_id = (
            preferred_workflow_id or self.workflow_combo.currentData())
        if selected_workflow_id is None:
            selected_workflow_id = getattr(self, "_displayed_script_id", None)
        self._workflow_configs: list[dict] = getattr(self, "_workflow_configs", [])
        temporary = next((cfg for cfg in self._workflow_configs
                          if cfg["id"].startswith("__loaded__:")), None)

        current_env = self._selected_run_env()
        try:
            # env 是脚本级启动契约：不匹配的脚本不是“可选但
            # 不能跑”，而是根本不进入当前环境的候选集。
            configs = list_exposed_scripts(current_env)
        except Exception as e:
            logger.error(f"发现脚本失败: {e}")
            # 发现结果已经不可用，上一次成功刷新留下的告警同样不能继续
            # 冒充当前状态。
            self._show_ignored_scripts([])
            return
        self._show_ignored_scripts(last_discovery_problems())
        if temporary is not None:
            configs.append(temporary)
        self._workflow_configs = configs
        # 这份候选集是按哪个环境算出来的。切换执行目标只投影草稿、屏蔽了
        # 环境信号，靠它判断“视图相对环境是否已经过期”。
        self._workflow_env = current_env
        self._refresh_workflow_groups()
        self._fill_workflow_combo(selected_workflow_id)

        # 批量刷新沿用本次顺序和勾选；不会重新加载配置组。
        if self._batch_tab is not None:
            self._batch_tab.refresh_scripts()
        logger.info(f"已加载 {len(self._workflow_configs)} 个脚本配置")

    def _sync_task_views_to_env(
            self, preferred_workflow_id: str | None = None) -> bool:
        """候选视图跟随当前环境重建，返回是否真的发生了重建。

        日常下拉、分组、参数面板和批量页的候选都由 ``_selected_run_env()``
        决定。切换执行目标走的是草稿投影：写入环境选择器时屏蔽了信号，不会
        触发 ``_on_env_changed``。缺了这次重建，页面会继续展示上一个目标
        环境（例如端游）的任务，而启动门禁又按当前环境判定——用户会看到
        当前连接根本跑不了的任务，还会在切走时把它记进该目标的草稿。

        环境没变时直接返回，避免同环境目标互切产生无谓的全量扫描，也避免
        覆盖各目标自己的任务选择。
        """
        if self._selected_run_env() == getattr(self, "_workflow_env", None):
            return False
        self._load_workflow_configs(preferred_workflow_id)
        return True

    def _refresh_workflow_groups(self):
        combo = self.workflow_group_combo
        selected = combo.currentData()
        groups = sorted({cfg.get("group", "") for cfg in self._workflow_configs})
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(tr("全部分组"), None)
        for group in groups:
            combo.addItem(group or tr("未分组"), group)
        index = combo.findData(selected)
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    def _fill_workflow_combo(self, selected_workflow_id=None):
        """只更新展示；筛选导致的默认项变化不能写回持久化选择。"""
        from ...workflows.discovery import script_display_name

        # 填充下拉列表（block 信号，避免 addItem 逐条触发 _on_workflow_combo_changed）
        self.workflow_combo.blockSignals(True)
        self.workflow_combo.clear()
        group = self.workflow_group_combo.currentData()
        for cfg in self._workflow_configs:
            if group is not None and cfg.get("group", "") != group:
                continue
            full_display_name = script_display_name(cfg)
            # 放完整名字：窄的时候 Qt 自己按可用宽度省略（CE_ComboBoxLabel
            # 会 elide），分栏拉宽后就能完整显示。预先截断成定长会让「拉宽」
            # 永远看不到更多内容。
            self.workflow_combo.addItem(full_display_name, cfg["id"])
            item_index = self.workflow_combo.count() - 1
            self.workflow_combo.setItemData(
                item_index,
                full_display_name,
                Qt.ItemDataRole.ToolTipRole,
            )
        selected_index = self.workflow_combo.findData(selected_workflow_id)
        if selected_index >= 0:
            self.workflow_combo.setCurrentIndex(selected_index)
        self.workflow_combo.blockSignals(False)

        # 初始化当前面板显示的脚本追踪（供日常配置持久化使用）
        current_cfg = self._get_selected_flow_config()
        self._displayed_script_id = current_cfg["id"] if current_cfg else None

    def _on_workflow_group_changed(self, _index: int):
        selected = self.workflow_combo.currentData()
        self._save_displayed_params()
        self._fill_workflow_combo(selected)
        self._rebuild_param_panel()
        # 筛选产生的默认项不反写脚本选择，只保存用户主动选择的展示分组。
        from ...core.config.interface import get_interface_store
        try:
            get_interface_store().update_node("daily", {"group": self.workflow_group_combo.currentData()})
        except Exception as exc:
            logger.warning("保存日常展示分组失败: {}", exc)

    def _on_refresh_workflows(self):
        self._save_displayed_params()
        self._load_workflow_configs()
        self._rebuild_param_panel()

    def _show_ignored_scripts(self, problems) -> None:
        """把发现层忽略的 .wf 及原因显示在脚本下拉框下方。"""
        label = getattr(self, "_ignored_scripts_warning", None)
        if label is None:
            return
        if not problems:
            label.setVisible(False)
            label.setText("")
            return
        lines = [
            tr("[警告] 以下 {n} 个脚本文件存在问题，请在脚本编辑器中修正：")
            .format(n=len(problems))
        ]
        lines += [f"• {item.wf_file}：{item.message}" for item in problems]
        label.setText("\n".join(lines))
        label.setVisible(True)

    def _on_load_workflow(self):
        """加载任意 .wf 文件为临时工作流项（非常驻，打开新文件会覆盖）

        名字/参数/可选项从 .wf 文件顶部的 `#%` front-matter 元数据提取。
        """
        from PyQt6.QtWidgets import QFileDialog

        from ...workflows.metadata import build_flow_config
        path, _ = QFileDialog.getOpenFileName(
            self, tr("加载工作流文件"), str(get_resolver().write_dir("workflows")),
            tr("工作流文件 (*.wf);;所有文件 (*)"),
        )
        if not path:
            return
        p = Path(path)
        cfg = build_flow_config(p)
        self._save_displayed_params()
        self._workflow_configs = [item for item in self._workflow_configs
                                  if not item["id"].startswith("__loaded__:")]
        self._workflow_configs.append(cfg)
        self._refresh_workflow_groups()
        self.workflow_group_combo.blockSignals(True)
        self.workflow_group_combo.setCurrentIndex(0)
        self.workflow_group_combo.blockSignals(False)
        self._fill_workflow_combo(cfg["id"])
        self._rebuild_param_panel()
        self._save_daily_config()
        self.append_log(f"[加载] 已加载工作流: {cfg['name']}")

    def _get_selected_flow_config(self) -> dict | None:
        """获取当前选中的工作流配置"""
        selected_id = self.workflow_combo.currentData()
        return next((cfg for cfg in self._workflow_configs if cfg["id"] == selected_id), None)

    # ─── 用户选择器 ────────────────────────────────────────

    def _refresh_user_combo(self):
        """刷新用户选择器下拉列表"""
        self.user_combo.blockSignals(True)
        self.user_combo.clear()
        users = self._user_manager.list_users()
        active = self._user_manager.get_active_user_name()
        self.user_combo.addItems(users)
        idx = self.user_combo.findText(active)
        if idx >= 0:
            self.user_combo.setCurrentIndex(idx)
        self.user_combo.blockSignals(False)
        # 日常/调律的执行用户选择是纯运行期状态；用户增删后刷新候选，
        # 仍保留有效的固定选择，不参与 session/config 持久化。
        from ..execution.user_selector import ExecutionUserSelector
        for selector in self.findChildren(ExecutionUserSelector):
            selector.refresh_users()
        if hasattr(self, "_daily_execution_user_selector"):
            self._on_daily_execution_user_changed(
                self._daily_execution_user_selector.resolve_username()
            )

    def _on_user_changed(self, index: int):
        """用户选择器切换"""
        if index < 0:
            return
        name = self.user_combo.currentText()
        old_name = self._user_manager.get_active_user_name()
        if name and name != old_name:
            # 切换只影响 UI 展示；正在运行的任务已在启动时绑定用户名，
            # 其 session 落盘归属不受此处切换影响
            self._user_manager.set_active_user(name)
            logger.info(f"已切换到用户: {name}")
            # “跟随当前用户”的执行选择器不会发生下拉索引变化，因此不会
            # 自己发出 resolved_user_changed；这里显式切换参数展示上下文。
            if hasattr(self, "_daily_execution_user_selector"):
                self._on_daily_execution_user_changed(
                    self._daily_execution_user_selector.resolve_username()
                )
        # 通知插件页面刷新其用户相关状态。
        self.user_changed.emit(self._user_manager.get_active_user_name() or "")

    # ─── 图库空间选择器 ────────────────────────────────────

    def _refresh_reference_space_combo(self):
        """重扫图库空间，并让主页面下拉跟随当前激活空间。"""
        self._reference_db.load()
        combo = self.reference_space_combo
        combo.blockSignals(True)
        combo.clear()
        # 显示本地化名字、itemData 存稳定 key：切换与方案套用一律按 key 比对
        for key in self._reference_db.get_spaces():
            combo.addItem(self._reference_db.space_label(key), key)
        index = combo.findData(self._reference_db.get_active_space())
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _on_reference_space_changed(self, index: int):
        """主页面选取图库空间即激活；失败时恢复实际激活项。"""
        if index < 0:
            return
        name = self.reference_space_combo.itemData(index)
        if not name or name == self._reference_db.get_active_space():
            return
        if self._reference_db.set_active_space(name):
            logger.info(f"已切换到图库: {name}")
            return
        self.reference_space_combo.blockSignals(True)
        restored = self.reference_space_combo.findData(
            self._reference_db.get_active_space())
        if restored >= 0:
            self.reference_space_combo.setCurrentIndex(restored)
        self.reference_space_combo.blockSignals(False)

    def _on_env_changed(self, index: int):
        """环境选择器切换：持久化 + 刷新工作流下拉框的"环境不支持"提示

        下拉框是应用内环境状态；切换后持久化到 session，并基于新的内存值
        刷新工作流下拉框提示。已启动的工作流持有自己的环境快照，不受影响。
        """
        if index < 0:
            return
        # 环境切换后参数定义可能改变；先提交旧面板里的可见字段，再按新环境
        # 重建。保存逻辑只更新找到的控件，不会覆盖另一环境的隐藏参数。
        if hasattr(self, "_save_displayed_params"):
            self._save_displayed_params()
        from ...core.config import save_env
        save_env(self._env_combo.itemData(index))
        self._load_workflow_configs()
        if hasattr(self, "_rebuild_param_panel"):
            self._rebuild_param_panel()

    def navigate_user(self, delta: int) -> None:
        """按 delta 偏移切换当前用户（-1 上一个 / +1 下一个）。

        边界夹止：到达列表首尾时不再移动。
        通过修改 user_combo.currentIndex 触发 _on_user_changed 完整链路。
        """
        count = self.user_combo.count()
        if count < 2:
            return
        new_idx = max(0, min(count - 1, self.user_combo.currentIndex() + delta))
        if new_idx != self.user_combo.currentIndex():
            self.user_combo.setCurrentIndex(new_idx)

    # ─── 布局选择器 ────────────────────────────────────────

    def _refresh_layout_combo(self, preferred_key: str = ""):
        """刷新布局列表，并优先保留调用方当前展示的选择。"""
        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()
        selected = preferred_key or self._layout_manager.get_active_layout_key()
        for entry in self._layout_manager.list_layout_entries():
            self.layout_combo.addItem(entry.name, entry.key)
        idx = self.layout_combo.findData(selected)
        if idx < 0:
            idx = self.layout_combo.findData(
                self._layout_manager.get_active_layout_key())
        if idx >= 0:
            self.layout_combo.setCurrentIndex(idx)
        self.layout_combo.blockSignals(False)
        self._update_layout_desc_label()

    def _on_layout_changed(self, index: int):
        """布局选择器切换"""
        if index < 0:
            return
        key = self.layout_combo.currentData()
        if key and key != self._layout_manager.get_active_layout_key():
            self._layout_manager.set_active_layout(key)
            logger.info(f"已切换到布局: {self.layout_combo.currentText()} ({key})")
        self._update_layout_desc_label()

    def _update_layout_desc_label(self):
        """更新布局描述标签"""
        key = self.layout_combo.currentData()
        desc = ""
        if key:
            layout = self._layout_manager.load_layout(key)
            if layout:
                desc = layout.desc
        self.layout_desc_label.setText(desc)

    # ─── 方案选择器 ────────────────────────────────────────

    def _refresh_plan_combo(self):
        """刷新方案下拉，并按 actives.plan 恢复选中态。"""
        from ...core.config.plans import (
            get_active_plan_id,
            load_plans,
            set_active_plan_id,
        )
        stored = get_active_plan_id()
        self.plan_combo.blockSignals(True)
        self.plan_combo.clear()
        self.plan_combo.addItem(PLAN_CUSTOM_LABEL, "")
        for plan in load_plans():
            self.plan_combo.addItem(plan.name, plan.id)
        idx = self.plan_combo.findData(stored)
        self.plan_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.plan_combo.blockSignals(False)
        if stored and idx < 0:
            # 上次选中的方案已被删除：说清楚，并把失效引用清掉。
            logger.warning(f"上次选中的方案 {stored} 已不存在，回到自定义")
            self.append_log(tr("[提示] 上次选中的方案已不存在，已回到自定义"))
            set_active_plan_id("")
        self._apply_selected_plan(persist=False)

    def _selected_plan(self):
        """当前下拉选中的方案；「自定义」或引用已失效时返回 None。"""
        from ...core.config.plans import load_plans
        plan_id = self.plan_combo.currentData()
        if not plan_id:
            return None
        for plan in load_plans():
            if plan.id == plan_id:
                return plan
        return None

    def _ensure_target_plan(self) -> None:
        """为闲置目标选择兼容方案，不改全局默认或其他目标的草稿。"""
        target = self._current_execution_target()
        plan = self._selected_plan()
        if (target is None or self._running or plan is None
                or plan.allows(target.kind)):
            return
        from ...core.config.plans import load_plans

        for candidate in load_plans():
            if not candidate.allows(target.kind):
                continue
            if ((candidate.space and self.reference_space_combo.findData(candidate.space) < 0)
                    or (candidate.env and self._env_combo.findData(candidate.env) < 0)
                    or (candidate.layout and self.layout_combo.findData(candidate.layout) < 0)):
                continue
            index = self.plan_combo.findData(candidate.id)
            if index < 0:
                continue
            blocked = self.plan_combo.blockSignals(True)
            self.plan_combo.setCurrentIndex(index)
            self.plan_combo.blockSignals(blocked)
            self._apply_selected_plan(persist=False)
            self._capture_launch_draft(target.id)
            self.append_log(
                tr("[执行目标] 当前方案不适用，已自动切换到「{name}」").format(
                    name=candidate.name))
            return
        self.append_log(tr("[提示] 没有适用于当前执行目标的方案，请配置连接方案"))

    def _on_plan_changed(self, index: int):
        """方案下拉切换：选方案则填充并锁定三个选择器，选自定义则放开。"""
        if index < 0:
            return
        self._apply_selected_plan(persist=True)

    def _apply_selected_plan(self, *, persist: bool) -> None:
        plan_id = self.plan_combo.currentData() or ""
        plan = self._selected_plan()
        if plan_id and plan is None:
            # 方案被删除但 actives.plan 还指着它：降级回自定义，别静默。
            logger.warning(f"方案 {plan_id} 已不存在，回到自定义")
            self.append_log(tr("[提示] 选中的方案已不存在，已回到自定义"))
            self.plan_combo.blockSignals(True)
            self.plan_combo.setCurrentIndex(0)
            self.plan_combo.blockSignals(False)
            plan_id = ""
        if persist:
            from ...core.config.plans import set_active_plan_id
            set_active_plan_id(plan_id)
        if plan is None:
            self._release_plan_context()
            return
        self._stash_custom_context()
        missing = self._apply_plan_context(plan)
        if missing:
            logger.warning(f"方案「{plan.name}」引用了不存在的内容: {missing}")
            self.append_log(
                tr("[提示] 方案「{name}」的 {missing} 已不存在，未能全部套用").format(
                    name=plan.name, missing="、".join(missing)))
        self._set_context_controls_locked(LOCK_REASON_PLAN, True)
        self._refresh_run_button()

    def _apply_plan_context(self, plan) -> list[str]:
        """把方案的三项写进选择器，返回未能套用的项名。"""
        missing: list[str] = []
        if plan.space:
            idx = self.reference_space_combo.findData(plan.space)
            if idx >= 0:
                self.reference_space_combo.setCurrentIndex(idx)
            else:
                missing.append(tr("图库"))
        if plan.env:
            idx = self._env_combo.findData(plan.env)
            if idx >= 0:
                self._env_combo.setCurrentIndex(idx)
            else:
                missing.append(tr("环境"))
        if plan.layout:
            idx = self.layout_combo.findData(plan.layout)
            if idx >= 0:
                self.layout_combo.setCurrentIndex(idx)
            else:
                missing.append(tr("布局"))
        return missing

    def _stash_custom_context(self) -> None:
        """进入方案前记下手上的自定义组合，切回自定义时原样还原。"""
        if getattr(self, "_custom_context", None) is not None:
            return
        self._custom_context = (
            self.reference_space_combo.currentText(),
            self._env_combo.currentData(),
            self.layout_combo.currentData(),
        )

    def _release_plan_context(self) -> None:
        """回到自定义：解锁三个选择器并还原进入方案前的组合。"""
        self._set_context_controls_locked(LOCK_REASON_PLAN, False)
        stashed = getattr(self, "_custom_context", None)
        self._custom_context = None
        if stashed is not None:
            space, env, layout = stashed
            idx = self.reference_space_combo.findText(space)
            if idx >= 0:
                self.reference_space_combo.setCurrentIndex(idx)
            idx = self._env_combo.findData(env)
            if idx >= 0:
                self._env_combo.setCurrentIndex(idx)
            idx = self.layout_combo.findData(layout)
            if idx >= 0:
                self.layout_combo.setCurrentIndex(idx)
        self._refresh_run_button()

