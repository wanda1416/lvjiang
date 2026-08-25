# 开发日志 2026-08-13（九）

> 接续本日《玩家数据模型重构（下）：三模型重命名与 DSL 集成》。
> 本轮主题：**场景编辑器增量保存 + OCR 通用清洗架构重构**。

---

## 一、OCR 通用清洗架构

### 1.1 清洗架构重构（`4243ee2`）

- 新增 `core/ocr_cleaner.py`：通用 OCR 清洗器（单例，YAML 配置驱动）；
- 新增 `config/system/ocr_rules.yaml`：系统默认清洗规则；
- OCR 引擎层统一清洗，所有 OCR 输出自动清洗；删除旧的 `equip_parser/cleaner.py`，`parser/dingyin_parser` 移除冗余清洗，接收已清洗数据；
- 图像识别对话框拆分为多 Tab（图像识别 + 清洗规则管理）；
- 符号统一：中文/全角括号 → 英文括号；清洗规则示例：猜准率→精准率、扁武学→扇武学、经甲→胫甲、噪声删除；
- 附带修复：DSL goto 标签缺失、`tuning_base.yaml` 品阶门槛 pct 调整、冷却期检查文本修正、`data_ops.py` ruff 警告。

### 1.2 后续调整

- 清洗规则 patterns 改为 dict 格式 + 规则编辑改为表格内联（`da8344e`）；
- OCR 对话框新增画布可视化 + 重命名（`b54c0ed`）。

---

## 二、场景编辑器增量保存

- per-scene dirty tracking with incremental save（`4b5ac5c`）；
- save canvas config from active tab, not first tab（`395a787`）；
- update info label when canvas config changes（`24395c1`）；
- 场景编辑器单区域编辑模式画布优化（`c0b84be`）。

## 三、Key 重命名与视图管理

- 视图管理支持 key 重命名和顺序调整（`17c98cb`）；
- 场景/分组 key 重命名 + 对话框窗口大小记忆（`70f2443`）；
- 场景编辑器支持 region/point/panel key 重命名（`f02d7e6`）；
- 场景 key 拼音化重命名（`aaf3a38`）；
- 装备场景合并——新增 `equip_detail` 统一通用交互区域（`16781a9`）；
- 场景批量重命名统一（`ui_school_main→school_main`、`ui_waiguan_yigui→waiguan_yigui`、`ui_waiguan_qingjing→waiguan_qingjing`、`training_xinde→training_xinfa`，scenes/scenes.yaml/layouts/DSL 四处命名统一）+ 批量 Tab 三页子 Tab 重构（`76a1659`）。

## 四、识别顺序与交互增强

- 场景编辑器识别结果按场景定义顺序展示 & 窗口位置持久化（`b9e8a97`）；
- sort regions/points/panels by scene definition order on save（`78c217f`），配套 layout JSON 排序更新（`4ebc63e`）；
- 场景编辑器识别增强 + 分割器尺寸持久化（`ca169b5`）；
- 图像识别对话框 UI 重构与选框交互（`af3ef8a`）；
- 场景编辑器增强——点吸附对齐 + 分组删除修复 + CRUD 确认对话框（`95e192a`）；
- 场景编辑器脚本测试结果格式化——分两行显示返回值 + 结果集，新增 `_format_value` 辅助函数（`b98486f`）；
- 场景编辑器编辑对话框场景切换时同步更新视图下拉框（`76bb8d7`，同一提交另含江湖号令看报流程优化，见工作流篇）；
- 材料识别支持分组筛选 + 校准/滚动下拉中文化（`35a15e1`）；
- DSL 函数文档拆分与场景编辑器脚本操作增强（`f9f5825`，文档部分见 DSL 篇）；
- 双阈值告警系统场景编辑器删除分组修复部分（`28c7d70`，告警系统主体见通知系统篇）。

---

## 结果

- OCR 清洗从「各解析器各自处理」收敛为统一单例清洗器 + YAML 规则驱动；
- 场景编辑器完成增量保存（per-scene dirty tracking）与大批量 key 重命名/顺序化整理；
- 本篇 commit 约 20 个。

---

## 关键设计决策（用户确认）

1. **OCR 清洗单例 + YAML 规则驱动**：所有 OCR 输出统一经 `ocr_cleaner` 清洗，规则可在 UI 表格内联编辑，不再散落在各解析器。
2. **场景编辑器改为 per-scene 增量保存**：脏标记精确到场景级别，避免全量保存造成的性能与冲突问题。
3. **场景/分组/区域 key 统一拼音化命名**：批量重命名统一术语，服务于后续跨场景引用的一致性。
