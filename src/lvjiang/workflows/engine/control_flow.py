"""控制流 Mixin：if / for / for-range / loop / loop-while / loop-until / continue / try-catch"""

from loguru import logger

from ..grammar import For, ForRange, If, Loop, UntilLoop, WhileLoop
from ..grammar.ast_nodes import Try
from .signals import WorkflowUserError, _BreakSignal, _ContinueSignal


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
        items = self._resolve(node.iterable)
        if not isinstance(items, list):
            logger.error(
                f"for: 迭代表达式结果不是列表类型: "
                f"{type(items).__name__}")
            return
        logger.debug(f"for {node.var} in {items}")

        for value in items:
            if self._stop_check():
                return
            self._wait_if_paused()  # 暂停检查
            # 设置循环变量（保持原始类型：int/float/str）
            self.variables[node.var] = value
            try:
                self._exec_body(node.body)
            except _BreakSignal:
                break
            except _ContinueSignal:
                continue

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
                return
            self._wait_if_paused()  # 暂停检查
            self.variables[node.var] = value  # int，保持原始类型
            try:
                self._exec_body(node.body)
            except _BreakSignal:
                break
            except _ContinueSignal:
                continue

    def _exec_loop(self, node: Loop):
        if isinstance(node.count, str):
            # 兼容旧式裸 NAME 变量：loop execute_times
            value = self.variables.get(node.count)
        else:
            value = self._resolve(node.count)
        try:
            count = int(value) if value is not None else 0
        except (TypeError, ValueError):
            logger.error(f"loop: 计数表达式结果非数值: {value!r}")
            return

        logger.debug(f"loop {count}")
        for _ in range(count):
            if self._stop_check():
                return
            self._wait_if_paused()  # 暂停检查
            try:
                self._exec_body(node.body)
            except _BreakSignal:
                break
            except _ContinueSignal:
                continue

    def _exec_while_loop(self, node: WhileLoop):
        """loop while <condition> ... end — 条件为真继续"""
        logger.debug("loop while 开始")
        safety_counter = 0
        while self._eval_condition(node.condition):
            if self._stop_check():
                return
            self._wait_if_paused()  # 暂停检查
            try:
                self._exec_body(node.body)
            except _BreakSignal:
                break
            except _ContinueSignal:
                continue
            safety_counter += 1
            if safety_counter > 1_000_000:
                logger.error("loop while 超过 1,000,000 次迭代，强制退出防止死循环")
                break

    def _exec_until_loop(self, node: UntilLoop):
        """loop until <condition> ... end — 条件为假继续（条件为真退出），至少执行一次"""
        logger.debug("loop until 开始")
        safety_counter = 0
        while True:
            if self._stop_check():
                return
            self._wait_if_paused()  # 暂停检查
            try:
                self._exec_body(node.body)
            except _BreakSignal:
                break
            except _ContinueSignal:
                # continue 跳过本轮剩余语句，直接进入下一轮条件检查
                continue
            # 本轮正常完成，检查退出条件
            if self._eval_condition(node.condition):
                break
            safety_counter += 1
            if safety_counter > 1_000_000:
                logger.error("loop until 超过 1,000,000 次迭代，强制退出防止死循环")
                break

    def _exec_try(self, node: Try):
        """try ... catch $err ... end — 异常处理

        捕获 WorkflowUserError / KeyError / ValueError / TypeError，
        控制流信号（_BreakSignal / _ReturnSignal / _GotoSignal / _ContinueSignal）穿透不捕获。
        """
        try:
            self._exec_body(node.body)
        except (WorkflowUserError, KeyError, ValueError, TypeError) as e:
            logger.debug(f"try: 捕获异常 {type(e).__name__}: {e}")
            if node.err_var is not None:
                self.variables[node.err_var] = str(e)
            if node.catch_body:
                self._exec_body(node.catch_body)
