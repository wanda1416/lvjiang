"""场景定义时数据模型

定义时类型：ViewDef / RegionDef / PointDef / PanelDef / SceneDef。
描述「YAML 场景配置文件的结构」，由 SceneRegistry 加载后消费。

与 layout_models（运行时坐标实例）不同，这些类不含具体坐标值，
只声明场景内有哪些可交互元素及其类型属性。
"""

from dataclasses import dataclass, field

from ..i18n import tr

# 合法的 region type 枚举
VALID_REGION_TYPES = {"attr", "slot", "func"}

# 基底视图：开启多视图后永远存在的第一个视图，展示上与普通视图
# 等同（不叠加到其他视图），仅承接存量定义且不可删除
# （定义的 view 字段为空等价于归属基底视图）
BASE_VIEW_KEY = "base"
BASE_VIEW_NAME = tr("基底")


@dataclass
class ViewDef:
    """场景内的一个视图（同一页面的一个可见态，典型场景：滚动后的另一屏）

    视图只影响编辑期的可见性与底图，不进入运行时寻址：
    key 仍在整个场景内全局唯一，DSL 依旧写 [scene].[key]。
    """
    key: str
    name: str
    # 同层视图：与基底处于**同一图层**，只是滚动/翻页后的另一个取景，不是另一个
    # 页面。菜单的 page_1 / page_2 就是典型。
    #
    # 因此同层视图**没有入口、只有跳转**：没有任何按钮“进入”它，你只是把同一页
    # 滚过去了；但它上面的按钮照样可以跳到别处。死视图检测必须跳过它们，否则
    # 满屏都是假警报。
    #
    # 默认为真：新建视图多半是滚动态，这个默认值不会制造假警报；确实是独立页面
    # 时再取消勾选，那时才要求它有入口。基底视图不适用（它是场景入口）。
    same_layer: bool = True

    def to_dict(self) -> dict:
        d: dict[str, object] = {"key": self.key, "name": self.name}
        if not self.same_layer:
            d["same_layer"] = False
        return d


@dataclass
class RegionDef:
    """单个区域的完整定义（场景内的一个可交互/可识别元素）"""
    key: str
    name: str
    type: str = "attr"                # attr/slot/func
    is_text: bool = True              # 是否需要文字识别（OCR）
    is_clickable: bool = False        # 是否可点击
    # 归属视图 key 列表，空 = 基底视图。同一个按钮可能出现在多个视图里
    # （典型：general_control.close_btn 在结果视图和返还视图都在），
    # 坐标只有一份、跟布局走，所以多归属不影响坐标。
    views: list[str] = field(default_factory=list)
    # 点击后到达的场景/视图，构成页面切换契约。格式：
    #   "equip_tune_detail"         → 进入该场景（基底视图）
    #   "equip_tune_detail/result"  → 进入该场景的 result 视图
    #   "/result"                   → 停留本场景，切到 result 视图
    # 空 = 不产生页面切换（纯识别区，或原地生效的操作）。
    to: str = ""

    @property
    def view(self) -> str:
        """兼容单值读写：读取返回首个归属视图，写入替换为单归属。

        存量代码和 YAML 都按单值理解，保留这个属性后 10 余处调用点无需改动。
        需要判断"在某视图下是否可见"时请用 ``views``，单值只看得到第一个。
        """
        return self.views[0] if self.views else ""

    @view.setter
    def view(self, value: str) -> None:
        self.views = [value] if value else []


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
    views: list[str] = field(default_factory=list)  # 见 RegionDef.views
    to: str = ""                    # 见 RegionDef.to

    @property
    def view(self) -> str:
        """兼容单值读写：读取返回首个归属视图，写入替换为单归属。

        存量代码和 YAML 都按单值理解，保留这个属性后 10 余处调用点无需改动。
        需要判断"在某视图下是否可见"时请用 ``views``，单值只看得到第一个。
        """
        return self.views[0] if self.views else ""

    @view.setter
    def view(self, value: str) -> None:
        self.views = [value] if value else []


@dataclass
class PanelDef:
    """单个 panel 的类型定义（声明式网格容器）

    描述「场景里存在这样一个网格区域」的类型信息（key/name）。
    行列数（rows/cols）属于布局级配置，保存在布局 JSON 的 Panel 中，
    不同设备/分辨率可有不同行列数。

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
    """
    key: str
    name: str
    min_visible: float = 0.95       # 行计入有效的最小可见比例（0.5-1.0）
    views: list[str] = field(default_factory=list)  # 见 RegionDef.views

    @property
    def view(self) -> str:
        """兼容单值读写：读取返回首个归属视图，写入替换为单归属。

        存量代码和 YAML 都按单值理解，保留这个属性后 10 余处调用点无需改动。
        需要判断"在某视图下是否可见"时请用 ``views``，单值只看得到第一个。
        """
        return self.views[0] if self.views else ""

    @view.setter
    def view(self, value: str) -> None:
        self.views = [value] if value else []

    calibration: str = "auto"       # "auto" | "even" | "image"
    scroll_direction: str = "vertical"  # "vertical" | "horizontal" | "both" | "none"


@dataclass
class SubsceneRefDef:
    """父场景中的可复用子场景引用声明。

    ``scene`` 指向一个 ``type: subscene`` 的场景；具体摆放外框属于布局实例，
    保存在布局 JSON 的 SubsceneRef 中。
    """
    key: str
    name: str
    scene: str
    views: list[str] = field(default_factory=list)  # 见 RegionDef.views

    @property
    def view(self) -> str:
        """兼容单值读写：读取返回首个归属视图，写入替换为单归属。

        存量代码和 YAML 都按单值理解，保留这个属性后 10 余处调用点无需改动。
        需要判断"在某视图下是否可见"时请用 ``views``，单值只看得到第一个。
        """
        return self.views[0] if self.views else ""

    @view.setter
    def view(self, value: str) -> None:
        self.views = [value] if value else []



@dataclass
class SceneRefDef:
    """跨场景 area 引用：把另一个**一级场景**的实体接进本场景命名空间。

    与 ``SubsceneRefDef`` 是两件不同的事，实现时不要混用：

    - ``SubsceneRefDef`` 是**几何嵌套**——子场景在父画布里有自己的外框，
      内部实体坐标相对外框，取屏幕坐标要做变换。
    - 本类是**别名透传**——只允许引用一级场景，其实体坐标本就是画布归一化，
      原样搬过来即可，零变换。这正是"只引用一级场景"这条约束的意义。

    坐标不复制：布局加载时从源场景的布局定义转读，源场景改坐标即刻同步。
    因此引用项在本场景里**只读**，编辑器只能新增/移除引用，不能改坐标。

    ``key`` 恒等于源实体 key，DSL 直接写 ``[本场景].[源实体key]``。
    """
    scene: str                        # 源场景 key（必须 type: scene）
    entity: str                       # 源实体 key
    views: list[str] = field(default_factory=list)  # 在本场景哪些视图可见

    @property
    def view(self) -> str:
        """兼容单值读写：读取返回首个归属视图，写入替换为单归属。

        存量代码和 YAML 都按单值理解，保留这个属性后 10 余处调用点无需改动。
        需要判断"在某视图下是否可见"时请用 ``views``，单值只看得到第一个。
        """
        return self.views[0] if self.views else ""

    @view.setter
    def view(self, value: str) -> None:
        self.views = [value] if value else []

    @property
    def key(self) -> str:
        """引用名恒等于源实体 key（不支持重命名，少一个概念）。"""
        return self.entity

    def to_dict(self) -> dict:
        d: dict = {"scene": self.scene, "entity": self.entity}
        if self.views:
            d["views"] = list(self.views)
        return d


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
    type: str = "scene"
    subscene_refs: list[SubsceneRefDef] = field(default_factory=list)
    references: list[SceneRefDef] = field(default_factory=list)

    @property
    def is_subscene(self) -> bool:
        return self.type == "subscene"
