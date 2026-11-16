"""界面状态持久化混入类 - interface.json 的 ui_state 与 daily 节点

``ui_state.main_page`` 存窗口尺寸、分栏比例与左右 Tab 页签；``daily`` 存
日常页当前选中的脚本与展示分组，脚本参数则按脚本 id 落在 wf_configs 里。

⚠️ 日常页只负责自己的脚本选择与展示分组：各工作流的参数由其专属页面管理，
这里禁止遍历清理不归自己管的 wf_configs（详见 _save_daily_config）。
同理，参数面板只对 scope=daily 的脚本绘制与读写。

依赖主类提供：
    _main_splitter / _left_tabs / tabs / workflow_combo / _param_panel /
    _workflow_note_label / _workflow_configs /
    _displayed_script_id / _get_selected_flow_config
"""

from copy import deepcopy

from loguru import logger
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QGroupBox,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)

from ...core.config.wf_configs import TRANSIENT_ID_PREFIX
from ...i18n import tr
from ..widgets.task_param_form import TaskParamForm

#: 参数保存防抖：连续输入只有在停顿后才落盘。
_PARAM_SAVE_DELAY_MS = 250


class UiStateMixin:
    """UI 状态与日常页配置持久化混入类"""

    def _restore_ui_state(self):
        """启动时恢复窗口几何、左右分栏比例和当前 Tab 页签。

        优先用 ``window_geometry``（``QWidget.saveGeometry()`` 的 base64）：
        它一次带回尺寸、屏幕位置、最大化状态和多屏 DPI，换显示器后 Qt 会
        把落到不可见区域的窗口拉回。``window_size`` 只作旧配置的回退，
        避免只有尺寸时"最大化关闭 → 下次变超大普通窗口"的跳变。
        """
        from PyQt6.QtCore import QByteArray

        from ...core.config import load_ui_page_state
        page = load_ui_page_state("main_page")

        geometry_restored = False
        raw_geometry = page.get("window_geometry")
        if isinstance(raw_geometry, str) and raw_geometry:
            try:
                geometry_restored = self.restoreGeometry(
                    QByteArray.fromBase64(raw_geometry.encode("ascii")))
            except (UnicodeEncodeError, ValueError, TypeError):
                geometry_restored = False
        if not geometry_restored:
            size = page.get("window_size")
            if isinstance(size, list) and len(size) == 2:
                self.resize(int(size[0]), int(size[1]))

        sizes = page.get("splitter_sizes")
        if isinstance(sizes, list) and len(sizes) == 2 and all(s > 0 for s in sizes):
            self._main_splitter.setSizes([int(s) for s in sizes])
        connection_sizes = page.get("connection_splitter_sizes")
        if isinstance(connection_sizes, list) and len(connection_sizes) == 2 \
                and all(s > 0 for s in connection_sizes):
            self._connection_splitter.setSizes([int(s) for s in connection_sizes])
        # 恢复左右 Tab 页签
        left_idx = page.get("left_tab_index", 0)
        right_idx = page.get("right_tab_index", 0)
        if 0 <= left_idx < self._left_tabs.count():
            self._left_tabs.setCurrentIndex(left_idx)
        if 0 <= right_idx < self.tabs.count():
            self.tabs.setCurrentIndex(right_idx)

    def _save_ui_state(self):
        """退出时安全合并 ui_state.main_page。"""
        from ...core.config import update_ui_page_state
        try:
            patch = {
                "window_geometry": bytes(self.saveGeometry().toBase64()).decode("ascii"),
                # 回退值：旧版本或几何串损坏时仍能还原出接近的窗口大小
                "window_size": [self.width(), self.height()],
                "splitter_sizes": self._main_splitter.sizes(),
                "left_tab_index": self._left_tabs.currentIndex(),
                "right_tab_index": self.tabs.currentIndex(),
            }
            connection_splitter = getattr(self, "_connection_splitter", None)
            if connection_splitter is not None:
                patch["connection_splitter_sizes"] = connection_splitter.sizes()
            update_ui_page_state("main_page", patch)
        except Exception as e:
            logger.warning(f"保存 UI 状态失败: {e}")

    def _save_tab_indices(self):
        """Tab 切换时保存当前左右页签索引"""
        from ...core.config import update_ui_page_state
        try:
            update_ui_page_state("main_page", {
                "left_tab_index": self._left_tabs.currentIndex(),
                "right_tab_index": self.tabs.currentIndex(),
            })
        except Exception as e:
            logger.warning(f"保存 Tab 页签失败: {e}")

    # ─── 日常页配置持久化（session.json daily 节点）───────

    def _on_workflow_combo_changed(self, index: int):
        """脚本下拉切换：先保存旧脚本参数，重建参数面板，再保存新状态"""
        # 面板仍显示旧脚本控件，用 _displayed_script_id 定位旧配置
        self._save_displayed_params()
        flow_cfg = self._get_selected_flow_config()
        self._displayed_script_id = flow_cfg["id"] if flow_cfg else None
        self._rebuild_param_panel()
        self._save_daily_config()

    def _on_daily_execution_user_changed(self, _username: str) -> None:
        """切换执行用户时保存旧参数上下文，再加载新用户的生效参数。

        全局参数与用户无关，全局对照区不重建——重建会把用户正在输入的
        全局改动重画掉，真正需要换归属的只有生效面板。
        """
        if not hasattr(self, "_workflow_configs"):
            return
        self._save_displayed_params()
        self._rebuild_effective_panel()

    # ─── 参数面板：生效参数 + 全局参数两块 ────────────────
    #
    # 数据仍然只有两份：会话里的 ``wf_configs[task]``（全局）与用户资料里的
    # ``workflow_params[task]``（独立）。两块面板只是同一批参数定义的两个
    # 投影，各写各的归属，互不覆盖：
    #
    # - 生效面板：共享时显示并写全局；独立时显示合并值并写该用户的独立参数。
    # - 全局面板：只在「使用独立参数」勾选时出现，始终显示并写全局，
    #   用于和独立值逐字段对照，也允许就地调整全局值。

    def _param_form(self) -> TaskParamForm:
        """生效参数表单（懒创建，精简宿主也能用）。"""
        form = getattr(self, "_effective_param_form", None)
        if form is not None:
            return form
        panel = self._param_panel
        layout = panel.layout()
        if layout is None:
            layout = QVBoxLayout(panel)
        form = TaskParamForm(panel)
        layout.addWidget(form)
        # 控件变化：文本类走防抖，其余立即落盘。
        form.value_changed.connect(self._on_param_value_changed)
        form.text_edited.connect(self._on_param_text_edited)
        self._effective_param_form = form
        return form

    def _global_panel(self) -> QGroupBox:
        """全局参数对照面板（懒创建）。"""
        panel = getattr(self, "_global_param_panel", None)
        if panel is not None:
            return panel
        panel = QGroupBox(tr("参数设置（全局参数）"))
        parent = self._param_panel.parentWidget()
        if parent is not None:
            panel.setParent(parent)
            layout = parent.layout()
            if layout is not None:
                layout.addWidget(panel)
        panel.setVisible(False)
        self._global_param_panel = panel
        return panel

    def _global_form(self) -> TaskParamForm:
        form = getattr(self, "_global_param_form", None)
        if form is not None:
            return form
        panel = self._global_panel()
        layout = panel.layout()
        if layout is None:
            layout = QVBoxLayout(panel)
        form = TaskParamForm(panel)
        layout.addWidget(form)
        form.value_changed.connect(self._on_param_value_changed)
        form.text_edited.connect(self._on_param_text_edited)
        self._global_param_form = form
        return form

    def _collect_displayed_params(self, parameters: list[dict]) -> dict:
        """读出生效面板的当前取值，只保留给定定义里声明的参数。

        启动草稿、按草稿重建与保存都读这一份，避免两处各写一套控件遍历
        而慢慢走偏。全局对照面板不参与：草稿只关心本次实际生效的值。
        """
        names = {
            str(item.get("name")) for item in parameters
            if isinstance(item, dict) and item.get("name")
        }
        current = self._param_form().collect()
        return {name: value for name, value in current.items() if name in names}

    def _save_displayed_params(self):
        """保存两块面板里已修改的字段；各自写回自己的归属。"""
        timer = getattr(self, "_param_save_timer", None)
        if timer is not None:
            timer.stop()
        target_cfg = getattr(self, "_displayed_param_cfg", None)
        # 切到批量 Tab 后参数面板不可见，但里面的控件和值仍然有效；批量启动
        # 前的兜底同步必须允许从这个隐藏面板收集。脚本 scope 在下方另行校验。
        if target_cfg is None or not getattr(self, "_param_panel", None):
            return
        self._save_effective_params(target_cfg)
        self._save_global_params(target_cfg)

    def _save_effective_params(self, target_cfg: dict) -> None:
        """生效面板：共享写全局、独立写该用户，临时脚本写内存草稿。"""
        panel = getattr(self, "_param_panel", None)
        form = getattr(self, "_effective_param_form", None)
        # 运行时投影的面板被禁用：那里的值来自历史快照，回写会覆盖当前配置。
        if panel is None or form is None or not panel.isEnabled():
            return
        sid = target_cfg["id"]
        # ⚠️ 专用脚本的参数由专属页面管理，日常页禁止读写
        if target_cfg.get("scope", "daily") != "daily":
            return
        if not target_cfg.get("parameters"):
            return
        params = form.collect()
        baseline = getattr(self, "_displayed_param_baseline", {})
        patch = {key: value for key, value in params.items()
                 if key not in baseline or baseline[key] != value}
        if not patch:
            return
        if sid.startswith(TRANSIENT_ID_PREFIX):
            target_cfg["_saved_params"] = {**target_cfg.get("_saved_params", {}), **patch}
        elif getattr(self, "_displayed_param_is_user_override", False):
            username = getattr(self, "_displayed_param_username", "")
            if username:
                # 用户管理对话框可能已删除当前展示用户，随后才刷新主窗口。
                # 此时旧面板没有可写回的归属，不能重新创建或写入已删除用户。
                user_manager = getattr(self, "_user_manager", None)
                if (user_manager is not None
                        and username not in user_manager.list_users()):
                    return
                from ...core.users.document import User, mutate_user_metadata

                def merge(user: User) -> None:
                    user.workflow_params.setdefault(sid, {}).update(patch)

                mutate_user_metadata(username, merge, self._user_manager._users_dir)
        else:
            from ...core.config.wf_configs import update_wf_config
            update_wf_config(sid, patch)
        self._displayed_param_baseline = deepcopy(params)
        self._clear_param_failure()

    def _save_global_params(self, target_cfg: dict) -> None:
        """全局对照面板：始终写全局配置，改完同步生效面板未覆盖的字段。"""
        panel = getattr(self, "_global_param_panel", None)
        form = getattr(self, "_global_param_form", None)
        # 有无对照内容由表单决定：切到没有独立参数的脚本时它会被清空。
        # 不能看可见性——切到批量 Tab 后整块日常页都不可见，待保存的全局
        # 编辑会因此永远不落盘。
        if panel is None or form is None or not form.has_fields():
            return
        # 运行时投影期间两块面板一起禁用，历史快照绝不能被写回全局。
        if not panel.isEnabled():
            return
        sid = target_cfg["id"]
        if target_cfg.get("scope", "daily") != "daily":
            return
        if not target_cfg.get("parameters"):
            return
        params = form.collect()
        baseline = getattr(self, "_global_param_baseline", {})
        patch = {key: value for key, value in params.items()
                 if key not in baseline or baseline[key] != value}
        if not patch:
            return
        from ...core.config.wf_configs import update_wf_config
        update_wf_config(sid, patch)
        self._global_param_baseline = deepcopy(params)
        self._sync_effective_from_global(patch)
        self._clear_param_failure()

    def _clear_param_failure(self) -> None:
        feedback = getattr(self, "_daily_start_feedback", None)
        if feedback is not None:
            feedback.clear_failure()

    def _user_overridden_keys(self) -> set[str]:
        """当前展示的用户对该任务实际覆盖了哪些字段。"""
        if not getattr(self, "_displayed_param_is_user_override", False):
            return set()
        username = getattr(self, "_displayed_param_username", "")
        cfg = getattr(self, "_displayed_param_cfg", None)
        user_manager = getattr(self, "_user_manager", None)
        if not username or cfg is None or user_manager is None:
            return set()
        from ...core.users.document import get_user_workflow_params
        values = get_user_workflow_params(
            username, str(cfg["id"]), user_manager._users_dir)
        return set(values or {})

    def _sync_effective_from_global(self, patch: dict) -> None:
        """全局值改动后，同步生效面板里该用户没有覆盖、也没有正在编辑的字段。

        已覆盖的字段保持用户值不动（这正是两块面板对照的意义）；正在编辑
        尚未落盘的字段也不动，否则会把用户刚输入的文本盖掉。
        """
        form = getattr(self, "_effective_param_form", None)
        if form is None or not patch:
            return
        current = form.collect()
        baseline = getattr(self, "_displayed_param_baseline", {})
        overridden = self._user_overridden_keys()
        updates: dict = {}
        for name, value in patch.items():
            if name not in current or name in overridden:
                continue
            if baseline.get(name) != current.get(name):
                continue
            updates[name] = value
        if not updates:
            return
        form.set_values(updates)
        for name, value in updates.items():
            baseline[name] = value

    def _on_param_value_changed(self, *_args):
        """非文本控件（勾选、下拉、数字）改动立即落盘。"""
        self._save_displayed_params()

    def _on_param_text_edited(self, *_args):
        """文本输入合并保存，避免每敲一个字都写文件。"""
        if not hasattr(self, "_param_save_timer"):
            self._param_save_timer = QTimer(self)
            self._param_save_timer.setSingleShot(True)
            self._param_save_timer.setInterval(_PARAM_SAVE_DELAY_MS)
            self._param_save_timer.timeout.connect(self._save_pending_params)
        self._param_save_timer.start()

    def _persist_param_change(self, *_args):
        """兼容入口：按触发源决定立即保存还是合并保存。"""
        if isinstance(self.sender(), (QLineEdit, QPlainTextEdit)):
            self._on_param_text_edited()
        else:
            self._save_displayed_params()

    def _save_pending_params(self) -> None:
        """计时器保存失败保留修改，下一次编辑或显式保存可重试。"""
        try:
            self._save_displayed_params()
        except (OSError, RuntimeError) as exc:
            message = tr("参数保存失败，请检查配置文件权限或占用后重试：{error}").format(error=exc)
            logger.warning(message)
            feedback = getattr(self, "_daily_start_feedback", None)
            if feedback is not None:
                feedback.show_failure(message)

    def _save_daily_config(self, *, save_workflow: bool = True):
        """保存日常页脚本选择与分组；关闭时仅保存分组，不反写筛选默认项。

        参数由 _save_displayed_params 按脚本字段级落盘。

        ⚠️ 警告：禁止在此处添加遍历清理其他工作流 wf_configs 的逻辑。
        各工作流的配置由其专属页面自行管理，日常页只负责自己的选择与分组。
        擅自清理不归自己管理的配置会破坏其他工作流的数据完整性。
        """
        from ...core.config import get_interface_store

        flow_cfg = self._get_selected_flow_config()
        state = {"group": self.workflow_group_combo.currentData()}
        if flow_cfg and save_workflow:
            state["workflow_id"] = flow_cfg["id"]
        # 只维护日常页选择；session.daily 中的脚本配置不属于这里。
        try:
            get_interface_store().update_node("daily", state)
        except Exception as e:
            logger.warning(f"保存日常配置失败: {e}")

    def _restore_daily_config(self):
        """启动时恢复日常页脚本选择与参数"""
        from ...core.config import get_interface_store

        # 加载 combo 时 block 了信号，参数面板始终为空，必须手动构建
        state = get_interface_store().get_node("daily", {})
        state = state if isinstance(state, dict) else {}
        workflow_id = state.get("workflow_id", "")
        group_index = self.workflow_group_combo.findData(state.get("group"))
        self.workflow_group_combo.blockSignals(True)
        self.workflow_group_combo.setCurrentIndex(max(0, group_index))
        self.workflow_group_combo.blockSignals(False)
        # 先恢复分组筛选，随后才能在可见候选中恢复脚本。
        self._fill_workflow_combo(workflow_id)

        # 选中上次使用的脚本
        if workflow_id:
            idx = self.workflow_combo.findData(workflow_id)
            if idx >= 0:
                self.workflow_combo.blockSignals(True)
                self.workflow_combo.setCurrentIndex(idx)
                self.workflow_combo.blockSignals(False)

        # 统一设置追踪变量并构建参数面板
        flow_cfg = self._get_selected_flow_config()
        self._displayed_script_id = flow_cfg["id"] if flow_cfg else None
        self._rebuild_param_panel()

    # ─── 面板重建 ─────────────────────────────────────────

    def _param_context(self, flow_cfg: dict | None):
        """算出绘制两块面板所需的一切：定义、生效值、来源、全局值。"""
        from ...core.config.wf_configs import get_wf_config
        from ...core.task_params import (
            merge_task_params,
            parameters_for_env,
            resolve_task_params,
        )

        params = parameters_for_env(
            flow_cfg.get("parameters", []) if flow_cfg else [],
            self._selected_run_env(),
        )
        if not params:
            return None
        selector = getattr(self, "_daily_execution_user_selector", None)
        username = selector.resolve_username() if selector is not None else ""
        user_manager = getattr(self, "_user_manager", None)
        users_dir = getattr(user_manager, "_users_dir", None)
        # 保持脚本配置对象的**同一性**：临时脚本的参数草稿写回该配置对象本身
        # （``_saved_params``），复制一份会让改动落在一个无人读取的副本上。
        cfg = flow_cfg if isinstance(flow_cfg, dict) else {}
        temporary = str(cfg["id"]).startswith(TRANSIENT_ID_PREFIX)
        if temporary:
            saved, source = merge_task_params(
                params, cfg.get("_saved_params", {}), None)
        else:
            saved, source = resolve_task_params(
                str(cfg["id"]), username, params, users_dir)
        global_values, _global_source = merge_task_params(
            params, get_wf_config(str(cfg["id"])), None)
        return {
            "flow_cfg": cfg,
            "definitions": params,
            "values": saved,
            "source": source,
            "global_values": global_values,
            "username": username,
            "temporary": temporary,
        }

    def _current_param_context(self):
        """当前脚本/用户下的面板上下文；脚本不适用时返回 None。"""
        flow_cfg = self._get_selected_flow_config()
        if flow_cfg is None or flow_cfg.get("scope", "daily") != "daily":
            return None
        return self._param_context(flow_cfg)

    def _refresh_workflow_note(self) -> None:
        flow_cfg = self._get_selected_flow_config()
        note = str(flow_cfg.get("note") or "").strip() if flow_cfg else ""
        label = getattr(self, "_workflow_note_label", None)
        if label is not None:
            label.setText(f"{tr('说明')}：{note}" if note else "")
            label.setVisible(bool(note))

    def _apply_panel_state(self, context: dict) -> None:
        """按来源刷新标题、独立参数勾选与全局对照区。"""
        independent = getattr(self, "_independent_params_checkbox", None)
        global_panel = self._global_panel()
        is_user = context["source"] == "user"
        set_title = getattr(self._param_panel, "setTitle", None)
        if callable(set_title):
            set_title(
                tr("参数设置（{name}独立）").format(name=context["username"])
                if is_user else tr("参数设置（共享）"))
        if independent is not None:
            independent.blockSignals(True)
            independent.setEnabled(bool(context["username"]))
            independent.setChecked(is_user)
            independent.blockSignals(False)
            independent.setVisible(not context["temporary"])
        # 全局对照区只在「使用独立参数」时出现；共享模式下本面板就是全局值，
        # 再画一遍只会造成"两份全局"的错觉。
        if is_user:
            global_form = self._global_form()
            global_form.set_parameters(
                context["definitions"], context["global_values"], editable=True)
            self._global_param_baseline = deepcopy(global_form.collect())
        global_panel.setVisible(is_user)

    def _rebuild_effective_panel(self) -> None:
        """只重建生效面板（切执行用户用；全局值与用户无关）。"""
        self._refresh_workflow_note()
        context = self._current_param_context()
        if context is None:
            self._rebuild_param_panel()
            return
        self._displayed_param_cfg = context["flow_cfg"]
        self._displayed_param_username = context["username"]
        self._displayed_param_is_user_override = context["source"] == "user"
        form = self._param_form()
        form.set_parameters(
            context["definitions"], context["values"], editable=True)
        self._displayed_param_baseline = deepcopy(form.collect())
        self._apply_panel_state(context)

    def _rebuild_param_panel(self):
        """重建参数面板：生效参数，以及独立参数下方的全局参数对照区。

        仅对 scope=daily 的脚本绘制参数面板；专用脚本不画面板，
        其参数由专属配置页面管理。
        """
        self._save_displayed_params()
        self._displayed_param_cfg = None
        self._displayed_param_baseline = {}
        self._global_param_baseline = {}
        refresh_feedback = getattr(self, "_refresh_daily_start_feedback", None)
        if callable(refresh_feedback):
            refresh_feedback()
        form = self._param_form()
        global_panel = self._global_panel()
        form.set_parameters([], {})
        global_form = getattr(self, "_global_param_form", None)
        if global_form is not None:
            global_form.set_parameters([], {})
        global_panel.setVisible(False)
        self._refresh_workflow_note()
        independent = getattr(self, "_independent_params_checkbox", None)
        if independent is not None:
            independent.setVisible(False)

        flow_cfg = self._get_selected_flow_config()
        # ⚠️ 专用脚本不画参数面板
        if flow_cfg and flow_cfg.get("scope", "daily") != "daily":
            self._param_panel.setVisible(False)
            return
        context = self._param_context(flow_cfg) if flow_cfg else None
        if context is None:
            self._param_panel.setVisible(False)
            return

        self._displayed_param_cfg = context["flow_cfg"]
        self._displayed_param_username = context["username"]
        self._displayed_param_is_user_override = context["source"] == "user"
        self._displayed_param_defs = list(context["definitions"])
        form.set_parameters(
            context["definitions"], context["values"], editable=True)
        self._displayed_param_baseline = deepcopy(form.collect())
        self._apply_panel_state(context)
        self._param_panel.setVisible(True)

    def _refresh_param_visibility(self) -> None:
        """按 require 依赖重算显隐（表单内部维护行号，这里只转发）。"""
        form = getattr(self, "_effective_param_form", None)
        if form is not None:
            form.refresh_visibility()

    def _on_independent_params_toggled(self, enabled: bool) -> None:
        """在共享参数与当前用户的独立参数之间切换。

        先落盘两块面板的待保存改动，再按归属切换；切换后重建面板，独立时
        下方会多出全局参数对照区。关闭独立参数会丢弃该用户已保存的独立值，
        因此由调用方负责二次确认。
        """
        self._save_displayed_params()
        flow_cfg = self._get_selected_flow_config()
        selector = getattr(self, "_daily_execution_user_selector", None)
        username = selector.resolve_username() if selector is not None else ""
        if not flow_cfg or not username:
            return
        workflow_id = str(flow_cfg["id"])
        user_manager = getattr(self, "_user_manager", None)
        users_dir = getattr(user_manager, "_users_dir", None)
        if enabled:
            from ...core.users.document import set_user_workflow_params
            set_user_workflow_params(
                username, workflow_id, self._param_form().collect(), users_dir)
        else:
            from ...core.users.document import delete_user_workflow_params
            delete_user_workflow_params(username, workflow_id, users_dir)
        self._rebuild_param_panel()
