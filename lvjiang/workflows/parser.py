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
    Click, Drag, Wait, Scan, Recognize, Find, Collect, Log, Call,
    If, For, Loop, Break, Return, Label, Goto, Eval, EvalFieldAssign, FuncCall,
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

    def click_stmt(self, items):
        target = items[0]  # click_target → SceneRef | VarRef
        return Click(target=target, line_no=self._line(items))

    def click_target(self, items):
        """click 目标：[scene].[region] → SceneRef，[scene].[$var] → SceneRef(region=VarRef)，$var → VarRef"""
        if len(items) == 2:
            scene_name = str(items[0])
            region = items[1]
            # region 可能是 bracket_expr (Token) 或 var_ref (VarRef)
            if isinstance(region, VarRef):
                return SceneRef(scene=scene_name, region=region)
            else:
                return SceneRef(scene=scene_name, region=str(region))
        else:
            # $var（var_ref 已返回 VarRef）
            return items[0]

    def drag_stmt(self, items):
        scene_ref = SceneRef(scene=str(items[0]), region=str(items[1]))
        arrow_ref = SceneRef(scene=str(items[0]), region=str(items[1]))  # 同场景
        # 重新解析：items[0]=scene bracket, items[1]=region bracket
        scene_name = str(items[0])
        arrow_name = str(items[1])
        scene_ref = SceneRef(scene=scene_name, region=arrow_name)
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
        if isinstance(arg, Token):
            if arg.type in ("FLOAT", "INT"):
                return Wait(delay=Literal(value=float(arg)), line_no=self._line(items))
            else:  # NAME
                return Wait(delay=Literal(value=str(arg)), line_no=self._line(items))
        # bracket_expr 返回 str → 视为命名延迟
        if isinstance(arg, str):
            return Wait(delay=Literal(value=arg), line_no=self._line(items))
        return Wait(delay=arg, line_no=self._line(items))

    def scan_stmt(self, items):
        scene_name = str(items[0])  # bracket_expr → str
        scene = SceneRef(scene=scene_name)
        fields = None
        target = None
        for item in items[1:]:
            if isinstance(item, list) and item and isinstance(item[0], Literal):
                fields = item  # field_list → list[Literal]
                scene = SceneRef(scene=scene_name)  # 保持 scene
            elif isinstance(item, VarRef):
                target = item  # var_ref → VarRef
        return Scan(scene=scene, fields=fields, target=target, line_no=self._line(items))

    def recognize_stmt(self, items):
        """recognize [scene].[f1, f2, ...] as $var"""
        scene_name = str(items[0])  # bracket_expr → str
        scene = SceneRef(scene=scene_name)
        fields = None
        target = None
        for item in items[1:]:
            if isinstance(item, list) and item and isinstance(item[0], Literal):
                fields = item  # field_list → list[Literal]
            elif isinstance(item, VarRef):
                target = item  # var_ref → VarRef
        return Recognize(scene=scene, fields=fields, target=target, line_no=self._line(items))

    def find_stmt(self, items):
        """find $source ("text" | $var) as $target [error "msg"]"""
        source = items[0]   # var_ref → VarRef
        # text 可以是字面量（STRING Token 或 Literal）或变量引用
        raw_text = items[1]
        if isinstance(raw_text, VarRef):
            text = raw_text
        else:
            text = self._ensure_literal(raw_text)  # STRING Token → Literal
        target = None
        error_msg = None
        for item in items[2:]:
            if isinstance(item, VarRef) and target is None:
                target = item
            elif isinstance(item, Literal):
                error_msg = item
        if target is None:
            raise ValueError(f"find 语句缺少目标变量: {items}")
        return Find(source=source, text=text, target=target, error_msg=error_msg, line_no=self._line(items))

    def error_clause(self, items):
        return self._ensure_literal(items[0])

    def collect_stmt(self, items):
        source = items[0]  # var_ref → VarRef
        alias = None
        if len(items) > 1:
            # collect_as_clause 返回字符串（alias 标签）
            alias_item = items[1]
            if isinstance(alias_item, str):
                alias = alias_item
            elif isinstance(alias_item, Token):
                alias = self._unquote(str(alias_item))
        return Collect(source=source, alias=alias, line_no=self._line(items))

    def collect_as_clause(self, items):
        """as "label" → 字符串"""
        return self._unquote(str(items[0]))

    def log_stmt(self, items):
        arg = items[0]
        if isinstance(arg, FuncCall):
            return Log(message=arg, line_no=self._line(items))
        return Log(message=self._ensure_literal(arg), line_no=self._line(items))

    def eval_assign_func(self, items):
        """eval $var = func($arg...)"""
        tokens = [i for i in items if isinstance(i, Token)]
        names = [str(t) for t in tokens]
        lists = [i for i in items if isinstance(i, list)]
        func_args = lists[0] if lists else []
        # names[0] = 赋值目标变量名, names[1] = 函数名
        return Eval(func_name=names[1], func_args=func_args, target=names[0], line_no=self._line(items))

    def eval_assign_lit(self, items):
        """eval $var = "string" | 123 | -1.5 | {}"""
        tokens = [i for i in items if isinstance(i, Token)]
        target_name = str(tokens[0])  # $ 后面的 NAME
        lit_value = items[1]
        # 空字典快捷路径
        if isinstance(lit_value, dict):
            return Eval(func_name="__empty_dict__", func_args=[], target=target_name, line_no=self._line(items))
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

    def eval_field_assign(self, items):
        """eval $dict.key = value"""
        tokens = [i for i in items if isinstance(i, Token)]
        names = [str(t) for t in tokens]
        # names[0] = 变量名, names[1] = 字段名
        var_name = names[0]
        field_name = names[1]
        # items 中最后一个非 Token 元素是 eval_rhs 的结果
        value = None
        for item in reversed(items):
            if not isinstance(item, Token):
                value = item
                break
        return EvalFieldAssign(var_name=var_name, field_name=field_name, value=value, line_no=self._line(items))

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
            if isinstance(item, list):
                for sub in item:
                    if isinstance(sub, tuple) and len(sub) == 2:
                        if isinstance(sub[0], VarRef):
                            args.append(sub)
                        else:
                            reads.append(sub)
        return Call(workflow=wf_path, args=args, reads=reads, line_no=self._line(items))

    def call_with_clause(self, items):
        """with $x as arg1, $y as arg2 → [(VarRef, 'arg1'), (VarRef, 'arg2')]"""
        return items  # 每个 item 是 call_arg 返回的 tuple

    def call_arg(self, items):
        """$x as arg1 → (VarRef, 'arg1')"""
        return (items[0], str(items[1]))

    def call_read_clause(self, items):
        """read "key" as $var → [('key', VarRef)]"""
        return items  # 每个 item 是 call_read_item 返回的 tuple

    def call_read_item(self, items):
        """"key" as $var → (Literal, VarRef)"""
        return (self._ensure_literal(items[0]), items[1])

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
        iterable = items[1]  # bracket_list → list[Literal]
        body = [i for i in items[2:] if i is not None and not isinstance(i, Token)]
        return For(var=var_name, iterable=iterable, body=body, line_no=self._line(items))

    def loop_stmt(self, items):
        count_val = items[0]
        if isinstance(count_val, (int, float)):
            count = int(count_val)  # number 规则产出 float，loop 需要 int
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
        field_access, string_token = items
        return Contains(left=field_access, right=Literal(value=self._unquote(str(string_token))), line_no=self._line(items))

    def equals_op(self, items):
        field_access, string_token = items
        return Equals(left=field_access, right=Literal(value=self._unquote(str(string_token))), line_no=self._line(items))

    def in_op(self, items):
        field_access, bracket_list = items
        return InList(left=field_access, right=bracket_list, line_no=self._line(items))

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

    def bracket_var(self, items):
        """[$var] → VarRef（动态引用，由父节点组装为 SceneRef(region=VarRef)）"""
        return items[0]  # var_ref 已返回 VarRef

    def bracket_list(self, items):
        """[a, b, "c"] → list[Literal]"""
        result = []
        for item in items:
            s = str(item)
            if s.startswith('"') and s.endswith('"'):
                s = self._unquote(s)
            result.append(Literal(value=s))
        return result

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
