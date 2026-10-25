# 03-error-reporting — 异常记账：什么时候打堆栈

> 最后更新：2026-10-26（基线 v0.15.4）。

日志里的堆栈只用于定位「代码没预料到」的位置。有些异常天生不需要它：
业务脚本主动拒绝写入（`profile_reject`）、DSL 脚本自身的可预期错误（变量名写错、
类型不匹配）——这些的消息里已经写清了原因、行号和上下文，堆栈里只剩引擎内部帧，
打出来只会把一条结论渲染成连环崩溃。

所以异常按「要不要堆栈」分成两类，判定只看类型，不看消息文本：

| 类别 | 类型 | 记账方式 | 典型来源 |
|------|------|----------|----------|
| 预期结果 | `ExpectedError` 及其子类 | 一行原因，不附堆栈，级别由 `log_level` 决定 | 业务拒绝、脚本可预期错误 |
| 意外 | 其余所有异常 | 原因 + 堆栈 | 引擎缺陷、系统调用失败 |

## 类型

定义在 `src/lvjiang/errors.py`：

- `ExpectedError`：预期结果。带 `log_level`（默认 `WARNING`）与
  `log_reason(message)`，按本类级别记一行。
- `ProfileWriteRejected(ValueError, ExpectedError)`：同步脚本拒绝候选写入，
  级别 `WARNING`——这是业务规则正常生效，事务整体回滚。
  保留 `ValueError` 血统，DSL `try/catch` 仍能捕获它。
- `ProfileWriteConflict(RuntimeError, ExpectedError)`：写入冲突（CAS 失败、
  历史已被改动），级别 `WARNING`——重读后重试即可，不是缺陷。
- `ProfileOperationRefused(ValueError, ExpectedError)`：当前状态不允许这次操作
  （撤销不是最新记录、记录关联了变更脚本、会带走同步数据等），级别 `WARNING`——
  原因是业务规则，直接展示给用户。
- `WorkflowUserError(ExpectedError)`：DSL 脚本自身的可预期错误，级别 `ERROR`——
  用户需要改脚本，但同样不打堆栈。
- `WorkflowAbort`：既不是预期结果也不是失败，宿主改道，**一行日志都不记**
  （见 `tests/workflows/test_host_abort_signal.py`）。

## 记账入口

- 单点记账：`log_failure(message, error)`（`src/lvjiang/errors.py`）按类别分流。
- 需要自定义措辞时用 `ExpectedError.log_reason(message)`，或自行 `isinstance`
  判断后调用 `logger.log(error.log_level, ...)`。

调用点：DSL 语句失败（`workflows/engine/core.py::_report_statement_failure`）、
`WorkflowWorker` 异常退出、PC/手机批量执行收尾、Profile 异步变更脚本失败、
Profile 总览的手动增减与备注写回、历史对话框的撤销与删除
（都是同一个入口，不再各写一份 `logger.error`）。

## 同一次失败只记一遍

`if` / 循环 / 子过程里的失败会穿透每一层 `_exec_body`，逐层记账会把一次失败打印
成多份重复 ERROR + 堆栈。引擎在异常实例上打 `_dsl_reported` 标记，只让行号最准确
的最内层记账；上层 `_exec_body` 保持静默穿透。宿主层的报告点（批量、运行线程）
各自只记一次自己的结论。

这个去重对意外异常同样生效：一次真实失败也只会有一份堆栈。
