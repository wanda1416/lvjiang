"""指令目录 —— 脚本工作台「指令面板」的数据：每条指令 = 模板 + 槽位规格

借鉴快捷指令（Shortcuts）的交互：用户不记语法，从列表里选一个操作，把槽位填上
（场景、区域、坐标、颜色、延迟……），面板实时预览生成的 DSL，点插入。

这里只放**数据与渲染**（纯 Python，可离线测试），不碰 Qt。槽位类型（``Slot.kind``）
决定面板用什么控件、从哪取值、以及渲染成什么文本：

    scene      [key]            下拉：场景注册表
    region     [key]            下拉：当前场景的区域 / 坐标点 / 面板
    coord      (x, y)           画布取点 / 手填
    rect       (x, y, w, h)     画布框选 / 手填
    color      "#rrggbb"        画布取色 / 手填
    delay      @name            下拉：命名延迟
    template   "name"           下拉：config/system/templates
    text       "…"              自由文本，自动加引号
    raw        原样             数字 / 表达式 / 条件
    var        $name            变量名（自动补 $）
    choice     原样             固定选项（见 Slot.choices）

可选槽位（``optional=True``）留空时整段不出现，非空时按 ``wrap`` 包一层
（例：after wait 子句）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..i18n import tr

SLOT_KINDS = ("scene", "region", "coord", "rect", "color", "delay", "template", "text", "raw", "var", "choice")


@dataclass(frozen=True)
class Slot:
    key: str
    label: str
    kind: str
    default: str = ""
    optional: bool = False
    wrap: str = "{v}"                 # 可选槽位非空时的包装；{v} 是渲染后的值
    choices: tuple[str, ...] = ()     # kind == choice
    help: str = ""

    def __post_init__(self):
        if self.kind not in SLOT_KINDS:
            raise ValueError(f"未知槽位类型: {self.kind}")


@dataclass(frozen=True)
class Action:
    key: str
    label: str
    category: str
    template: str                     # 用 {slot_key} 引用槽位；可多行
    slots: tuple[Slot, ...] = field(default_factory=tuple)
    doc: str = ""
    keywords: tuple[str, ...] = ()    # 额外搜索词（英文指令名等）


class RenderError(ValueError):
    """槽位缺失或非法"""


# ─── 渲染 ───────────────────────────────────────────────

def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return s[1:-1]
    return s


def render_slot(slot: Slot, value: str) -> str:
    """单个槽位值 → DSL 片段（不含可选包装）"""
    v = (value or "").strip()
    if v == "":
        if slot.optional:
            return ""
        raise RenderError(tr("「{label}」未填写").format(label=slot.label))
    kind = slot.kind
    if kind in ("scene", "region"):
        v = v.strip("[]").strip()
        if not v:
            raise RenderError(tr("「{label}」未填写").format(label=slot.label))
        return f"[{v}]"
    if kind == "delay":
        return v if v.startswith("@") else f"@{v}"
    if kind == "var":
        v = v.lstrip("$")
        if not v:
            raise RenderError(tr("「{label}」未填写").format(label=slot.label))
        return f"${v}"
    if kind in ("text", "template"):
        return f'"{_strip_quotes(v)}"'
    if kind == "color":
        v = _strip_quotes(v)
        v = v if v.startswith("#") else f"#{v}"
        if len(v) != 7:
            raise RenderError(tr("「{label}」应为 #rrggbb").format(label=slot.label))
        return f'"{v}"'
    if kind in ("coord", "rect"):
        inner = v.strip("()")
        parts = [p.strip() for p in inner.split(",") if p.strip()]
        want = 2 if kind == "coord" else 4
        if len(parts) != want:
            raise RenderError(tr("「{label}」应为 {n} 个数").format(label=slot.label, n=want))
        return "(" + ", ".join(parts) + ")"
    # raw / choice
    return v


def render(action: Action, values: dict[str, str]) -> str:
    """按槽位值渲染整条指令；缺必填槽位抛 RenderError"""
    fills: dict[str, str] = {}
    for slot in action.slots:
        piece = render_slot(slot, values.get(slot.key, slot.default))
        if piece and slot.optional:
            piece = slot.wrap.format(v=piece)
        fills[slot.key] = piece
    try:
        return action.template.format(**fills)
    except KeyError as e:  # 模板引用了未声明的槽位——目录本身写错，直接暴露
        raise RenderError(f"模板引用了未声明的槽位 {e}") from e


# ─── 目录 ───────────────────────────────────────────────

def _wait_slot() -> Slot:
    return Slot("wait", tr("操作后等待"), "delay", optional=True, wrap=" after wait {v}",
                help=tr("命名延迟，留空则不等待"))


CAT_INTERACT = tr("交互")
CAT_PERCEIVE = tr("感知")
CAT_VISION = tr("图色")
CAT_FLOW = tr("控制流")
CAT_DATA = tr("数据与输出")

ACTIONS: tuple[Action, ...] = (
    # ── 交互 ──
    Action("click_region", tr("点击区域"), CAT_INTERACT,
           "click {scene}.{region}{wait}",
           (Slot("scene", tr("场景"), "scene"), Slot("region", tr("区域/点"), "region"), _wait_slot()),
           doc=tr("点击布局里已定义的区域或坐标点中心"), keywords=("click",)),
    Action("click_coord", tr("点击坐标"), CAT_INTERACT,
           "click {coord}{wait}",
           (Slot("coord", tr("坐标"), "coord", help=tr("画布上点一下取值")), _wait_slot()),
           doc=tr("点击画布归一化坐标"), keywords=("click",)),
    Action("click_var", tr("点击变量"), CAT_INTERACT,
           "click {var}{wait}",
           (Slot("var", tr("变量"), "var", help=tr("find 结果、矩形或坐标变量")), _wait_slot()),
           keywords=("click",)),
    Action("drag_coords", tr("拖拽（两点）"), CAT_INTERACT,
           "drag {from_} {to}{dur}{hold}",
           (Slot("from_", tr("起点"), "coord"), Slot("to", tr("终点"), "coord"),
            Slot("dur", tr("时长(秒)"), "raw", optional=True, wrap=" {v}"),
            Slot("hold", tr("到达后按住(秒)"), "raw", optional=True, wrap=" hold {v}")),
           keywords=("drag", "swipe")),
    Action("drag_region", tr("拖拽区域翻页"), CAT_INTERACT,
           "drag {scene}.{region} {dir}{n}",
           (Slot("scene", tr("场景"), "scene"), Slot("region", tr("面板/区域"), "region"),
            Slot("dir", tr("方向"), "choice", default="down", choices=("up", "down", "left", "right")),
            Slot("n", tr("行/列数"), "raw", optional=True, wrap=" {v}")),
           keywords=("drag", "scroll")),
    Action("press", tr("按键"), CAT_INTERACT,
           'press "{key}"{mode}',
           (Slot("key", tr("键名"), "raw", default="ESC", help=tr("ESC / HOME / 字母键；设备端 ESC=返回")),
            Slot("mode", tr("模式"), "choice", default="", choices=("", "down", "up"), optional=True, wrap=" {v}")),
           keywords=("press", "key")),
    Action("mouse_button", tr("鼠标键原始事件"), CAT_INTERACT,
           "mouse {button} {state}",
           (Slot("button", tr("鼠标键"), "choice", default="left",
                 choices=("left", "right", "middle", "x1", "x2")),
            Slot("state", tr("状态"), "choice", default="down",
                 choices=("down", "up"))),
           doc=tr("保留鼠标键按下或松开的原始事件，不自动合并为点击"),
           keywords=("mouse", "button", "down", "up")),
    Action("wait_named", tr("等待（命名延迟）"), CAT_INTERACT,
           "wait {delay}", (Slot("delay", tr("延迟"), "delay"),), keywords=("wait", "sleep")),
    Action("wait_secs", tr("等待（秒）"), CAT_INTERACT,
           "wait {secs}", (Slot("secs", tr("秒"), "raw", default="1.0"),), keywords=("wait", "sleep")),
    Action("wait_stable", tr("等待画面稳定"), CAT_INTERACT,
           "wait stable {timeout}{on}",
           (Slot("timeout", tr("最长等待(秒)"), "raw", default="5"),
            Slot("on", tr("只看某区域"), "raw", optional=True, wrap=" on {v}",
                 help=tr("形如 [scene].[region]"))),
           keywords=("wait", "stable")),
    # ── 感知 ──
    Action("scan", tr("OCR 扫描区域"), CAT_PERCEIVE,
           "scan {scene}.{region} as {var}{by}",
           (Slot("scene", tr("场景"), "scene"), Slot("region", tr("区域"), "region"),
            Slot("var", tr("存入变量"), "var", default="text"),
            Slot("by", tr("只判断是否含"), "text", optional=True, wrap=" by contains {v}")),
           doc=tr("不带 by 得到文字；带 by 得到命中与否"), keywords=("scan", "ocr")),
    Action("find_text", tr("找文字（可点击）"), CAT_PERCEIVE,
           "find{area} as {var} by {mode} {text}",
           (Slot("area", tr("限定区域"), "raw", optional=True, wrap=" {v}", help=tr("形如 [scene].[region]，留空全画布")),
            Slot("var", tr("存入变量"), "var", default="hit"),
            Slot("mode", tr("匹配"), "choice", default="contains", choices=("contains", "equals")),
            Slot("text", tr("文字"), "text")),
           keywords=("find", "ocr")),
    Action("find_image", tr("找图（模板定位）"), CAT_PERCEIVE,
           "find{area} as {var} by image {tpl}{conf}",
           (Slot("area", tr("限定区域"), "raw", optional=True, wrap=" {v}"),
            Slot("var", tr("存入变量"), "var", default="icon"),
            Slot("tpl", tr("模板"), "template"),
            Slot("conf", tr("最低分(0-1)"), "raw", optional=True, wrap=" where confidence >= {v}")),
           keywords=("find", "image", "template")),
    Action("recognize", tr("图库识别（分类）"), CAT_PERCEIVE,
           "recognize {scene}.{region} as {var}",
           (Slot("scene", tr("场景"), "scene"), Slot("region", tr("区域"), "region"),
            Slot("var", tr("存入变量"), "var", default="item")),
           keywords=("recognize",)),
    # ── 图色 ──
    Action("color_ratio", tr("颜色占比"), CAT_VISION,
           "{var} = color_ratio({rect}, {color}, {tol})",
           (Slot("var", tr("存入变量"), "var", default="ratio"),
            Slot("rect", tr("区域"), "rect", help=tr("画布框选取值")),
            Slot("color", tr("目标色"), "color"),
            Slot("tol", tr("容差"), "raw", default="40")),
           doc=tr("区域里接近目标色的像素比例 0–1，判断按钮亮没亮/在哪个界面"),
           keywords=("color", "ratio")),
    Action("pixel", tr("取点颜色"), CAT_VISION,
           "{var} = pixel({coord})",
           (Slot("var", tr("存入变量"), "var", default="rgb"), Slot("coord", tr("坐标"), "coord")),
           keywords=("pixel", "color")),
    Action("bright", tr("取点亮度"), CAT_VISION,
           "{var} = bright({coord})",
           (Slot("var", tr("存入变量"), "var", default="b"), Slot("coord", tr("坐标"), "coord")),
           keywords=("bright",)),
    Action("bright_segs", tr("亮段计数"), CAT_VISION,
           "{var} = bright_segs({rect}, {on}, {off})",
           (Slot("var", tr("存入变量"), "var", default="segs"), Slot("rect", tr("区域"), "rect"),
            Slot("on", tr("进入亮段阈值"), "raw", default="300"), Slot("off", tr("退出阈值"), "raw", default="150")),
           keywords=("bright", "segments")),
    Action("find_icons", tr("找同色图标"), CAT_VISION,
           "{var} = find_icons({rect}, {channel}, {cmin}, {margin})",
           (Slot("var", tr("存入变量"), "var", default="icons"), Slot("rect", tr("区域"), "rect"),
            Slot("channel", tr("主导通道"), "choice", default="1", choices=("0", "1", "2"), help=tr("0=红 1=绿 2=蓝")),
            Slot("cmin", tr("通道下限"), "raw", default="150"), Slot("margin", tr("高出其他通道"), "raw", default="40")),
           doc=tr("返回列表，按面积降序；$first = $icons[0] 后可 click"), keywords=("icons", "blob")),
    # ── 控制流 ──
    Action("if", tr("如果…"), CAT_FLOW,
           "if {cond}\n    \nend",
           (Slot("cond", tr("条件"), "raw", default="$hit", help=tr("如 $x > 0.5 / $v contains \"ok\" / not $hit")),),
           keywords=("if",)),
    Action("if_else", tr("如果…否则…"), CAT_FLOW,
           "if {cond}\n    \nelse\n    \nend",
           (Slot("cond", tr("条件"), "raw", default="$hit"),), keywords=("if", "else")),
    Action("loop_count", tr("重复 N 次"), CAT_FLOW,
           "loop {n}\n    \nend", (Slot("n", tr("次数"), "raw", default="3"),), keywords=("loop",)),
    Action("loop_until", tr("重复直到…"), CAT_FLOW,
           "loop until {cond}\n    \nend", (Slot("cond", tr("条件"), "raw", default="$done"),),
           keywords=("loop", "until")),
    Action("loop_while", tr("当…时重复"), CAT_FLOW,
           "loop while {cond}\n    \nend", (Slot("cond", tr("条件"), "raw", default="$running"),),
           keywords=("loop", "while")),
    Action("for", tr("遍历列表"), CAT_FLOW,
           "for {var} in {items}\n    \nend",
           (Slot("var", tr("循环变量"), "raw", default="item"), Slot("items", tr("列表"), "raw", default="$list")),
           keywords=("for",)),
    Action("try", tr("出错也继续"), CAT_FLOW,
           "try\n    \ncatch {err}\n    log warn {err}\nend",
           (Slot("err", tr("错误变量"), "var", default="err"),), keywords=("try", "catch")),
    Action("def", tr("定义子过程"), CAT_FLOW,
           "def {name}()\n    \nend", (Slot("name", tr("过程名"), "raw", default="my_proc"),), keywords=("def",)),
    Action("call", tr("调用子过程"), CAT_FLOW,
           "call {name}()", (Slot("name", tr("过程名"), "raw", default="my_proc"),), keywords=("call",)),
    Action("return_fail", tr("失败退出"), CAT_FLOW, "return -1", (), keywords=("return",)),
    # ── 数据与输出 ──
    Action("assign", tr("赋值"), CAT_DATA,
           "{var} = {value}",
           (Slot("var", tr("变量"), "var", default="x"), Slot("value", tr("值/表达式"), "raw", default="0")),
           keywords=("eval", "set")),
    Action("assign_rect", tr("定义区域变量"), CAT_DATA,
           "{var} = {rect}",
           (Slot("var", tr("变量"), "var", default="area"), Slot("rect", tr("区域"), "rect")),
           doc=tr("画布框选的区域存成变量，之后 click / 图色函数都能用"), keywords=("rect", "region")),
    Action("log", tr("打日志"), CAT_DATA,
           "log {level}{msg}",
           (Slot("level", tr("级别"), "choice", default="", choices=("", "info", "warn", "error"), optional=True, wrap="{v} "),
            Slot("msg", tr("内容"), "text", default="到这里了")),
           keywords=("log",)),
    Action("collect", tr("收集到输出"), CAT_DATA,
           "collect {var}{as_}",
           (Slot("var", tr("变量"), "var"), Slot("as_", tr("输出键名"), "text", optional=True, wrap=" as {v}")),
           keywords=("collect", "output")),
    Action("screenshot", tr("截图存档"), CAT_DATA, "screenshot", (), keywords=("screenshot",)),
    Action("notify", tr("通知"), CAT_DATA, "eval notify({msg})", (Slot("msg", tr("内容"), "text", default="完成"),),
           keywords=("notify",)),
    Action("pause", tr("暂停等人工"), CAT_DATA, "eval pause({msg})",
           (Slot("msg", tr("提示"), "text", default="请手动处理后点击继续"),), keywords=("pause",)),
)

_BY_KEY = {a.key: a for a in ACTIONS}


def get_action(key: str) -> Action:
    return _BY_KEY[key]


def categories() -> list[str]:
    seen: list[str] = []
    for a in ACTIONS:
        if a.category not in seen:
            seen.append(a.category)
    return seen


def search(query: str) -> list[Action]:
    """按标签 / key / 关键词 / 类别 子串匹配（大小写不敏感）；空查询返回全部"""
    q = (query or "").strip().lower()
    if not q:
        return list(ACTIONS)
    out = []
    for a in ACTIONS:
        hay = " ".join([a.label, a.key, a.category, a.doc, *a.keywords]).lower()
        if q in hay:
            out.append(a)
    return out
