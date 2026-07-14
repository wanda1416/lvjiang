"""区域编辑器子包 - 在截图上框选区域并绑定字段"""

from .canvas import RegionCanvas
from .scene_tab import SceneTab
from .dialog import RegionEditorDialog

__all__ = ["RegionCanvas", "SceneTab", "RegionEditorDialog"]
