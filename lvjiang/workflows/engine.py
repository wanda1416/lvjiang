"""工作流 DSL v2 引擎

递归执行 AST 节点，支持完整控制流（if/for/loop/break/goto）。
引擎本身不持有运行时状态，所有操作委托给 BaseWorkflow 实例。
"""

import traceback

from loguru import logger
from pathlib import Path

from .ast_nodes import (
    Program,
    Click, Drag, Wait, Scan, ScanAs, ClickMatch, Collect, Log,
    If, For, Loop, Break, Label, Goto, Eval,
    VarRef, Literal, FieldAccess, Contains, Equals, InList, IsEmpty,
    Not, And, Or,
)
from .parser import parse_file
from .base import BaseWorkflow


# ─── 控制流信号 ───────────────────────────────────────────

class _BreakSignal(Exception):
    """break 语句触发的跳出信号"""


class _GotoSignal(Exception):
    """goto 语句触发的跳转信号"""

    def __init__(self, target: str):
        self.target = target


class _HaltSignal(Exception):
    """工作流提前终止信号（如 click_match 失败）"""

    def __init__(self, message: str):
        self.message = message


# ─── 引擎 ─────────────────────────────────────────────────

class WorkflowEngine:
    """DSL v2 工作流解释器"""

    def __init__(self, workflow: BaseWorkflow):
        self._wf = workflow

    def run(self, workflow_path: Path | str) -> list[dict]:
        """加载并执行 .wf 文件"""
        program = parse_file(workflow_path)
        logger.info(f"=== DSL 工作流开始: {Path(workflow_path).stem} ({len(program.body)} 条顶层指令) ===")

        try:
            self._exec_body(program.body)
        except _HaltSignal as sig:
            logger.info(f"=== DSL 工作流提前终止: {sig.message} ===")
            return []

        logger.info(f"=== DSL 工作流完成，收集到 {len(self._wf.output)} 项数据 ===")
        return self._wf.output

    # ─── 语句执行 ──────────────────────────────────────────

    def _exec_body(self, stmts: list) -> None:
        """执行一组语句（含 goto 跳转支持）

        collect 的输出记录到 self._collect_output，不中断执行流。
        仅 break/goto/停止请求 才会提前终止。
        """
        # 预扫描标签位置
        label_index = self._build_label_index(stmts)

        pc = 0
        while pc < len(stmts):
            if self._wf.is_stopped:
                logger.info("工作流被用户停止")
                return

            node = stmts[pc]

            try:
                self._exec_stmt(node)
            except _GotoSignal as sig:
                target = sig.target
                if target not in label_index:
                    logger.error(f"goto 目标标签不存在: {target}")
                    return
                pc = label_index[target]
                continue  # 从标签位置继续，不 pc+1
            except BaseException as e:
                line_info = f"(行 {node.line_no})" if hasattr(node, 'line_no') and node.line_no else ""
                logger.error(f"DSL 执行异常 {line_info}: {e}")
                logger.error(f"异常详情:\n{traceback.format_exc()}")
                raise  # 异常即终止，不跳过继续

            pc += 1

    def _exec_stmt(self, node):
        """执行单条语句"""
        match node:
            case Click():
                self._exec_click(node)
            case Drag():
                self._exec_drag(node)
            case Wait():
                self._exec_wait(node)
            case Scan() | ScanAs():
                self._exec_scan(node)
            case ClickMatch():
                self._exec_click_match(node)
            case Collect():
                self._exec_collect(node)
            case Log():
                logger.info(self._resolve(node.message))
            case If():
                self._exec_if(node)
            case For():
                self._exec_for(node)
            case Loop():
                self._exec_loop(node)
            case Break():
                raise _BreakSignal()
            case Label():
                pass  # 标签无操作
            case Goto():
                raise _GotoSignal(node.target)
            case Eval():
                self._exec_eval(node)
            case _:
                logger.error(f"未知节点类型: {type(node).__name__}")

    # ─── 基础指令 ─────────────────────────────────────────

    def _exec_click(self, node: Click):
        scene = self._resolve_param(node.scene)
        field = self._resolve_param(node.field)
        self._wf.click_any(scene, field)

    def _exec_drag(self, node: Drag):
        scene = self._resolve_param(node.scene)
        arrow = self._resolve_param(node.arrow)
        duration = self._resolve_duration(node.duration) if node.duration else None
        self._wf.drag_arrow(scene, arrow, duration=duration)

    def _exec_wait(self, node: Wait):
        delay = node.delay
        if isinstance(delay, Literal):
            val = delay.value
            if isinstance(val, (int, float)):
                self._wf.wait_seconds(float(val))
            else:
                # 命名延迟
                self._wf.wait_delay(str(val))
        else:
            self._wf.wait_delay(str(delay))

    def _exec_scan(self, node: Scan | ScanAs):
        scene = self._resolve_param(node.scene)
        field_keys = None
        if node.fields:
            field_keys = [self._resolve_param(f) for f in node.fields]

        result = self._wf.ocr_scene(scene, field_keys)

        if isinstance(node, ScanAs):
            var_name = self._resolve_var_name(node.target)
            self._wf.set_variable(var_name, result)

    def _exec_click_match(self, node: ClickMatch):
        text = self._resolve(node.text)
        error_msg = self._resolve(node.error_msg) if node.error_msg else None
        result = self._wf.click_match_text(str(text), str(error_msg) if error_msg else None)
        if result is not None:
            raise _HaltSignal(result)

    def _exec_collect(self, node: Collect):
        """collect [var] [as [alias]] — 将变量值追加到输出"""
        var_name = node.source.name if isinstance(node.source, VarRef) else str(node.source)
        value = self._wf.get_variable(var_name)
        if value is None:
            logger.warning(f"collect: 变量 [{var_name}] 未定义，跳过")
            return
        if node.alias:
            self._wf.output.append({node.alias: value})
            logger.debug(f"collect: {node.alias} = {value}")
        else:
            self._wf.output.append(value)
            logger.debug(f"collect: {value}")

    def _exec_eval(self, node: Eval):
        # 解析参数
        resolved_args = []
        for arg in node.func_args:
            resolved_args.append(self._resolve(arg))

        result = self._wf.call_function(node.func_name, resolved_args)

        if node.target is not None:
            self._wf.set_variable(node.target, result)
            logger.debug(f"eval: {node.target} = {result}")
        else:
            logger.debug(f"eval: {node.func_name}(...) = {result} (丢弃)")

    # ─── 控制流 ───────────────────────────────────────────

    def _exec_if(self, node: If):
        cond_result = self._eval_condition(node.condition)
        logger.debug(f"if 条件求值: {self._cond_desc(node.condition)} -> {cond_result}")

        if cond_result:
            self._exec_body(node.then_body)
        elif node.else_body:
            self._exec_body(node.else_body)

    def _exec_for(self, node: For):
        # 解析迭代列表
        items = [self._resolve(item) for item in node.iterable]
        logger.debug(f"for {node.var} in {items}")

        for value in items:
            if self._wf.is_stopped:
                logger.info("工作流被用户停止")
                return
            # 设置循环变量
            self._wf.set_variable(node.var, str(value))
            try:
                self._exec_body(node.body)
            except _BreakSignal:
                break

    def _exec_loop(self, node: Loop):
        # 解析循环次数
        if isinstance(node.count, int):
            count = node.count
        else:
            # 变量引用
            val = self._wf.get_variable(str(node.count))
            count = int(val) if val is not None else 0

        logger.debug(f"loop {count}")
        for _ in range(count):
            if self._wf.is_stopped:
                logger.info("工作流被用户停止")
                return
            try:
                self._exec_body(node.body)
            except _BreakSignal:
                break

    # ─── 条件求值 ─────────────────────────────────────────

    def _eval_condition(self, node) -> bool:
        """递归求值条件表达式 AST 节点"""
        match node:
            case Contains():
                left = self._eval_field_access(node.left)
                right = self._resolve(node.right)
                return str(right) in str(left) if left else False
            case Equals():
                left = self._eval_field_access(node.left)
                right = self._resolve(node.right)
                return str(left) == str(right)
            case InList():
                left = self._eval_field_access(node.left)
                right = [str(self._resolve(item)) for item in node.right]
                return str(left) in right if left else False
            case IsEmpty():
                left = self._eval_field_access(node.expr)
                return not left or str(left).strip() == ""
            case Not():
                return not self._eval_condition(node.operand)
            case And():
                return self._eval_condition(node.left) and self._eval_condition(node.right)
            case Or():
                return self._eval_condition(node.left) or self._eval_condition(node.right)
            case VarRef():
                # 条件中的 [var] → truthy 检查
                val = self._wf.get_variable(node.name)
                return bool(val)
            case _:
                logger.error(f"未知条件节点: {type(node).__name__}")
                return False

    def _eval_field_access(self, node: FieldAccess) -> str:
        """求值 [var].field → 从变量中取字段"""
        var_val = self._wf.get_variable(node.var.name)
        if isinstance(var_val, dict):
            return str(var_val.get(node.field_name, ""))
        return ""

    # ─── 变量解析 ─────────────────────────────────────────

    def _resolve(self, node) -> str:
        """解析表达式节点为运行时值

        VarRef → 查变量表（找不到则返回 name 本身作为字面量回退）
        Literal → 直接返回值
        """
        match node:
            case VarRef():
                val = self._wf.get_variable(node.name)
                if val is not None:
                    return val
                # 回退：未定义的变量视为字面量
                return node.name
            case Literal():
                return node.value
            case _:
                return str(node) if node is not None else ""

    def _resolve_param(self, node) -> str:
        """解析 click/scan 参数

        VarRef → 变量优先，回退字面量
        Literal → 直接返回
        """
        return self._resolve(node)

    def _resolve_var_name(self, node) -> str:
        """提取变量名（用于 scan as / collect_as 等赋值目标）"""
        if isinstance(node, VarRef):
            return node.name
        return str(node)

    def _resolve_duration(self, node) -> float | tuple[float, float]:
        """解析拖拽时长：Literal → float，list[Literal] → tuple"""
        if isinstance(node, list):
            return (float(node[0].value), float(node[1].value))
        if isinstance(node, Literal):
            return float(node.value)
        return float(node)

    # ─── 工具 ─────────────────────────────────────────────

    @staticmethod
    def _build_label_index(stmts: list) -> dict[str, int]:
        """预扫描语句列表，建立 label → 索引 映射"""
        index = {}
        for i, stmt in enumerate(stmts):
            if isinstance(stmt, Label):
                index[stmt.name] = i
        return index

    @staticmethod
    def _cond_desc(node) -> str:
        """条件的调试描述"""
        match node:
            case Contains():
                return f"{node.left.var.name}.{node.left.field_name} contains {node.right}"
            case Equals():
                return f"{node.left.var.name}.{node.left.field_name} equals {node.right}"
            case InList():
                return f"{node.left.var.name}.{node.left.field_name} in [...]"
            case IsEmpty():
                return f"{node.expr.var.name}.{node.expr.field_name} is_empty"
            case Not():
                return f"not ({WorkflowEngine._cond_desc(node.operand)})"
            case And():
                return f"({WorkflowEngine._cond_desc(node.left)} and {WorkflowEngine._cond_desc(node.right)})"
            case Or():
                return f"({WorkflowEngine._cond_desc(node.left)} or {WorkflowEngine._cond_desc(node.right)})"
            case VarRef():
                return f"[{node.name}]"
            case _:
                return str(type(node).__name__)
