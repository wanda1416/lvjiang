# 开发日志 2026-08-13（四）

> 接续本日《调律配置页与调律工具增强》。
> 本轮主题：**DSL 语言能力扩展——整面板扫描、返回值绑定、静态预检、编辑器工具链**。

---

## 一、面板扫描与访问语法

- 整面板扫描与 `[r][c]` 数字 key 访问——单格结果统一为 key 过滤语义（`fca9a24`）；
- 整面板 `scan`/`recognize` 支持 `by` 子句（`241b2ff`）；
- `recognize as rich with` 子句 + 扁平 dict 结构（`466b5fe`）；
- 新增 `split` 内置函数 + 统一列表索引语法为 `[0]`（`bd24dd8`）；
- `wait stable on [scene].[region]` 区域限定稳定检测（`9e433b9`）。

## 二、返回值与调用语义

- `call \=proc()` 返回值绑定语法（`0a7ef17`）；`call proc() as \` 语法（`b21255c`）；
- engine 捕获顶层 `return` 值（`8daaf67`）；
- 子过程异常返回约定 `return -1`（`92a8e35`）；
- 主流程 `return -1` 供批处理感知，明确默认返回 `null`（`0b194b0`）；
- 修复 `call` 语句传递变量参数时值丢失（`c225a00`）；
- DSL 引擎作用域隔离与 `default` 递归解析修复（`bf3d54c`）。

## 三、其他语言特性

- `validate_only` 静态预检 + 系统工作流引用门禁 + 上机预检脚本（`46558c6`）；
- 新增 `screenshot` 截图指令（`28c0bd6`）；
- `where` 子句 + 字符串拼接 + `log` 表达式支持（`d30d3cc`）；
- `count_key` 重命名为 `count_nonempty`（`8a81571`）；
- `wait stable` 语法 + 命名延迟 `@` 前缀（`f829e93`）；`wait stable` 新增 `least` 参数（最低等待期）（`37d246d`）；`wait stable` 语法扩展与超时语义变更（`76425f5`）；`wait stable` 支持 `@` 命名延迟和 `\` 引用（`a799d29`）；
- DSL 语法允许块语句内空行（`da98de3`）；
- `log` 指令增加级别支持（debug/info/warn/error）（`f08f36a`）；
- Panel 校准模式扩展与 DSL 指令扩充——Panel 新增 `calibration` 字段（auto/even/image 三模式）与 `scroll_direction` 字段（vertical/horizontal/both/none）（`8f98ae5`）；
- `PanelRef` 的 scene/panel 支持 `$var` 动态引用——语法层 `const_or_var` 早已允许，但运行时把 scene/panel 当裸字符串使用，遇到 `VarRef` 会查布局失败，补齐三处运行时解析，与 `SceneRef` 动态引用语义对齐（`94df251`）。

## 四、subcall 基础设施

- DSL subcall 桥接引擎能力（`1371d03`）；
- 导航逻辑迁移至 DSL subcall（`0b9cb9f`）；
- `equip_analysis.wf` 改用 subcall 与局部收集模式（`6a570f6`）；
- 合并导航 subcall 为 `navigation.wf`（`5c7d1b0`）；
- 工作流 `log` 拼接迁移为 `+` 拼接（`d9ceeec`）。

## 五、编辑器工具链

- VS Code / Qoder DSL 工作流插件 Level 2（`11156e8`）；
- VSCode Level 3 语义智能与编辑器增强（`b0a2c9b`）；
- VSCode 语言服务器 + 场景编辑器面板微调（`c8af4fb`）；
- gitignore 增加 `.vsix` 构建产物（`127b455`）。

## 六、文档

- DSL 引擎作用域隔离修复对应文档（`ade141e`）；
- 数据通道文档（`36d60b2`）；
- 语法文档更新——`return` 返回值和 `call \=proc()` 语法（`a6b1e4a`）；
- 语法文档重组 + `where`/字符串拼接文档更新（`29dca56`）；
- 强调 DSL `return` 支持 dict/list 结构化返回值（`45b4cfb`）；
- DSL 函数文档拆分为 `06-1-basic-functions`/`06-2-system-interaction`/`06-3-game-functions`（`f9f5825`，与场景编辑器脚本操作增强同一提交）。

---

## 结果

- DSL 语言层本轮新增/调整语法点接近 20 处，涵盖面板访问、返回值传递、等待语义与静态预检；
- 编辑器侧工具链（VS Code 插件）从 Level 2 演进到 Level 3。

---

## 关键设计决策（用户确认）

1. **单格结果统一 key 过滤语义**：整面板扫描结果通过 `[r][c]` 数字 key 访问，与命名 key 语义统一。
2. **子过程异常返回约定 `return -1`**：为批处理感知失败提供统一的返回值约定，无需额外异常协议。
3. **subcall 取代旧 goto 拼接方式**：导航、装备分析等公共逻辑收敛为 subcall 调用，替代此前分散的 wf 拼接。
