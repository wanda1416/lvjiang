"""场景定义时数据模型

定义时类型：ViewDef / RegionDef / PointDef / PanelDef / SceneDef。
描述「YAML 场景配置文件的结构」，由 SceneRegistry 加载后消费。

与 layout_models（运行时坐标实例）不同，这些类不含具体坐标值，
只声明场景内有哪些可交互元素及其类型属性。
"""

from dataclasses import dataclass, field

# 合法的 region type 枚举
VALID_REGION_TYPES = {"attr", "slot", "func"}

# 基底视图：开启多视图后永远存在的第一个视图，展示上与普通视图
# 等同（不叠加到其他视图），仅承接存量定义且不可删除
# （定义的 view 字段为空等价于归属基底视图）
BASE_VIEW_KEY = "base"
BASE_VIEW_NAME = "基底"


@dataclass
class ViewDef:
    """场景内的一个视图（同一页面的一个可见态，典型场景：滚动后的另一屏）

    视图只影响编辑期的可见性与底图，不进入运行时寻址：
    key 仍在整个场景内全局唯一，DSL 依旧写 [scene].[key]。
    """
    key: str
    name: str

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name}


@dataclass
class RegionDef:
    """单个区域的完整定义（场景内的一个可交互/可识别元素）"""
    key: str
    name: str
    type: str = "attr"                # attr/slot/func
    is_text: bool = True              # 是否需要文字识别（OCR）
    is_clickable: bool = False        # 是否可点击
    view: str = ""                    # 归属视图 key，空 = 基底视图


@dataclass
class PointDef:
    """单个坐标点的类型定义（圆形交互锚点）

    描述「场景里存在这样一个可交互坐标点」的类型信息（key/name/type/is_text/is_clickable）。
    具体的坐标位置与半径属于实例数据，保存在布局 JSON 的 Point 中，
    半径可在画布上随意调整，不在此处限死。
    """
    key: str
    name: str
    type: str = "func"              # attr/slot/func
    is_text: bool = False           # 是否需要文字识别（OCR）
    is_clickable: bool = True       # 是否可点击
    view: str = ""                  # 归属视图 key，空 = 基底视图


@dataclass
class PanelDef:
    """单个 panel 的类型定义（声明式网格容器）

    描述「场景里存在这样一个网格区域」的类型信息（key/name/行列数）。
    具体的格子坐标由引擎运行时通过图像自对齐（方差分析 + 黑边检测）计算，
    并缓存在 WorkflowEngine._panel_alignments 中。
    span（间距）由对齐算法自动检测，无需手动指定。

    calibration 校准模式：
    - "auto"：先图像检测，失败降级为等分（默认值）
    - "even"：跳过图像检测，直接按 rows/cols 等分
    - "image"：仅图像检测，失败返回 None

    scroll_direction 滚动方向：
    - "vertical"：纵向滚动（默认），rows 允许 expected-1
    - "horizontal"：横向滚动，cols 允许 expected-1
    - "both"：双向滚动，rows/cols 都允许 expected-1
    - "none"：固定网格，rows/cols 必须精确匹配
    约束：rows=1 时禁止 vertical/both，cols=1 时禁止 horizontal/both
    """
    key: str
    name: str
    cols: int = 6                   # 列数
    rows: int = 3                   # 行数
    min_visible: float = 0.95       # 行计入有效的最小可见比例（0.5-1.0）
    view: str = ""                  # 归属视图 key，空 = 基底视图
    calibration: str = "auto"       # "auto" | "even" | "image"
    scroll_direction: str = "vertical"  # "vertical" | "horizontal" | "both" | "none"


@dataclass
class SceneDef:
    """单个场景的完整定义

    views 为空表示未开启多视图（场景只有一层定义，与旧行为一致）；
    开启后 views[0] 恒为基底视图。
    """
    key: str
    name: str
    regions: list[RegionDef] = field(default_factory=list)
    points: list[PointDef] = field(default_factory=list)
    panels: list[PanelDef] = field(default_factory=list)
    views: list[ViewDef] = field(default_factory=list)
