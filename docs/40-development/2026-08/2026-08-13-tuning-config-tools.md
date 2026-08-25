# 开发日志 2026-08-13（三）

> 接续本日《调律规则引擎核心语义重构》。
> 本轮主题：**调律配置页与调律工具增强——rules_editor 改版、玩法绑定开关、进度对话框**。

---

## 一、配置页结构调整

- rules_editor 调律门槛迁基础配置页 + 行为页仅首词条列 + 布局优化（`38aec7a`）；
- `rules_editor` 目录重命名为 `tune_config`（`f8e3a9e`）；
- 部位页新增「初始跳过」与「指定调律」互斥设置（`0c8374f`）；
- 调律配置新增武器跳过规则（`c7b86d5`）；
- 更新调律基础配置（`ab3bf3d`）；修复基础配置页测试适配新默认值（`a422f9b`）；
- 调律设置页面增强（`fb56b45`）；调律配置对话框增强（`e78a339`）；
- 调律规则配置更新与规则重命名（`04ef94d`）；
- 调律规则编辑器列顺序与命名优化——列顺序调整为部位→品阶→判定规则→判定结果→首词条%→动作，重命名「判定语义→判定规则」「评级→判定结果」（`8ee8e8d`）；
- 支持规则禁用与全量规则枚举（`9a213b5`）；
- 保存时全选部位折叠为「全部」简写（`85a4bb2`）；
- `tune_full_recycle` 结束处理 + parts 全部简写（`2129d7a`）；YAML 配置改用 `parts: [全部]` 简写（`ba0174e`）。

## 二、装备回收与自选词条

- 装备回收锁定检测 + 自选词条弹窗多选（`f9b2cd9`）；
- `equip_judge_dialog` 新增模拟调律功能（`774bd21`）；
- 扫描处理新增最大连续回收次数配置——`ScanBehavior.max_consecutive_recycles`（默认 50），扫描处理页新增 SpinBox，回收补位循环改用配置值（`1d3ad5d`）；
- 调律材料缓存优化：首次 OCR + 后续轮次缓存扣减（`b949498`）。

### 2.1 玩法绑定开关

- 玩法绑定开关核心逻辑——`Playstyle` 新增可选 `switch` 字段，判定时等价于激活该开关；`referenced_switches()` 收集玩法绑定开关；解析并校验 `playstyles.*.switch` 格式（`6911ed8`）；
- 玩法绑定开关 UI 与设备端——`tune_config_widget` 玩法复选框旁显示只读绑定开关，`rule_settings_page` 新增「绑定开关」下拉列，设备端 `playstyles` 输出新增 `switch` 字段（`3240486`）；
- `keep_pvp` 拆分为 `keep_danti`（保留单体奇术增）+ `keep_wanjia`（保留玩家增效），会心通用规则中火九绑定 `keep_danti`，冠胄/胫甲条件分别使用对应开关（`7b1932c`）。

## 三、进度对话框

- 调律进度信号桥 + 浮动进度对话框——`tuning_progress_hub` 定义信号桥（`slot_entered`/`equipment_started`/`tune_round_completed`/`scan_decision`/`equipment_finished`/`batch_progress`/`tuning_finished`/`status_message`），`tuning_progress_dialog` 为非模态独立窗口，关闭即隐藏不销毁，支持 reconnect 复用，展示扫描处理决策（回收/保留/强制调律/调满后回收）（`51e6283`）；
- 进度对话框修复与增强——HTML 渲染 `\n → <br>` 修复富文本换行，词条进度固定 5 行占位消除布局抖动，`auto_open_progress` 复选框状态持久化到 `wf_configs`（`ea6d66d`）；
- 进度对话框评级语义拆分——最大预期 + 实际评级，新增 `judge_equipment_actual`（`partial=False`）与 `judge_equipment_potential` 对称，`TuningJudge` 新增 `compute_actual_rating`，仅词条满 5 条后调用（`7a78d06`）；
- 调律启动校验失败底部状态栏提示 + 百业场景注册 + 参数重命名（`1d03021`）；
- 自动调律 `wait_delay` → `wait_stable` 替换，`wait_stable` 支持命名参数（`9d6342c`）。

## 四、其他修复

- 调律报告 `resets` 字段始终初始化 + 测试数据修正（`514e28b`）；
- 调律说明文档以 `tuning_rules` YAML 为基准校准——品阶筛选明确七部位全局默认（佩放行紫色）、转律优先级补全、可用词条库加对玩家增效、副武器会意率（`f08bc23`）；
- 修复 `test_auto_tuning_flow` 测试缺少 `base_group` 导致 CI 失败（`4a0553b`）。

---

## 结果

- 本篇 commit 约 25 个，调律相关配置页与运行反馈（进度对话框、状态栏提示）基本成型；
- 玩法绑定开关打通规则配置层与设备端下发。

---

## 关键设计决策（用户确认）

1. **`rules_editor` 更名 `tune_config`**：目录与模块命名收敛，与「调律配置」概念对齐。
2. **parts 全部简写**：YAML 配置中部位全选统一写作 `[全部]`，UI 保存时自动折叠，避免逐个部位罗列。
3. **进度对话框非模态、关闭不销毁**：便于多次调律过程中复用同一窗口而不重复创建。
