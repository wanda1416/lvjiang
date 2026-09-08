"""属性配置对话框

独立窗口，管理装备之外的战斗属性从哪来：心法、武学天赋、套装、突破等，
以及由它们推导基础属性。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from .....i18n import tr
from ...config import get_game_config
from ...core.attr_model import invalidate_attr_model_cache
from .attr_config_tab import AttrConfigTab


class AttrConfigDialog(QDialog):
    """属性配置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("属性配置"))
        self.setMinimumSize(900, 700)
        self.resize(1200, 800)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 两个 manager 都是进程级单例；每次打开重读一次，以便发现两次
        # 对话框之间的外部改动（词条上限改了会直接影响整条词条的取值）。
        get_game_config().reload()
        invalidate_attr_model_cache()
        layout.addWidget(AttrConfigTab())
