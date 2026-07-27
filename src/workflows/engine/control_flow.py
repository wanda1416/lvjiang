"""控制流 Mixin：if / for / for-range / loop"""

from loguru import logger

from ..grammar import If, For, ForRange, Loop, VarRef, FuncCall
from .signals import _BreakSignal


class _ControlFlowMixin:
    """控制流语句执行"""

    def _exec_if(self, node: If):
        cond_result = self._eval_condition(node.condition)
        logger.debug(f"if 条件求值: {self._cond_desc(node.condition)} -> {cond_result}")

        if cond_result:
            self._exec_body(node.then_body)
        elif node.else_body:
            self._exec_body(node.else_body)

    def _exec_for(self, node: For):
        # 解析迭代列表：支持静态列表、动态变量和函数调用
        if isinstance(node.iterable, VarRef):
            # 动态迭代：for $x in $list_var
            raw = self.variables.get(node.iterable.name)
            if raw is None:
                logger.error(f"for: 变量 ${node.iterable.name} 未定义")
                return
            if not isinstance(raw, list):
                logger.error(f"for: ${node.iterable.name} 不是列表类型，无法迭代")
                return
            items = raw
        elif isinstance(node.iterable, FuncCall):
            # 函数调用迭代：for i in range(1, 100)
            items = self._call_func(node.iterable)
            if not isinstance(items, list):
                logger.error(f"for: 函数 {node.iterable.func_name}() 返回值不是列表类型")
                return
        else:
            # 静态迭代：for $x in [a, b, c]
            items = [self._resolve(item) for item in node.iterable]
        logger.debug(f"for {node.var} in {items}")

        for value in items:
            if self._stop_check():
                logger.info("工作流被用户停止")
                return
            # 设置循环变量
            self.variables[node.var] = str(value)
            try:
                self._exec_body(node.body)
            except _BreakSignal:
                break

    def _exec_for_range(self, node: ForRange):
        """for i in [start...end] — 闭区间范围迭代"""
        start_val = self._resolve(node.start)
        end_val = self._resolve(node.end)
        try:
            start_int = int(float(start_val))
            end_int = int(float(end_val))
        except (TypeError, ValueError):
            logger.error(f"for_range: 起止值非数值: start={start_val}, end={end_val}")
            return
        if start_int > end_int:
            logger.error(f"for_range: 起始值大于结束值: {start_int} > {end_int}")
            return
        items = list(range(start_int, end_int + 1))  # 闭区间
        logger.debug(f"for {node.var} in [{start_int}...{end_int}] → {items}")

        for value in items:
            if self._stop_check():
                logger.info("工作流被用户停止")
                return
            self.variables[node.var] = str(value)
            try:
                self._exec_body(node.body)
            except _BreakSignal:
                break

    def _exec_loop(self, node: Loop):
        # 解析循环次数
        if isinstance(node.count, int):
            count = node.count
        elif isinstance(node.count, VarRef):
            # 变量引用：loop $execute_times
            val = self.variables.get(node.count.name)
            count = int(val) if val is not None else 0
        else:
            # 字符串（NAME）
            val = self.variables.get(str(node.count))
            count = int(val) if val is not None else 0

        logger.debug(f"loop {count}")
        for _ in range(count):
            if self._stop_check():
                logger.info("工作流被用户停止")
                return
            try:
                self._exec_body(node.body)
            except _BreakSignal:
                break
