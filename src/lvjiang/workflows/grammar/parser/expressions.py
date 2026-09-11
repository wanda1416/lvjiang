"""_ExprMixin：by 子句/match、内联 eval、算术、字典字面量、条件表达式、
keyword_ref 字段访问与通用原子回调"""

from lark import Token

from ..ast_nodes import (
    And,
    ArithOp,
    ByClause,
    Collect,
    Contains,
    EntityRef,
    Equals,
    Eval,
    EvalFieldChainAssign,
    FieldAccess,
    FuncCall,
    GreaterEqual,
    GreaterThan,
    InList,
    IsEmpty,
    KeywordRef,
    LessEqual,
    LessThan,
    Literal,
    Log,
    Not,
    NotEqual,
    NumericEqual,
    Or,
    Screenshot,
    TupleLiteral,
    VarRef,
    WhereClause,
)


class _ExprMixin:
    """by 子句、collect/log、eval 赋值全组、算术、字典、条件表达式、
    field_access/keyword_ref 链与通用原子回调"""

    # ─── null / bool 字面量 ────────────────────────────────

    # ─── by 子句（短路 / 全量识别）───────────────────────────────

    def by_clause(self, items):
        """[full] by <match_mode> <target> → ByClause"""
        # FULL_KEYWORD 是命名终端，如果存在会作为 Token 出现在 items[0]
        full = False
        if items and isinstance(items[0], Token) and items[0].type == "FULL_KEYWORD":
            full = True
            items = items[1:]  # 剥离 FULL_KEYWORD，剩余为 match_mode + by_target
        match_mode = items[0]   # str: equals / contains / equals_any / contains_any
        target_node = items[1]  # VarRef | Token(STRING)
        # STRING token 须显式去引号包装为 Literal；VarRef 直接透传
        if isinstance(target_node, VarRef):
            target = target_node
        else:
            target = Literal(value=self._unquote(str(target_node)))
        return ByClause(match_mode=match_mode, target=target, full=full)

    def by_image_clause(self, items):
        """by image [模板名/Region 引用]：scan 省略目标。"""
        if not items:
            return ByClause(match_mode="image")
        target_node = items[0]
        target = (target_node if isinstance(target_node, (VarRef, EntityRef))
                  else Literal(value=self._unquote(str(target_node))))
        return ByClause(match_mode="image", target=target)

    def image_region_ref(self, items):
        """[scene].[region] → 只作为模板绑定来源，不解析其坐标。"""
        return EntityRef(scene=str(items[0]), entity=str(items[1]))

    def match_equals(self, _):
        return "equals"

    def match_contains(self, _):
        return "contains"

    def match_equals_any(self, _):
        return "equals_any"

    def match_contains_any(self, _):
        return "contains_any"


    def group_clause(self, items):
        """on group "分组名" / $var / ["a", "b"] → Literal | VarRef | list"""
        target_node = items[0]  # Token(STRING) | VarRef | list（list_literal）
        if isinstance(target_node, VarRef):
            return target_node
        elif isinstance(target_node, list):
            return target_node  # list_literal 已返回 Python list
        else:
            return Literal(value=self._unquote(str(target_node)))

    def where_clause(self, items):
        """where confidence >= <threshold> → WhereClause

        threshold 支持数字字面量和变量引用。
        """
        threshold = items[0]  # float（number 规则返回）| VarRef
        if isinstance(threshold, VarRef):
            min_conf = threshold
        else:
            min_conf = Literal(value=float(threshold))
        return WhereClause(min_confidence=min_conf)

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
        """解析场景名：bracket_expr→str, var_ref→VarRef"""
        if isinstance(item, VarRef):
            return item  # 动态场景名
        return str(item)  # bracket_expr

    def collect_stmt(self, items):
        source = self._normalize_expr(items[0])
        alias = None
        alias_var = None
        if len(items) > 1:
            # collect_as_clause 返回 str 或 VarRef
            alias_item = items[1]
            if isinstance(alias_item, VarRef):
                alias_var = alias_item  # 动态 alias
            elif isinstance(alias_item, Token):
                alias = self._unquote(str(alias_item))
            elif isinstance(alias_item, str):
                alias = alias_item
        return Collect(source=source, alias=alias, alias_var=alias_var, line_no=self._line(items))

    def collect_as_clause(self, items):
        """as "label" 或 as $var → 字符串或 VarRef"""
        item = items[0]
        if isinstance(item, VarRef):
            return item  # 动态 alias
        return self._unquote(str(item))  # 静态 alias

    def log_stmt(self, items):
        # 可选的 LOG_LEVEL 终端
        level = "info"
        arg_idx = 0
        if len(items) == 2:
            level = str(items[0]).lower()
            arg_idx = 1
        arg = items[arg_idx]
        # log 参数可以是：字符串常量、函数调用、变量引用、字段访问
        if isinstance(arg, FuncCall):
            return Log(message=arg, level=level, line_no=self._line(items))
        if isinstance(arg, VarRef):
            return Log(message=arg, level=level, line_no=self._line(items))
        if isinstance(arg, FieldAccess):
            return Log(message=arg, level=level, line_no=self._line(items))
        return Log(message=self._ensure_literal(arg), level=level, line_no=self._line(items))

    def log_arg(self, items):
        """log_arg: 透传任何表达式"""
        return items[0]

    def screenshot_stmt(self, items):
        """screenshot: 截取当前画面并保存"""
        return Screenshot(line_no=self._line(items))

    def eval_discard_expr(self, items):
        """eval func(...) — 对统一表达式中的函数调用求值并丢弃结果。"""
        call = items[0]
        return Eval(
            func_name=call.func_name,
            func_args=call.func_args,
            target=None,
            line_no=self._line(items),
        )

    def eval_assign_expr(self, items):
        """eval $var = expression — 统一赋值入口。"""
        tokens = [i for i in items if isinstance(i, Token)]
        target_name = str(tokens[0])
        return self._build_assignment(target_name, items[1], items)

    def eval_field_assign(self, items):
        """eval $dict.key = value 或 eval $dict.key1.key2 = value — 字段赋值"""
        # items: field_access, expression
        target = items[0]  # FieldAccess
        value = items[1]
        return EvalFieldChainAssign(target=target, value=value, line_no=self._line(items))

    # ─── 隐式 eval（省略 eval 关键字）─────────────────────

    def implicit_eval_assign_expr(self, items):
        """$var = expression — 与显式 eval 共用同一 AST 构造。"""
        tokens = [i for i in items if isinstance(i, Token)]
        target_name = str(tokens[0])
        return self._build_assignment(target_name, items[1], items)

    def _build_assignment(self, target_name, expr, items):
        """从统一 expression 构造兼容的 Eval AST。"""
        expr = self._normalize_expr(expr)
        line_no = self._line(items)
        if isinstance(expr, FuncCall):
            return Eval(
                func_name=expr.func_name, func_args=expr.func_args,
                target=target_name, line_no=line_no)
        if isinstance(expr, dict):
            return Eval(
                func_name="__dict__", func_args=[expr],
                target=target_name, line_no=line_no)
        if isinstance(expr, list):
            return Eval(
                func_name="__list__", func_args=expr,
                target=target_name, line_no=line_no)
        if isinstance(expr, TupleLiteral):
            return Eval(
                func_name="__tuple__", func_args=expr.elements,
                target=target_name, line_no=line_no)
        if isinstance(expr, ArithOp):
            return Eval(
                func_name="__arith__", func_args=[expr],
                target=target_name, line_no=line_no)
        if isinstance(expr, Literal):
            return Eval(
                func_name="__literal__", func_args=[expr],
                target=target_name, line_no=line_no)
        return Eval(
            func_name="__expr__", func_args=[expr],
            target=target_name, line_no=line_no)

    def implicit_eval_field_assign(self, items):
        """$dict.key = value — 隐式字段赋值"""
        target = items[0]
        value = items[1]
        return EvalFieldChainAssign(target=target, value=value, line_no=self._line(items))

    def default_stmt(self, items):
        """default $var = expression — 仅当变量未设置时求值。"""
        tokens = [i for i in items if isinstance(i, Token)]
        target_name = str(tokens[0])
        expr = self._normalize_expr(items[1])
        return Eval(
            func_name="__default__", func_args=[expr],
            target=target_name, line_no=self._line(items))

    # ─── 算术表达式 ─────────────────────────────────────

    def arith_add(self, items):
        """arith_expr "+" term → ArithOp(+)
        left/right 可能是 float（number 直出）、VarRef、FieldAccess、ArithOp 等"""
        return ArithOp(op="+", left=items[0], right=items[1], line_no=self._line(items))

    def arith_sub(self, items):
        """arith_expr "-" term → ArithOp(-)"""
        return ArithOp(op="-", left=items[0], right=items[1], line_no=self._line(items))

    def arith_mul(self, items):
        """term "*" factor → ArithOp(*)"""
        return ArithOp(op="*", left=items[0], right=items[1], line_no=self._line(items))

    def arith_div(self, items):
        """term "/" factor → ArithOp(/)"""
        return ArithOp(op="/", left=items[0], right=items[1], line_no=self._line(items))

    def string_atom(self, items):
        """STRING → Literal（算术表达式中的字符串字面量，去引号）"""
        return Literal(value=self._unquote(str(items[0])))

    def null_atom(self, _items):
        return Literal(value=None)

    def true_atom(self, _items):
        return Literal(value=True)

    def false_atom(self, _items):
        return Literal(value=False)

    def func_call(self, items):
        """func_name(arg_list?) → FuncCall"""
        tokens = [i for i in items if isinstance(i, Token)]
        func_name = str(tokens[0])
        lists = [i for i in items if isinstance(i, list)]
        func_args = lists[0] if lists else []
        return FuncCall(func_name=func_name, func_args=func_args, line_no=self._line(items))

    # ─── 字典字面量 ─────────────────────────────────────

    def dict_literal(self, items):
        """{"k": v, ...} → dict[str, AST节点]"""
        result = {}
        for pair in items:
            if isinstance(pair, tuple):
                result[pair[0]] = pair[1]
        return result

    def dict_pair(self, items):
        """STRING ":" expression → (key_str, expression_node)"""
        key = self._unquote(str(items[0]))
        value = self._normalize_expr(items[1])
        return (key, value)

    def arg_list(self, items):
        return [self._normalize_expr(item) for item in items]

    # ─── 条件表达式 ───────────────────────────────────────

    def or_op(self, items):
        return Or(left=items[0], right=items[1], line_no=self._line(items))

    def and_op(self, items):
        return And(left=items[0], right=items[1], line_no=self._line(items))

    def not_op(self, items):
        return Not(operand=items[0], line_no=self._line(items))

    def contains_op(self, items):
        left, right = items
        return Contains(left=left, right=right, line_no=self._line(items))

    def equals_op(self, items):
        left, right = items
        return Equals(left=left, right=right, line_no=self._line(items))

    def in_op(self, items):
        field_access, list_literal = items
        return InList(left=field_access, right=list_literal, line_no=self._line(items))

    def is_empty_op(self, items):
        return IsEmpty(expr=items[0], line_no=self._line(items))

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

    # ─── keyword_ref 字段访问（session / context） ──────────────

    def session_ref(self, items):
        """session → KeywordRef('session')"""
        return KeywordRef(name="session")

    def context_ref(self, items):
        """context → KeywordRef('context')"""
        return KeywordRef(name="context")

    def kw_field_base(self, items):
        """session.field → FieldAccess(root=KeywordRef, field_name=str)"""
        kw_ref, field_name = items
        return FieldAccess(root=kw_ref, field_name=str(field_name))

    def kw_field_var_base(self, items):
        """session.$key → FieldAccess(root=KeywordRef, field_name=VarRef)"""
        kw_ref, key_ref = items
        return FieldAccess(root=kw_ref, field_name=key_ref)

    def kw_field_str_base(self, items):
        """session."key" → FieldAccess(root=KeywordRef, field_name=Literal)"""
        kw_ref, string_token = items
        return FieldAccess(root=kw_ref, field_name=Literal(value=self._unquote(str(string_token))))

    def kw_field_bracket_base(self, items):
        """session.[key] → FieldAccess(root=KeywordRef, field_name=Literal)"""
        kw_ref, name_token = items
        return FieldAccess(root=kw_ref, field_name=Literal(value=str(name_token)))

    def field_kw_chain(self, items):
        """field_access.session → FieldAccess(root=FieldAccess, field_name=KeywordRef)"""
        prev_access, kw_ref = items
        return FieldAccess(root=prev_access, field_name=kw_ref)

    def gt_op(self, items):
        left, right = items
        return GreaterThan(left=left, right=right, line_no=self._line(items))

    def lt_op(self, items):
        left, right = items
        return LessThan(left=left, right=right, line_no=self._line(items))

    def ge_op(self, items):
        left, right = items
        return GreaterEqual(left=left, right=right, line_no=self._line(items))

    def le_op(self, items):
        left, right = items
        return LessEqual(left=left, right=right, line_no=self._line(items))

    def ne_op(self, items):
        left, right = items
        return NotEqual(left=left, right=right, line_no=self._line(items))

    def eq_num_op(self, items):
        left, right = items
        return NumericEqual(left=left, right=right, line_no=self._line(items))

    def number_float(self, items):
        return float(items[0])

    def number_int(self, items):
        return int(items[0])

    def number_neg_float(self, items):
        return -float(items[0])

    def number_neg_int(self, items):
        return -int(items[0])

    # ─── 通用原子 ─────────────────────────────────────────

    def var_ref(self, items):
        """$name → VarRef"""
        return VarRef(name=str(items[0]))

    def entity_ref(self, items):
        """[scene].[entity] → EntityRef（表达式上下文，用于赋值与算术运算）"""
        return EntityRef(scene=str(items[0]), entity=str(items[1]))

    def subscene_entity_ref(self, items):
        from ..ast_nodes import SubsceneEntityRef
        return SubsceneEntityRef(
            scene=str(items[0]), reference=str(items[1]), entity=str(items[2]))

    def bracket_expr(self, items):
        """[name] → str（场景名或实体名，由父节点组装为 EntityRef）"""
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
        """[item1, item2, ...] → list[Literal | VarRef]

        空列表 ``[]`` 时 lark 会为可选的 list_item 组填一个占位 ``None``，
        必须一并滤掉——否则 ``[]`` 求值成 ``[None]``（长度 1 的真值列表），
        「空列表」判空、for 迭代、传参全部走偏。null 列表项本身是
        ``Literal(value=None)``，不会被这条误伤。
        """
        return [self._normalize_expr(item) for item in items if item is not None]

    def field_list(self, items):
        """.[f1, f2, ...] → list[Literal]"""
        return [Literal(value=str(t)) for t in items]
