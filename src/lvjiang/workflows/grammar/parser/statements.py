"""_StmtMixin：程序入口与语句/目标回调（click/drag/press/wait/scan/recognize/align/panel）"""

from lark import Token

from ...engine.signals import WorkflowUserError
from ..ast_nodes import (
    Align,
    AndroidAppAction,
    ByClause,
    Click,
    CoordPoint,
    Drag,
    EntityRef,
    Find,
    Import,
    Literal,
    MouseButton,
    Move,
    PanelGridDrag,
    PanelRef,
    Paste,
    Place,
    Press,
    PressMode,
    ProcDef,
    Program,
    Recognize,
    ReplayInputTrace,
    Scan,
    Scroll,
    SubsceneEntityRef,
    TupleLiteral,
    VarRef,
    Wait,
    WaitStable,
    WhereClause,
)

# click 鼠标键别名：back/forward 更直观，规范化为轨迹格式统一使用的
# x1/x2（与 replay input_trace / recorder 高精度录制的键名保持一致）。
_CLICK_BUTTON_ALIASES = {"back": "x1", "forward": "x2"}


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

    # ─── 公共 wait_clause 展开 ──────────────────────────────

    @staticmethod
    def _extract_wait_pairs(items):
        """从 items 末尾提取 wait_clauses 产生的 [timing, Wait] 对列表。

        wait_clauses 规则产生一个嵌套列表，位于 items 末尾。
        返回 wait_pairs 列表（可能为空），以及去除 wait 后的 core_items。
        """
        if not items:
            return [], items
        last = items[-1]
        if (isinstance(last, list) and last
                and isinstance(last[0], list) and len(last[0]) == 2
                and isinstance(last[0][1], (Wait, WaitStable))):
            return last, items[:-1]
        return [], items

    @staticmethod
    def _expand_wait_clauses(action_node, wait_pairs):
        """公共 wait_clause 展开：around → before + after，按语义顺序组装。

        返回 list：[before_waits..., action_node, after_waits...]
        若无 wait_pairs 则返回 action_node 本身。
        """
        if not wait_pairs:
            return action_node

        expanded = []
        for timing, wait_node in wait_pairs:
            if timing == "around":
                expanded.append(("before", wait_node))
                expanded.append(("after", wait_node))
            else:
                expanded.append((timing, wait_node))

        before_waits = [w for t, w in expanded if t == "before"]
        after_waits = [w for t, w in expanded if t == "after"]
        return before_waits + [action_node] + after_waits

    @staticmethod
    def _expand_wait_clauses_many(action_nodes, wait_pairs):
        """把 wait_clause 包在一组不可拆分的语法糖动作之外。"""
        if not wait_pairs:
            return action_nodes
        expanded = []
        for timing, wait_node in wait_pairs:
            if timing == "around":
                expanded.extend((("before", wait_node), ("after", wait_node)))
            else:
                expanded.append((timing, wait_node))
        before_waits = [w for timing, w in expanded if timing == "before"]
        after_waits = [w for timing, w in expanded if timing == "after"]
        return before_waits + list(action_nodes) + after_waits

    # ─── 基础指令 ─────────────────────────────────────────

    def android_app_timeout(self, items):
        return ("timeout", items[0])

    def android_app_stmt(self, items):
        action = str(items[0]).lower()
        raw_name = items[1]
        name = raw_name if isinstance(raw_name, VarRef) else Literal(
            value=self._unquote(str(raw_name)))
        timeout = None
        if len(items) > 2 and isinstance(items[2], tuple):
            raw_timeout = items[2][1]
            timeout = raw_timeout if isinstance(raw_timeout, VarRef) else Literal(
                value=float(raw_timeout))
        return AndroidAppAction(
            action=action, name=name, timeout=timeout,
            line_no=self._line(items),
        )

    def _resolve_const_or_var(self, item):
        """解析 const_or_var：bracket_expr | var_ref"""
        if isinstance(item, VarRef):
            return item
        else:
            return str(item)

    def replay_input_trace_stmt(self, items):
        """replay input_trace "path"。"""
        return ReplayInputTrace(
            path=self._unquote(str(items[0])),
            line_no=self._line(items),
        )

    def click_stmt(self, items):
        """click 目标 [left|right|middle|x1|x2|back|forward]? [before|after|around wait 参数 ...]

        鼠标键可选，省略时默认左键（click_node 已由 click_*_target 按此
        默认构造）；显式指定时按 token 类型（而非位置）提取，与
        scroll_stmt 提取 SCROLL_DIR 同一套路。
        显式 wait_clause 时抑制 click 默认的 before/after_click_wait 延迟。
        around 是语法糖，等价于同时指定 before 和 after（同一参数）。
        """
        click_node = items[0]  # 已由 click_*_target 构造为 Click（默认 button="left"）
        wait_pairs, core_items = self._extract_wait_pairs(items[1:])

        button = click_node.button
        for item in core_items:
            if isinstance(item, Token) and item.type == "CLICK_BUTTON":
                raw = str(item).lower()
                button = _CLICK_BUTTON_ALIASES.get(raw, raw)

        if not wait_pairs and button == click_node.button:
            return click_node

        # 显式指定按键和/或 wait_clause → 重建节点；wait_clause 存在时抑制默认延迟
        click_node = Click(target=click_node.target, line_no=click_node.line_no,
                          suppress_defaults=bool(wait_pairs), button=button)
        if not wait_pairs:
            return click_node
        return self._expand_wait_clauses(click_node, wait_pairs)

    def mouse_button_stmt(self, items):
        """mouse left|right|middle|x1|x2 down|up — 原始鼠标键事件。"""
        raw_button = str(items[0]).lower()
        button = _CLICK_BUTTON_ALIASES.get(raw_button, raw_button)
        pressed = str(items[1]).lower() == "down"
        return MouseButton(
            button=button,
            pressed=pressed,
            line_no=self._line(items),
        )

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

    def click_subscene_target(self, items):
        return Click(target=SubsceneEntityRef(
            scene=self._resolve_const_or_var(items[0]),
            reference=self._resolve_const_or_var(items[1]),
            entity=self._resolve_const_or_var(items[2])),
            line_no=self._line(items))

    def scan_subscene_stmt(self, items):
        ref = SubsceneEntityRef(*(self._resolve_const_or_var(i) for i in items[:3]))
        target = items[3]
        by_clause = next((i for i in items[4:] if isinstance(i, ByClause)), None)
        where_clause = next((i for i in items[4:] if isinstance(i, WhereClause)), None)
        self._reject_image_by(by_clause, "scan", items)
        return Scan(scene=ref, target=target, by=by_clause, where=where_clause,
                    line_no=self._line(items))

    def recognize_subscene_stmt(self, items):
        ref = SubsceneEntityRef(*(self._resolve_const_or_var(i) for i in items[:3]))
        rich = any(isinstance(i, Token) and i.type == "RICH_KEYWORD" for i in items)
        values = [i for i in items[3:]
                  if not (isinstance(i, Token) and i.type == "RICH_KEYWORD")]
        target = values[0]
        by_clause = next((i for i in values[1:] if isinstance(i, ByClause)), None)
        where_clause = next((i for i in values[1:] if isinstance(i, WhereClause)), None)
        with_func = next((Literal(value=i[1]) for i in values[1:]
                          if isinstance(i, tuple) and i[0] == "__with_func__"), None)
        group = next((i for i in values[1:]
                      if isinstance(i, (Literal, VarRef, list))), None)
        self._reject_image_by(by_clause, "recognize", items)
        self._reject_recognize_rich_by(rich, by_clause, items)
        return Recognize(scene=ref, target=target, by=by_clause, group=group,
                         where=where_clause, rich=rich, with_func=with_func,
                         line_no=self._line(items))

    def click_coord_target(self, items):
        """click (rx, ry) — 画布归一化坐标点"""
        cp = items[0]  # CoordPoint
        self._validate_coord_point(cp, relative=False, command="click")
        return Click(target=cp, line_no=0)

    def click_var_target(self, items):
        """click $var — 裸变量引用（find 指令产出的 FoundRegion）"""
        var_ref = items[0]  # VarRef
        return Click(target=var_ref, line_no=self._line(items))

    def coord_point(self, items):
        """(rx, ry) → CoordPoint，number 规则已转为 float"""
        return CoordPoint(rx=float(items[0]), ry=float(items[1]))

    @staticmethod
    def _validate_coord_point(
        point: CoordPoint,
        *,
        relative: bool,
        command: str,
    ):
        low = -1.0 if relative else 0.0
        if not (low <= point.rx <= 1.0 and low <= point.ry <= 1.0):
            label = "相对位移" if relative else "绝对坐标"
            raise WorkflowUserError(
                f"{command}: {label} ({point.rx}, {point.ry}) 超出 "
                f"[{int(low)},1] 归一化范围")

    def place_stmt(self, items):
        """place (rx, ry) — 直接设置鼠标位置。"""
        wait_pairs, core_items = self._extract_wait_pairs(items)
        self._validate_coord_point(
            core_items[0], relative=False, command="place")
        node = Place(target=core_items[0], line_no=self._line(items))
        return self._expand_wait_clauses(node, wait_pairs)

    def move_duration(self, items):
        """duration <number|$var>。"""
        value = items[0]
        return value if isinstance(value, VarRef) else Literal(value=float(value))

    def move_to_action(self, items):
        """[起点] to 目标 [duration t]，显式起点展开为 place。"""
        click_index, click_node = next(
            (index, item) for index, item in enumerate(items)
            if isinstance(item, Click)
        )
        start = next(
            (item for item in items[:click_index]
             if isinstance(item, CoordPoint)),
            None,
        )
        if start is not None:
            self._validate_coord_point(
                start, relative=False, command="move 起点")
        if isinstance(click_node.target, CoordPoint):
            self._validate_coord_point(
                click_node.target, relative=False, command="move to")
        duration = next(
            (item for item in items
             if isinstance(item, (Literal, VarRef))),
            None,
        )
        move = Move(
            target=click_node.target,
            mode="to",
            duration=duration,
            line_no=click_node.line_no,
        )
        return [Place(start, line_no=click_node.line_no), move] if start else [move]

    def move_by_action(self, items):
        """[起点] by (横向比例, 纵向比例) [duration t]。"""
        points = [item for item in items if isinstance(item, CoordPoint)]
        start = points[0] if len(points) == 2 else None
        delta = points[-1]
        if start is not None:
            self._validate_coord_point(
                start, relative=False, command="move 起点")
        self._validate_coord_point(delta, relative=True, command="move by")
        duration = next(
            (item for item in items
             if isinstance(item, (Literal, VarRef))),
            None,
        )
        move = Move(
            target=delta,
            mode="by",
            duration=duration,
            line_no=self._line(items),
        )
        return [Place(start, line_no=move.line_no), move] if start else [move]

    def move_stmt(self, items):
        """move ... [wait_clauses]，等待包围完整 place+move 序列。"""
        wait_pairs, core_items = self._extract_wait_pairs(items)
        return self._expand_wait_clauses_many(core_items[0], wait_pairs)

    def scroll_stmt(self, items):
        """scroll [目标] up|down [数量] [interval 秒数] [before|after|around wait ...] — 鼠标滚轮滚动

        与 click/move/drag 语法风格保持一致：目标（如果存在）紧跟指令关键字，
        方向 up/down 紧随目标出现，数量参数收尾。
        复用 click_target 子规则解析目标（如果存在），
        按 token 类型（而非位置）提取方向，提取数量参数（如果存在）。
        interval 由 scroll_interval 包裹为 Literal，以便与数量（裸 int/float/VarRef）区分。
        scroll 没有默认延迟，不需要 suppress_defaults。
        """
        wait_pairs, core_items = self._extract_wait_pairs(items)

        direction = None
        target = None
        amount = 1
        interval = None

        for item in core_items:
            if isinstance(item, Click):
                target = item.target
            elif isinstance(item, Token) and item.type == "SCROLL_DIR":
                direction = str(item).lower()
            elif isinstance(item, Literal):
                interval = item.value
            elif isinstance(item, VarRef):
                amount = item
            elif isinstance(item, (int, float)):
                amount = int(item)

        scroll_node = Scroll(
            direction=direction,
            target=target,
            amount=amount,
            interval=interval,
            line_no=self._line(items),
        )
        return self._expand_wait_clauses(scroll_node, wait_pairs)

    def scroll_interval(self, items):
        """interval <秒数> → Literal(float)。包裹为 Literal 是为了在 scroll_stmt
        中与裸 int/float 的数量参数区分开（两者同为数值，不能按类型区分）。"""
        return Literal(value=float(items[0]))

    def drag_stmt(self, items):
        """drag 目标 [duration] [hold] [before|after|around wait 参数 ...] — 支持 before/after 任意组合

        显式 wait_clause 时抑制 drag 默认的 before/after_click_wait 延迟。
        around 是语法糖，等价于同时指定 before 和 after（同一参数）。
        """
        wait_pairs, core_items = self._extract_wait_pairs(items)

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
        return self._expand_wait_clauses(result, wait_pairs)

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
        self._validate_coord_point(
            from_point, relative=False, command="drag 起点")
        self._validate_coord_point(
            to_point, relative=False, command="drag 终点")
        return Drag(scene=None, arrow=None, from_point=from_point, to_point=to_point, line_no=0)

    def drag_duration(self, items):
        item = items[0]
        if isinstance(item, list):
            return item[:2]
        return Literal(value=float(item))

    def drag_hold(self, items):
        """hold <seconds> → float"""
        return float(items[0])

    # ─── press 指令 ─────────────────────────────────────

    def press_key_chain(self, items):
        """组合键各项；字符串去引号，变量保持 VarRef。"""
        return tuple(
            item if isinstance(item, VarRef) else self._unquote(str(item))
            for item in items
        )

    def press_stmt(self, items):
        """press key ("+" key)* [hold N | down | up] [wait ...]

        按键名可以是字符串字面量，也可以是变量——引擎侧 _exec_press 用
        _resolve 取值后统一 str() 再 normalize_key，所以变量存数字也能按
        （调用方若要明确语义，可在 wf 里自行转成字符串）。

        wait_clause 展开为独立 Wait 语句，按语义顺序排列：before 在前，after 在后。
        press 没有默认延迟，不需要 suppress_defaults。
        """
        keys = tuple(items[0])
        key = keys[0]
        mode = PressMode.PRESS
        duration = None
        # 先用共享 helper 提取 wait_pairs
        wait_pairs, non_wait_items = self._extract_wait_pairs(items[1:])

        for item in non_wait_items:
            if item is None:
                continue
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], PressMode):
                mode, duration = item

        press_node = Press(
            key=key, keys=keys, mode=mode, duration=duration,
            line_no=self._line(items))
        return self._expand_wait_clauses(press_node, wait_pairs)

    def press_hold(self, items):
        """hold <number> → (PressMode.HOLD, duration)"""
        return (PressMode.HOLD, float(items[0]))

    def press_down(self, items):
        """down → (PressMode.DOWN, None)"""
        return (PressMode.DOWN, None)

    def press_up(self, items):
        """up → (PressMode.UP, None)"""
        return (PressMode.UP, None)

    def paste_stmt(self, items):
        """paste <expr> [wait ...]，wait 子句按通用规则展开。"""
        wait_pairs, core_items = self._extract_wait_pairs(items)
        value = next(item for item in core_items if item is not None)
        node = Paste(value=value, line_no=self._line(items))
        return self._expand_wait_clauses(node, wait_pairs)

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

    def rect_literal(self, items):
        """(x, y, w, h) → 4 元 TupleLiteral；引擎求值为 RectCoordRef"""
        return self._build_tuple_literal(items)

    def _reject_image_by(self, by_clause, verb: str, items):
        """by image 只属于 find；scan/recognize 是文字/参考图识别。"""
        if by_clause is not None and by_clause.match_mode == "image":
            raise WorkflowUserError(
                f"'by image' 仅 find 语句支持，{verb} 不支持（第 {self._line(items)} 行）"
            )

    def _reject_recognize_rich_by(self, rich: bool, by_clause, items):
        """recognize 的 rich 与 by 返回类型冲突，不允许组合。"""
        if rich and by_clause is not None:
            raise WorkflowUserError(
                "recognize 不能同时使用 'as rich' 和 'by'："
                "'as rich' 返回完整识别信息，'by' 返回命中位置"
                f"（第 {self._line(items)} 行）"
            )

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
        self._reject_image_by(by_clause, "scan", items)
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
        self._reject_image_by(by_clause, "scan", items)
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
        self._reject_image_by(by_clause, "recognize", items)
        self._reject_recognize_rich_by(rich, by_clause, items)
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
        self._reject_image_by(by_clause, "recognize", items)
        self._reject_recognize_rich_by(rich, by_clause, items)
        return Recognize(scene=panel_ref, target=target, by=by_clause, group=group_clause, where=where_clause, rich=rich, with_func=with_func, line_no=self._line(items))

    def with_clause(self, items):
        """with <func_name> — 指定 rich 模式的 dict->dict 转换函数"""
        return ("__with_func__", str(items[0]))
