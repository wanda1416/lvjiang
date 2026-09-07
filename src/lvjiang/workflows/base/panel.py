"""WorkflowEngine Panel 原语的 Python 工作流程门面。

Panel 的对齐、坐标换算、点击和网格拖拽均由 WorkflowEngine
的公共原语实现。DSL 执行器负责把 AST 参数适配到这些原语；Python
业务工作流程只保留同名薄门面，不另行维护对齐算法或缓存。
"""

from typing import TYPE_CHECKING

from .engine_ref import require_engine

if TYPE_CHECKING:
    from ..align import GridAlignment
    from ..engine.core import WorkflowEngine


class _PanelMixin:
    """WorkflowEngine Panel 原语的 Python 便捷入口。"""

    def _panel_engine(self) -> "WorkflowEngine":
        return require_engine(self, "Panel 原语")

    def _find_panel(self, scene_key: str, panel_key: str):
        return self._panel_engine().find_panel(scene_key, panel_key)

    def _capture_panel_image(self, panel_obj):
        return self._panel_engine().capture_panel_image(panel_obj)

    def _panel_ratio_to_screen(
        self, panel_obj, cx: float, cy: float,
    ) -> tuple[int, int]:
        return self._panel_engine().panel_ratio_to_screen(panel_obj, cx, cy)

    def align_panel(
        self, scene_key: str, panel_key: str,
    ) -> "GridAlignment | None":
        return self._panel_engine().align_panel(scene_key, panel_key)

    def _ensure_aligned(
        self, scene_key: str, panel_key: str,
    ) -> "GridAlignment | None":
        return self._panel_engine().ensure_panel_aligned(scene_key, panel_key)

    def _invalidate_align(self, scene_key: str, panel_key: str) -> None:
        self._panel_engine().invalidate_panel_alignment(scene_key, panel_key)

    def click_panel(
        self, scene_key: str, panel_key: str, row: int, col: int,
    ) -> bool:
        return self._panel_engine().click_panel(
            scene_key, panel_key, row, col)

    def drag_grid(
        self,
        scene_key: str,
        panel_key: str,
        direction: str,
        distance: float = 1.0,
        hold: float | None = None,
    ) -> None:
        self._panel_engine().drag_grid(
            scene_key, panel_key, direction, distance=distance, hold=hold)

    def get_panel_rows(self, scene_key: str, panel_key: str) -> int:
        cal = self._panel_engine().ensure_panel_aligned(scene_key, panel_key)
        return cal.n_rows if cal else 0

    def get_panel_cols(self, scene_key: str, panel_key: str) -> int:
        cal = self._panel_engine().ensure_panel_aligned(scene_key, panel_key)
        return cal.n_cols if cal else 0
