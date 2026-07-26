# TODO

## 打通「游戏配置」与「调律规则」两个对话框

现状分析（2026-07）：两者是上下游关系——attributes.yaml 的 `_aliases`
是标准词条全称的唯一权威源，调律规则 YAML 里的全称词条（武学增伤 /
对首领单位增伤 / 神力词条等）必须与之一致，否则判定静默失配。

### 已完成（2026-07 调律规则标准词条化重构）

1. ~~**运行时校验打通**~~：已落地——删除 SYMBOL_VOCAB/SYMBOL_MAP 符号层，
   `TuningRuleManager` 校验时引入 `AttrRuleManager.get_normal_affix_names()`
   标准词条全集，越界名 → 保存拒绝 + 状态栏报错；启动加载失配 →
   logger.error + 跳过该规则文件。
2. ~~**调律规则 UI 词条候选动态化**~~：已落地——`SYMBOL_ORDER` /
   `DIVINE_CANDIDATES` 等硬编码候选删除，全部改从标准词条全集取
   （AffixPickerDialog 统一选择入口）；同步完成：去 variants 层、
   heal 拆为 heal_pure/heal_fire、规则可新建/删除（Tab 增删）。

### 待办

3. **交互互通**：两对话框右上角互挂入口按钮（属性 ↔ 规则 ↔ 验证 成环）；
   进阶：属性管理重命名词条时扫描调律规则引用并提示批量替换。
4. **weapons 表引用流派配置**：调律规则各 YAML 的 weapons 表仍各自
   硬编码（旧式 `{main: {武器: 词条}, sub: [武器]}`），未来改为引用
   attributes.yaml 的 `schools` 流派配置；新增流程收敛为：装备配置加武器 →
   指定武学增效加词条 → 流派配置绑定，调律规则零改动。
