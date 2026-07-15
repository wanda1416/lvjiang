"""工作流 DSL 引擎 - 纯 DSL 解释器

职责：解析 .wf 文件，逐条指令委托给 BaseWorkflow 实例执行。
引擎本身不持有任何运行时状态，所有操作通过 workflow 完成。
"""

import traceback

from loguru import logger
from pathlib import Path

from .parser import Step, EvalNode, IfNode, parse_file
from .base import BaseWorkflow


class WorkflowEngine:
    """DSL 工作流解释器

    接收一个 BaseWorkflow 实例，解析 .wf 文件并逐条指令委托执行。
    """

    def __init__(self, workflow: BaseWorkflow):
        self._wf = workflow

    def run(self, workflow_path: Path | str) -> dict | str:
        """加载并执行 .wf 文件"""
        workflow_path = Path(workflow_path)
        nodes = parse_file(workflow_path)
        logger.info(f"=== DSL 工作流开始: {workflow_path.stem} ({len(nodes)} 条顶层指令) ===")

        direct_output = self._execute_nodes(nodes)

        if direct_output is not None:
            logger.info(f"=== DSL 工作流完成 ===")
            return direct_output
        if self._wf.output:
            logger.info(f"=== DSL 工作流完成，收集到 {len(self._wf.output)} 项数据 ===")
            return self._wf.output
        logger.info(f"=== DSL 工作流完成 ===")
        return {}

    # ─── 节点遍历 ──────────────────────────────────────────

    def _execute_nodes(self, nodes: list) -> dict | str | None:
        """执行一组 AST 节点，返回 collect 的直接输出（如果有）

        容错策略：单步异常时跳过当前槽位剩余步骤，继续下一个 click 槽位。
        """
        direct_output = None
        skip_until_click = False  # 当前槽位已失败，跳过到下一个 click

        for i, node in enumerate(nodes):
            if self._wf.is_stopped:
                logger.info("工作流被用户停止")
                return "(已停止)"

            try:
                if isinstance(node, IfNode):
                    self._exec_if(node)
                elif isinstance(node, EvalNode):
                    self._exec_eval(node)
                elif isinstance(node, Step):
                    # 容错：跳过失败槽位的剩余步骤
                    if skip_until_click:
                        if node.instruction == "click":
                            skip_until_click = False
                            logger.debug(f"  步骤 {i+1}/{len(nodes)}: [新槽位] {node.instruction} {node.args}")
                        else:
                            logger.debug(f"  步骤 {i+1}/{len(nodes)}: [跳过] {node.instruction}")
                            continue
                    else:
                        logger.debug(f"  步骤 {i+1}/{len(nodes)}: {node.instruction} {node.args}")

                    result = self._exec_step(node)
                    if result is not None:
                        direct_output = result
                else:
                    logger.error(f"未知节点类型: {type(node)}")
            except BaseException as e:
                line_info = f" (行 {node.line_no})" if hasattr(node, "line_no") else ""
                logger.error(f"DSL 执行异常{line_info}: {e}")
                logger.error(f"异常详情:\n{traceback.format_exc()}")
                # 容错：标记当前槽位失败，跳到下一个 click
                if isinstance(node, Step):
                    skip_until_click = True
                    logger.warning(f"  当前槽位异常终止，跳过剩余步骤")
                else:
                    raise

        return direct_output

    def _exec_step(self, step: Step) -> dict | None:
        """执行单条基础指令，委托给 workflow"""
        match step.instruction:
            case "click":
                self._wf.click_region(step.args["scene"], step.args["field"])
            case "wait":
                if "seconds" in step.args:
                    self._wf.wait_seconds(step.args["seconds"])
                else:
                    self._wf.wait_delay(step.args["delay_name"])
            case "scan":
                self._wf.ocr_scene(step.args["scene"], step.args.get("fields"))
            case "click_match":
                result = self._wf.click_match_text(
                    step.args["text"],
                    step.args.get("error_msg"),
                )
                if result is not None:
                    return result  # 错误：提前终止
            case "collect":
                return self._wf.collect_current()
            case "collect_as":
                self._wf.collect_as(step.args["key"])
            case "log":
                logger.info(step.args["message"])
        return None

    # ─── 条件分支 ──────────────────────────────────────────

    def _exec_if(self, node: IfNode):
        """执行 if/else/end，委托条件求值给 workflow"""
        cond_result = self._wf.eval_condition(node.condition)
        logger.debug(f"if 条件求值: -> {cond_result}")

        if cond_result:
            self._execute_nodes(node.consequent)
        else:
            self._execute_nodes(node.alternative)

    # ─── 函数调用 ──────────────────────────────────────────

    def _exec_eval(self, node: EvalNode):
        """执行 eval 指令，委托给 workflow"""
        resolved = self._wf.resolve_args(node.func_args)
        result = self._wf.call_function(node.func_name, resolved)

        if node.var_name is not None:
            self._wf.set_variable(node.var_name, result)
            logger.debug(f"eval: {node.var_name} = {result}")
        else:
            logger.debug(f"eval: {node.func_name}(...) = {result} (丢弃)")
