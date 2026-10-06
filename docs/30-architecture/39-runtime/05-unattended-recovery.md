# 无人值守的改道与恢复

> 产品边界（改道语义、恢复阶段、谁来勾选）见
> [20-requirements/30-batch/03-unattended-batch.md](../../20-requirements/30-batch/03-unattended-batch.md)。
> 本文只描述实现契约：拦截点、注入信号、配置兜底与落点。

## 拦截点只有一个

所有任务交互都经由 `engine._ui_callback` 走到宿主；生命周期脚本与条目脚本
共用同一个回调。无人值守回调仅在 `kind == "pause"` 时抛出
`UnattendedInterrupt`，按异常记失败且不重试。`confirm/input/choose/notify`
及其他事件正常转发到宿主，保留返回值；不会设置异常恢复标记。

自动调律向公共导航的 `nav_main_to_equip`、`nav_equip_to_tune` 传入
`ask_confirm=true`，并传递至背包导航，因此这些路径使用确认而非 pause。
其他调用省略参数时保持异常 pause。重置二次确认缺失也通过 confirm 等待用户；
拒绝时使用既有 `TuningRunState.end_requested` 结束运行，不再操作未知页面。

## 调度器注入的信号

| 变量 | 用途 |
|---|---|
| `batch_recover_pending` | 本条目是否还有未执行的任务；为真时恢复要走第二阶段 |
| `batch_recover_username` | 本轮用户名；重启后重新选账号、以及回写 `batch_state.role` 要用 |

## 配置层的兜底

界面禁用「没有配置异常恢复工作流却勾选无人值守」的组合；配置层 `normalize()` 会清掉
手改出来的这种组合，调度器启动前再确认一次。三道防线共用一个判据：定义层是否配了
异常恢复工作流。

## 实现位置

| 关注点 | 位置 |
|---|---|
| 配置字段与前置校验 | `core/batch_config.py`（`unattended` / `recover_unattended`） |
| 弹窗改道与恢复调度 | `ui/batch/batch_runner.py`（`UnattendedInterrupt`、`_unattended_ui_callback`、`_recover_unattended`） |
| 界面联动 | `ui/batch/batch_config_dialog.py` |
| 随包恢复流程 | `config/system/workflows/batch/recover_to_login.wf` |
