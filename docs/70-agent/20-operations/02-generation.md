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
