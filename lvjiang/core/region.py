"""POI 区域定义 + 坐标换算系统"""

from loguru import logger

from ..config import CoordinateConfig, RegionConfig, load_coordinate_config
from ..constants import BASE_RESOLUTION


class CoordinateSystem:
    """
    坐标换算系统
    所有坐标配置基于基准分辨率（1920x1080），运行时按实际分辨率缩放
    """

    def __init__(self, config: CoordinateConfig | None = None):
        if config is None:
            config = load_coordinate_config()
        self.config = config
        self._base_w, self._base_h = config.base_resolution
        self._scale_x = 1.0
        self._scale_y = 1.0

    def set_actual_resolution(self, width: int, height: int):
        """设置实际分辨率（投屏窗口的实际尺寸）"""
        self._scale_x = width / self._base_w
        self._scale_y = height / self._base_h
        logger.info(f"坐标缩放: x={self._scale_x:.3f}, y={self._scale_y:.3f}")

    def get_box(self, name: str) -> tuple[int, int, int, int] | None:
        """获取 box 类型区域：返回 (left, top, width, height)"""
        region = self.config.regions.get(name)
        if region is None or region.type != "box":
            return None
        return (
            int(region.left * self._scale_x),
            int(region.top * self._scale_y),
            int(region.width * self._scale_x),
            int(region.height * self._scale_y),
        )

    def get_point(self, name: str) -> tuple[int, int] | None:
        """获取 point 类型区域：返回 (x, y)"""
        region = self.config.regions.get(name)
        if region is None or region.type != "point":
            return None
        return (
            int(region.x * self._scale_x),
            int(region.y * self._scale_y),
        )

    def get_grid_cell(self, name: str, row: int, col: int) -> tuple[int, int] | None:
        """获取 grid 类型中指定格子的中心坐标：返回 (cx, cy)"""
        region = self.config.regions.get(name)
        if region is None or region.type != "grid":
            return None
        if row >= region.rows or col >= region.cols or row < 0 or col < 0:
            return None

        cell_w, cell_h = region.cell_size
        gap_x, gap_y = region.gap
        first_x, first_y = region.first_cell

        cx = first_x + col * (cell_w + gap_x)
        cy = first_y + row * (cell_h + gap_y)

        return (
            int(cx * self._scale_x),
            int(cy * self._scale_y),
        )

    def get(self, name: str):
        """通用获取：根据区域类型返回对应坐标"""
        region = self.config.regions.get(name)
        if region is None:
            return None
        if region.type == "box":
            return self.get_box(name)
        elif region.type == "point":
            return self.get_point(name)
        elif region.type == "grid":
            # grid 类型需要指定 row/col，这里返回第一个格子
            return self.get_grid_cell(name, 0, 0)
        return None

    def list_regions(self) -> list[str]:
        """列出所有已定义的区域名称"""
        return list(self.config.regions.keys())
