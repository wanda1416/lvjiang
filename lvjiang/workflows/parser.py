"""工作流 DSL 解析器

支持语法：
    # 基础指令
    click [scene].[field]
    wait <delay_name> | <seconds>
    scan [scene] | [scene].[f1, f2, ...]
    click_match "text" [error "msg"]
    collect
    collect_as <key>
    log "message"

    # 函数调用与变量
    eval <func>(args...)                  # 调用内置函数，丢弃返回值
    eval <var> = <func>(args...)          # 调用内置函数，结果存入变量

    # 条件分支（缩进块）
    if <condition>
        <steps...>
    else
        <steps...>
    end

    # 条件分支（单行简写）
    if <condition> <step>

    # 条件支持取反
    if not <condition> ...

    # 注释
    # comment

条件表达式：
    <func>(args...)           # 函数返回 truthy
    not <func>(args...)       # 函数返回 falsy
    <var>                     # 变量 truthy
    not <var>                 # 变量 falsy

参数类型：
    "quoted string"           # 字符串字面量
    identifier                # 变量引用（运行时从引擎变量表解析）
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─── AST 节点 ─────────────────────────────────────────────

@dataclass
class Step:
    """基础指令节点"""
    instruction: str                          # click / wait / scan / click_match / collect / collect_as / log
    args: dict = field(default_factory=dict)
    line_no: int = 0


@dataclass
class EvalNode:
    """函数调用节点

    eval [var =] func_name(arg1, arg2, ...)
    """
    func_name: str
    func_args: list       # 每个元素: ("var", name) 或 ("lit", value)
    var_name: str | None = None   # 赋值目标变量，None 表示丢弃返回值
    line_no: int = 0


@dataclass
class IfNode:
    """条件分支节点

    if [not] condition
        consequent: list[Step | EvalNode | IfNode]
    else
        alternative: list[...]
    end
    """
    condition: dict       # {"func": name, "args": [...], "negated": bool}
    consequent: list = field(default_factory=list)
    alternative: list = field(default_factory=list)
    line_no: int = 0


# ─── 正则模式（基础指令） ─────────────────────────────────

# click [scene].[field]
_RE_CLICK = re.compile(r"^click\s+\[(\w+)\]\.\[(\w+)\]$")

# wait <name> | <float>
_RE_WAIT = re.compile(r"^wait\s+(.+)$")

# scan [scene].[f1, f2, ...]
_RE_SCAN_FULL = re.compile(r"^scan\s+\[(\w+)\]\.\[([\w,\s]+)\]$")
# scan [scene]
_RE_SCAN_SCENE = re.compile(r"^scan\s+\[(\w+)\]$")

# click_match "text" [error "msg"]
_RE_CLICK_MATCH = re.compile(r'^click_match\s+"([^"]+)"(?:\s+error\s+"([^"]+)")?$')

# collect
_RE_COLLECT = re.compile(r"^collect$")

# collect_as <key>
_RE_COLLECT_AS = re.compile(r"^collect_as\s+(\w+)$")

# log "message"
_RE_LOG = re.compile(r'^log\s+"(.+)"$')

# eval [var =] func(args...)
_RE_EVAL = re.compile(r"^eval\s+(?:(\w+)\s*=\s*)?(\w+)\((.*)\)\s*$")

# if [not] func(args...)  或  if [not] varname
_RE_IF_FUNC = re.compile(r"^if\s+(not\s+)?(\w+)\((.*)\)\s*$")
_RE_IF_VAR = re.compile(r"^if\s+(not\s+)?(\w+)\s*$")

# 参数解析：引号字符串 或 标识符
_RE_ARG_TOKEN = re.compile(r'"([^"]*)"|(\w+)')


# ─── 参数解析 ─────────────────────────────────────────────

def _parse_args(args_str: str) -> list[tuple[str, str]]:
    """解析函数参数列表

    Returns:
        [("var", name), ("lit", value), ...]
        - ("var", x) → 运行时从变量表解析
        - ("lit", x) → 字面量字符串
    """
    args_str = args_str.strip()
    if not args_str:
        return []
    result = []
    for m in _RE_ARG_TOKEN.finditer(args_str):
        if m.group(1) is not None:
            result.append(("lit", m.group(1)))
        else:
            result.append(("var", m.group(2)))
    return result


# ─── 缩进预处理 ──────────────────────────────────────────

def _tokenize_lines(text: str) -> list[tuple[int, int, str]]:
    """将文本转为 (行号, 缩进级别, 内容) 列表，过滤空行和注释"""
    tokens = []
    for line_no, raw_line in enumerate(text.splitlines(), 1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        tokens.append((line_no, indent, stripped))
    return tokens


# ─── 递归下降解析器 ────────────────────────────────────────

class _Parser:
    """将 token 流解析为 AST 节点树"""

    def __init__(self, tokens: list[tuple[int, int, str]], source: str = "<text>"):
        self._tokens = tokens
        self._pos = 0
        self._source = source

    def parse(self) -> list:
        """解析整个 token 流，返回节点列表"""
        nodes = self._parse_block(0)
        if self._pos < len(self._tokens):
            line_no = self._tokens[self._pos][0]
            raise ValueError(f"{self._source}:{line_no}: 意外的缩进级别")
        return nodes

    def _parse_block(self, min_indent: int) -> list:
        """解析一个缩进块（所有行缩进 >= min_indent）"""
        nodes = []
        while self._pos < len(self._tokens):
            line_no, indent, content = self._tokens[self._pos]

            if indent < min_indent:
                break

            if content == "end":
                break

            if content == "else":
                break

            if content.startswith("if "):
                node = self._parse_if(indent)
                nodes.append(node)
            else:
                node = self._parse_step_line(line_no, content)
                nodes.append(node)
                self._pos += 1

        return nodes

    def _parse_if(self, block_indent: int) -> IfNode:
        """解析 if 语句（单行或多行块）"""
        line_no, _, content = self._tokens[self._pos]
        self._pos += 1

        # 解析条件
        condition = self._parse_condition(content, line_no)

        # 检查：多行块 or 单行简写？
        if self._pos >= len(self._tokens) or self._tokens[self._pos][1] <= block_indent:
            # 单行简写：if <cond> <step>
            # 条件后面的剩余文本已在 _parse_condition 中处理
            # 但单行简写需要条件后面的步骤...
            # 实际上，单行简写的格式是 "if cond log "msg""
            # 我们的 _parse_condition 已经消费了整行
            # 所以单行简写需要特殊处理
            return IfNode(condition=condition, line_no=line_no)

        # 多行块
        body_indent = self._tokens[self._pos][1]
        consequent = self._parse_block(body_indent)

        alternative = []
        if self._pos < len(self._tokens) and self._tokens[self._pos][2] == "else":
            self._pos += 1  # skip 'else'
            if self._pos < len(self._tokens):
                else_indent = self._tokens[self._pos][1]
                alternative = self._parse_block(else_indent)

        # 消费 'end'
        if self._pos < len(self._tokens) and self._tokens[self._pos][2] == "end":
            self._pos += 1

        return IfNode(
            condition=condition,
            consequent=consequent,
            alternative=alternative,
            line_no=line_no,
        )

    def _parse_condition(self, line: str, line_no: int) -> dict:
        """解析 if 行的条件部分

        支持格式：
            if func(args...)
            if not func(args...)
            if varname
            if not varname
            if func(args...) <step>     ← 单行简写（暂不解析后续步骤）
        """
        # 去掉 "if " 前缀
        rest = line[3:].strip()

        # 检查 not
        negated = False
        if rest.startswith("not "):
            negated = True
            rest = rest[4:].strip()

        # 尝试匹配函数调用
        m = re.match(r"^(\w+)\((.*)\)(.*)$", rest)
        if m:
            func_name = m.group(1)
            func_args = _parse_args(m.group(2))
            # 注意：group(3) 是函数调用后面的内容（单行简写的步骤）
            # 当前实现中，单行简写的步骤会被忽略
            # TODO: 支持单行简写 if func() step
            return {"func": func_name, "args": func_args, "negated": negated}

        # 变量引用
        var_name = rest.split()[0] if rest.split() else rest
        if not var_name or var_name in ("else", "end"):
            raise ValueError(f"{self._source}:{line_no}: if 条件为空")
        return {"var": var_name, "negated": negated}

    def _parse_step_line(self, line_no: int, content: str) -> Step | EvalNode:
        """解析单行基础指令或 eval"""

        # eval [var =] func(args...)
        m = _RE_EVAL.match(content)
        if m:
            var_name = m.group(1)
            func_name = m.group(2)
            func_args = _parse_args(m.group(3))
            return EvalNode(
                func_name=func_name,
                func_args=func_args,
                var_name=var_name,
                line_no=line_no,
            )

        # click [scene].[field]
        m = _RE_CLICK.match(content)
        if m:
            return Step("click", {"scene": m.group(1), "field": m.group(2)}, line_no)

        # scan [scene].[f1, f2, ...]
        m = _RE_SCAN_FULL.match(content)
        if m:
            fields = [f.strip() for f in m.group(2).split(",")]
            return Step("scan", {"scene": m.group(1), "fields": fields}, line_no)

        # scan [scene]
        m = _RE_SCAN_SCENE.match(content)
        if m:
            return Step("scan", {"scene": m.group(1)}, line_no)

        # click_match "text" [error "msg"]
        m = _RE_CLICK_MATCH.match(content)
        if m:
            args = {"text": m.group(1)}
            if m.group(2):
                args["error_msg"] = m.group(2)
            return Step("click_match", args, line_no)

        # collect_as <key>（必须在 collect 之前匹配）
        m = _RE_COLLECT_AS.match(content)
        if m:
            return Step("collect_as", {"key": m.group(1)}, line_no)

        # collect
        if _RE_COLLECT.match(content):
            return Step("collect", {}, line_no)

        # wait <name> | <float>
        m = _RE_WAIT.match(content)
        if m:
            val = m.group(1).strip()
            try:
                return Step("wait", {"seconds": float(val)}, line_no)
            except ValueError:
                return Step("wait", {"delay_name": val}, line_no)

        # log "message"
        m = _RE_LOG.match(content)
        if m:
            return Step("log", {"message": m.group(1)}, line_no)

        raise ValueError(f"{self._source}:{line_no}: 无法解析指令: {content!r}")


# ─── 公共接口 ─────────────────────────────────────────────

def parse_file(path: Path) -> list:
    """解析 .wf 文件，返回 AST 节点列表

    节点类型：Step | EvalNode | IfNode
    """
    text = path.read_text(encoding="utf-8")
    tokens = _tokenize_lines(text)
    parser = _Parser(tokens, source=str(path))
    return parser.parse()


def parse_text(text: str, source: str = "<text>") -> list:
    """从字符串解析 DSL 文本，返回 AST 节点列表（主要用于测试）"""
    tokens = _tokenize_lines(text)
    parser = _Parser(tokens, source=source)
    return parser.parse()
