"""_ExprMixin：by 子句/match、内联 eval、算术、字典字面量、条件表达式、
keyword_ref 字段访问与通用原子回调"""

from lark import Token

from ..ast_nodes import (
    And,
    ArithOp,
    ByClause,
    Collect,
    Contains,
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
    VarRef,
)


class _ExprMixin:
    """by 子句、collect/log、eval 赋值全组、算术、字典、条件表达式、
    field_access/keyword_ref 链与通用原子回调"""

    # ─── by 子句（短路识别）───────────────────────────────

    def by_clause(self, items):
        """by <match_mode> <target> → ByClause"""
        match_mode = items[0]   # str: equals / contains / equals_any / contains_any
        target_node = items[1]  # VarRef | Token(STRING)
        # STRING token 须显式去引号包装为 Literal；VarRef 直接透传
        if isinstance(target_node, VarRef):
            target = target_node
        else:
            target = Literal(value=self._unquote(str(target_node)))
        return ByClause(match_mode=match_mode, target=target)

    def match_equals(self, _):
        return "equals"

    def match_contains(self, _):
        return "contains"

    def match_equals_any(self, _):
        return "equals_any"

    def match_contains_any(self, _):
        return "contains_any"

    def group_clause(self, items):
        """group "分组名" 或 group $var → Literal 或 VarRef"""
        target_node = items[0]  # Token(STRING) 或 VarRef
        if isinstance(target_node, VarRef):
            return target_node
        else:
            return Literal(value=self._unquote(str(target_node)))

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
        source = items[0]  # var_ref → VarRef, field_access → FieldAccess, literal → float/str/Token
        alias = None
        alias_var = None
        # 将字面量包装为 Literal AST 节点
        if isinstance(source, (float, int, str)) and not isinstance(source, (VarRef, FieldAccess)):
            # 处理字符串 Token（需要 unquote）
            if isinstance(source, Token) and source.type == 'STRING':
                source = Literal(value=self._unquote(str(source)))
            else:
                source = Literal(value=source)
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
        """eval $var = "string" | 123 | -1.5 | {} | {"k": v} | [list] | (min, max)"""
        tokens = [i for i in items if isinstance(i, Token)]
        target_name = str(tokens[0])  # $ 后面的 NAME
        lit_value = items[1]
        # 字典快捷路径（空字典和非空字典统一处理）
        if isinstance(lit_value, dict):
            return Eval(func_name="__dict__", func_args=[lit_value], target=target_name, line_no=self._line(items))
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

    def eval_assign_arith(self, items):
        """eval $var = arith_expr — 算术表达式赋值（含运算符或裸值）"""
        tokens = [i for i in items if isinstance(i, Token)]
        target_name = str(tokens[0])
        expr = items[1]
        # ArithOp → 算术运算
        if isinstance(expr, ArithOp):
            return Eval(func_name="__arith__", func_args=[expr], target=target_name, line_no=self._line(items))
        # float/int（来自 number）→ 字面量赋值
        if isinstance(expr, (int, float)):
            return Eval(func_name="__literal__", func_args=[Literal(value=expr)], target=target_name, line_no=self._line(items))
        # VarRef / FieldAccess / FuncCall → 表达式赋值
        return Eval(func_name="__expr__", func_args=[expr], target=target_name, line_no=self._line(items))

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
        """$var = "string" | 123 | {} | {"k": v} | [list] | (min, max) — 隐式字面量赋值"""
        tokens = [i for i in items if isinstance(i, Token)]
        target_name = str(tokens[0])
        lit_value = items[1]
        if isinstance(lit_value, dict):
            return Eval(func_name="__dict__", func_args=[lit_value], target=target_name, line_no=self._line(items))
        if isinstance(lit_value, list):
            return Eval(func_name="__list__", func_args=lit_value, target=target_name, line_no=self._line(items))
        if isinstance(lit_value, tuple):
            return Eval(func_name="__range__", func_args=[Literal(value=lit_value)], target=target_name, line_no=self._line(items))
        if isinstance(lit_value, Token):
            lit_value = self._unquote(str(lit_value))
        return Eval(func_name="__literal__", func_args=[Literal(value=lit_value)], target=target_name, line_no=self._line(items))

    def implicit_eval_assign_arith(self, items):
        """$var = arith_expr — 隐式算术表达式赋值"""
        tokens = [i for i in items if isinstance(i, Token)]
        target_name = str(tokens[0])
        expr = items[1]
        if isinstance(expr, ArithOp):
            return Eval(func_name="__arith__", func_args=[expr], target=target_name, line_no=self._line(items))
        if isinstance(expr, (int, float)):
            return Eval(func_name="__literal__", func_args=[Literal(value=expr)], target=target_name, line_no=self._line(items))
        return Eval(func_name="__expr__", func_args=[expr], target=target_name, line_no=self._line(items))

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
        """eval_rhs: literal → Literal | dict | list"""
        val = items[0]
        if isinstance(val, dict):
            return val  # 字典（空或非空）
        if isinstance(val, Token):
            return Literal(value=self._unquote(str(val)))
        return val  # number (float)

    def eval_rhs_field(self, items):
        """eval_rhs: field_access → FieldAccess"""
        return items[0]

    def eval_rhs_arith(self, items):
        """eval_rhs: arith_expr → 透传算术表达式节点"""
        return items[0]

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

    def func_call(self, items):
        """func_name(arg_list?) → FuncCall"""
        tokens = [i for i in items if isinstance(i, Token)]
        func_name = str(tokens[0])
        lists = [i for i in items if isinstance(i, list)]
        func_args = lists[0] if lists else []
        return FuncCall(func_name=func_name, func_args=func_args, line_no=self._line(items))

    def empty_dict(self, items):
        """{} → 空字典（兼容旧规则，已由 dict_literal 替代）"""
        return {}

    # ─── 字典字面量 ─────────────────────────────────────

    def dict_literal(self, items):
        """{"k": v, ...} → dict[str, AST节点]"""
        result = {}
        for pair in items:
            if isinstance(pair, tuple):
                result[pair[0]] = pair[1]
        return result

    def dict_pair(self, items):
        """STRING ":" dict_value → (key_str, value_node)"""
        key = self._unquote(str(items[0]))
        value = items[1]
        return (key, value)

    def dict_val_str(self, items):
        """字典值：字符串 → Literal"""
        return Literal(value=self._unquote(str(items[0])))

    def dict_val_num(self, items):
        """字典值：数字 → Literal"""
        return Literal(value=items[0])

    def dict_val_var(self, items):
        """字典值：变量引用 → VarRef"""
        return items[0]  # var_ref 已返回 VarRef

    def dict_val_dict(self, items):
        """字典值：嵌套字典 → dict[str, AST节点]"""
        return items[0]  # dict_literal 已返回 dict

    def dict_val_list(self, items):
        """字典值：列表 → list[AST节点]"""
        return items[0]  # list_literal 已返回 list

    def arg_list(self, items):
        return list(items)

    def arg_lit(self, items):
        return Literal(value=self._unquote(str(items[0])))

    def arg_num(self, items):
        """number 作为函数参数 → float"""
        return items[0]  # number 已返回 float

    def arg_var(self, items):
        """var_ref 作为函数参数 → VarRef"""
        return items[0]  # var_ref 已返回 VarRef

    def arg_field(self, items):
        """field_access 作为函数参数 → FieldAccess"""
        return items[0]  # field_access 已返回 FieldAccess

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

    def list_item_dict(self, items):
        """列表项：嵌套字典 → dict[str, AST节点]"""
        return items[0]  # dict_literal 已返回 dict

    def list_item_list(self, items):
        """列表项：嵌套列表 → list[AST节点]"""
        return items[0]  # list_literal 已返回 list

    def field_list(self, items):
        """.[f1, f2, ...] → list[Literal]"""
        return [Literal(value=str(t)) for t in items]
