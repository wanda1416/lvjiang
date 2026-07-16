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
    Click, Wait, Scan, ScanAs, ClickMatch, Collect, CollectAs, Log,
    If, For, Loop, Break, Label, Goto, Eval,
    VarRef, Literal, FieldAccess, Contains, Equals, InList, IsEmpty,
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
        scene, field = items
        return Click(scene=scene, field=field, line_no=self._line(items))

    def wait_stmt(self, items):
        arg = items[0]
        if isinstance(arg, Token):
            if arg.type == "FLOAT":
                return Wait(delay=Literal(value=float(arg)), line_no=self._line(items))
            else:  # NAME
                return Wait(delay=Literal(value=str(arg)), line_no=self._line(items))
        return Wait(delay=arg, line_no=self._line(items))

    def scan_stmt(self, items):
        scene = items[0]
        fields = None
        target = None
        for item in items[1:]:
            if isinstance(item, list) and item and isinstance(item[0], Literal):
                fields = item  # field_list 返回的是 list[Literal]
            elif isinstance(item, VarRef):
                target = item  # as_clause 返回 VarRef
        if target is not None:
            return ScanAs(scene=scene, fields=fields, target=target, line_no=self._line(items))
        return Scan(scene=scene, fields=fields, line_no=self._line(items))

    def as_clause(self, items):
        """as [var] → VarRef"""
        return items[0]  # bracket_expr 已转为 VarRef（在条件上下文中）或 Literal

    def click_match_stmt(self, items):
        text = self._ensure_literal(items[0])
        error_msg = self._ensure_literal(items[1]) if len(items) > 1 else None
        return ClickMatch(text=text, error_msg=error_msg, line_no=self._line(items))

    def error_clause(self, items):
        return self._ensure_literal(items[0])

    def collect_stmt(self, items):
        return Collect(line_no=self._line(items))

    def collect_as_stmt(self, items):
        return CollectAs(key=items[0], line_no=self._line(items))

    def log_stmt(self, items):
        return Log(message=self._ensure_literal(items[0]), line_no=self._line(items))

    def eval_stmt(self, items):
        """eval [var =] func(args...)"""
        tokens = [i for i in items if isinstance(i, Token)]
        names = [str(t) for t in tokens]
        args = [i for i in items if isinstance(i, list)]
        func_args = args[0] if args else []

        # 判断是否有赋值目标：如果有两个 NAME token，第一个是目标变量
        if len(names) == 2:
            return Eval(func_name=names[1], func_args=func_args, target=names[0], line_no=self._line(items))
        else:
            return Eval(func_name=names[0], func_args=func_args, target=None, line_no=self._line(items))

    def arg_list(self, items):
        return list(items)

    def arg_lit(self, items):
        return Literal(value=self._unquote(str(items[0])))

    def arg_var(self, items):
        return VarRef(name=str(items[0]))

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
        count_token = items[0]
        if isinstance(count_token, Token):
            count = int(str(count_token))
        else:
            # NAME → 运行时从变量解析，暂存为字符串
            count = str(count_token)
        body = [i for i in items[1:] if i is not None and not isinstance(i, Token)]
        return Loop(count=count, body=body, line_no=self._line(items))

    def break_stmt(self, items):
        return Break(line_no=self._line(items))

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
        """条件中的 [var] → VarRef（truthy 检查）"""
        return items[0]

    def field_access(self, items):
        var_ref, field_name = items
        return FieldAccess(var=var_ref, field_name=str(field_name))

    # ─── 通用原子 ─────────────────────────────────────────

    def bracket_expr(self, items):
        """[name] — 在条件中为 VarRef，在 click/scan 中由父节点转为 Literal"""
        name = str(items[0])
        return VarRef(name=name)

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


# ─── 上下文后处理 ─────────────────────────────────────────

class _ContextPostProcessor:
    """将 VarRef 根据上下文转为 Literal

    bracket_expr 在语法层面统一返回 VarRef，
    但在 click/scan/wait 等指令的参数位置应为 Literal（字面量 scene/field 名）。
    此处理器遍历 AST，根据父节点类型决定子节点的语义。
    """

    @staticmethod
    def process(program: Program) -> Program:
        new_body = [_ContextPostProcessor._process_stmt(s) for s in program.body]
        return Program(body=new_body, source=program.source)

    @staticmethod
    def _process_stmt(node):
        """后处理：仅处理需要字面量的位置

        click/scan 中的 [name] 保持 VarRef，由引擎运行时解析（变量优先，回退字面量）。
        仅 bare NAME（Token）在 wait/collect_as 等位置需转为 Literal。
        """
        if isinstance(node, If):
            return If(
                condition=node.condition,
                then_body=[_ContextPostProcessor._process_stmt(s) for s in node.then_body],
                else_body=[_ContextPostProcessor._process_stmt(s) for s in node.else_body],
                line_no=node.line_no,
            )
        if isinstance(node, For):
            return For(
                var=node.var,
                iterable=node.iterable,
                body=[_ContextPostProcessor._process_stmt(s) for s in node.body],
                line_no=node.line_no,
            )
        if isinstance(node, Loop):
            return Loop(
                count=node.count,
                body=[_ContextPostProcessor._process_stmt(s) for s in node.body],
                line_no=node.line_no,
            )
        # bare NAME Token → Literal（wait/collect_as 等位置的裸标识符）
        if isinstance(node, Wait):
            delay = node.delay
            if isinstance(delay, Token):
                delay = Literal(value=str(delay))
            return Wait(delay=delay, line_no=node.line_no)
        if isinstance(node, CollectAs):
            key = node.key
            if isinstance(key, Token):
                key = Literal(value=str(key))
            return CollectAs(key=key, line_no=node.line_no)
        return node

    @staticmethod
    def _to_literal(node) -> Literal:
        """VarRef/Token → Literal（参数位置的 [name] 或裸 name 视为字面量）"""
        if isinstance(node, VarRef):
            return Literal(value=node.name)
        if isinstance(node, Token):
            return Literal(value=str(node))
        return node

    @staticmethod
    def _list_to_literal(items) -> list | None:
        if items is None:
            return None
        return [_ContextPostProcessor._to_literal(i) for i in items]


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
    # 上下文后处理：参数位置的 VarRef → Literal
    program = _ContextPostProcessor.process(Program(body=program.body, source=source))
    return program
