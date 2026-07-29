# 开发日志 2026-07-27

> 接续 07-26 调律规则配置化引擎落地。
> 本轮主题：**自动调律链路补全 + 区域识别重构 + 品阶反查修复**，以及 UI
> 周边的配套打磨（panel 可见比例、脚本录制、录屏截屏、遍历容错）。
> pytest ~540 → **~600+ 例全绿**。

---

## 一、调律规则 v2（`4647a85`）

### 1.1 背景

07-26 落地的规则 YAML 在实战中暴露几处表达力不足：

- 武器部位规则需要按「武器类型 × 增伤词条」二维展开，原 schema 只支持一维；
- 三档条件（顶级/优秀/一般）需要更灵活的组合表达；
- 全局 PVP 开关需要跨规则统一；
- 部分规则 key 命名不规范。

### 1.2 改动

- 武器规则表新增 `weapons.{key}.main` 二维结构（武器类型 → 增伤词条映射）；
- 条件组支持三档（top/excellent/normal）独立定义；
- 全局 `keep_pvp` 开关提升为规则级公共字段；
- 规则 key 重命名：`lieshi_big` → `lieshi`、`huixin_big/small` 保留。

---

## 二、click/drag 增加 wait 语法糖（`0a507dc`）

DSL 指令 `click` / `drag` 新增三个可选等待参数：

- `wait_before`：执行前等待；
- `wait_after`：执行后等待；
- `wait_around`：前后都等待。

语法糖统一走 `wait_delay(name)` 路径，命名等待从 workflows.yaml 加载。

---

## 三、修正 xinde_exchage 拼写错误（`4df9efc`）

- 场景 key `xinde_exchage` → `xinde_exchange`（心得采买场景）；
- 全库 grep 同步（workflows YAML + 工作流 Python + 测试）。

---

## 四、自动调律链路补全 + 区域识别重构 + 品阶反查修复（`d380546`）

### 4.1 背景

自动调律工作流（auto_tuning）此前只通了「潜力判定」半链路，实际调律执行
（进调律页 → 添加狗粮 → 点调律 → 收结果 → 狗粮返还二次弹窗）尚未接入。
同时区域识别（equip_type 文本 → 部位/类型）与品阶反查（base_attr 数值 →
品阶/等级）存在多处 bug。

### 4.2 链路补全

`AutoTuningWorkflow` 新增/完善：

- `_tune_once()`：进调律页 → 选材料 → 点调律 → 等待动画 → 收结果；
- `_on_tuning_result()`：处理调律结果弹窗，兼容狗粮返还机制（关闭弹窗后
  补扫补关二次弹窗，`65c5e06`）；
- `_final_judge()`：词条已满装备的终局判定；
- `_on_junk_blank()` / `_on_equipment_done()`：预留空接口（垃圾胚子处理 /
  调律后处理，待后续落地）。

### 4.3 区域识别重构

`EquipmentParser._parse_equip_type()` 重写：

- 类型段单字命中（「冠」→冠胄、「胸」→胸甲、「胫」→胫甲、「腕」→腕甲、
  「环」→环、「佩」→佩）；
- equip_type 缺失时由基础属性值反查回填部位 + 类型（`8a45445`）；
- 品阶判定改为区间两端精确匹配（`a8c15b9`），补充武器紫/蓝基础属性数据。

### 4.4 品阶反查修复

`EquipmentParser._infer_quality()` 修复：

- 旧逻辑用 `<=` 比较，区间边界模糊；
- 新逻辑按 `attributes.yaml` 声明的精确数值区间匹配（`min <= value <= max`）；
- 等级/类型缺失时由数值反查回填 equip.level / equip.type（仅数值唯一对应
  部位时回填；冠/胫/腕同值无法区分）。

---

## 五、panel 新增 min_visible 行可见比例参数（`1c7a71b`）

### 5.1 背景

背包格子面板（bag_grid）在滚动时，顶部/底部行可能只露出一部分。旧逻辑
按「整行可见」判定，容易漏读或重复读取。

### 5.2 改动

- `PanelConfig` 新增 `min_visible` 字段（0.0~1.0），默认 0.5；
- 行可见比例 ≥ min_visible 即视为有效行；
- panel 点击坐标钳位到行可见区域中心（避免点到半露行的边缘）。

---

## 六、场景编辑器面板支持编辑行可见比例（`3094cb8`）

- 面板属性表单新增 `min_visible` 编辑框（QDoubleSpinBox，0.0~1.0，步长 0.1）；
- 脏标记与未保存修改保护（关闭时弹 QMessageBox 确认）。

---

## 七、品阶判定改为区间两端精确匹配（`a8c15b9`）

详见 §4.4。补充武器紫/蓝基础属性数据至 attributes.yaml。

---

## 八、调律遍历支持整行列遍历 + 滚动指纹漂移容错（`376d3c7`）

### 8.1 整行列遍历

`bag_traversal/` 包新增列遍历支持：一行多列（如 6 列背包）逐列处理，
列间切换自动点击对应格子。

### 8.2 指纹漂移容错

滚动过程中，同一物理行的指纹可能因 OCR 抖动而漂移。新增：

- 漂移诊断日志（打印前后指纹差异）；
- 容错阈值（编辑距离 ≤ 2 视为同一行）；
- 漂移超过阈值时触发二次确认（再读一次）。

---

## 九、新增「跳过实际调律」临时测试开关（`de2f363`）

- 自动调律工作流新增 `skip_tuning` 开关（session.json 持久化）；
- 开启后：潜力判定 → 值得调律 → 进调律页 → **不点调律按钮** → 直接退出；
- 用途：验证潜力判定准确性，不消耗材料。

---

## 十、兼容狗粮返还机制（`65c5e06`）

- 调律结果弹窗关闭后，狗粮可能返还背包（游戏机制）；
- 工作流补扫补关二次弹窗（狗粮返还提示），避免卡死。

---

## 十一、宏录制迁出主窗口 + 新增「脚本录制」对话框（`cb64163`）

- 主窗口 `macro_recorder.py` 迁出为独立对话框 `script_record_dialog.py`；
- 对话框支持录制 click/drag/wait 序列，导出为 .wf 脚本。

---

## 十二、新增录屏/截屏采集面板（`4dce2f2`）

- 主窗口新增「采集」Tab（`capture_panel.py`）；
- 支持录屏（mp4）/ 截屏（png）/ 回放；
- 数据落盘 `data/video/` 与 `data/local/`。

---

## 十三、词条已满/垃圾胚子不再收集 tuning_reports（`e60277e`）

### 13.1 背景

自动调律遍历几百件装备时，词条已满（5 条）和垃圾胚子（潜力判定不值得）
的 report 会淹没真正有价值的调律报告。

### 13.2 改动

- `AutoTuningWorkflow._process_equipment()` 调整：
  - 词条已满 → 仅做终局判定，不收集 report；
  - 垃圾胚子 → 不收集 report；
  - 实际调律的装备 → report 追加到 `output["tuning_reports"]`。

---

## 十四、equip_type 缺失时由基础属性值反查回填（`8a45445`）

详见 §4.3。

---

## 十五、F10 停止时仍输出已收集的部分结果（`ed752ac`）

- 工作流 `run()` 捕获 `BaseWorkflow.is_stopped` 信号；
- 停止后仍返回 `self.output`（已收集的 tuning_reports 等）；
- 避免用户按 F10 中断后丢失全部结果。

---

## 十六、tune_results 挂进本件 report（`7c6f032`）

- 每件装备的调律结果（每轮调律的词条变化）挂到该件 report 的 `tune_results`
  字段，与装备一一对应；
- 便于后续统计报表按件聚合。

---

## 十七、武器规则表武器/增伤词条改为数据源下拉选择（`a950353`）

- 规则编辑器武器规则表（weapons 段）的武器类型 / 增伤词条改为下拉选择
  （QComboBox），数据源从 attributes.yaml `weapon_types` 动态加载；
- 避免手输错字。

---

## 十八、流派配置表单改两列等宽网格对齐（`03b9a78`）

- 流派配置表单（school_settings_page.py）改两列等宽 QFormLayout；
- 主武器 / 副武器 / 主增效 / 副增效 四行对齐。

---

## 十九、结果

- pytest 全绿；
- 全部改动已提交并推送至 `origin/master`（最新 `03b9a78`）。

---

## 二十、关键设计决策（用户确认）

1. **词条已满/垃圾胚子不收集 report**：避免淹没有效信息。
2. **指纹漂移容错**：编辑距离 ≤ 2 视为同一行，超过阈值二次确认。
3. **panel min_visible**：行可见比例 ≥ 0.5 即视为有效行。
4. **skip_tuning 测试开关**：验证潜力判定准确性，不消耗材料。

---

## 二十一、用户关键指令索引

| 指令 | 影响范围 |
|------|----------|
| 「自动调律链路补全」 | auto_tuning.py + single_tuning.py |
| 「区域识别重构」 | equip_parser/parser.py |
| 「品阶反查修复」 | equip_parser/parser.py + attributes.yaml |
| 「panel min_visible」 | region_config.py + scene_editor |
| 「跳过实际调律测试开关」 | auto_tuning.py + session.json |
| 「兼容狗粮返还」 | auto_tuning.py |
| 「宏录制迁出」 | script_record_dialog.py |
| 「录屏/截屏采集面板」 | capture_panel.py |
| 「词条已满/垃圾胚子不收集 report」 | auto_tuning.py |
| 「F10 停止仍输出部分结果」 | base_workflow.py |
| 「tune_results 挂进本件 report」 | auto_tuning.py |
| 「武器规则表数据源下拉」 | rules_editor/pool_page.py |
