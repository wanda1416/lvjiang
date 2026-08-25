# 开发日志 2026-08-21

> 接续 08-20 战斗属性布局策略模式重构、DSL/Python 工作流暂停/恢复机制、页面检测去重、用户指南全面重组与 F8/F12 热键修复。
> 本轮主题：**目标环境(env)配置 + 桌面窗口模式后台投递回归标记 + v0.4.3 发布 + DSL press/scroll/else-if 指令补全 + 导航策略拆分与武库拦截 + 桌面小屏布局**。

---

## 一、二次确认延迟参数化（`e8f5a72`）

- `app.yaml` 新增 `secondary_confirm`（6-7s）延迟参数，专用于二次确认场景；
- `daily_jianghu.wf` 情境保存操作改用 `@secondary_confirm`，替代此前的双重 `@page_refresh`；
- `recycler.py` 重置调律的二次确认改用 `wait_delay(secondary_confirm)`。

---

## 二、工作环境（env）配置与 DSL 内置函数（`1a0b2b3`）

- `app.yaml` 新增 `envs` 配置项，定义可用环境列表（桌面/安卓）；
- `session.json` 持久化用户当前选择的环境；
- DSL 新增 `env()` 内置函数：无参返回当前环境名，传参 `env("xxx")` 判断是否为该环境；
- Grammar 扩展：条件表达式支持函数调用（`if env("desktop")`）；
- 配置管理新增「系统参数」Tab，可编辑可用环境列表；
- 日常/批量/调律三个 Tab 的按钮下方新增环境下拉框。

这是本轮 DSL `env()`/`is_send()`/`is_post()` 系列环境判断函数、以及后续导航逻辑按环境下沉重构的基础设施。

---

## 三、桌面窗口模式后台投递回归标记（`7838759`）

窗口模式后台 PostMessage 投递发现严重回归，本次先做标记而非修复：

- `post_message.py` 添加 deprecated 警告，标记当前版本该路径暂不可用；
- README 功能特性中明确标注该回归问题，避免用户踩坑；
- 保留 `send_input.py` / `win32_util.py` 的调试改动，供后续诊断使用；
- 新增 `diag_postmessage.py` 诊断脚本、`test_win32_dpi.py` 测试；
- `layouts.yaml` 布局配置同步更新。

---

## 四、v0.4.3 发布（`12ba2d8`, `3f413cf`）

- 版本号 0.4.2 → 0.4.3（`pyproject.toml`、`_version.py`、`build.gradle.kts`）；
- 新增 `docs/50-releases/v0.4.3.md` 发布文档；
- 顺带修复 `resolver.py` mypy 类型注解、`settings_dialog.py` import 排序；
- `uv.lock` 更新（PyInstaller 打包产生）。

---

## 五、面板行列配置收拢至布局级（`2711abf`）

- `PanelDef` 移除 `rows`/`cols` 字段，行列数改为布局级配置而非场景级；
- 场景 YAML 不再存储 `rows`/`cols`，清理 7 个场景文件中的死数据；
- 编辑面板时从已绑定的 Panel（布局级）读取 `rows`/`cols`；保存时写回已绑定的 Panel；
- 绑定新面板时使用 Panel 默认值（`rows=3, cols=6`）；
- 修复 `canvas_interaction.py` 和 `dialog.py` 中访问已删除属性导致的崩溃。

---

## 六、navigation.wf 装备列表校验修正（`c0ac274`）

`subcall/navigation.wf` 装备列表校验逻辑存在缺陷：两处判断把 `or` 误写为 `and` 语义相反的场景（应为"任一成功即通过"），修正后改为两个都找不到才触发暂停/切换。同一 commit 顺带把语法文档、用户指南若干文件从 `03-1-xxx.md` 命名迁移为 `03.1-xxx.md` 风格（纯改名，无内容变化）。

---

## 七、DSL 语法文档重组（`455b752`）

- 拆分 `03-interaction.md` 为 `03.2`/`03.3`/`03.4` 三个专题文档（鼠标/键盘分离）；
- 新增 `scroll` 指令文档（语法、后端行为、与 `move`/`drag` 的关系）；
- 新增 `move` 指令文档；
- 新增 `is_send()`/`is_post()` 系统函数说明；
- 概览表新增 `scroll` 行，分组列表补充 `move`/`scroll`；
- 失败语义表补充 `scroll` 相关条目；
- 修复交叉引用锚点（后缀等待子句章节编号变更导致的失效链接）；
- userguide 多处链接与措辞同步修正。

---

## 八、DSL press/scroll/else-if 指令补全

本轮 DSL 补全的核心是把 `press`（键盘）、`scroll`（滚轮）两条全链路指令，以及 `else if` 条件链补齐到语法与四个执行后端（SendInput、PostMessage、ADB、设备端）。

### 8.1 press 指令实现（`09955c6`）

- 新增 `press` 指令支持四种模式：`PRESS`/`HOLD`/`DOWN`/`UP`；
- `KeyStateRegistry` 管理按键按下状态，支持组合键的 down/up 配对；
- SendInput 后端：统一 ctypes 结构体定义，修复 64 位下的指针截断问题；
- PostMessage 后端：DPI 感知坐标转换（`screen_to_client_logical`）；
- ADB 后端：空操作 + 警告日志（安卓端键盘输入语义不同）；
- 工作流退出时自动释放所有按下状态的按键，避免残留按键卡死；
- 新增 `win32_keyboard.py` 键盘映射与发送基础设施；
- 新增 `test_key_state.py` / `test_press.py` 测试。

### 8.2 else if 支持 + 导航下沉 + is_send/is_post 系统函数（`5752656`）

- parser 新增 `elif_clause`，支持 `else if` 链式条件；
- `navigation.wf`：环境分支（android/desktop）下沉到 `nav_main_to_equip` / `nav_back_to_main` 内部，调用方不再需要自己判断环境；
- `scan_equipped.wf` 导航逻辑简化为 `call nav_main_to_equip()`；
- 新增 `is_send()` / `is_post()` 系统函数，判断当前输入后端是 SendInput 模式还是 PostMessage 模式；
- `test_auto_tuning_flow` 移除随之失效的环境相关导航断言。

### 8.3 scroll 指令实现（`0890b1d`）

- Grammar 新增 `scroll_stmt` 规则（`up`/`down` + 可选目标 + 可选数量）；
- AST 新增 `Scroll` dataclass（`direction`/`target`/`amount`）；
- Parser `scroll_stmt` 回调，复用既有 `click_target` 解析目标；
- Backend 新增 `scroll_screen` 抽象方法 + 4 个子类实现：SendInput（`SetCursorPos` + `MOUSEEVENTF_WHEEL`）、PostMessage（`WM_MOUSEWHEEL` + DPI 感知坐标转换）、ADB（`input swipe` 短距离滑动模拟）、设备端（空操作）；
- Engine `_exec_scroll` 目标解析 + `_resolve_and_scroll_at_entity` 回退；
- 静态分析 `workflow_references` 新增 `Scroll` 引用收集；
- 新增 8 个解析测试 + `_FakeInput.scroll_screen` 测试桩。

### 8.4 press 指令二次扩展 + KeyStateRegistry 语法收口（`cd123b4`）

- grammar：press 语法补齐 hold/release/press 三态语义；
- actions：`_exec_press` 实现落地，`KeyStateRegistry` 改为懒初始化；
- `system.py` 新增 `check_env()` 系统函数——检查当前环境是否在允许列表中，不在则直接抛 `WorkflowUserError` 中止工作流，用于工作流开头快速校验环境依赖；
- `statements.py` 抽取 `_extract_wait_pairs` / `_expand_wait_clauses` 两个公共方法，把原本 `click` 专用的 wait_clause 展开逻辑（around → before + after）收拢成通用逻辑，`press` 解析直接复用；
- discovery/metadata：工作流发现与元数据增强；
- `run_control.py`：执行控制细节优化；
- 修复若干 mypy 类型标注问题；
- 测试覆盖 press 指令、key state、解析器。

---

## 九、快速开始文档补充（`ac3fbe2`, `2024803`）

- 补充模拟器 ADB 连接说明；
- 布局继承调整相关说明；
- 数据模型说明；
- README 新增用户手册入口；
- 后续第二次更新补充细节（`2024803`，19 行改动）。

---

## 十、"目标环境"更名与布局/备战方案细节

### 10.1 「工作环境」统一更名为「目标环境」（`de0f70d`）

- `main_window`：标签重命名 + 新增环境说明 tooltip（桌面/安卓/模拟器各自的注意事项）；
- `settings_dialog`：Tab4 标题与说明文案同步更新；
- quick-start 补充「目标环境」设置说明 + 模拟器画面比例提示。

第 2 节引入的"工作环境"概念，在本轮验证阶段被判定为命名不够直观，统一改名"目标环境"，UI 标签、tooltip、设置页、文档同步。

### 10.2 布局新增 desc 描述字段（`7c2e8f2`）

- `LayoutDef` 新增 `desc` 字段；`layout_manager` 加载/保存时处理；
- `layouts.yaml` 补充 `desc` 条目；
- 主窗口布局选择器旁新增描述标签，`run_control` 切换布局时同步更新。

### 10.3 备战方案默认视图模式改为 half（`cf7dec4`, `e9d583f`）

- 默认 `view_mode` 从 `sidebar` 改为 `half`（半栏模式），更符合日常使用习惯；
- 补充 `test_view_mode_persisted_in_ui_state` 测试匹配新默认值。

---

## 十一、登录页退出流程优化（`c14ba80`）

- `game_login_page` 场景新增 `exit`/`back_to_login` 区域定义；
- `_exit_to_login.wf` 二次确认改用 `scan` 检测弹窗按钮，而非固定等待；
- 雀鹰小将 `game_login_page` 布局同步更新。

---

## 十二、桌面小屏布局（`859f060`）

新增「桌面小屏」布局目录，适配小尺寸桌面窗口的全场景布局文件（26 个场景），覆盖活动、背包、装备、挂机、训练、外观、战斗等全部页面。

---

## 十三、运行日志级别筛选与环境选择器整合（`2e398c7`, `bd719c4`）

- 日志级别下拉新增 WARNING(30)、ERROR(40) 选项，按数值升序排列；`_log_append` 级别检测从 DEBUG/INFO 二选一扩展为 ERROR/WARNING/DEBUG/INFO 四级检测；
- 顶部「用户/环境/布局」标签去掉冒号后缀；环境 `?` 按钮增加 `clicked` 信号，点击弹出 `QMessageBox` 显示说明（此前只有 hover tooltip）；
- 日常/批量/调律三个 Tab 各自重复的环境下拉收敛到主窗口顶部第一行统一控件（`用户 [combo] 环境 [combo] (?) 布局 [combo] [desc]`），消除三处重复代码。

---

## 十四、导航策略拆分 + 武库拦截 + 回收重构（`dae7090`）

- `navigation.wf` 各导航函数按 desktop/android 拆出独立分支；
- 新增 `route_strategy.py`，把路径策略抽象出来；Android 离开调律页需要额外收起弹窗，桌面端不需要；
- `recycler.py` 回收流程拆分为 `_try_open_recycle` + `_handle_recycle_confirm` 两步；
- `auto_tuning.py` 新增武库装备前置拦截：分组部位遇到武库跳过，非分组部位扫到底；
- 新增 `GROUPED_SLOTS`/`SCAN_FIELDS`/`min_level` 覆盖机制，`tuning_context.py` 新增 `min_level` 字段；
- 测试覆盖武库跳过、扫描到底、指纹隔离等场景。

---

## 十五、其他收尾

- 窗口扫描排除自身进程窗口（`f532af9`）：`list_visible_windows()` 增加 `GetWindowThreadProcessId` 过滤，跳过律匠自身进程窗口，避免自动匹配窗口策略时误命中；
- 布局坐标校准 + 场景新增 status/func_area 区域（`ed8f7c6`）：默认布局多个场景坐标微调；装备场景新增 `status` 区域用于武库检测；`equip_detail` 新增 `func_area` 区域作为桌面端调律/回收入口；`bugan_detail` 新增 `cat_6` 分类；
- `scan_wallet` 工作流小幅更新（`4e63d93`）；
- 快速上手文档再次更新（`2024803`）；
- `daily_jianghu` 情境编辑等待策略调整（`254328a`）：`goto_qingjing`/`goto_editing` 改用 `@secondary_confirm` 替代 `page_refresh`，避免页面渲染未完成时过早操作；
- ruff 清理未使用 import（`f52f8aa`）：`win32_keyboard.py` 移除 `sys`/`logger`，`scene_panel_editor.py` 移除 `QLabel`。

---

## 十六、结果

- 本轮提交约 28 commits（`e8f5a72` ~ `f52f8aa`，其中末尾一批 commit 时间戳已跨入 08-22 凌晨，为同一开发时段延续）；
- 完成并标记发布 v0.4.3；
- DSL 补齐 `press`/`scroll`/`else if` 三项能力，`env()`/`is_send()`/`is_post()`/`check_env()` 系统函数落地；
- 桌面窗口模式后台 PostMessage 投递回归被标记但尚未修复，留待后续排查。

---

## 十七、关键设计决策（用户确认）

1. **目标环境（env）作为一等配置**：`envs` 列表 + `env()`/`check_env()` 内置函数，DSL 条件分支可按运行环境（桌面/安卓）走不同逻辑；导航逻辑随后按此下沉到各函数内部，调用方无需关心环境差异。
2. **"工作环境"→"目标环境"改名**：命名验证阶段发现原名不够直观，统一改名并补充 tooltip 说明桌面/安卓/模拟器差异。
3. **面板行列配置收拢至布局级**：`rows`/`cols` 不再属于场景定义，而是布局级配置，避免同一场景在不同布局下网格设置冲突。
4. **PostMessage 回归先标记不抢修**：窗口模式后台投递严重回归，先在代码与 README 中明确标注不可用，避免用户误用，修复留待专门诊断。
5. **武库装备前置拦截按分组区分策略**：分组部位遇到武库跳过，非分组部位扫描到底，避免调律流程在武库装备上卡死或误处理。
