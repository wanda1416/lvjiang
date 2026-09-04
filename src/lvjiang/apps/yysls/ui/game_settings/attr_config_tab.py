"""属性配置 - 主容器

十一类属性来源按「怎么填」分组成几个 Tab，而不是全部挤在一个下拉里：
心法一门六重共 222 条，和只有几条的吃食放在同一个列表里翻，找什么都
要先翻一遍。

Tab 标题带进度（`心法 12/222`），不点进去也知道哪一页还没填完。最后
一页是推导，配装、比对与保存都在那里，不再是个从别处弹出来的对话框。
"""

from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from .....i18n import tr
from .attr_derive_panel import AttrDerivePanel
from .attr_source_panel import AttrSourcePanel

#: (标题, 覆盖的来源类别)。分组依据是填写方式而非游戏菜单：
#: 心法与武学各自量大且规则不同，独占一页；其余按「角色长出来的」
#: 与「身上带的」分开，消耗品另算。
_PAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("心法", ("inner_way",)),
    ("武学天赋", ("martial_art",)),
    ("角色成长", ("base", "breakthrough", "oddity")),
    ("装备与外物", ("gear_set", "arsenal", "divinecraft")),
    ("消耗与秘籍", ("food", "script")),
)


class AttrConfigTab(QWidget):
    """属性配置主面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._panels: list[tuple[int, str, AttrSourcePanel]] = []
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._tabs = QTabWidget()
        for title, kinds in _PAGES:
            panel = AttrSourcePanel(kinds)
            index = self._tabs.addTab(panel, tr(title))
            self._panels.append((index, tr(title), panel))
            panel.progress_changed.connect(self._refresh_titles)

        self._derive_panel = AttrDerivePanel()
        self._tabs.addTab(self._derive_panel, tr("基础属性推导"))
        # 切到推导页时重读来源：填数据与推导在同一个窗口里来回切，
        # 不重读的话刚填的值不会反映到推导结果上。
        self._tabs.currentChanged.connect(self._on_tab_changed)

        layout.addWidget(self._tabs)
        self._refresh_titles()

    def _refresh_titles(self) -> None:
        """把进度写进 Tab 标题，不点进去也知道哪一页还没填完"""
        for index, title, panel in self._panels:
            done, total = panel.progress()
            self._tabs.setTabText(
                index, f"{title}  {done}/{total}" if total else title)

    def _on_tab_changed(self, index: int) -> None:
        if self._tabs.widget(index) is self._derive_panel:
            self._derive_panel.reload()
