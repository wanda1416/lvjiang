# 开发日志 2026-08-13（五）

> 接续本日《DSL 语言能力扩展》。
> 本轮主题：**批处理框架从 0 到 1——账号/角色批量编排，四阶段生命周期**。

---

## 一、批量执行基础架构

- 批量执行基础——User 模型扩展 `game_account`/`game_character` + 批量 Tab 与编排器骨架（`099ab8f`）；
- 批处理 MVP 架构（`e7fe69a`）；
- 批处理 DSL 工作流提取公共 def——新增 `_common.wf` 定义 `select_role_and_enter(\)`，`_switch_account.wf`/`_select_role.wf` 通过 import 复用，消除选角色→进入游戏→等待主页的重复逻辑（`89afd47`）；
- 批处理 session 切换使用 role 而非 account——user 是角色名，不是账号名，session 切换和结果落盘路径统一使用 role（`f487220`）；
- 批处理模块独立为 `batch` 包——新建 `ui/batch/` 包，移入 `batch_runner`/`batch_tab`/`batch_config_dialog`（`b39f897`）；
- 用户切换时联动更新主页面当前用户显示——`BatchWorker` 新增 `user_changed` 信号，删除 `run_control.py` 中废弃的 `run_batch` 方法（`8726856`）。

## 二、三工作流槽位与生命周期

- 三工作流槽位架构适配——新增 `preprocess.wf`/`switch.wf` 作为 Runner 调用槽位入口，删除 `_common.wf`，公共逻辑合并到 `_select_role.wf`（`bed8d7f`）；
- 场景批量重命名统一 + 批量 Tab 三页子 Tab 重构（`76a1659`，场景重命名部分见「场景编辑器」篇）；
- 四阶段生命周期重构（`batch_setup`/`prepare_item`/`finish_item`/`batch_teardown`）——删除旧的 `preprocess.wf`/`switch.wf` 双 wf 架构，新增 `prepare_item.wf`，引入 `batch_state` 通用字典由 wf 自行读写、runner 只透传，返回协议标准化为 `{status, message, state}`，runner 移除所有业务语义硬编码（`951c291`）；
- 脚本性质（daily/dedicated）+ 批量引擎参数加载重构 + 配置治理——脚本配置对话框新增「脚本性质」列，专用脚本由专属页面管理，日常 Tab 按 scope 过滤参数面板和读写；批量引擎移除 `BatchScript.params`，执行时从 `wf_configs` 按 `script.id` 加载；单次执行路径对专用脚本同步从 `wf_configs` 加载参数；`auto_tuning` 标记为 `scope: dedicated`（`2eb9544`）。

### 2.5 其他小修

- 统一三个 Tab（日常/批量/调律）开始/停止按钮样式——位置统一放第一行，样式统一 padding/font-size/margin（`30c4b17`）；
- 批处理模块 lint 修复——`batch_config_dialog.py`/`batch_runner.py` import 排序、空白行清理（`380ec76`，同一提交另含江湖工作流 goto 优化，见工作流篇）。

## 三、账号切换修复

- 批处理账号切换逻辑修复 + 测试 mock 完善——`switch.wf` 比较 `prev_account` 与 `account`（游戏账号）而非 tail（`594f1c3`）；
- 批处理账号切换逻辑改进——添加当前账号日志输出，使用 equals 替代 `==` 进行字符串比较，修正日志显示为 role 而非 account（`333dc3c`）。

## 四、执行链路与报告

- 执行链路用户归属改为启动时快照绑定——`WorkflowEngine` 新增 `run_username` 属性，启动时绑定全程使用；`batch_runner` 彻底去全局化，删除 `set_active_user`/`user_changed`，role 重命名为 `username`；架构原则：执行链路只认启动时快照的用户名，UI 切换不影响运行中任务（`22b82ad`）；
- 批量执行报告 + mypy 修复——新增 `batch_report.py`（`BatchReport` 类），记录启动时间、启动参数、逐行耗时与结果，生成行级/脚本级统计的 Markdown 报告（`b38640a`）。

---

## 结果

- 批处理框架完成从骨架到「四阶段生命周期 + 启动时快照绑定」的收敛，约 15 个 commit；
- 账号/角色切换语义（role vs account）反复修正后趋于稳定。

---

## 关键设计决策（用户确认）

1. **wf 返回协议标准化为 `{status, message, state}`**：runner 不再硬编码业务语义，业务状态全部经 `batch_state` 字典透传。
2. **执行链路用户归属启动时快照绑定**：运行中任务只认启动时绑定的用户名，UI 切换用户不影响正在执行的任务，避免运行时状态错乱。
3. **脚本按「日常/专用」分类管理参数**：专用脚本（如 auto_tuning）参数不进日常 Tab 面板，避免参数面板膨胀。
