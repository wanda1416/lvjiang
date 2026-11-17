"""图库管理对话框 - 新增参考图与图库管理两个独立 Tab"""

from typing import Callable

import numpy as np
from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from lvjiang.core.reference_db import ReferenceDatabase
from lvjiang.ui.widgets.combo_box import AutoWidthComboBox

from ...i18n import tr, tr_label
from ..widgets.button_styles import apply_button_style
from ..widgets.dialog_guards import EscapeCloseConfirmationMixin
from .browser_panel import BrowserPanel
from .canvas import ReferenceCanvas
from .combo_sizing import set_combo_minimum_character_capacity
from .grid_panel import GridPanel
from .meta_schema_panel import MetaSchemaPanel
from .template_form_panel import TemplateFormPanel
from .template_panel import TemplatePanel

#: 右侧面板固定分配：画布 3 ∶ 右侧 2，且右侧有最小宽度，避免切换模式时重排
_CANVAS_STRETCH = 3
_RIGHT_STRETCH = 2
_RIGHT_PANEL_MIN_WIDTH = 420

#: 分段开关的共用样式：选中块上边距 +1、下边距 -1，视觉上压下去一格；
#: 底色与边框都加深一档，与未选中的平铺态拉开层次（总高度不变，不会跳动）。
_SEGMENT_BASE_STYLE = (
    "QPushButton { background-color: palette(button); color: palette(button-text);"
    " border: 1px solid palette(mid); padding: 4px 18px; font-weight: 600; }"
    "QPushButton:hover { background-color: palette(midlight); }"
    "QPushButton:checked { background-color: palette(midlight);"
    " border: 1px solid palette(dark); padding: 5px 18px 3px 18px; }"
)
#: 左右两块只在外侧保留圆角、中间不留缝隙，读起来是一个控件
_SEGMENT_CORNERS = (
    "QPushButton { border-top-left-radius: 5px; border-bottom-left-radius: 5px;"
    " border-top-right-radius: 0; border-bottom-right-radius: 0; border-right: none; }",
    "QPushButton { border-top-right-radius: 5px; border-bottom-right-radius: 5px;"
    " border-top-left-radius: 0; border-bottom-left-radius: 0; }",
)

_SPACE_COMBO_CHARACTER_CAPACITY = 6


def _set_space_combo_minimum_capacity(combo: AutoWidthComboBox) -> int:
    """Keep enough room for six full-width Chinese characters."""
    return set_combo_minimum_character_capacity(
        combo, _SPACE_COMBO_CHARACTER_CAPACITY
    )


class ReferenceManagerDialog(EscapeCloseConfirmationMixin, QDialog):
    """图库管理对话框

    三个顶级 Tab，完全独立的工作流：
      - 新增参考图：导入截图 → 网格切割 → 编辑 cell 信息 → 提交到图库
      - 图库管理：按分组浏览、搜索、编辑已有参考图
      - 元数据定义：定义名称/分组之外的 meta 字段
    """

    def __init__(self, parent=None, screenshot_callback: Callable | None = None,
                 persist_ui_state: bool = True):
        """``persist_ui_state=False`` 表示这是一次性实例（预览、测试、脚本）。

        界面参数一律不落盘：窗口尺寸与网格默认值写在真实
        ``config/session/interface.json``，测试或离屏预览改它等于污染用户配置，
        所以由调用方显式关掉，而不是靠环境变量猜。
        """
        super().__init__(parent)
        self.setWindowTitle(tr("图库管理"))
        self.resize(1200, 800)
        # 启用最小化/最大化按钮
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        # 加载图库工具自身的界面参数
        from lvjiang.core.config import ReferenceGridConfig, load_reference_grid
        self._grid_config = ReferenceGridConfig(**load_reference_grid())

        self._db = ReferenceDatabase()
        self._db.load()
        self._source_name = ""
        self._data_changed = False  # 跟踪是否有数据变动
        self._screenshot_callback = screenshot_callback
        self._persist_ui_state = persist_ui_state
        self._init_ui()
        self._restore_window_size()

    def _restore_window_size(self):
        """从 interface.json 恢复窗口大小"""
        from lvjiang.core.config import load_ui_page_state
        rm = load_ui_page_state("reference_manager")
        if not isinstance(rm, dict):
            return
        size = rm.get("size")
        if isinstance(size, list) and len(size) == 2:
            self.resize(int(size[0]), int(size[1]))

    def _save_window_size(self):
        """保存窗口大小到 interface.json（写入 ui_state.reference_manager）"""
        if not self._persist_ui_state:
            return
        try:
            from lvjiang.core.config import update_ui_page_state
            update_ui_page_state(
                "reference_manager", {"size": [self.width(), self.height()]})
        except Exception as e:
            logger.warning(f"保存图库管理器窗口大小失败: {e}")

    def closeEvent(self, event):
        self._save_window_size()
        super().closeEvent(event)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        # ── 图库空间栏（图库内部概念，外部消费方无感）──
        # 这里只做「编辑哪些图」的空间管理与展示，不再提供「激活空间」：
        # 运行时用哪个空间由主页面下拉或方案决定，两个入口各自持有一份状态会打架。
        space_bar = QHBoxLayout()
        space_bar.addWidget(QLabel(tr_label('图库空间')))
        self._space_combo = AutoWidthComboBox()
        self._fill_space_combo()
        self._select_space(self._db.get_active_space())
        _set_space_combo_minimum_capacity(self._space_combo)
        self._space_combo.currentIndexChanged.connect(self._on_space_changed)
        space_bar.addWidget(self._space_combo)
        self._btn_edit_space = QPushButton(tr("编辑"))
        self._btn_edit_space.clicked.connect(self._on_edit_space)
        space_bar.addWidget(self._btn_edit_space)
        self._btn_refresh_space = QPushButton(tr("刷新"))
        self._btn_refresh_space.clicked.connect(self._on_refresh_spaces)
        space_bar.addWidget(self._btn_refresh_space)
        self._btn_new_space = QPushButton(tr("新建"))
        self._btn_new_space.clicked.connect(self._on_new_space)
        space_bar.addWidget(self._btn_new_space)
        self._btn_del_space = QPushButton(tr("删除"))
        self._btn_del_space.clicked.connect(self._on_delete_space)
        space_bar.addWidget(self._btn_del_space)
        apply_button_style(
            self._btn_edit_space, self._btn_refresh_space, self._btn_new_space)
        apply_button_style(self._btn_del_space, variant="danger")
        self._refresh_del_space_enabled()
        space_bar.addStretch()
        self._active_space_label = QLabel()
        self._update_space_labels()
        space_bar.addWidget(self._active_space_label)
        main_layout.addLayout(space_bar)

        # ── 顶级 Tab ──
        self._tabs = QTabWidget()

        # Tab 1: 新增图像（右侧切换写参考图还是模板图）
        self._add_tab = self._build_add_tab()
        self._tabs.addTab(self._add_tab, tr("新增图像"))

        # Tab 2: 参考图管理（浏览与编辑已有参考图）
        self._browser = BrowserPanel(self._db)
        self._browser.set_known_groups(self._db.get_groups())
        self._browser.refresh()
        self._browser.data_changed.connect(self._on_data_changed)
        self._tabs.addTab(self._browser, tr("参考图管理"))

        # Tab 3: 参考图元数据（名称/分组之外的 meta 字段）
        self._meta_panel = MetaSchemaPanel(self._db)
        self._meta_panel.schema_changed.connect(self._on_schema_changed)
        self._tabs.addTab(self._meta_panel, tr("参考图元数据"))

        # Tab 4: 模板图管理（分组 + 引用 + 所有权受控的操作）
        self._template_panel = TemplatePanel()
        self._template_panel.data_changed.connect(self._on_data_changed)
        self._tabs.addTab(self._template_panel, tr("模板图管理"))

        self._tabs.currentChanged.connect(self._on_tab_changed)
        main_layout.addWidget(self._tabs)
        self._update_space_controls()

    # ── 图库空间切换 ──

    def _fill_space_combo(self):
        """填充空间下拉：显示本地化名字、itemData 存稳定 key；系统空间置灰"""
        from PyQt6.QtGui import QBrush, QColor
        user_mode = self._db.is_user_mode()
        self._space_combo.clear()
        for key in self._db.get_spaces():
            self._space_combo.addItem(self._db.space_label(key), key)
        if not user_mode:
            return
        model = self._space_combo.model()
        for row in range(self._space_combo.count()):
            if not self._db.is_system_space(self._space_combo.itemData(row)):
                continue
            item = model.item(row)
            item.setForeground(QBrush(QColor(Qt.GlobalColor.gray)))
            item.setToolTip(tr("系统空间：可编辑其中内容，但不能删除该空间"))

    def _refresh_del_space_enabled(self):
        """删除按钮跟随下拉选中项：不可删时禁用并把原因写进 tooltip"""
        reason = self._db.can_delete_space(self._space_combo.currentData())
        self._btn_del_space.setEnabled(not reason)
        self._btn_del_space.setToolTip(reason or tr("删除选中的图库空间（含其全部参考图）"))

    def _on_space_changed(self, index: int):
        """下拉切换**正在编辑的空间**：只重载本对话框，不改运行空间。

        运行时用哪个图库由主页面下拉 / 连接方案决定（``actives.space``）；
        管理界面不能反作用于它，否则方案固定的空间会被这里悄悄改掉。
        """
        self._refresh_del_space_enabled()
        if index < 0:
            return
        name = self._space_combo.itemData(index)
        if not name or name == self._db.get_active_space():
            return
        if not self._db.open_space(name):
            self._space_combo.blockSignals(True)
            self._select_space(self._db.get_active_space())
            self._space_combo.blockSignals(False)
            return
        self._refresh_panels()

    def _on_edit_space(self):
        """编辑选中空间的展示名（多语言）；key 不可改。"""
        name = self._space_combo.currentData()
        if not name:
            return
        from .space_name_dialog import SpaceNameDialog

        dialog = SpaceNameDialog(
            self, space_key=name, current_name=self._current_space_name(name),
            existing_keys=self._db.get_spaces())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not self._db.set_space_name(name, dialog.names()):
            QMessageBox.warning(
                self, tr("编辑图库空间"), tr("保存空间展示名失败，详见日志"))
            return
        self._data_changed = True
        self._reload_space_combo(self._db.get_active_space())
        self._refresh_panels()

    def _current_space_name(self, key: str) -> object:
        """磁盘上该空间 ``name`` 的原值（dict / 字面值 / None），供编辑框预填。"""
        from lvjiang.core.config import load_yaml

        for root in (self._db.local_spaces_dir, self._db.system_spaces_dir):
            path = root / f"{key}.yaml"
            if path.is_file():
                try:
                    return load_yaml(path).get("name")
                except Exception as exc:  # noqa: BLE001 读坏就当没有名字
                    logger.warning(f"读取空间展示名失败 {path}: {exc}")
                    return None
        return None

    def _on_refresh_spaces(self):
        """重扫空间目录并重载当前空间：外部改过 yaml / 图片目录时用它对齐。"""
        self._db.load()
        self._reload_space_combo(self._db.get_active_space())
        self._refresh_panels()
        logger.info("图库空间已刷新")

    def _on_new_space(self):
        """新建空间：与「编辑」共用同一个对话框，只是 key 可填。

        创建后只切到新空间查看/编辑（``open_space``），不改运行时空间。
        """
        from .space_name_dialog import SpaceNameDialog

        dialog = SpaceNameDialog(
            self, creating=True, existing_keys=self._db.get_spaces())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key = dialog.space_key()
        if not self._db.create_space(key, dialog.names()):
            QMessageBox.warning(self, tr("新建空间"), tr("无法创建空间，详见日志"))
            return
        self._data_changed = True
        self._db.open_space(key)
        self._reload_space_combo(key)
        self._refresh_panels()

    def _on_delete_space(self):
        """删除选中空间：二次确认（写明连带删除的参考图数量）后落盘"""
        name = self._space_combo.currentData()
        reason = self._db.can_delete_space(name)
        if reason:
            QMessageBox.warning(self, tr("删除空间"), reason)
            self._refresh_del_space_enabled()
            return
        count = len(self._db.entries) if name == self._db.get_active_space() else None
        detail = f"，其中 {count} 条参考图将一并删除" if count else "及其全部参考图"
        if QMessageBox.question(
            self, tr("删除空间"),
            f"确定删除图库空间「{name}」{detail}？此操作不可撤销。",
        ) != QMessageBox.StandardButton.Yes:
            return
        if not self._db.delete_space(name):
            QMessageBox.warning(self, tr("删除空间"), f"删除空间「{name}」失败，详见日志")
            return
        self._reload_space_combo(self._db.get_active_space())
        self._refresh_panels()

    def _reload_space_combo(self, current: str):
        """重填下拉并选中指定项（静默，不触发切换信号）"""
        self._space_combo.blockSignals(True)
        self._fill_space_combo()
        self._select_space(current)
        self._space_combo.blockSignals(False)
        self._refresh_del_space_enabled()

    def _update_space_labels(self) -> None:
        """左边是运行时空间（主页面/方案决定），右边是这里正在编辑的。"""
        runtime = self._db.runtime_space()
        editing = self._db.get_active_space()
        runtime_label = self._db.space_label(runtime)
        if runtime == editing:
            text = f"运行用：{runtime_label}（正在编辑）"
        else:
            text = (f"运行用：{runtime_label} ｜ 正在编辑："
                    f"{self._db.space_label(editing)}")
        self._active_space_label.setText(text)

    def _select_space(self, key: str) -> None:
        """按空间 key 选中下拉项（显示的是本地化名字，不能用文本匹配）。"""
        index = self._space_combo.findData(key)
        self._space_combo.setCurrentIndex(index if index >= 0 else 0)

    def _refresh_panels(self):
        """空间切换后刷新依赖 db 的面板

        不同空间可有不同 meta_schema：除列表/网格外，动态 meta 控件
        （browser 筛选/编辑字段、grid cell 编辑器）也必须按新空间 schema 重建，
        否则旧 schema 的 key 会被写进新空间。
        """
        groups = self._db.get_groups()
        self._browser.set_known_groups(groups)
        self._browser.rebuild_meta_fields()
        self._browser.refresh()
        self._meta_panel.reload()
        self._grid_panel.set_known_groups(groups, self._db.get_all_labels_by_group())
        self._grid_panel.set_meta_fields(self._db.get_meta_schema())
        self._update_space_labels()

    # ── Tab 1: 新增参考图 ──

    def _build_add_tab(self) -> QWidget:
        """构建「新增图像」Tab：画布共用，右侧按目标切换参考图/模板图。"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 写入目标：同一张截图既可能切参考图，也可能截模板图。
        # 两个互斥的切换按钮比下拉更直接（一眼看到只有两种选择、当前是哪个）。
        target_row = QHBoxLayout()
        target_row.setSpacing(0)
        target_row.addWidget(QLabel(tr_label("写入目标")))
        self._write_target = "reference"
        self._target_group = QButtonGroup(self)
        self._target_buttons: dict[str, QPushButton] = {}
        for index, (key, text) in enumerate(
                (("reference", tr("参考图")), ("template", tr("模板图")))):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            # 分段开关：两块拼成一个控件，选中那块用「下沉」的观感
            button.setStyleSheet(
                _SEGMENT_BASE_STYLE + _SEGMENT_CORNERS[index])
            self._target_group.addButton(button)
            self._target_buttons[key] = button
            target_row.addWidget(button)
        self._target_buttons["reference"].setChecked(True)
        self._target_group.buttonClicked.connect(self._on_target_changed)
        target_row.addStretch()
        layout.addLayout(target_row)

        # 顶部工具栏
        toolbar = QToolBar()
        self._import_btn = QPushButton(tr("导入图片"))
        self._import_btn.clicked.connect(self._on_import)
        toolbar.addWidget(self._import_btn)

        self._paste_btn = QPushButton(tr("粘贴 (Ctrl+V)"))
        self._paste_btn.clicked.connect(self._on_paste)
        toolbar.addWidget(self._paste_btn)

        self._screenshot_btn = QPushButton(tr("截图"))
        self._screenshot_btn.clicked.connect(self._on_screenshot)
        toolbar.addWidget(self._screenshot_btn)

        self._clear_btn = QPushButton(tr("清空画布"))
        self._clear_btn.clicked.connect(self._on_clear)
        toolbar.addWidget(self._clear_btn)

        toolbar.addSeparator()

        self._select_all_btn = QPushButton(tr("全选"))
        self._select_all_btn.setEnabled(False)
        toolbar.addWidget(self._select_all_btn)

        self._deselect_all_btn = QPushButton(tr("全不选"))
        self._deselect_all_btn.setEnabled(False)
        toolbar.addWidget(self._deselect_all_btn)

        apply_button_style(
            self._import_btn,
            self._paste_btn,
            self._screenshot_btn,
            self._clear_btn,
            self._select_all_btn,
            self._deselect_all_btn,
            variant="neutral",
        )

        toolbar.addSeparator()
        self._source_label = QLabel(tr("  未导入图片"))
        toolbar.addWidget(self._source_label)

        layout.addWidget(toolbar)

        # 主体：画布 + 网格面板
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 信息栏放在画布与右侧面板之上：两列从同一条基线开始，不会错开
        self._info_label = QLabel(tr("未框选区域"))
        self._info_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        self._info_label.setMaximumHeight(20)
        layout.addWidget(self._info_label)

        # 画布容器
        canvas_container = QWidget()
        canvas_layout = QVBoxLayout(canvas_container)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(0)

        self._canvas = ReferenceCanvas()
        self._canvas.region_changed.connect(self._on_region_changed)
        self._canvas.selection_changed.connect(self._on_selection_changed)
        canvas_layout.addWidget(self._canvas)

        splitter.addWidget(canvas_container)

        # 连接全选/全不选按钮（在 _canvas 创建后）
        self._select_all_btn.clicked.connect(self._canvas.select_all_cells)
        self._deselect_all_btn.clicked.connect(self._canvas.deselect_all_cells)

        self._grid_panel = GridPanel(
            rows=self._grid_config.rows,
            cols=self._grid_config.cols,
            gap=self._grid_config.gap,
            height=self._grid_config.height,
            width=self._grid_config.width,
        )
        # 初始化已知分组列表（供批量分组下拉使用）
        self._grid_panel.set_known_groups(self._db.get_groups(), self._db.get_all_labels_by_group())
        # 同步当前 meta 字段定义（供 cell 编辑器生成输入）
        self._grid_panel.set_meta_fields(self._db.get_meta_schema())
        # 第一组：网格参数实时响应
        self._grid_panel.grid_params_changed.connect(self._on_grid_params_changed)
        # 五项网格参数手动改动 → 落盘 interface.json（下次打开作为默认值）
        self._grid_panel.grid_defaults_changed.connect(self._on_grid_defaults_changed)
        # 第二组：生成网格
        self._grid_panel.generate_grid_requested.connect(self._on_generate_grid)
        self._grid_panel.clear_grid_requested.connect(self._on_clear_grid)
        # 第三组：执行切割
        self._grid_panel.execute_requested.connect(self._on_execute_cut)
        self._grid_panel.submit_cells.connect(self._on_submit_cells)
        # 模板图模式：单矩形裁剪 + 命名 + 录制尺寸 + 测试匹配
        self._template_form = TemplateFormPanel()
        self._template_form.saved.connect(self._on_template_saved)

        # 右侧两块共用一个堆叠容器：容器宽高取两页的最大值，切换模式时
        # 分割器不需要重新分配空间，画面不会跳一下
        self._right_stack = QStackedWidget()
        self._right_stack.addWidget(self._grid_panel)
        self._right_stack.addWidget(self._template_form)
        self._right_stack.setCurrentWidget(self._grid_panel)
        self._right_stack.setMinimumWidth(_RIGHT_PANEL_MIN_WIDTH)
        splitter.addWidget(self._right_stack)

        # 固定分配：画布 3、右侧 2，切模式不改比例
        splitter.setStretchFactor(0, _CANVAS_STRETCH)
        splitter.setStretchFactor(1, _RIGHT_STRETCH)
        splitter.setChildrenCollapsible(False)
        self._add_splitter = splitter

        layout.addWidget(splitter)
        return widget

    # ── 工具栏槽函数 ──

    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择参考图片"), "",
            tr("图片文件 (*.png *.jpg *.bmp *.webp)")
        )
        if not path:
            return

        import cv2
        from PIL import Image
        try:
            # PIL 读中文路径 → numpy → cv2 BGR
            img_rgb = np.array(Image.open(path))
            img = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
        except Exception as e:
            QMessageBox.warning(self, tr("错误"), f"无法读取图片:\n{path}\n{e}")
            return

        self._source_name = path.split("/")[-1].split("\\")[-1]
        self._canvas.set_image(img)
        self._update_source_label()
        self._sync_template_source()

    def _on_paste(self):
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        image = clipboard.image()
        if image.isNull():
            QMessageBox.information(self, tr("提示"), tr("剪贴板中没有图片"))
            return

        # QImage → numpy BGR
        qimg = image.convertToFormat(QImage.Format.Format_RGBA8888)
        w, h = qimg.width(), qimg.height()
        ptr = qimg.bits()
        ptr.setsize(w * h * 4)
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))
        import cv2
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)

        self._source_name = "clipboard"
        self._canvas.set_image(bgr)
        self._update_source_label()
        self._sync_template_source()

    def _on_screenshot(self):
        """通过回调截取当前窗口/设备截图，加载到画布"""
        if self._screenshot_callback is None:
            QMessageBox.information(self, tr("提示"), tr("截图功能不可用，请先在主窗口定位窗口或连接设备"))
            return

        try:
            result = self._screenshot_callback()
        except Exception as e:
            logger.error(f"截图回调异常: {e}")
            QMessageBox.warning(self, tr("截图失败"), f"截图过程出错: {e}")
            return

        new_image, error_msg = result if isinstance(result, tuple) else (result, None)
        if new_image is None:
            QMessageBox.warning(self, tr("截图失败"), error_msg or tr("无法获取截图"))
            return

        self._source_name = "screenshot"
        self._canvas.set_image(new_image)
        self._update_source_label()
        self._sync_template_source()
        logger.info(f"图库管理：截图载入画布 {new_image.shape[1]}x{new_image.shape[0]}")

    def _on_clear(self):
        self._canvas.clear_image()
        self._source_name = ""
        self._update_source_label()
        self._sync_template_source()

    def _update_source_label(self):
        if self._source_name:
            self._source_label.setText(f"  来源: {self._source_name}")
        else:
            self._source_label.setText(tr("  未导入图片"))

    # ── 写入目标：参考图 / 模板图 ──

    def _is_template_target(self) -> bool:
        return self._write_target == "template"

    def set_write_target(self, key: str) -> None:
        """程序化切换写入目标（界面用上面的切换按钮，这里给外部/测试用）。"""
        if key not in self._target_buttons:
            raise ValueError(f"未知的写入目标: {key!r}")
        self._write_target = key
        for name, button in self._target_buttons.items():
            button.setChecked(name == key)
        self._on_target_changed()

    def _on_target_changed(self, _button=None) -> None:
        for name, button in self._target_buttons.items():
            if button.isChecked():
                self._write_target = name
        template_mode = self._is_template_target()
        # 两页共用同一个容器，切换只换显示内容；这里显式沿用切换前的分配，
        # 连用户手动拖过的宽度也不会被重新协商
        sizes = self._add_splitter.sizes()
        self._right_stack.setCurrentWidget(
            self._template_form if template_mode else self._grid_panel)
        self._add_splitter.setSizes(sizes)
        self._canvas.set_show_grid(
            False if template_mode else self._canvas.get_grid_rect() is not None)
        self._sync_template_source()
        if template_mode:
            self._template_form.refresh_groups()
        self._update_space_controls()

    def _sync_template_source(self) -> None:
        """把画布图片与框选同步给模板表单（记录尺寸/裁剪都基于它）。"""
        self._template_form.update_source(
            self._canvas.get_image(), self._canvas.get_region_pixels())

    def _on_template_saved(self, name: str) -> None:
        self._data_changed = True
        self._template_panel.refresh()
        logger.info(f"模板图已保存: {name}")

    # ── 页签切换与空间控件 ──

    def _on_tab_changed(self, index: int) -> None:
        if index == 3:
            self._template_panel.refresh()
        self._update_space_controls()

    def _space_controls_apply(self) -> bool:
        """当前页签/目标是否受「图库空间」影响（模板一律不受）。"""
        index = self._tabs.currentIndex()
        if index == 0:
            return not self._is_template_target()
        return index in (1, 2)

    def _update_space_controls(self) -> None:
        applies = self._space_controls_apply()
        reason = tr("模板按分组存放，与图库空间无关")
        for widget in (self._space_combo, self._btn_edit_space,
                       self._btn_refresh_space, self._btn_new_space,
                       self._btn_del_space):
            widget.setEnabled(applies)
            widget.setToolTip("" if applies else reason)

    # ── 画布事件 ──

    def _on_region_changed(self, x1, y1, x2, y2):
        """区域变化时启用/禁用切割按钮，更新信息栏"""
        has_region = (x2 - x1) > 0.01 and (y2 - y1) > 0.01
        self._grid_panel.set_execute_enabled(has_region)
        self._sync_template_source()
        if has_region:
            if self._is_template_target():
                # 模板图只框一个矩形，不叠网格
                self._canvas.set_show_grid(False)
            else:
                self._canvas.set_grid_size(
                    self._grid_panel.rows, self._grid_panel.cols)
                self._canvas.set_show_grid(True)
            # 启用全选/全不选按钮
            self._select_all_btn.setEnabled(True)
        else:
            self._select_all_btn.setEnabled(False)
            self._deselect_all_btn.setEnabled(False)
        self._update_info_label()

    def _on_grid_params_changed(self, rows: int, cols: int, gap: int):
        """网格参数变化时实时更新画布网格和信息栏"""
        self._canvas.set_grid_size(rows, cols)
        self._canvas.set_grid_gap(gap)
        self._update_info_label()

    def _on_grid_defaults_changed(self, grid: dict):
        if not self._persist_ui_state:      # 预览/测试不改真实界面参数
            return
        """五项网格参数手动改动后写入 interface.ui_state.reference_manager.grid。"""
        from lvjiang.core.config import save_reference_grid
        save_reference_grid(grid)

    def _on_generate_grid(self, rows: int, cols: int, gap: int, height: int, width: int):
        """根据单cell尺寸生成网格区域"""
        img_size = self._canvas.image_size
        if img_size is None:
            QMessageBox.warning(self, tr("提示"), tr("请先导入图片"))
            return

        img_w, img_h = img_size
        # 计算总尺寸
        total_w = cols * width + (cols - 1) * gap
        total_h = rows * height + (rows - 1) * gap

        # 检查是否超出图片范围
        if total_w > img_w or total_h > img_h:
            QMessageBox.warning(
                self, tr("参数超出图片大小"),
                f"生成的网格尺寸 {total_w}×{total_h} 超出图片尺寸 {img_w}×{img_h}"
            )
            return

        # 从左上角 (0, 0) 开始生成
        self._canvas.set_region_from_pixels(0, 0, total_w, total_h)
        self._update_info_label()

    def _on_clear_grid(self):
        """清除网格区域"""
        self._canvas.set_grid_rect(None)
        self._canvas.set_show_grid(False)
        self._grid_panel.set_execute_enabled(False)
        self._select_all_btn.setEnabled(False)
        self._deselect_all_btn.setEnabled(False)
        self._update_info_label()

    def _on_selection_changed(self):
        """画布单元格选择变化时更新按钮状态"""
        has_selection = self._canvas.has_selection()
        self._deselect_all_btn.setEnabled(has_selection)

    def _update_info_label(self):
        """更新信息栏显示"""
        region_px = self._canvas.get_region_pixels()
        if region_px is None:
            self._info_label.setText(tr("未框选区域"))
            return

        x1, y1, x2, y2 = region_px
        region_w = x2 - x1
        region_h = y2 - y1

        rows = self._grid_panel.rows
        cols = self._grid_panel.cols
        gap = self._grid_panel.gap

        # 计算 cell 尺寸
        total_gap_w = gap * (cols - 1)
        total_gap_h = gap * (rows - 1)
        cell_w = (region_w - total_gap_w) // cols if cols > 0 else 0
        cell_h = (region_h - total_gap_h) // rows if rows > 0 else 0

        self._info_label.setText(
            f"区域: {region_w}×{region_h} px  |  "
            f"Cell: {cell_w}×{cell_h} px  ({rows}行×{cols}列，间隔{gap})"
        )

    def _on_execute_cut(self):
        """执行切割：从画布获取已选 cell 图像，传递给编辑面板"""
        # 检查是否有选中的单元格
        if not self._canvas.has_selection():
            QMessageBox.warning(self, tr("提示"), tr("请至少选择一个参考图"))
            return

        rows = self._grid_panel.rows
        cols = self._grid_panel.cols
        gap = self._grid_panel.gap

        # 先保存已选中的单元格（set_grid_size 会清空选择）
        selected = self._canvas.get_selected_cells()
        self._canvas.set_grid_size(rows, cols)

        cells_coords = self._canvas.get_grid_cells(gap=gap)
        if not cells_coords:
            QMessageBox.warning(self, tr("提示"), tr("请先框选区域"))
            return

        image = self._canvas.get_image()
        if image is None:
            QMessageBox.warning(self, tr("提示"), tr("请先导入图片"))
            return

        # 只裁剪已选中的 cell
        cell_images = []
        for idx, (x1, y1, x2, y2) in enumerate(cells_coords):
            row = idx // cols
            col = idx % cols
            if (row, col) in selected:
                cell = image[y1:y2, x1:x2].copy()
                cell_images.append(cell)

        # 传递给编辑面板
        self._grid_panel.show_cut_cells(cell_images)

    def _on_submit_cells(self, cells_data: list[tuple]):
        """提交切割结果到数据库

        Args:
            cells_data: list of (image, label, group, notes, meta)
        """
        from lvjiang.core.reference_db import validate_group_names
        try:
            for _image, _label, group, _notes, _cell_meta in cells_data:
                validate_group_names(group)
        except ValueError as exc:
            # 提交是数据库边界：非法分组在这里兜底，避免写了一半才失败
            QMessageBox.warning(self, tr("分组名非法"), str(exc))
            return

        count = 0
        for image, label, group, notes, cell_meta in cells_data:
            if image is None:
                continue
            meta = dict(cell_meta)
            if group:
                meta["group"] = group
            self._db.add_entry(
                label=label,
                meta=meta,
                source=self._source_name,
                notes=notes,
                image_data=image,
            )
            count += 1

        if count > 0:
            self._data_changed = True  # 标记数据已变动
            # 刷新分组列表（新增参考图面板 + 图库管理面板）
            groups = self._db.get_groups()
            labels_by_group = self._db.get_all_labels_by_group()
            self._grid_panel.set_known_groups(groups, labels_by_group)
            self._browser.set_known_groups(groups)
            # 刷新图库面板
            self._browser.refresh()
            # 切换到图库管理 tab 查看结果
            self._tabs.setCurrentIndex(1)

    def _on_data_changed(self):
        """图库管理面板数据变动时标记"""
        self._data_changed = True

    def _on_schema_changed(self):
        """元数据定义变更时，重建动态字段并刷新"""
        self._browser.rebuild_meta_fields()
        self._browser.refresh()
        self._grid_panel.set_meta_fields(self._db.get_meta_schema())

    @property
    def data_changed(self) -> bool:
        """返回是否有数据变动（新增/修改/删除参考图）"""
        return self._data_changed

    # ── 键盘事件 ──

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_V and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # 只在新增参考图 tab 时响应粘贴
            if self._tabs.currentIndex() == 0:
                self._on_paste()
        else:
            super().keyPressEvent(event)
