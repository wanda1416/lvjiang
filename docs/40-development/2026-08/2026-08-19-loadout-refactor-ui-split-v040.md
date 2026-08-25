# 开发日志 2026-08-19

> 接续 08-18 最优毕业率组合搜索从 0 到 1 + 测试套件大精简（172s→92s）+ 导航工作流加固 + auto_tuning 进度可视化与评级修复。
> 本轮主题：**多备战方案架构重构 + UI 模块拆分（loadout/tuning）+ 满配假设选项 + 最优组合搜索性能优化（向量化）+ ADB 断连恢复 + v0.4.0/0.4.1/0.4.2 三连发布**。

---

## 一、多备战方案架构重构

### 1.1 loadout 模块落地（`4be32cc`）

- 新增 loadout 模块：`LoadoutPlan`/`LoadoutState` 数据模型 + `LoadoutRepository` 原子持久化；
- 重构 `EquipmentInventory` 门面层：基于 fp-keyed 装备池统一管理；
- 新增 `LoadoutPanel` UI：方案切换、武学绑定、折叠按钮对称布局；
- 新增 `equipment_ingest` 工作流内置函数：`write_equipment`/`write_equipped`；
- 适配 `equip_scan`/`equip_analysis` 工作流到新架构；
- 新增迁移脚本 `migrate_loadouts.py`；配套测试覆盖 repository/panel/migrate/ingest。

### 1.2 装备页功能增强与 bug 修复（`16b2458`）

- 修复槽位编辑遗留孤儿指纹 bug：`update_equipped_mock` 先写新后清旧；
- 武器智能路由：按流派武器类型自动分配主/副武器槽，不匹配时拒绝；
- 方案创建强制绑定双武学：`_PlanCreateDialog` + `create_plan` 校验；
- 已装备装备不再显示在未装备区：`_rebuild_grid` 过滤 `active_plan_fps`；
- 装备 tab 重命名为「装备」，新增来源筛选（全部/背包/模拟）。

### 1.3 信号链路修复与性能优化（`3393256`）

- 移除 `LoadoutPanel` 轮询机制（`_poll_revision`），完全依赖进程内信号量触发刷新；
- 删除 `refresh()` 末尾冗余 `equipment_changed` emit，消除级联重复刷新（3 次→2 次）；
- 信号桥改延迟发射 + 150ms 防抖合并：工作流线程零阻塞，批量写入仅触发一次全量刷新。

### 1.4 收尾修复（`1f9b752`）

- `repository.py`：循环变量 `fp` 重命名为 `eq_fp`，消除对外层同名变量的遮蔽；
- `loadout_panel.py`：清理布局时防护 item 为 None 的情况；
- 修复两处测试文件的 ruff import 排序告警（I001）。

---

## 二、最优毕业率组合搜索：多项优化 + 性能 + 正确性修复

### 2.1 布局重构（`1ee51ab`）

- 支配剪枝/调律规则/搜索按钮移至窗口顶部；
- 候选装备改为 4×2 网格布局，每个部位独立滚动；
- 候选装备与最优结果拆分为两个 Tab，搜索完成自动切换；
- 对话框最小尺寸调整为 800×550。

### 2.2 功能多项优化（`db3dedc`）

- 新增排除模拟装备选项，默认勾选；
- 候选装备悬浮显示详细信息（词条数值+定音）；
- 新增弓玦套装选择器，支持动态切换；
- 调律规则+玩法合并为单一下拉框（规则名-玩法名）；
- 新增不应用规则选项，调律规则改为前置过滤。

### 2.3 满承音/满定音/满等级假设选项（`b4dbd86`）

- 新增 `apply_hypothetical_caps` 函数：满承音（词条→承音上限）、满定音（定音→100%）、满等级（低于赛季最高等级视为承音装备）三种假设；
- 战斗属性页和最优组合搜索均支持三种假设选项；
- 攻击属性（min/max outer/mingjin/lieshi/pozhu/qiansi/wuxiang）显示时四舍五入取整；
- 装备筛选配置从全局 session.json 迁移到用户级 loadout 存储（`LoadoutState.ui_state`），切换用户时自动隔离；
- 「支配剪枝」重命名为「智能筛选」。

### 2.4 性能优化：对象操作 → 原地累加 → 向量化（`8951fe0`, `9ee639c`）

- `8951fe0`：`CombatAttributes` 新增 `__iadd__` 原地加法，避免内层循环创建中间对象；进度更新改前台定时轮询（1 秒），后台仅更新计数器；调律规则改为前置过滤，搜索时直接使用用户勾选的装备；
- `9ee639c`：内层循环彻底改为纯数组累加，避免反射开销（fields/getattr/setattr），预计算装备贡献向量；
  - 修正 precision 基准计算：使用硬编码常量 `PRECISION_BASE/100`（0.65），而非基础面板值；
  - 修正 `BONUS_PERCENT_FIELDS` 与 `extra_attrs` 抗性键的 `buff_divisor` 除法逻辑（`apply_bonus_resistance` 默认 base=0.0），新增 `_FIXED_ATTR_NAMES` 防止固定字段被多除；
  - 用 200 组随机属性组合差分验证，向量化路径与 `build_graduation_attrs` 逐维计算 0 偏差；256 组合穷举对照测试全部通过。

---

## 三、UI 模块拆分：loadout / tuning 子包

### 3.1 战斗属性配置区布局迭代

- 布局优化（`fe3a2ff`）：弓玦套装改名弓玦，统一「基础属性」「计算方案」下拉框宽度为 130px，编辑按钮独立并改名「编辑基础属性」；
- 显示选项与按钮重命名（`788043d`）：新增「显示选项」区域（仅展示抗性结果复选框，状态持久化到 session），按钮重命名（最优组合→计算最优组合，创建装备→创建模拟装备）；
- 展示模式规范化（`594cc56`）：定义 `DISPLAY_MODE_FULL`/`HALF`/`HALF_COMPACT` 三种常量；半屏退化为单列布局并过滤零值攻击属性行；修复最优组合搜索对话框 UI 卡死（后台线程 emit 但 sender 亲和性为 UI 线程，改用 `QueuedConnection`）；
- 自适应布局优化（`4a7f455`）：退化模式阈值调整为 38 汉字宽度（532px），迟滞区间 532-568px；穿透类数值强制保留两位小数（force_decimal），攻击属性保持取整；
- 半屏/全屏切换修复（`e1a5e60`）：从退化模式切回全屏时恢复卡片网格为多列布局，`QTimer.singleShot` 延迟宽度检查避免布局未稳定时误判；
- 视图切换布局简化（`1827f14`）：删除各 shell 顶部独立控制条，展开模式切换按钮移至首行工具栏，节省 34px 垂直空间。

### 3.2 大规模模块拆分（`533df47`）

- `ui/` 下 12 个扁平文件迁移至 `ui/loadout/` 与 `ui/tuning/` 子包；
- `combat_attrs_tab.py`（1974 行）拆分为 5 个 Mix-in 模块：cards/layout/graduation/play_style_dialog/attrs_tab；
- DPS/毕业率数据所有权从 `CombatAttrsTab` 迁移到 `LoadoutPanel`，`graduation_updated` 信号携带结果对象，消除跨层级 getattr；
- 提取 `PlanCreateDialog` 为独立文件；修复半屏紧凑模式用户切换后网格错位 bug。

### 3.3 Profile / 装备状态页二次拆分（`73fee58`）

- `overview.py` → `column_management.py`（`ProfileColumnMixin`）+ `cell_editing.py`（`ProfileCellEditingMixin`）；
- `status_tab.py` → `cards.py`（`_StatusTagBar`/`_SlotCard`/`_CompactEquipCard`）。

### 3.4 拆分收尾修复

- `cards.py` 延迟导入 `EquipStatusTab` 修复 NameError 崩溃（`8e163e2`）；
- 武器槽位分组两轮调整：先改为始终按类型严格分组+组内等级降序（`2e11692`），随即回退为保持用户排序顺序、不强制覆盖等级降序（`4f667c4`）；
- 移除 `cards.py` 未使用的 `TYPE_CHECKING` 导入（`38888ae`）。

---

## 四、工作流重构

### 4.1 登录流程抽离与战令领奖（`2c216b4`）

- `_select_role.wf` 抽离 `login_to_main_page()`（加载等待+主页检测+弹窗处理）；
- 新增 `claim_zhanling_reward()` 战令一键领取，战令检测与关闭活动弹窗互斥；
- 新增 `zhanling_detail` 场景与布局；`game_login_page` 补充 `zllj_btn` 区域。

### 4.2 批处理账号切换解耦（`9b7a2dd`）

- `_switch_account` 剥离 `_select_role`，职责收窄为切账号→登录→角色选择页；
- `prepare_item.wf` 三态分支简化为两步串行：判断切账号 → 统一选角色；
- `navigation.wf` 新增 `nav_main_to_item()`，`daily_jianghu` 醉意导航改用公共函数；
- 新增 `weekly_baiye_freight.wf` 周常百业货运（WIP）；
- `daily_zhayu` → `gather_zhayu` 重命名。

### 4.3 scan 类工作流统一前缀，命名反转当日纠正（`de280f6`, `256874a`）

- `de280f6`：`equip_scan` → `scan_equipped`，`equip_analysis` → `scan_unequipped`；同时修复 grammar↔engine 循环导入（`WorkflowEngine` 改 `__getattr__` 延迟加载）；
- 命名语义实际反了（`scan_equipped` 应对应已装备，`scan_unequipped` 应对应未装备，首次改名搞反）；`256874a` 当天互换两文件内容修正：`scan_equipped` 逐槽读已装备详情面板，`scan_unequipped` 背包全量滚动扫描未装备。

---

## 五、新场景：活动火云

- 新增活动火云场景配置，更新角色详情场景配置（`81867e0`）；
- 对应布局更新（`91f8e29`）。

---

## 六、ADB 断连暂停恢复机制（`582d840`）

- ADB 超时/断连时捕获异常，显示醒目非阻塞横幅，暂停工作流线程；
- 等用户手动重连设备并点击「恢复」后，重试失败命令继续执行；
- `resume_event` 提升到主窗口级别，不随 device 断连/重连而丢失；
- 重连完成时若工作流正阻塞在断连等待上，刷新运行中引擎的截图/输入后端引用，避免旧 scrcpy 流已死导致 `capture()` 返回陈旧帧；
- 恢复后加 3 秒稳定等待（响应 F10），再重试命令；单行紧凑横幅，点恢复直接隐藏，F10 停止时唤醒并隐藏。

---

## 七、脚本配置与 Profile UI 优化（`6b68473`）

- `script_config_dialog`：全部列 `ResizeToContents`，最低三汉字宽度；
- Profile 引擎（`profile_db`/`profile_ops`）增强；
- `combat_attrs_tab`/`loadout_panel` 装备 UI 调整。

---

## 八、v0.4.0 / v0.4.1 / v0.4.2 三连发布

### 8.1 v0.4.0（`3fc0d71`）

- 版本号升级 0.3.1 → 0.4.0；
- 40 commits（自 v0.3.1），306 files changed（+274,235 / -53,263），pytest 1918 例全绿；
- 涵盖最优毕业率组合搜索、满承音/满定音/满等级假设、ADB 断连恢复、DSL 全量匹配与范围索引、UI 模块大拆分等。

### 8.2 v0.4.1（`778d0d1`）：武学绑定与关于对话框修复

- 武学绑定允许单独设置主/副武学，不再强制同时绑定；
- 装备状态页流派获取改用 `_get_current_school()`，修复依赖 `graduation_context` 可能为空的问题（`685d1f5`）；
- 关于对话框新增「本项目完全开源免费」声明；
- 1 commit（自 v0.4.0）。

### 8.3 微信交流群二维码（`61c2755`）

- 用户手册首页新增微信交流群二维码。

### 8.4 v0.4.2（`688973f`）：反馈对话框与导航增强

- 新增 `feedback_dialog.py`：微信二维码 + GitHub Issue 入口，帮助菜单新增「反馈与建议」入口；
- `navigation.wf`：`nav_main_to_bag` 新增二次确认（通过「整理」按钮区域验证），tab 识别失败时确认失败则返回 -1，上游调用方统一传播导航失败；
- `bag_equip_detail` 新增「整理」/「制造」两个可点击区域；
- 二维码图片统一至 `data/image/`，用户文档与应用程序共享同一份资源，消除重复文件（`2c2f8e9`）；
- 2 commits（自 v0.4.1）。

---

## 结果

- 本日（含 08-20 凌晨收尾）提交 35 commits；
- v0.4.0、v0.4.1、v0.4.2 三个版本发布完成；
- 最优毕业率组合搜索完成性能优化闭环：对象操作 → `__iadd__` 原地累加 → 纯数组向量化；
- UI 层完成 loadout/tuning 子包拆分，`combat_attrs_tab.py` 从单文件 1974 行拆为 5 个 Mix-in 模块。

---

## 关键设计决策（用户确认）

1. **装备指纹作为跨方案唯一身份键**：`EquipmentInventory` 门面层改为 fp-keyed 统一管理，多备战方案共享同一装备池，仅方案内记录装备是否在场/已装备。
2. **装备筛选状态从全局迁移到用户级**：迁移到 `LoadoutState.ui_state`，切换用户账号时自动隔离筛选条件，避免串账号误操作。
3. **最优组合搜索三阶段性能优化，每阶段用差分测试验证正确性不回退**：对象操作 → `__iadd__` 原地累加 → 纯数组向量化，最终阶段附带 200 组随机组合差分验证 + 256 组合穷举对照。
4. **ADB 断连处理为暂停而非报错终止**：横幅提示 + `resume_event` 挂在主窗口级别，重连后刷新截图/输入后端引用再重试失败命令，最大限度保留已执行进度。
5. **scan 工作流命名先出错后当场纠正**：`de280f6` 重命名语义搞反，`256874a` 在同一天内互换修正，未流入下一版本。
