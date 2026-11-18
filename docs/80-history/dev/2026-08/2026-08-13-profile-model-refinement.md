# 开发日志 2026-08-13（八）

> 接续本日《玩家数据模型重构（上）：四模型体系与 SQLite 迁移》。
> 本轮主题：**玩家数据模型重构（下）——三模型重命名、CAS 写入、来源词表、DSL 集成**。

---

## 一、三模型重命名与写入语义规范化

- 重命名三模型 `daily→quota`/`realtime→regen`/`resource→stock`，并统一 `change_type` 与 detail 格式：常量 `MODEL_DAILY`/`REALTIME`/`RESOURCE` → `MODEL_QUOTA`/`REGEN`/`STOCK`；类名 `DailyKeyDef`/`RealtimeKeyDef`/`ResourceKeyDef` → `QuotaKeyDef`/`RegenKeyDef`/`StockKeyDef`；标签 日常→配额/实时→再生/资源→存量；`change_type: manual → override`；detail 统一为「操作:信息」格式（`reset:0`/`regen:+X`/`delta:+X`/`override:V`/`sync_from:K`）；内部函数 `_compute_realtime_value → _compute_regen_value`、`_count_daily_regens → _count_quota_regens`；修复导入路径 `config.profile.models → config.profile_models`；迁移脚本新增 `_TYPE_RENAME` 映射旧 JSON 类型名；SQLite 开发数据已手动迁移（`8fa319d`）；
- Stock 模型支持增减幅度 + 修正提醒阈值备注为 `>=`——`StockKeyDef` 新增 `steps` 字段，右键菜单支持 Stock 自定义增减幅度，定义面板 Stock 编辑对话框新增增减幅度输入，Stock 无详情时摘要留空（`af8f1af`）；
- Profile 引擎写入策略从盲目 upsert 改为 CAS 模式，修复首次初始化丢失，CAS 失败日志升为 warning（`90bceab`）；
- 分钟级 regen 写入语义规范化——小数表示时间进度，存储取整 + `updated_at` 回拨（`d298fff`）；
- 分钟/小时级再生值不再每次 tick 落盘——跳过持久化不触发 modified 和 UI 刷新，tooltip 展示展示值/精确值/存储值三行，用户手动操作走 CAS 写入（`e400744`）；
- regen 模型拆分为 realtime/boundary 两种类型（`cca664c`）；
- 元数据定义对话框列结构重构——cap/soft 提升为基础字段，上限/周期/来源/用途独立成列，表头加粗（`140576f`）；
- 移除 `migrate_from_legacy` 死代码（`b5ecd16`）。

## 二、总览交互增强

- 总览 cell 右键菜单增强——覆写入口与历史记录查看器：双击 cell 编辑改为计算 delta 走 action 路径触发 Quota→Stock sync，右键新增「覆写...」直接设定绝对值走 override 路径不触发 sync，右键新增「查看历史记录」弹出 QDialog 展示最近 50 条变更（时间/类型/旧值/新值/详情）（`8243c8c`）；
- 总览交互增强——sync 上限锁定、regen 写入优化、详情三列并列：quota `sync_to` 达硬上限时禁止编辑，clamp 后值未变时跳过写入和 history 记录，regen tick 仅整数部分变化时写入 DB，其他信息 tab 三模型横向并列（配额|存量|再生）（`954cf89`）；
- 历史表新增 source 列与来源词表管理（`86dd7f3`）；
- profile-tab 快捷菜单/自定义对话框/历史表适配 source 列（`40f80dd`）；
- `_sync_to_stock` 日志格式 `:+d → :+g` 兼容 float delta（`f7c08be`）；
- quota 周/月周期按进度着色 + tooltip 显示距重置时间（`73a7873`）；
- `profile_tab` 拆分为 `ui/profile` 子包（`18d1378`）；
- 档案概览右键菜单修复——角色名列右键菜单允许右侧新增列，移除 `menu.setEnabled(False)`（`8c8b4fe`，同一提交另含 `fast_test.py` 迭代测试脚本，见项目整理篇）。

## 三、来源/用途与触发器

- Profile 触发器同步升级——`sync_targets` 多目标/倍率/方向限定/链式递归（`5cb2e79`）；
- 来源词表拆分为来源/用途——增加展示来源、减少展示用途、覆写叠加展示（来源在上）（`72b7d9c`）；
- 修复审查问题——cell 编辑按 delta 方向选词表，覆写对话框标签改来源/用途，roundtrip 测试补 uses（`0c3fce0`）；
- 数值输入对话框改用 QLineEdit + 校验器——增减空白、覆写预填、空值友好提示（`89190dd`）。

## 四、DSL 集成与读写管线统一

- DSL 内置函数 `profile_get`/`set`/`inc` + `updated_time` 迁移 v3（`70f1f3b`）；
- Profile 读写管线统一 + decimal 小数支持——新增 `profile_ops.py` 共享读写管线（`profile_read`/`profile_read_all`/`profile_action`），DSL `profile_get`/`profile_all`/`profile_set`/`profile_inc` 统一委托，UI `_adjust_value` 委托 `profile_action` 消除重复写入逻辑，`KeyDef` 基类新增 `decimal` 属性支持小数输入（心力/通宝/宝钱设为 decimal），新增 `scan_wallet.wf` 扫描钱袋货币工作流，参数面板默认范围改为 0-999999（`c540fbe`）；
- 更新玩家档案需求文档的实施进度与已完成功能说明（`b856f55`）。

---

## 结果

- 三模型完成语义重命名（quota/regen/stock），写入策略统一为 CAS 模式；
- Profile 与 DSL 打通读写管线，工作流可直接读写玩家档案数据；
- 本篇 commit 约 20 个，是本日体量最大的专题之一。

---

## 关键设计决策（用户确认）

1. **三模型语义重命名**：daily/realtime/resource → quota/regen/stock，配合 `change_type` 与 detail 格式统一，术语与游戏内语义（配额/再生/存量）对齐。
2. **写入策略改为 CAS**：替代盲目 upsert，避免首次初始化丢失，冲突时记录 warning 而非静默失败。
3. **覆写与增减两条路径分离**：手动编辑走 delta+action（触发 sync），右键覆写走 override（不触发 sync），语义互不干扰。
4. **Profile 读写收拢到 `profile_ops.py` 共享管线**：DSL 内置函数与 UI 操作统一入口，避免重复写入逻辑。
