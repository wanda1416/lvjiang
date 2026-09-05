"""会话状态持久化混入类 - session.json 的 ui_state 与 daily 两个节点

``ui_state.main_page`` 存窗口尺寸、分栏比例与左右 Tab 页签；``daily`` 存
日常页当前选中的脚本，脚本参数则按脚本 id 落在 wf_configs 里。

⚠️ 日常页只负责自己的 workflow_id：各工作流的参数由其专属页面管理，
这里禁止遍历清理不归自己管的 wf_configs（详见 _save_daily_config）。
同理，参数面板只对 scope=daily 的脚本绘制与读写。

依赖主类提供：
    _main_splitter / _left_tabs / tabs / workflow_combo / _param_panel /
    _param_layout / _workflow_note_label / _workflow_configs /
    _displayed_script_id / _get_selected_flow_config
"""

from loguru import logger
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QWidget,
)

from ...i18n import tr
from ..widgets import FlowLayout


class _FlowContainer(QWidget):
    """FlowLayout 容器，正确传递 heightForWidth 给外层 QFormLayout。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._flow = None
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        policy.setVerticalPolicy(QSizePolicy.Policy.Fixed)
        self.setSizePolicy(policy)

    def set_flow_layout(self, flow: FlowLayout):
        self._flow = flow

    def hasHeightForWidth(self):
        return self._flow is not None and self._flow.hasHeightForWidth()

    def heightForWidth(self, width: int) -> int:
        if self._flow is not None:
            return self._flow.heightForWidth(width)
        return super().heightForWidth(width)


class UiStateMixin:
    """UI 状态与日常页配置持久化混入类"""

    @staticmethod
    def _migrate_ui_state():
        """一次性迁移：旧扁平 ui_state → 按页面归档嵌套结构"""
        from ...core.config import get_session_store
        store = get_session_store()
        state = store.get_node("ui_state", {})
        if not isinstance(state, dict) or "main_page" in state:
            return  # 已是新格式或为空

        migrated = False

        # main_page
        old_main_keys = {"window_size", "splitter_sizes"}
        if any(k in state for k in old_main_keys):
            page = {}
            for k in old_main_keys:
                if k in state:
                    page[k] = state.pop(k)
            state["main_page"] = page
            migrated = True

        # scene_editor
        se_prefix = "scene_editor_"
        se_keys = [k for k in state if k.startswith(se_prefix)]
        if se_keys:
            se = state.get("scene_editor", {})
            for k in se_keys:
                se[k[len(se_prefix):]] = state.pop(k)
            state["scene_editor"] = se
            migrated = True

        # reference_manager
        if "reference_manager_size" in state:
            rm = state.get("reference_manager", {})
            rm["size"] = state.pop("reference_manager_size")
            state["reference_manager"] = rm
            migrated = True

        if migrated:
            store.set_node("ui_state", state)

    def _restore_ui_state(self):
        """启动时恢复窗口大小、左右分栏比例和当前 Tab 页签"""
        from ...core.config import load_ui_page_state
        page = load_ui_page_state("main_page")
        size = page.get("window_size")
        if isinstance(size, list) and len(size) == 2:
            self.resize(int(size[0]), int(size[1]))
        sizes = page.get("splitter_sizes")
        if isinstance(sizes, list) and len(sizes) == 2 and all(s > 0 for s in sizes):
            self._main_splitter.setSizes([int(s) for s in sizes])
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
            update_ui_page_state("main_page", {
                "window_size": [self.width(), self.height()],
                "splitter_sizes": self._main_splitter.sizes(),
                "left_tab_index": self._left_tabs.currentIndex(),
                "right_tab_index": self.tabs.currentIndex(),
            })
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
        self._rebuild_param_panel()
        # 更新追踪为当前脚本
        flow_cfg = self._get_selected_flow_config()
        self._displayed_script_id = flow_cfg["id"] if flow_cfg else None
        self._save_daily_config()

    def _save_displayed_params(self):
        """将当前参数面板的值写入 _displayed_script_id 对应的配置项

        仅对 scope=daily 的脚本生效；专用脚本的参数由专属页面管理，
        日常页禁止读写。
        """
        sid = getattr(self, '_displayed_script_id', None)
        # 切到批量 Tab 后参数面板不可见，但里面的控件和值仍然有效；批量启动
        # 前的兜底同步必须允许从这个隐藏面板收集。脚本 scope 在下方另行校验。
        if not sid or not self._param_panel:
            return
        # 找到对应配置项，临时用 _collect_flow_params 的逻辑从面板搜集值
        target_cfg = next((c for c in self._workflow_configs if c["id"] == sid), None)
        if not target_cfg:
            return
        # ⚠️ 专用脚本的参数由专属页面管理，日常页禁止读写
        if target_cfg.get("scope", "daily") != "daily":
            return
        if not target_cfg.get("parameters"):
            return
        params = {}
        from PyQt6.QtWidgets import QCheckBox, QComboBox, QSpinBox, QWidget
        for param_def in target_cfg.get("parameters", []):
            name = param_def["name"]
            # checkgroup：从容器内收集各复选框状态为 dict
            if param_def.get("type") == "checkgroup":
                container = self._param_panel.findChild(QWidget, name)
                if container is not None:
                    group = {}
                    for chk in container.findChildren(QCheckBox):
                        group[chk.objectName()] = chk.isChecked()
                    params[name] = group
                continue
            widget = self._param_panel.findChild(QSpinBox, name)
            if widget is not None:
                params[name] = str(widget.value())
                continue
            widget = self._param_panel.findChild(QCheckBox, name)
            if widget is not None:
                params[name] = widget.isChecked()
                continue
            widget = self._param_panel.findChild(QComboBox, name)
            if widget is not None:
                data = widget.currentData()
                params[name] = data if data is not None else widget.currentText()
        target_cfg["_saved_params"] = params
        from ...core.config.wf_configs import update_wf_config
        update_wf_config(sid, params)

    def _persist_param_change(self, *_args):
        """参数控件变更后立即写回共享配置。

        日常与批量执行共用 ``wf_configs``。如果只在切换脚本或单独运行时
        保存，用户改完参数直接启动批量时，批量线程会读到上一次的值。
        """
        self._save_displayed_params()

    def _save_daily_config(self):
        """保存日常页脚本选择；参数由 _save_displayed_params 按脚本字段级落盘

        ⚠️ 警告：禁止在此处添加遍历清理其他工作流 wf_configs 的逻辑。
        各工作流的配置由其专属页面自行管理，日常页只负责自己的 workflow_id。
        擅自清理不归自己管理的配置会破坏其他工作流的数据完整性。
        """
        from ...core.config import get_session_store

        flow_cfg = self._get_selected_flow_config()
        if not flow_cfg:
            return

        # workflow_id 仍存 daily 节点（UI 状态，非工作流配置）
        try:
            get_session_store().update_node("daily", {"workflow_id": flow_cfg["id"]})
        except Exception as e:
            logger.warning(f"保存日常配置失败: {e}")

    def _restore_daily_config(self):
        """启动时恢复日常页脚本选择与参数"""
        from ...core.config import get_session_store
        from ...core.config.wf_configs import get_wf_config

        # 加载 combo 时 block 了信号，参数面板始终为空，必须手动构建
        daily = get_session_store().get_node("daily", {})
        if not isinstance(daily, dict):
            daily = {}
        workflow_id = daily.get("workflow_id")

        # 从统一存储读取各脚本参数；仅对 scope=daily 的脚本生效
        # 专用脚本的参数由专属页面管理，日常页禁止读写
        for cfg in self._workflow_configs:
            if cfg.get("scope", "daily") != "daily":
                continue
            if not cfg.get("parameters"):
                continue
            saved = get_wf_config(cfg["id"])
            if saved:
                cfg["_saved_params"] = saved

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

    def _rebuild_param_panel(self):
        """重建参数面板

        仅对 scope=daily 的脚本绘制参数面板；专用脚本不画面板，
        其参数由专属配置页面管理。
        """
        while self._param_layout.rowCount() > 0:
            self._param_layout.removeRow(0)
        flow_cfg = self._get_selected_flow_config()
        note = str(flow_cfg.get("note") or "").strip() if flow_cfg else ""
        self._workflow_note_label.setText(f"{tr('说明')}：{note}" if note else "")
        self._workflow_note_label.setVisible(bool(note))
        # ⚠️ 专用脚本不画参数面板
        if flow_cfg and flow_cfg.get("scope", "daily") != "daily":
            self._param_panel.setVisible(False)
            return
        params = flow_cfg.get("parameters", []) if flow_cfg else []
        if not params:
            self._param_panel.setVisible(False)
            return
        saved = flow_cfg.get("_saved_params", {}) if flow_cfg else {}
        for param_def in params:
            name = param_def["name"]
            label = param_def.get("label", name)
            param_type = param_def.get("type", "select")
            # 已保存值优先于定义默认值
            default = saved.get(name, param_def.get("default"))
            options = param_def.get("options", [])
            if param_type == "number":
                spin = QSpinBox()
                spin.setObjectName(name)
                spin.setRange(param_def.get("min", 0), param_def.get("max", 999999))
                spin.setValue(int(default) if default is not None else 1)
                spin.valueChanged.connect(self._persist_param_change)
                self._param_layout.addRow(label + ":", spin)
            elif param_type == "bool":
                chk = QCheckBox()
                chk.setObjectName(name)
                if isinstance(default, str):
                    chk.setChecked(default.lower() in ("true", "1", "yes", "on"))
                else:
                    chk.setChecked(bool(default))
                chk.toggled.connect(self._persist_param_change)
                self._param_layout.addRow(label + ":", chk)
            elif param_type == "checkgroup":
                # 分组复选框：值为 dict {key: bool}，使用 FlowLayout 自动换行
                container = _FlowContainer()
                container.setObjectName(name)
                flow = FlowLayout(container, spacing=6)
                flow.setContentsMargins(0, 0, 0, 0)
                container.set_flow_layout(flow)
                if isinstance(default, dict):
                    saved_dict = default
                else:
                    saved_dict = {}
                for opt in options:
                    if isinstance(opt, dict):
                        opt_key = opt["value"]
                        opt_label = opt.get("label", opt_key)
                    else:
                        opt_key = str(opt)
                        opt_label = str(opt)
                    chk = QCheckBox(opt_label)
                    chk.setObjectName(opt_key)
                    chk.setChecked(bool(saved_dict.get(opt_key, True)))
                    chk.toggled.connect(self._persist_param_change)
                    flow.addWidget(chk)
                # 标签独占一行；选项从下一行起使用表单的完整宽度。
                self._param_layout.addRow(QLabel(label + ":"))
                self._param_layout.addRow(container)
            else:
                combo = QComboBox()
                combo.setObjectName(name)
                if param_type == "select" and options:
                    for opt in options:
                        if isinstance(opt, dict):
                            combo.addItem(opt["label"], opt["value"])
                        else:
                            combo.addItem(str(opt), str(opt))
                    if default is not None:
                        idx = combo.findData(str(default))
                        if idx >= 0:
                            combo.setCurrentIndex(idx)
                combo.currentIndexChanged.connect(self._persist_param_change)
                self._param_layout.addRow(label + ":", combo)
        self._param_panel.setVisible(True)
