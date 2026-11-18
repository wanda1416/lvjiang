# 生成独立调律配置与启动

先读 get_tuning_config 获取当前有效基础组、规则模板及开关注册表，结合用户目标
和计算结果生成 payload。可以参考已有配置，但不会改写它们。

生成前必须读取 get_tuning_config 返回的 rule_contract，不能靠模板中出现过的键推断接口范围。pattern_parts 列出合法 patterns 键，pattern_part_aliases 给出实际部位到模式的归并，behavior_parts 列出行为条件 parts 的合法部位。归并关系为：佩使用环的模式、胸甲使用冠胄的模式、腕甲使用胫甲的模式，主武器和副武器分别定义。这是共用判定规则，不是缺少对这些装备的支持。

归并只适用于 patterns；行为条件 parts 仍使用实际部位。例如 patterns.环 同时判定环和佩，但行为条件 parts: [佩] 只筛选佩。不得把行为条件中的佩、胸甲、腕甲也替换成归并键；不要逐个尝试无效 patterns 键来发现约定。

payload 字段为：

| 字段 | 含义 |
|------|------|
| name | 生成结果展示名称，使用用途描述，不含私人身份 |
| goal | 私人目标摘要，与规则 YAML 分开存储 |
| plan_ids | 分析引用的指定用户方案 ID |
| plan_targets | 按方案 ID 记录本次 playstyle、graduation_scheme、base_attribute 覆盖，原方案不变 |
| suggestions | 培养与换装建议文本列表 |
| base_group | 完整基础规则组对象，包含 materials、scan、tune、smart_tuning |
| rules | 1～10 条完整调律规则，包括 name、playstyles、affix_pool、patterns 和评价条件 |
| run_config | 部位、现有 switches 值、等级等运行参数 |

key 由服务分配；已有模板的 key 不会被覆盖。run_config 不接受 rules、base_group、
smart_tuning_enabled 或 skip_tuning，这些绑定由服务生成。规则玩法、词条、
材料和行为语义参考公开评价规格及 get_tuning_config 的实际结构，未知字段报错。
公共 switches 是注册表，用户 switches 才是取值，不得创建未经登记的新开关。

基础组按顺序处理 scan.rules、materials.food_rules、tune.rules，并包含智能收益
判断。保护条件应放在回收条件之前；传入、全部、自选规则的判断范围不同。回收、
调满后回收、重置耗尽回收、自动锁定与狗粮使用按最终配置执行。Agent 应说明
这些动作的效果，并依照用户的目标生成配置，不要求在页面逐项授予权限。

调用 preview_generated_tuning 检查，然后 create_generated_tuning(user, request_id,
payload) 成套保存。相同 request_id 和内容重试返回原结果，重新设计使用新 ID。
只会创建新 local 基础组和规则，用户默认选择不变。

结果与培养建议在 Agent 对话中展示。调用 validate_auto_tuning(user, result_id,
target_id) 复核配置与设备状态，再调用 start_auto_tuning；target_id 可省略以使用
当前设备，也可从 list_targets 选择另一个已连接设备。启动不执行培养建议、转律
或应用最优组合，调律沿用规则与毕业率判断；进度与历史进入律匠调律管理。

如用户明确要求保存为以后默认使用的选择，可调用 update_tuning_config，并携带
get_tuning_config 返回的修订。仅生成和启动不保存默认；保存后可从原自动调律
入口启动。切换处理用户只改工具 user 参数，不改变 MCP 连接配置。

## 预检结果与生成方案启动

`validate_auto_tuning` 返回 `smart_tuning.required/active/plans/reason`。
`plans` 包含参与与排除的方案、原因和可计算的基线。基础组启用毕业率判定却没有
有效方案，或明确指定的目标方案未全部参与时，`ready=false`；先补齐数据或修正
目标再启动。明确关闭基础组的毕业率判定代表普通规则调律，不得宣称具有智能收益保护。

启动请求先按原始参数与 request_id 查询已有任务。重复请求即使设备忙、离线或
当前选中目标已改变，也返回原任务；不会再次启动。修改结果或显式目标需换新 ID。

用户也可在智能调律对话框点击“查看生成方案并启动”，选择带执行用户和创建时间的
结果，查看目标及培养建议后启动。此入口与 MCP 使用同一结果准备逻辑，保留
plan_ids 和 plan_targets。普通自动调律入口使用用户默认配置，不自动带入生成
结果的临时目标覆盖；需要重现生成目标时使用生成方案入口。

## 最小生成示例

以下是会意首饰规则的完整结构示例，不是适用于所有用户的推荐策略。先通过
get_tuning_config 和 get_game_config 核对词条、玩法及规则约定，再填写实际
plan_ids 和目标。示例空 plan_ids 表示按规则匹配已有方案；缺少可用方案会在预检拒绝。

```json
{
  "name": "会意首饰示例",
  "goal": "示例：比较现有方案的首饰提升",
  "plan_ids": [],
  "base_group": {
    "materials": {"food_rules": []},
    "scan": {"rules": []},
    "tune": {"rules": [], "max_resets": 0, "reset_exhausted_action": "skip", "lock_qualified": false},
    "smart_tuning": {"plan_scope": "incoming", "evaluation": {"enabled": true}}
  },
  "rules": [{
    "name": "会意首饰",
    "playstyles": ["无名"],
    "affix_pool": ["最大外功攻击", "势", "劲"],
    "patterns": {"环": {
      "first": ["最大外功攻击"],
      "top_conditions": [{"contains_all": ["最大外功攻击", "势", "劲"]}]
    }}
  }],
  "run_config": {"selected_slots": ["ring", "pendant"]}
}
```

生成/覆盖/设置补丁的字段结构随工具 schema 发布。基础组与规则的完整业务语法
仍由原解析器校验，不能只以 JSON schema 通过作为可执行依据。运行 switches
必须来自有效注册表，未知开关在预览、创建和保存时拒绝。
