"""工作流 DSL v2 解析器

基于 Lark 的解析器，将 .wf 文件解析为 AST 节点树。

对外接口：
    parse_file(path) -> Program
    parse_text(text) -> Program
"""

from pathlib import Path

from lark import Lark, Transformer, Token, Tree

from .ast_nodes import (
    Program,
    Click, Drag, Wait, Scan, Recognize, Collect, Log, Call,
    If, For, Loop, Break, Return, Label, Goto, Eval, EvalFieldChainAssign, FuncCall,
    SceneRef, VarRef, Literal, FieldAccess,
    Contains, Equals, InList, IsEmpty,
    GreaterThan, LessThan, GreaterEqual, LessEqual, NotEqual, NumericEqual,
    Not, And, Or,
)

# ─── Lark 实例（延迟初始化） ──────────────────────────────

_parser: Lark | None = None


def _get_parser() -> Lark:
    global _parser
    if _parser is None:
        grammar_path = Path(__file__).parent / "grammar.lark"
        _parser = Lark(
            grammar_path.read_text(encoding="utf-8"),
            parser="earley",
            propagate_positions=True,
        )
    return _parser


# ─── Transformer：Parse Tree → AST ────────────────────────

class _DSLTransformer(Transformer):
    """将 Lark 解析树转换为 DSL AST 节点"""

    # ─── 程序入口 ─────────────────────────────────────────

    def start(self, items):
        """过滤掉 None（空行），收集所有语句"""
        stmts = [item for item in items if item is not None]
        return Program(body=stmts)

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
        """click scene.coord — scene 和 coord 都可以是常量或变量"""
        scene, coord = items  # 两个 const_or_var
        scene_val = self._resolve_const_or_var(scene)
        coord_val = self._resolve_const_or_var(coord)
        return Click(target=SceneRef(scene=scene_val, region=coord_val), line_no=self._line(items))

    def drag_stmt(self, items):
        """drag scene.arrow — scene 和 arrow 都可以是常量或变量"""
        scene_val = self._resolve_const_or_var(items[0])
        arrow_val = self._resolve_const_or_var(items[1])
        scene_ref = SceneRef(scene=scene_val, region=arrow_val)
        duration = None
        hold = None
        for item in items[2:]:
            if isinstance(item, Literal):
                duration = item
            elif isinstance(item, list):
                duration = item
            elif isinstance(item, float):
                hold = item
        return Drag(scene=scene_ref, arrow=scene_ref, duration=duration, hold=hold, line_no=self._line(items))

    def drag_duration(self, items):
        item = items[0]
        if isinstance(item, list):
            return item[:2]
        return Literal(value=float(item))

    def drag_hold(self, items):
        """hold <seconds> → float"""
        return float(items[0])

    def wait_stmt(self, items):
        arg = items[0]
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
        return Scan(scene=scene, fields=fields, target=target, region_var=region_var, line_no=self._line(items))

    def recognize_stmt(self, items):
        """recognize [scene].[f1, f2, ...] as $var 或 recognize [scene].$var as $var"""
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
        return Recognize(scene=scene, fields=fields, target=target, region_var=region_var, line_no=self._line(items))

    def scene_target_static(self, items):
        """[scene] 或 [scene].[f1, f2] 或 $var.[f1] 等"""
        scene_name = self._resolve_scene_name(items[0])
        field_list = items[1] if len(items) > 1 else None
        return (scene_name, field_list)

    def scene_target_dyn(self, items):
        """[scene].$var 或 $var.$field 等"""
        scene_name = self._resolve_scene_name(items[0])
        var_ref = items[1]  # VarRef
        return (scene_name, var_ref)

    def _resolve_scene_name(self, item):
        """解析场景名：bracket_expr→str, STRING→去引号str, var_ref→VarRef"""
        if isinstance(item, VarRef):
            return item  # 动态场景名
        if isinstance(item, Token) and item.type == 'STRING':
            return self._unquote(str(item))  # 字符串常量
        return str(item)  # bracket_expr

    def collect_stmt(self, items):
        source = items[0]  # var_ref → VarRef
        alias = None
        alias_var = None
        if len(items) > 1:
            # collect_as_clause 返回 str 或 VarRef
            alias_item = items[1]
            if isinstance(alias_item, VarRef):
                alias_var = alias_item  # 动态 alias
            elif isinstance(alias_item, str):
                alias = alias_item
            elif isinstance(alias_item, Token):
                alias = self._unquote(str(alias_item))
        return Collect(source=source, alias=alias, alias_var=alias_var, line_no=self._line(items))

    def collect_as_clause(self, items):
        """as "label" 或 as $var → 字符串或 VarRef"""
        item = items[0]
        if isinstance(item, VarRef):
            return item  # 动态 alias
        return self._unquote(str(item))  # 静态 alias

    def log_stmt(self, items):
        arg = items[0]
        # log 参数可以是：字符串常量、函数调用、变量引用、字段访问
        if isinstance(arg, FuncCall):
            return Log(message=arg, line_no=self._line(items))
        if isinstance(arg, VarRef):
            return Log(message=arg, line_no=self._line(items))
        if isinstance(arg, FieldAccess):
            return Log(message=arg, line_no=self._line(items))
        return Log(message=self._ensure_literal(arg), line_no=self._line(items))

    def log_arg(self, items):
        """log_arg: 透传任何表达式"""
        return items[0]

    def eval_assign_func(self, items):
        """eval $var = func($arg...)"""
        tokens = [i for i in items if isinstance(i, Token)]
        names = [str(t) for t in tokens]
        lists = [i for i in items if isinstance(i, list)]
        func_args = lists[0] if lists else []
        # names[0] = 赋值目标变量名, names[1] = 函数名
        return Eval(func_name=names[1], func_args=func_args, target=names[0], line_no=self._line(items))

    def eval_assign_lit(self, items):
        """eval $var = "string" | 123 | -1.5 | {} | [list] | (min, max)"""
        tokens = [i for i in items if isinstance(i, Token)]
        target_name = str(tokens[0])  # $ 后面的 NAME
        lit_value = items[1]
        # 空字典快捷路径
        if isinstance(lit_value, dict):
            return Eval(func_name="__empty_dict__", func_args=[], target=target_name, line_no=self._line(items))
        # 列表快捷路径
        if isinstance(lit_value, list):
            return Eval(func_name="__list__", func_args=lit_value, target=target_name, line_no=self._line(items))
        # 范围元组路径：(min, max)
        if isinstance(lit_value, tuple):
            return Eval(func_name="__range__", func_args=[Literal(value=lit_value)], target=target_name, line_no=self._line(items))
        if isinstance(lit_value, Token):
            lit_value = self._unquote(str(lit_value))
        # 用 Eval 节点承载字面量赋值：func_name="__literal__"，func_args=[Literal(value)]
        return Eval(func_name="__literal__", func_args=[Literal(value=lit_value)], target=target_name, line_no=self._line(items))

    def eval_discard(self, items):
        """eval func($arg...) — 丢弃返回值"""
        tokens = [i for i in items if isinstance(i, Token)]
        names = [str(t) for t in tokens]
        lists = [i for i in items if isinstance(i, list)]
        func_args = lists[0] if lists else []
        return Eval(func_name=names[0], func_args=func_args, target=None, line_no=self._line(items))

    def eval_assign_expr(self, items):
        """eval $var = field_access | var_ref — 表达式赋值"""
        tokens = [i for i in items if isinstance(i, Token)]
        target_name = str(tokens[0])
        expr = items[1]  # FieldAccess or VarRef
        return Eval(func_name="__expr__", func_args=[expr], target=target_name, line_no=self._line(items))

    def eval_field_assign(self, items):
        """eval $dict.key = value 或 eval $dict.key1.key2 = value — 字段赋值"""
        # items: field_access, eval_rhs
        target = items[0]  # FieldAccess
        value = items[1]   # eval_rhs result
        return EvalFieldChainAssign(target=target, value=value, line_no=self._line(items))

    # ─── 隐式 eval（省略 eval 关键字）─────────────────────

    def implicit_eval_assign_func(self, items):
        """$var = func($arg...) — 隐式函数调用赋值"""
        tokens = [i for i in items if isinstance(i, Token)]
        names = [str(t) for t in tokens]
        lists = [i for i in items if isinstance(i, list)]
        func_args = lists[0] if lists else []
        return Eval(func_name=names[1], func_args=func_args, target=names[0], line_no=self._line(items))

    def implicit_eval_assign_lit(self, items):
        """$var = "string" | 123 | {} | [list] | (min, max) — 隐式字面量赋值"""
        tokens = [i for i in items if isinstance(i, Token)]
        target_name = str(tokens[0])
        lit_value = items[1]
        if isinstance(lit_value, dict):
            return Eval(func_name="__empty_dict__", func_args=[], target=target_name, line_no=self._line(items))
        if isinstance(lit_value, list):
            return Eval(func_name="__list__", func_args=lit_value, target=target_name, line_no=self._line(items))
        if isinstance(lit_value, tuple):
            return Eval(func_name="__range__", func_args=[Literal(value=lit_value)], target=target_name, line_no=self._line(items))
        if isinstance(lit_value, Token):
            lit_value = self._unquote(str(lit_value))
        return Eval(func_name="__literal__", func_args=[Literal(value=lit_value)], target=target_name, line_no=self._line(items))

    def implicit_eval_assign_expr(self, items):
        """$var = field_access | $other — 隐式表达式赋值"""
        tokens = [i for i in items if isinstance(i, Token)]
        target_name = str(tokens[0])
        expr = items[1]
        return Eval(func_name="__expr__", func_args=[expr], target=target_name, line_no=self._line(items))

    def implicit_eval_field_assign(self, items):
        """$dict.key = value — 隐式字段赋值"""
        target = items[0]
        value = items[1]
        return EvalFieldChainAssign(target=target, value=value, line_no=self._line(items))

    def default_stmt(self, items):
        """default $var = literal — 仅当变量未设置时赋默认值"""
        tokens = [i for i in items if isinstance(i, Token)]
        target_name = str(tokens[0])
        lit_value = items[1]
        if isinstance(lit_value, Token):
            lit_value = self._unquote(str(lit_value))
        # 统一用 __default__，引擎根据值的类型处理
        return Eval(func_name="__default__", func_args=[Literal(value=lit_value)], target=target_name, line_no=self._line(items))

    def eval_rhs_func(self, items):
        """eval_rhs: NAME ( arg_list? ) → FuncCall"""
        tokens = [i for i in items if isinstance(i, Token)]
        func_name = str(tokens[0])
        lists = [i for i in items if isinstance(i, list)]
        func_args = lists[0] if lists else []
        return FuncCall(func_name=func_name, func_args=func_args, line_no=self._line(items))

    def eval_rhs_lit(self, items):
        """eval_rhs: literal → Literal | dict"""
        val = items[0]
        if isinstance(val, dict):
            return val  # 空字典
        if isinstance(val, Token):
            return Literal(value=self._unquote(str(val)))
        return val  # number (float)

    def eval_rhs_field(self, items):
        """eval_rhs: field_access → FieldAccess"""
        return items[0]

    def eval_rhs_var(self, items):
        """eval_rhs: var_ref → VarRef"""
        return items[0]

    def func_call(self, items):
        """func_name(arg_list?) → FuncCall"""
        tokens = [i for i in items if isinstance(i, Token)]
        func_name = str(tokens[0])
        lists = [i for i in items if isinstance(i, list)]
        func_args = lists[0] if lists else []
        return FuncCall(func_name=func_name, func_args=func_args, line_no=self._line(items))

    def empty_dict(self, items):
        """{} → 空字典标记"""
        return {}

    def arg_list(self, items):
        return list(items)

    def arg_lit(self, items):
        return Literal(value=self._unquote(str(items[0])))

    def arg_var(self, items):
        """var_ref 作为函数参数 → VarRef"""
        return items[0]  # var_ref 已返回 VarRef

    def arg_field(self, items):
        """field_access 作为函数参数 → FieldAccess"""
        return items[0]  # field_access 已返回 FieldAccess

    # ─── 子工作流调用 ─────────────────────────────────────

    def call_stmt(self, items):
        wf_path = self._ensure_literal(items[0])
        args = []
        reads = []
        for item in items[1:]:
            if isinstance(item, tuple) and len(item) == 2:
                tag, pairs = item
                if tag == "with":
                    args = pairs
                elif tag == "read":
                    reads = pairs
        return Call(workflow=wf_path, args=args, reads=reads, line_no=self._line(items))

    def call_with_clause(self, items):
        """with $x as "name" → ("with", [(left, right), ...])"""
        return ("with", list(items))

    def call_read_clause(self, items):
        """read "key" as $var → ("read", [(left, right), ...])"""
        return ("read", list(items))

    def as_var(self, items):
        """$x as "name" 或 "key" as $var → (left_node, right_node)
        
        VarRef 原样保留，STRING Token 转为 Literal
        """
        left = items[0] if isinstance(items[0], VarRef) else self._ensure_literal(items[0])
        right = items[1] if isinstance(items[1], VarRef) else self._ensure_literal(items[1])
        return (left, right)

    # ─── 控制流 ───────────────────────────────────────────

    def if_stmt(self, items):
        condition = items[0]
        then_body = []
        else_body = []
        in_else = False
        for item in items[1:]:
            if isinstance(item, tuple) and len(item) == 2 and item[0] == "else":
                in_else = True
                else_body = item[1]
            elif item is not None and not isinstance(item, Token):
                if in_else:
                    else_body.append(item)
                else:
                    then_body.append(item)
        return If(condition=condition, then_body=then_body, else_body=else_body, line_no=self._line(items))

    def else_clause(self, items):
        """返回标记元组，便于 if_stmt 区分 then/else"""
        body = [i for i in items if i is not None]
        return ("else", body)

    def for_stmt(self, items):
        var_name = str(items[0])
        iterable = items[1]  # for_iter → list[Literal] | VarRef
        body = [i for i in items[2:] if i is not None and not isinstance(i, Token)]
        return For(var=var_name, iterable=iterable, body=body, line_no=self._line(items))

    def for_iter_static(self, items):
        """for_iter: list_literal → list[Literal | VarRef]"""
        return items[0]  # list_literal 已返回 list

    def for_iter_var(self, items):
        """for_iter: var_ref → VarRef"""
        return items[0]  # var_ref 已返回 VarRef

    def loop_stmt(self, items):
        count_val = items[0]
        if isinstance(count_val, (int, float)):
            count = int(count_val)  # number 规则产出 float，loop 需要 int
        elif isinstance(count_val, VarRef):
            count = count_val  # 变量引用，运行时解析
        elif isinstance(count_val, Token):
            count = int(str(count_val))
        else:
            count = str(count_val)
        body = [i for i in items[1:] if i is not None and not isinstance(i, Token)]
        return Loop(count=count, body=body, line_no=self._line(items))

    def break_stmt(self, items):
        return Break(line_no=self._line(items))

    def return_stmt(self, items):
        return Return(line_no=self._line(items))

    def label_stmt(self, items):
        return Label(name=str(items[0]), line_no=self._line(items))

    def goto_stmt(self, items):
        return Goto(target=str(items[0]), line_no=self._line(items))

    # ─── 条件表达式 ───────────────────────────────────────

    def cond_passthrough(self, items):
        return items[0]

    def or_op(self, items):
        return Or(left=items[0], right=items[1], line_no=self._line(items))

    def and_op(self, items):
        return And(left=items[0], right=items[1], line_no=self._line(items))

    def not_op(self, items):
        return Not(operand=items[0], line_no=self._line(items))

    def contains_op(self, items):
        field_access, text_node = items
        right = text_node if isinstance(text_node, VarRef) else Literal(value=self._unquote(str(text_node)))
        return Contains(left=field_access, right=right, line_no=self._line(items))

    def equals_op(self, items):
        field_access, text_node = items
        right = text_node if isinstance(text_node, VarRef) else Literal(value=self._unquote(str(text_node)))
        return Equals(left=field_access, right=right, line_no=self._line(items))

    def in_op(self, items):
        field_access, list_literal = items
        return InList(left=field_access, right=list_literal, line_no=self._line(items))

    def is_empty_op(self, items):
        return IsEmpty(expr=items[0], line_no=self._line(items))

    def var_cond(self, items):
        """条件中的 $var → VarRef（truthy 检查）"""
        return items[0]

    def field_base(self, items):
        """$var.field → FieldAccess(root=VarRef, field_name)"""
        var_ref, field_name = items
        return FieldAccess(root=var_ref, field_name=str(field_name))

    def field_chain(self, items):
        """field_access.field → FieldAccess(root=FieldAccess, field_name)"""
        prev_access, field_name = items
        return FieldAccess(root=prev_access, field_name=str(field_name))

    def field_index_base(self, items):
        """$list[$i] → FieldAccess(root=VarRef, field_name=VarRef) — 列表索引访问"""
        var_ref, index_ref = items
        return FieldAccess(root=var_ref, field_name=index_ref)

    def field_index_chain(self, items):
        """field_access[$i] → FieldAccess(root=FieldAccess, field_name=VarRef) — 列表索引链"""
        prev_access, index_ref = items
        return FieldAccess(root=prev_access, field_name=index_ref)

    def field_var_base(self, items):
        """$dict.$key → FieldAccess(root=VarRef, field_name=VarRef)"""
        var_ref, key_ref = items
        return FieldAccess(root=var_ref, field_name=key_ref)

    def field_var_chain(self, items):
        """field_access.$key → FieldAccess(root=FieldAccess, field_name=VarRef)"""
        prev_access, key_ref = items
        return FieldAccess(root=prev_access, field_name=key_ref)

    def field_str_base(self, items):
        """$var."key" → FieldAccess(root=VarRef, field_name=Literal)"""
        var_ref, string_token = items
        return FieldAccess(root=var_ref, field_name=Literal(value=self._unquote(str(string_token))))

    def field_str_chain(self, items):
        """field_access."key" → FieldAccess(root=FieldAccess, field_name=Literal)"""
        prev_access, string_token = items
        return FieldAccess(root=prev_access, field_name=Literal(value=self._unquote(str(string_token))))

    def field_bracket_base(self, items):
        """$var.[key] → FieldAccess(root=VarRef, field_name=Literal)"""
        var_ref, name_token = items
        return FieldAccess(root=var_ref, field_name=Literal(value=str(name_token)))

    def field_bracket_chain(self, items):
        """field_access.[key] → FieldAccess(root=FieldAccess, field_name=Literal)"""
        prev_access, name_token = items
        return FieldAccess(root=prev_access, field_name=Literal(value=str(name_token)))

    def gt_op(self, items):
        field_access, number = items
        return GreaterThan(left=field_access, right=number, line_no=self._line(items))

    def lt_op(self, items):
        field_access, number = items
        return LessThan(left=field_access, right=number, line_no=self._line(items))

    def ge_op(self, items):
        field_access, number = items
        return GreaterEqual(left=field_access, right=number, line_no=self._line(items))

    def le_op(self, items):
        field_access, number = items
        return LessEqual(left=field_access, right=number, line_no=self._line(items))

    def ne_op(self, items):
        field_access, number = items
        return NotEqual(left=field_access, right=number, line_no=self._line(items))

    def eq_num_op(self, items):
        field_access, number = items
        return NumericEqual(left=field_access, right=number, line_no=self._line(items))

    def number_float(self, items):
        return float(items[0])

    def number_int(self, items):
        return float(int(items[0]))  # 统一为 float

    def number_neg_float(self, items):
        return -float(items[0])

    def number_neg_int(self, items):
        return -float(int(items[0]))

    # ─── 通用原子 ─────────────────────────────────────────

    def var_ref(self, items):
        """$name → VarRef"""
        return VarRef(name=str(items[0]))

    def bracket_expr(self, items):
        """[name] → str（场景名或区域名，由父节点组装为 SceneRef）"""
        return str(items[0])

    def bracket_list(self, items):
        """[a, b, "c"] → list[Literal]"""
        result = []
        for item in items:
            s = str(item)
            if s.startswith('"') and s.endswith('"'):
                s = self._unquote(s)
            result.append(Literal(value=s))
        return result

    def list_literal(self, items):
        """[item1, item2, ...] → list[Literal | VarRef]"""
        return [item for item in items if item is not None]

    def list_item_str(self, items):
        """字符串列表项 → Literal"""
        return Literal(value=self._unquote(str(items[0])))

    def list_item_num(self, items):
        """数字列表项 → Literal"""
        return Literal(value=items[0])

    def list_item_var(self, items):
        """变量列表项 → VarRef"""
        return items[0]  # var_ref 已返回 VarRef

    def field_list(self, items):
        """.[f1, f2, ...] → list[Literal]"""
        return [Literal(value=str(t)) for t in items]

    # ─── 工具方法 ─────────────────────────────────────────

    @staticmethod
    def _line(items) -> int:
        """从子节点中提取行号"""
        for item in items:
            if isinstance(item, Token) and hasattr(item, 'line'):
                return item.line
            if hasattr(item, 'line_no') and item.line_no:
                return item.line_no
            if isinstance(item, Tree) and hasattr(item, 'meta') and item.meta:
                return getattr(item.meta, 'line', 0)
        return 0

    @staticmethod
    def _unquote(s: str) -> str:
        """去除字符串两端的双引号"""
        if s.startswith('"') and s.endswith('"'):
            return s[1:-1]
        return s

    @staticmethod
    def _ensure_literal(node) -> Literal:
        """确保返回 Literal（处理 STRING Token 未被子规则转换的情况）"""
        if isinstance(node, Literal):
            return node
        if isinstance(node, Token):
            s = str(node)
            if s.startswith('"') and s.endswith('"'):
                s = s[1:-1]
            return Literal(value=s)
        return node


# ─── 公共接口 ─────────────────────────────────────────────

def parse_file(path: Path | str) -> Program:
    """解析 .wf 文件，返回 Program AST 节点"""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    return parse_text(text, source=str(path))


def parse_text(text: str, source: str = "<text>") -> Program:
    """从字符串解析 DSL 文本，返回 Program AST 节点（主要用于测试）"""
    parser = _get_parser()
    # 确保文本以换行结尾（grammar 要求 _NL 终止）
    if not text.endswith("\n"):
        text += "\n"
    tree = parser.parse(text)
    program = _DSLTransformer().transform(tree)
    return Program(body=program.body, source=source)
