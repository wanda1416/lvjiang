"""坐标引用类型体系 — CoordRef / RectCoordRef / CircleCoordRef / Offset

DSL 中三种坐标描述法的统一运行时表示：
- tuple (cx, cy)              → CoordRef（原始坐标点）
- [scene].[area]              → RectCoordRef / CircleCoordRef（Layout 解析）
- find 产出的 FoundRegion     → RectCoordRef（运行时发现）

运算规则：
- CoordRef + Offset → CoordRef（保持子类）
- CoordRef - Offset → CoordRef（保持子类）
- CoordRef - CoordRef → Offset（隐式降级为中心点）
- Offset + Offset → Offset
- Offset - Offset → Offset
- Offset * n / Offset / n → Offset
- tuple → Offset（隐式转换）

禁止：CoordRef * n / CoordRef / n（破坏向量运算法则）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class CoordRef:
    """坐标点基类（中心点）

    所有坐标源的统一表示：Region/Panel/Point/FoundRegion/CoordPoint
    最终都解析为 CoordRef 或其子类。

    Attributes:
        cx: 中心 x 坐标（归一化比例）
        cy: 中心 y 坐标（归一化比例）
    """
    cx: float
    cy: float

    def __add__(self, other: Offset) -> CoordRef:
        """CoordRef + Offset → CoordRef（保持子类）"""
        if not isinstance(other, Offset):
            return NotImplemented
        return self._make_like(self.cx + other.dx, self.cy + other.dy)

    def __sub__(self, other: Union[Offset, CoordRef]) -> Union[CoordRef, Offset]:
        """CoordRef - Offset → CoordRef（保持子类）
        CoordRef - CoordRef → Offset（隐式降级）
        """
        if isinstance(other, Offset):
            return self._make_like(self.cx - other.dx, self.cy - other.dy)
        if isinstance(other, CoordRef):
            return Offset(dx=self.cx - other.cx, dy=self.cy - other.cy)
        return NotImplemented

    def _make_like(self, cx: float, cy: float) -> CoordRef:
        """创建同类型的实例（保持子类）"""
        return type(self)(cx=cx, cy=cy)

    def __radd__(self, other: object) -> CoordRef:
        """Offset + CoordRef → CoordRef（保持子类）"""
        if isinstance(other, Offset):
            return self._make_like(self.cx + other.dx, self.cy + other.dy)
        return NotImplemented


@dataclass(frozen=True)
class RectCoordRef(CoordRef):
    """矩形区域坐标

    对应 Region、Panel、FoundRegion 等具有宽高的区域。
    中心点 (cx, cy) 由左上角 + w/2, h/2 计算得出。

    Attributes:
        cx: 中心 x 坐标（归一化比例）
        cy: 中心 y 坐标（归一化比例）
        w: 宽度（归一化比例）
        h: 高度（归一化比例）
    """
    w: float = 0
    h: float = 0

    def _make_like(self, cx: float, cy: float) -> RectCoordRef:
        """创建 RectCoordRef（保持 w, h）"""
        return RectCoordRef(cx=cx, cy=cy, w=self.w, h=self.h)


@dataclass(frozen=True)
class CircleCoordRef(CoordRef):
    """圆形区域坐标

    对应 Point 等具有半径的坐标点。

    Attributes:
        cx: 中心 x 坐标（归一化比例）
        cy: 中心 y 坐标（归一化比例）
        r: 半径（归一化比例）
    """
    r: float = 0

    def _make_like(self, cx: float, cy: float) -> CircleCoordRef:
        """创建 CircleCoordRef（保持 r）"""
        return CircleCoordRef(cx=cx, cy=cy, r=self.r)


@dataclass(frozen=True)
class Offset:
    """位移向量

    表示两个坐标之间的差值，用于坐标平移运算。
    tuple (dx, dy) 可隐式转换为 Offset。

    Attributes:
        dx: x 方向位移
        dy: y 方向位移
    """
    dx: float
    dy: float

    def __add__(self, other: Union[Offset, CoordRef]) -> Union[Offset, CoordRef]:
        """Offset + Offset → Offset
        Offset + CoordRef → CoordRef（保持子类）
        """
        if isinstance(other, Offset):
            return Offset(dx=self.dx + other.dx, dy=self.dy + other.dy)
        if isinstance(other, CoordRef):
            return other._make_like(other.cx + self.dx, other.cy + self.dy)
        return NotImplemented

    def __sub__(self, other: Offset) -> Offset:
        """Offset - Offset → Offset"""
        if isinstance(other, Offset):
            return Offset(dx=self.dx - other.dx, dy=self.dy - other.dy)
        return NotImplemented

    def __mul__(self, n: Union[int, float]) -> Offset:
        """Offset * n → Offset（向量缩放）"""
        if isinstance(n, (int, float)):
            return Offset(dx=self.dx * n, dy=self.dy * n)
        return NotImplemented

    def __rmul__(self, n: Union[int, float]) -> Offset:
        """n * Offset → Offset"""
        return self.__mul__(n)

    def __truediv__(self, n: Union[int, float]) -> Offset:
        """Offset / n → Offset（向量缩放）"""
        if isinstance(n, (int, float)):
            if n == 0:
                return Offset(dx=0.0, dy=0.0)
            return Offset(dx=self.dx / n, dy=self.dy / n)
        return NotImplemented

    def __neg__(self) -> Offset:
        """-Offset → Offset（反向）"""
        return Offset(dx=-self.dx, dy=-self.dy)


# tuple → Offset 隐式转换辅助函数
def to_offset(value: Union[Offset, tuple[float, float]]) -> Offset:
    """将 tuple 或 Offset 转换为 Offset

    tuple (dx, dy) 隐式转为 Offset。
    """
    if isinstance(value, Offset):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        return Offset(dx=float(value[0]), dy=float(value[1]))
    raise TypeError(f"无法转换为 Offset: {type(value).__name__}")
