"""_StmtMixin：程序入口与语句/目标回调（click/drag/wait/scan/recognize/align/panel）"""

from lark import Token

from ...engine.signals import WorkflowUserError
from ..ast_nodes import (
    Align,
    ByClause,
    Click,
    CoordPoint,
    Drag,
    EntityRef,
    Find,
    Import,
    Literal,
    PanelGridDrag,
    PanelRef,
    ProcDef,
    Program,
    Recognize,
    Scan,
    TupleLiteral,
    VarRef,
    Wait,
    WaitStable,
    WhereClause,
)


class _StmtMixin:
    """程序入口、基础指令（click/drag/wait/scan/recognize）与 align/panel 索引回调"""

    # ─── 程序入口 ─────────────────────────────────────────

    def start(self, items):
        """过滤掉 None（空行），分离 import / def / body；展平语法糖产生的列表"""
        imports = []
        procs = {}
        stmts = []
        for item in items:
            if item is None:
                continue
            if isinstance(item, list):
                # 语法糖展开：click/drag + wait_clause -> 多条语句
                for sub in item:
                    if isinstance(sub, Import):
                        imports.append(sub)
                    elif isinstance(sub, ProcDef):
                        procs[sub.name] = sub
                    else:
                        stmts.append(sub)
            elif isinstance(item, Import):
                imports.append(item)
            elif isinstance(item, ProcDef):
                procs[item.name] = item
            else:
                stmts.append(item)
        return Program(body=stmts, imports=imports, procs=procs)

    # ─── 基础指令 ─────────────────────────────────────────

    def _resolve_const_or_var(self, item):
        """解析 const_or_var：bracket_expr | var_ref"""
        if isinstance(item, VarRef):
            return item
        else:
            return str(item)

    def click_stmt(self, items):
        """click 目标 [before|after|around wait 参数 ...] — 支持 before/after 任意组合

        显式 wait_clause 时抑制 click 默认的 before/after_click_wait 延迟。
        around 是语法糖，等价于同时指定 before 和 after（同一参数）。
        """
        click_node = items[0]
        # wait_clauses 返回一个列表，元素是每个 wait_clause 的 [timing, Wait] 对
        wait_pairs = []
        if len(items) > 1 and isinstance(items[1], list):
            wait_pairs = [it for it in items[1] if isinstance(it, list) and len(it) == 2
                          and isinstance(it[1], (Wait, WaitStable))]
        if not wait_pairs:
            return click_node

        # 显式 wait_clause → 抑制默认延迟
        click_node = Click(target=click_node.target, line_no=click_node.line_no,
                          suppress_defaults=True)

        # 展开 around 为 before + after
        expanded = []
        for timing, wait_node in wait_pairs:
            if timing == "around":
                expanded.append(("before", wait_node))
                expanded.append(("after", wait_node))
            else:
                expanded.append((timing, wait_node))

        # 按语义顺序组装：before 在 click 前，after 在 click 后
        before_waits = [w for t, w in expanded if t == "before"]
        after_waits = [w for t, w in expanded if t == "after"]
        return before_waits + [click_node] + after_waits

    def click_panel_target(self, items):
        """click [scene].[panel][row][col] — panel 三级索引

        items: scene_const, panel_const, row_index, col_index
        """
        scene_val = self._resolve_const_or_var(items[0])
        panel_val = self._resolve_const_or_var(items[1])
        row = items[2]   # int | VarRef
        col = items[3]   # int | VarRef
        return Click(target=PanelRef(scene=scene_val, panel=panel_val, row=row, col=col),
                     line_no=self._line(items))

    def click_scene_target(self, items):
        """click scene.coord — scene 和 coord 都可以是常量或变量"""
        scene, coord = items  # 两个 const_or_var
        scene_val = self._resolve_const_or_var(scene)
        coord_val = self._resolve_const_or_var(coord)
        return Click(target=EntityRef(scene=scene_val, entity=coord_val), line_no=self._line(items))

    def click_coord_target(self, items):
        """click (rx, ry) — 画布归一化坐标点"""
        cp = items[0]  # CoordPoint
        return Click(target=cp, line_no=0)

    def click_var_target(self, items):
        """click $var — 裸变量引用（find 指令产出的 FoundRegion）"""
        var_ref = items[0]  # VarRef
        return Click(target=var_ref, line_no=self._line(items))

    def coord_point(self, items):
        """(rx, ry) → CoordPoint，number 规则已转为 float"""
        return CoordPoint(rx=float(items[0]), ry=float(items[1]))

    def drag_stmt(self, items):
        """drag 目标 [duration] [hold] [before|after|around wait 参数 ...] — 支持 before/after 任意组合

        显式 wait_clause 时抑制 drag 默认的 before/after_click_wait 延迟。
        around 是语法糖，等价于同时指定 before 和 after（同一参数）。
        """
        # wait_clauses 结果是嵌套在 items 末尾的列表
        wait_pairs = []
        core_items = items
        if len(items) > 1 and isinstance(items[-1], list):
            # items[-1] 是 wait_clauses 的结果（一个列表，元素是 [timing, Wait]）
            clauses_list = items[-1]
            if clauses_list and isinstance(clauses_list[0], list) and len(clauses_list[0]) == 2 and isinstance(clauses_list[0][1], (Wait, WaitStable)):
                wait_pairs = clauses_list
                core_items = items[:-1]

        drag_node = core_items[0]  # 已由 drag_*_target 构造为 Drag
        duration = None
        hold = None
        for item in core_items[1:]:
            if isinstance(item, Literal):
                duration = item
            elif isinstance(item, list):
                duration = item
            elif isinstance(item, float):
                hold = item
        # 显式 wait_clause → 抑制默认延迟
        suppress = len(wait_pairs) > 0
        result = Drag(
            scene=drag_node.scene, arrow=drag_node.arrow,
            duration=duration, hold=hold,
            from_point=drag_node.from_point, to_point=drag_node.to_point,
            from_scene_ref=drag_node.from_scene_ref, to_scene_ref=drag_node.to_scene_ref,
            direction=drag_node.direction, distance=drag_node.distance,
            line_no=drag_node.line_no,
            suppress_defaults=suppress,
        )
        if not wait_pairs:
            return result

        # 展开 around 为 before + after
        expanded = []
        for timing, wait_node in wait_pairs:
            if timing == "around":
                expanded.append(("before", wait_node))
                expanded.append(("after", wait_node))
            else:
                expanded.append((timing, wait_node))

        before_waits = [w for t, w in expanded if t == "before"]
        after_waits = [w for t, w in expanded if t == "after"]
        return before_waits + [result] + after_waits

    def drag_panel_target(self, items):
        """drag [scene].[panel][row][col] [up|down [n]] — panel 三级索引 + 可选方向距离"""
        scene_val = self._resolve_const_or_var(items[0])
        panel_val = self._resolve_const_or_var(items[1])
        row = items[2]
        col = items[3]
        direction = None
        distance = 1
        # 可选的 drag_direction
        if len(items) > 4:
            dir_info = items[4]  # (direction_str, distance_int) or None
            if dir_info is not None:
                direction, distance = dir_info
        panel_ref = PanelRef(scene=scene_val, panel=panel_val, row=row, col=col)
        return Drag(scene=panel_ref, arrow=panel_ref, direction=direction, distance=distance, line_no=self._line(items))

    def drag_grid_target(self, items):
        """drag [scene].[panel] up|down|left|right [n] — panel grid 级拖拽（中心起拖）"""
        scene_val = self._resolve_const_or_var(items[0])
        panel_val = self._resolve_const_or_var(items[1])
        direction, distance = items[2]  # drag_direction 返回 (str, int|VarRef)
        return Drag(
            scene=PanelGridDrag(scene=scene_val, panel=panel_val,
                                direction=direction, distance=distance,
                                line_no=self._line(items)),
            arrow=None,
            direction=direction, distance=distance,
            line_no=self._line(items),
        )

    def drag_direction(self, items):
        """up|down [n | $var] → (direction_str, distance_float_or_VarRef)

        距离支持整数、浮点数（如 0.5 表示半行）、变量引用
        """
        dir_token = str(items[0]).lower()
        distance = 1.0
        if len(items) > 1:
            rows_str = str(items[1])
            if rows_str.startswith('$'):
                distance = VarRef(name=rows_str[1:])  # 动态行数
            else:
                distance = float(rows_str)  # 静态行数（支持浮点数，如 0.5）
        return (dir_token, distance)

    def drag_scene_target(self, items):
        """drag scene.arrow — scene 和 arrow 都可以是常量或变量"""
        scene_val = self._resolve_const_or_var(items[0])
        arrow_val = self._resolve_const_or_var(items[1])
        scene_ref = EntityRef(scene=scene_val, entity=arrow_val)
        return Drag(scene=scene_ref, arrow=scene_ref, line_no=self._line(items))

    def drag_point_pair_target(self, items):
        """drag [scene].[point_1] [scene].[point_2] — 两个命名点之间拖拽"""
        from_scene = self._resolve_const_or_var(items[0])
        from_point = self._resolve_const_or_var(items[1])
        to_scene = self._resolve_const_or_var(items[2])
        to_point = self._resolve_const_or_var(items[3])
        from_ref = EntityRef(scene=from_scene, entity=from_point)
        to_ref = EntityRef(scene=to_scene, entity=to_point)
        return Drag(scene=None, arrow=None, from_scene_ref=from_ref, to_scene_ref=to_ref,
                    line_no=self._line(items))

    def drag_coord_target(self, items):
        """drag (rx1, ry1) (rx2, ry2) — 两个画布归一化坐标点"""
        from_point, to_point = items  # 两个 CoordPoint
        return Drag(scene=None, arrow=None, from_point=from_point, to_point=to_point, line_no=0)

    def drag_duration(self, items):
        item = items[0]
        if isinstance(item, list):
            return item[:2]
        return Literal(value=float(item))

    def drag_hold(self, items):
        """hold <seconds> → float"""
        return float(items[0])

    # ─── align 指令 ─────────────────────────────────────

    def align_stmt(self, items):
        """align [scene].[panel] — 触发 panel 图像自对齐"""
        scene_name = str(items[0])
        panel_name = str(items[1])
        return Align(scene=scene_name, panel=panel_name, line_no=self._line(items))

    # ─── find 指令 ─────────────────────────────────────────

    def find_stmt_area(self, items):
        """find [scene].[area] as $var by ... [where ...] — 指定区域搜索"""
        scene_target = items[0]  # tuple: (scene_name, fields_or_var)
        scene_name = scene_target[0]
        var_ref = items[1]       # var_ref → VarRef
        var_name = var_ref.name
        by_clause = items[2]     # ByClause（必填）
        if by_clause.full:
            raise WorkflowUserError(
                f"'full by' 仅 recognize 语句支持，find 不支持（第 {self._line(items)} 行）"
            )
        where_clause = items[3] if len(items) > 3 else None  # WhereClause | None
        # 解析搜索区域（与 scan 一致）
        search_region = None
        if len(scene_target) > 1 and scene_target[1] is not None:
            second = scene_target[1]
            if isinstance(second, list):
                # field_list → 取第一个字段
                if second:
                    search_region = second[0].value if hasattr(second[0], 'value') else str(second[0])
            elif isinstance(second, VarRef):
                search_region = second  # 动态 region
        return Find(
            var_name=var_name, by=by_clause,
            search_scene=scene_name, search_region=search_region,
            where=where_clause,
            line_no=self._line(items),
        )

    def find_stmt_full(self, items):
        """find as $var by ... [where ...] — 全画布搜索"""
        var_ref = items[0]       # var_ref → VarRef
        var_name = var_ref.name
        by_clause = items[1]     # ByClause（必填）
        if by_clause.full:
            raise WorkflowUserError(
                f"'full by' 仅 recognize 语句支持，find 不支持（第 {self._line(items)} 行）"
            )
        where_clause = items[2] if len(items) > 2 else None  # WhereClause | None
        return Find(
            var_name=var_name, by=by_clause,
            search_scene=None, search_region=None,
            where=where_clause,
            line_no=self._line(items),
        )

    # ─── panel 索引 ─────────────────────────────────────────

    def panel_index_int(self, items):
        """panel 数字索引：[2] → int

        语法："[" INT "]"，匿名终端 "[" "]" 不传入 transformer，
        items 仅包含 INT token。
        """
        return int(str(items[0]))

    def panel_index_var(self, items):
        """panel 变量索引：[$var] → VarRef

        语法："[" var_ref "]"，匿名终端不传入 transformer，
        items 仅包含 VarRef。
        """
        return items[0]  # var_ref 已返回 VarRef

    # ─── recognize 专用 panel 索引（额外支持范围 [start...end]）─────

    def recognize_panel_index_int(self, items):
        """recognize panel 数字索引：[2] → int"""
        return int(str(items[0]))

    def recognize_panel_index_var(self, items):
        """recognize panel 变量索引：[$var] → VarRef"""
        return items[0]

    def recognize_panel_index_range(self, items):
        """recognize panel 范围索引：[1...3] → tuple(start, end)

        items 包含 [recognize_range_endpoint, RANGE_OP, recognize_range_endpoint]，
        RANGE_OP 是命名终端会传入，取 items[0] 和 items[2]。
        """
        return (items[0], items[2])

    def recognize_range_endpoint(self, items):
        """范围端点：INT → int | var_ref → VarRef"""
        val = items[0]
        if isinstance(val, VarRef):
            return val
        return int(str(val))

    # ─── scan 专用 panel 索引（额外支持范围 [start...end]）───────

    def scan_panel_index_int(self, items):
        """scan panel 数字索引：[2] → int"""
        return int(str(items[0]))

    def scan_panel_index_var(self, items):
        """scan panel 变量索引：[$var] → VarRef"""
        return items[0]

    def scan_panel_index_range(self, items):
        """scan panel 范围索引：[1...3] → tuple(start, end)"""
        return (items[0], items[2])

    def scan_range_endpoint(self, items):
        """范围端点：INT → int | var_ref → VarRef"""
        val = items[0]
        if isinstance(val, VarRef):
            return val
        return int(str(val))

    def wait_stmt(self, items):
        arg = items[0]
        return self._build_wait_node(arg, items)

    def wait_clauses(self, items):
        """多个后缀等待子句的容器 — 直接透传列表，由 click_stmt/drag_stmt 拆解"""
        return items

    def wait_clause(self, items):
        """后缀等待子句 — 返回 [timing_str, Wait_node]

        timing_str: "before" / "after" / "around"
        Wait_node 的参数处理与 wait_stmt 完全一致；另支持 wait stable 透传。
        """
        timing = str(items[0]).lower()  # "before" / "after" / "around"
        arg = items[1]
        if isinstance(arg, WaitStable):
            # wait stable 已是完整节点，直接透传
            return [timing, arg]
        wait_node = self._build_wait_node(arg, items)
        return [timing, wait_node]

    def _build_wait_node(self, arg, items):
        """构造 Wait 节点（复用 wait_stmt / wait_clause 的参数解析逻辑）"""
        if isinstance(arg, TupleLiteral):
            # wait_range → TupleLiteral（支持混合数字和变量）
            return Wait(delay=arg, line_no=self._line(items))
        if isinstance(arg, tuple) and len(arg) == 2:
            # 向后兼容：旧式 Python tuple
            return Wait(delay=TupleLiteral(elements=[Literal(value=float(arg[0])), Literal(value=float(arg[1]))]),
                        line_no=self._line(items))
        if isinstance(arg, VarRef):
            # $var → 动态等待时间
            return Wait(delay=arg, line_no=self._line(items))
        # number 规则已将 INT/FLOAT 转为 Python float，直接包装为 Literal
        if isinstance(arg, (int, float)):
            return Wait(delay=Literal(value=arg), line_no=self._line(items))
        if isinstance(arg, Token):
            if arg.type in ("FLOAT", "INT"):
                return Wait(delay=Literal(value=float(arg)), line_no=self._line(items))
            else:  # NAME
                return Wait(delay=Literal(value=str(arg)), line_no=self._line(items))
        # bracket_expr 返回 str → 视为命名延迟
        if isinstance(arg, str):
            return Wait(delay=Literal(value=arg), line_no=self._line(items))
        return Wait(delay=arg, line_no=self._line(items))

    def _build_tuple_literal(self, items):
        """将 tuple_elem 列表转为 TupleLiteral（元素已是 VarRef / Literal）"""
        # tuple_elem 规则已保证元素为 VarRef | Literal，此处直接透传
        return TupleLiteral(elements=list(items))

    def wait_range(self, items):
        """(min, max) → TupleLiteral，支持混合数字和变量"""
        return self._build_tuple_literal(items)

    def tuple_elem(self, items):
        """元组元素：number | var_ref → 直接透传"""
        item = items[0]
        if isinstance(item, VarRef):
            return item
        if isinstance(item, (int, float)):
            return Literal(value=float(item))
        if isinstance(item, Token):
            return Literal(value=float(item))
        return item

    def delay_ref(self, items):
        """@delay_name → NAME token（命名延迟引用）"""
        return items[0]  # NAME Token，_build_wait_node 按 NAME 处理

    # ─── wait stable ───────────────────────────────────────

    def wait_stable_stmt(self, items):
        """wait stable ... → WaitStable 节点（透传 wait_stable_ref 结果）"""
        return items[0]

    def wait_stable_ref(self, items):
        """stable <timeout> [on [scene].[region]] [threshold <v>] [interval <v>] [duration <v>] [least <v>] → WaitStable

        参数支持三种形式：number(float) / delay_ref(NAME Token) / var_ref(VarRef)
        """
        timeout = self._to_ws_param(items[0])
        threshold = 0.02
        interval = 0.3
        stable_duration = 0.5
        least = 0.5
        area = None
        for item in items[1:]:
            if isinstance(item, EntityRef):
                area = item
            elif isinstance(item, dict):
                threshold = item.get("threshold", threshold)
                interval = item.get("interval", interval)
                stable_duration = item.get("stable_duration", stable_duration)
                least = item.get("least", least)
        return WaitStable(timeout=timeout, threshold=threshold,
                          interval=interval, stable_duration=stable_duration,
                          least=least, area=area, line_no=self._line(items))

    def wait_stable_on(self, items):
        """on [scene].[entity] → EntityRef（区域限定子句）"""
        scene_val = self._resolve_const_or_var(items[0])
        entity_val = self._resolve_const_or_var(items[1])
        return EntityRef(scene=scene_val, entity=entity_val)

    def _to_ws_param(self, item):
        """将 wait stable 参数项转为 Literal / VarRef / float"""
        if isinstance(item, VarRef):
            return item
        if isinstance(item, Token):
            # NAME Token（来自 delay_ref）→ Literal
            return Literal(value=str(item))
        # number 已转为 float
        return float(item)

    def wait_stable_opts(self, items):
        """收集 threshold/interval/duration 选项为 dict"""
        result = {}
        for item in items:
            if isinstance(item, dict):
                result.update(item)
        return result

    def wait_stable_opt(self, items):
        """单个选项，直接传递子节点返回的 dict"""
        return items[0]

    def wait_stable_threshold(self, items):
        return {"threshold": self._to_ws_param(items[0])}

    def wait_stable_interval(self, items):
        return {"interval": self._to_ws_param(items[0])}

    def wait_stable_duration(self, items):
        return {"stable_duration": self._to_ws_param(items[0])}

    def wait_stable_least(self, items):
        return {"least": self._to_ws_param(items[0])}

    def range_literal(self, items):
        """(min, max) → TupleLiteral，支持混合数字和变量，用于 eval 赋值"""
        return self._build_tuple_literal(items)

    def scan_stmt(self, items):
        scene_target = items[0]  # tuple: (scene_name, fields_or_var)
        scene_name = scene_target[0]
        scene = EntityRef(scene=scene_name)
        fields = None
        region_var = None
        target = items[1]  # var_ref → VarRef (as 子句)
        if len(scene_target) > 1 and scene_target[1] is not None:
            second = scene_target[1]
            if isinstance(second, list):
                fields = second  # field_list → list[Literal]
            elif isinstance(second, VarRef):
                region_var = second  # 动态 region
        by_clause = None
        where_clause = None
        for item in items[2:]:
            if isinstance(item, ByClause):
                by_clause = item
                if by_clause.full:
                    raise WorkflowUserError(
                        f"'full by' 仅 recognize 语句支持，scan 不支持（第 {self._line(items)} 行）"
                    )
            elif isinstance(item, WhereClause):
                where_clause = item
        return Scan(scene=scene, fields=fields, target=target, region_var=region_var, by=by_clause, where=where_clause, line_no=self._line(items))

    def scan_panel_stmt(self, items):
        """scan [scene].[panel][row][col] as $var [by ...] [where ...]"""
        scene_val = self._resolve_const_or_var(items[0])
        panel_val = self._resolve_const_or_var(items[1])
        row = items[2]
        col = items[3]
        target = items[4]  # var_ref → VarRef
        by_clause = None
        where_clause = None
        for item in items[5:]:
            if isinstance(item, ByClause):
                by_clause = item
            elif isinstance(item, WhereClause):
                where_clause = item
        panel_ref = PanelRef(scene=scene_val, panel=panel_val, row=row, col=col)
        return Scan(scene=panel_ref, target=target, by=by_clause, where=where_clause, line_no=self._line(items))

    def recognize_stmt(self, items):
        """recognize [scene].[f1, f2, ...] as [rich] $var [by ...] [group ...] [where ...] [with ...]"""
        scene_target = items[0]  # tuple: (scene_name, fields_or_var)
        scene_name = scene_target[0]
        scene = EntityRef(scene=scene_name)
        fields = None
        region_var = None
        # 检测 as rich：RICH_KEYWORD Token 出现在 items 中
        rich = any(isinstance(it, Token) and it.type == "RICH_KEYWORD" for it in items)
        # 仅过滤 RICH_KEYWORD Token，保留 const_or_var 可能产生的 STRING Token
        non_token = [it for it in items if not (isinstance(it, Token) and it.type == "RICH_KEYWORD")]
        target = non_token[1]  # var_ref → VarRef (as 子句)
        if len(scene_target) > 1 and scene_target[1] is not None:
            second = scene_target[1]
            if isinstance(second, list):
                fields = second  # field_list → list[Literal]
            elif isinstance(second, VarRef):
                region_var = second  # 动态 region
        by_clause = None
        group_clause = None
        where_clause = None
        with_func = None
        # 解析可选的 by_clause、group_clause、where_clause 和 with_clause
        for item in non_token[2:]:
            if isinstance(item, ByClause):
                by_clause = item
            elif isinstance(item, (Literal, VarRef, list)):
                group_clause = item  # group 子句：Literal | VarRef | list
            elif isinstance(item, WhereClause):
                where_clause = item
            elif isinstance(item, tuple) and item[0] == "__with_func__":
                with_func = Literal(value=item[1])
        return Recognize(scene=scene, fields=fields, target=target, region_var=region_var, by=by_clause, group=group_clause, where=where_clause, rich=rich, with_func=with_func, line_no=self._line(items))

    def recognize_panel_stmt(self, items):
        """recognize [scene].[panel][row][col] as [rich] $var [by ...] [on group ...] [where ...] [with ...]"""
        # 检测 as rich：RICH_KEYWORD Token 出现在 items 中
        rich = any(isinstance(it, Token) and it.type == "RICH_KEYWORD" for it in items)
        # 仅过滤 RICH_KEYWORD Token，保留 const_or_var 可能产生的 STRING Token
        non_token = [it for it in items if not (isinstance(it, Token) and it.type == "RICH_KEYWORD")]
        scene_val = self._resolve_const_or_var(non_token[0])
        panel_val = self._resolve_const_or_var(non_token[1])
        row = non_token[2]
        col = non_token[3]
        target = non_token[4]  # var_ref → VarRef
        by_clause = None
        group_clause = None
        where_clause = None
        with_func = None
        for item in non_token[5:]:
            if isinstance(item, ByClause):
                by_clause = item
            elif isinstance(item, (Literal, VarRef, list)):
                group_clause = item  # group 子句：Literal | VarRef | list
            elif isinstance(item, WhereClause):
                where_clause = item
            elif isinstance(item, tuple) and item[0] == "__with_func__":
                with_func = Literal(value=item[1])
        panel_ref = PanelRef(scene=scene_val, panel=panel_val, row=row, col=col)
        return Recognize(scene=panel_ref, target=target, by=by_clause, group=group_clause, where=where_clause, rich=rich, with_func=with_func, line_no=self._line(items))

    def with_clause(self, items):
        """with <func_name> — 指定 rich 模式的 dict->dict 转换函数"""
        return ("__with_func__", str(items[0]))
