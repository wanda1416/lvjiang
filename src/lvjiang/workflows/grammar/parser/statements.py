"""_StmtMixin：程序入口与语句/目标回调（click/drag/wait/scan/recognize/align/panel）"""

from lark import Token

from ..ast_nodes import (
    Align,
    ByClause,
    Click,
    CoordPoint,
    Drag,
    Find,
    Import,
    Literal,
    PanelGridDrag,
    PanelRef,
    ProcDef,
    Program,
    Recognize,
    Scan,
    SceneRef,
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
        """解析 const_or_var：bracket_expr | STRING | var_ref"""
        if isinstance(item, VarRef):
            return item
        elif isinstance(item, Token) and item.type == 'STRING':
            return self._unquote(str(item))
        else:
            return str(item)

    def click_stmt(self, items):
        """click 目标 [before|after|around wait 参数] — 有 wait_clause 时展开为多条语句"""
        click_node = items[0]
        if len(items) > 1 and isinstance(items[1], list) and len(items[1]) == 2 and isinstance(items[1][1], (Wait, WaitStable)):
            timing, wait_node = items[1]
            if timing == "before":
                return [wait_node, click_node]
            elif timing == "after":
                return [click_node, wait_node]
            else:  # "around" -> before + after 同一参数
                return [wait_node, click_node, wait_node]
        return click_node

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
        return Click(target=SceneRef(scene=scene_val, region=coord_val), line_no=self._line(items))

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
        """drag 目标 [duration] [hold] [before|after|around wait 参数] — 有 wait_clause 时展开为多条语句"""
        # 检查末尾是否有 wait_clause 列表 [timing, Wait_node]
        wait_timing = None
        wait_node = None
        if items and isinstance(items[-1], list) and len(items[-1]) == 2 and isinstance(items[-1][1], (Wait, WaitStable)):
            wait_timing, wait_node = items[-1]
            items = items[:-1]

        drag_node = items[0]  # 已由 drag_*_target 构造为 Drag
        duration = None
        hold = None
        for item in items[1:]:
            if isinstance(item, Literal):
                duration = item
            elif isinstance(item, list):
                duration = item
            elif isinstance(item, float):
                hold = item
        result = Drag(
            scene=drag_node.scene, arrow=drag_node.arrow,
            duration=duration, hold=hold,
            from_point=drag_node.from_point, to_point=drag_node.to_point,
            from_scene_ref=drag_node.from_scene_ref, to_scene_ref=drag_node.to_scene_ref,
            direction=drag_node.direction, distance=drag_node.distance,
            line_no=drag_node.line_no,
        )
        if wait_node:
            if wait_timing == "before":
                return [wait_node, result]
            elif wait_timing == "after":
                return [result, wait_node]
            else:  # "around"
                return [wait_node, result, wait_node]
        return result

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
        scene_ref = SceneRef(scene=scene_val, region=arrow_val)
        return Drag(scene=scene_ref, arrow=scene_ref, line_no=self._line(items))

    def drag_point_pair_target(self, items):
        """drag [scene].[point_1] [scene].[point_2] — 两个命名点之间拖拽"""
        from_scene = self._resolve_const_or_var(items[0])
        from_point = self._resolve_const_or_var(items[1])
        to_scene = self._resolve_const_or_var(items[2])
        to_point = self._resolve_const_or_var(items[3])
        from_ref = SceneRef(scene=from_scene, region=from_point)
        to_ref = SceneRef(scene=to_scene, region=to_point)
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

    def wait_stmt(self, items):
        arg = items[0]
        return self._build_wait_node(arg, items)

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
        if isinstance(arg, tuple) and len(arg) == 2:
            # wait_range → (min, max) 随机范围
            return Wait(delay=arg, line_no=self._line(items))
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

    def wait_range(self, items):
        """(min, max) → (float, float) 随机范围元组"""
        return (float(items[0]), float(items[1]))

    def delay_ref(self, items):
        """@delay_name → NAME token（命名延迟引用）"""
        return items[0]  # NAME Token，_build_wait_node 按 NAME 处理

    # ─── wait stable ───────────────────────────────────────

    def wait_stable_stmt(self, items):
        """wait stable ... → WaitStable 节点（透传 wait_stable_ref 结果）"""
        return items[0]

    def wait_stable_ref(self, items):
        """stable <timeout> [threshold <v>] [interval <v>] [duration <v>] [least <v>] → WaitStable

        参数支持三种形式：number(float) / delay_ref(NAME Token) / var_ref(VarRef)
        """
        timeout = self._to_ws_param(items[0])
        threshold = 0.02
        interval = 0.3
        stable_duration = 0.5
        least = 0.5
        if len(items) > 1 and isinstance(items[1], dict):
            opts = items[1]
            threshold = opts.get("threshold", threshold)
            interval = opts.get("interval", interval)
            stable_duration = opts.get("stable_duration", stable_duration)
            least = opts.get("least", least)
        return WaitStable(timeout=timeout, threshold=threshold,
                          interval=interval, stable_duration=stable_duration,
                          least=least, line_no=self._line(items))

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
        """(min, max) → (float, float) 范围元组，用于 eval 赋值"""
        return (float(items[0]), float(items[1]))

    def scan_stmt(self, items):
        scene_target = items[0]  # tuple: (scene_name, fields_or_var)
        scene_name = scene_target[0]
        scene = SceneRef(scene=scene_name)
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
        """recognize [scene].[f1, f2, ...] as $var [by ...] [group ...] [where ...]"""
        scene_target = items[0]  # tuple: (scene_name, fields_or_var)
        scene_name = scene_target[0]
        scene = SceneRef(scene=scene_name)
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
        group_clause = None
        where_clause = None
        # 解析可选的 by_clause、group_clause 和 where_clause
        for item in items[2:]:
            if isinstance(item, ByClause):
                by_clause = item
            elif isinstance(item, (Literal, VarRef)):
                group_clause = item  # group 子句返回的是 Literal 或 VarRef
            elif isinstance(item, WhereClause):
                where_clause = item
        return Recognize(scene=scene, fields=fields, target=target, region_var=region_var, by=by_clause, group=group_clause, where=where_clause, line_no=self._line(items))

    def recognize_panel_stmt(self, items):
        """recognize [scene].[panel][row][col] as $var [by ...] [on group ...] [where ...]"""
        scene_val = self._resolve_const_or_var(items[0])
        panel_val = self._resolve_const_or_var(items[1])
        row = items[2]
        col = items[3]
        target = items[4]  # var_ref → VarRef
        by_clause = None
        group_clause = None
        where_clause = None
        for item in items[5:]:
            if isinstance(item, ByClause):
                by_clause = item
            elif isinstance(item, (Literal, VarRef)):
                group_clause = item
            elif isinstance(item, WhereClause):
                where_clause = item
        panel_ref = PanelRef(scene=scene_val, panel=panel_val, row=row, col=col)
        return Recognize(scene=panel_ref, target=target, by=by_clause, group=group_clause, where=where_clause, line_no=self._line(items))
