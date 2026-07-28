# 调律规则通用开关化改造方案（废弃 PVP 专用语义）

> 状态：方案定稿，待实施。本文档为独立会话执行任务的完整依据。
> 前置阅读：`src/apps/yysls/evaluator/`（judge.py、tuning_rules 包）、
> `src/apps/yysls/ui/rules_editor/`、`config/system/yysls/tuning_rules/*.yaml`。

## 1. 背景与动机

当前 `keep_pvp` 是全局复选框 + 引擎硬编码覆盖层（judge.py 三处特判）：

1. 词条替换：胫甲 `对玩家单位增效 → 对首领单位增伤`（仅当源词条不在规则池内时生效——隐晦）；
2. 临时并库：冠胄 `add_to_pool: 单体类奇术增伤`；
3. `is_pvp` 标记：装备含 PVP 词条且未判垃圾即标记——不管评级是否真的受益于 PVP 等价
   （如纯奶池内本来就有 对玩家单位增效，纯 PVE 口径也能评级，却被标"因 PVP 保留"）。

问题本质：业务语义藏在引擎 if 分支里，规则 YAML 读不出来，判定结果说不清楚。

**核心洞察（用户提出）**：PVP 不是特殊机制，只是一个布尔开关 + 几条带开关前提的普通条件。
勾选时无非是：头胸允许 单体类奇术增伤；腿手把 对玩家单位增效 视作 对首领单位增伤（任一出现即可）。
因此彻底废弃 PVP 专用语义，改为**通用开关机制**。调律规则最终成为「玩法 × 开关」双维度：
玩法管武器判定（增伤/属性），开关管非武器判定，两者可正交。

注：此前讨论过的 `required` 必含词条字段方案**不采纳**，必含语义继续用垃圾条件表达（count_max=0）。

## 2. 已定案决策（用户拍板记录）

| # | 决策 |
|---|---|
| 1 | 评级四档定名：**垃圾、一般、优秀、顶级**（"能用"改名"一般"） |
| 2 | `is_pvp` 字段**废弃**，全部改为开关语义和玩法语义 |
| 3 | 增效类词条（单体类奇术增伤/对玩家单位增效/对首领单位增伤等）**游戏内出现部位固定**（单体奇术只出现在头胸、对玩家/对首领增只出现在腿手），入池不会泄漏到其他部位，无需额外排除条件 |
| 4 | 条件组两层结构**保留**：组间 OR、组内 AND；档位拆成顶部 Tab，每 Tab 内可加多个条件组 |
| 5 | 条件原语收敛为 4 个：必须同时出现 / 不得同时出现 / 计数不得超过 / 计数不得低于；`not_contains` 废弃（≡ count_max max=0，含潜力求值语义也等价，已验证） |
| 6 | 每个原语都支持「含首词条」（include_first）修饰 |
| 7 | 判定顺序：垃圾 → 一般 → 优秀 → 顶级（新增"优秀"显式条件档）；四档全不命中 → 规则级**默认判定** |
| 8 | 词条库语义不变并强调：不在 affix_pool 中的词条一律垃圾词条（池外即垃圾），武器部位玩法增伤词条豁免不变 |
| 9 | 基础配置页新增「装备评级」区块（先展示四档，为后续抽象自定义评级留位）与「开关设定」区块（用户可增删 key+name 的全局开关） |

## 3. 目标模型

### 3.1 评级四档与默认判定

- `Rating` 枚举改为：`TOP="顶级"`、`EXCELLENT="优秀"`、`NORMAL="一般"`、`JUNK="垃圾"`
  （成员 `USABLE` 更名 `NORMAL`，值 "能用" 改 "一般"；全库 grep `USABLE`、`能用` 同步）。
- 规则 YAML 新增顶层字段 `default_rating`，取值 `junk/normal/excellent/top`，缺省 `excellent`。
  UI 上放在规则设置页**玩法设定上方**（下拉框，NoWheelComboBox）。
- 判定顺序：垃圾 → 一般 → 优秀 → 顶级，先命中先得；全不命中 → `default_rating`。
  存量规则 excellent 档为空，迁移后行为与现状一致（junk→normal→top→默认优秀）。

### 3.2 条件原语定稿（4 个）

| 原语 key | UI 名称 | 语义 | 参数 |
|---|---|---|---|
| `contains_all` | 必须同时出现 | 全部词条各自出现（集合语义，非计数） | symbols, include_first |
| `not_together` | 不得同时出现 | 全部词条同时出现即违反（¬contains_all） | symbols(≥2), include_first |
| `count_max` | 计数不得超过 | symbols 总计数 ≤ max（max=0 即"未出现任一"） | symbols, max, include_first |
| `count_min` | 计数不得低于 | symbols 总计数 ≥ min | symbols, min, include_first |

- `not_contains` 从 `COND_KINDS` 删除，解析器遇到时报错并提示迁移（加入 legacy 提示信息）；
  存量 YAML 全部机械改写为 `count_max: {symbols: [...], max: 0}`（include_first 不写=false，保持原行为）。
- `not_together` 保留理由：词条可重复出现（如会心率×2），`count_max([a,b],1)` 会被 `[a,a]` 误伤
  而 `not_together` 不会，二者不等价。同时放开"恰 2 个"限制为 ≥2（全部同时出现才违反）。
- `contains_all` 不可用 count_min 替代：`count_min([劲,势],2)` 会被 `[劲,劲]` 满足，集合覆盖≠计数达标。
- include_first 扩展到集合式原语：include_first=true 时首词条参与集合/计数判断
  （现状仅计数式支持、集合式一律忽略首词条；迁移存量置 false 保持行为）。
- 潜力求值（still_hits/potential）同步支持四原语 + include_first：
  - `count_max(0)` 的 still_hits 与原 not_contains 完全等价（补 1 个即解除）；
  - contains_all/count_min 命中后补牌不反转 → 维持命中；potential 按缺失数 ≤ n_avail；
  - not_together：缺失词条数 > n_avail 才维持命中（现有逻辑，泛化到 ≥2 个词条）。

### 3.3 条件组结构与开关绑定

两层结构保留：**组间 OR、组内 AND**。开关绑定挂在**条件组级**
（用户 PVP 场景全是单条件组，与"每个条件挂开关"直觉等效；组级绑定避免了
AND 组内单条件失效导致空组恒真的歧义）。

YAML 三种组形态（解析器全部接受，写盘按规则输出）：

```yaml
junk_conditions:
# 形态 1：单键 dict = 无开关单条件组（写盘首选，与现状一致）
- count_max:
    symbols: [全武学增效]
    max: 0
# 形态 2：list = 无开关多条件 AND 组
- - count_max: {symbols: [最小无相攻击], max: 0}
  - count_max: {symbols: [精准率], max: 1, include_first: true}
# 形态 3：object = 带开关组（when: 开关 key → 期望值，多键为 AND；all: 组内条件）
- when:
    keep_pvp: false
  all:
  - count_min: {symbols: [单体类奇术增伤], min: 1}
```

- `when` 中引用的开关 key 必须在开关注册表中，否则校验失败；
- 判定时先按当前开关状态过滤条件组（`when` 全部匹配才参与），再走 OR/AND 求值；
- 开关未在运行配置中出现 → 视为 False。

### 3.4 开关注册表（tuning_base.yaml）

删除整个 `pvp:` 段，新增：

```yaml
switches:
  keep_pvp:
    name: 保留PVP装备
```

- key 校验同规则 key（`^[a-z][a-z0-9_]*$`），name 非空；dict 有序，UI 按声明序渲染；
- 用户可在基础配置页增删开关；删除开关前须校验无规则条件组引用（引用中则拒绝删除并提示）。

### 3.5 新判定流水线（judge.py `_judge_attempt`）

1. 首词条不在 `first` → 跳过；
2. 非武器部位属攻→无相等价（不变）；
3. 池外且非本次玩法增伤的词条 → 垃圾（不变，damage 豁免不变）；
4. 玩法要求增伤但词条缺失 → 垃圾（不变）；
5. 按开关状态过滤各档条件组，依次 垃圾→一般→优秀→顶级 求值（组间 OR、组内 AND），
   先命中先得；全不命中 → `default_rating`。

删除的引擎逻辑：`keep_pvp` 特判三处（词条替换/临时并库/pvp_hit 标记）、
`tuning_base.pvp_names/pvp_parts` 读取。潜力判定 `_eval_partial` 同步：
垃圾/一般/优秀档走 still_hits（评级封顶），顶级档走 potential，兜底 `default_rating`。

### 3.6 判定器配置形状

`TuningJudge.config` 由 `{"playstyles": [...], "keep_pvp": bool}` 改为
`{"playstyles": [...], "switches": {"keep_pvp": bool, ...}}`。
`self.keep_pvp` 属性删除，改 `self.switches: dict[str, bool]`。

## 4. Schema/数据变更明细

### 4.1 规则 YAML（每个文件）

- 新增 `default_rating: excellent`（迁移全部规则显式写入）；
- `usable_conditions` 字段更名 `normal_conditions`（解析器不再接受旧名，报错提示迁移）；
- 新增 `excellent_conditions`（存量迁移为空，不写即可）；
- 所有 `not_contains` → `count_max(max=0)` 机械改写；
- PVP 相关迁移见 §6。

### 4.2 tuning_base.yaml

- 删 `pvp:` 段（names/substitutions/add_to_pool）；
- 增 `switches:` 段（含 keep_pvp 一项，见 §3.4）；
- `quality_thresholds` 不动。

### 4.3 会话（config/local/yysls/session.json → tuning 节）

- `keep_pvp: bool` → `switches: {keep_pvp: bool}`；
- 主窗口 `_load_tuning_config` 做一次性兼容：读不到 `switches` 时回退读旧 `keep_pvp` 键。

## 5. 代码变更清单

### 5.1 evaluator/tuning_rules/models.py

- 删 `PVP_NAMES`、`PvpPartRule`、`TuningBase.pvp_names/pvp_parts`；
- `COND_KINDS` 删 `not_contains`；`Condition.check/potential/still_hits` 移除 not_contains 分支，
  集合式原语支持 include_first，not_together 放开为 ≥2；
- 新增 `ConditionGroup`（或等价结构）：`when: dict[str, bool]` + `conditions: list[Condition]`；
  `PartPattern` 四档字段：`junk/normal/excellent/top_conditions: list[ConditionGroup]`；
- `TuningRule` 增 `default_rating: str = "excellent"`；
- `TuningBase` 增 `switches: dict[str, str]`（key → 显示名）；
- 评级取值常量（junk/normal/excellent/top ↔ Rating 映射）供解析与 UI 共用。

### 5.2 evaluator/tuning_rules/parsing.py

- `_parse_condition`：只认 4 原语；include_first 对全部原语生效；not_together 校验 ≥2；
- `_parse_condition_groups`：接受三种组形态（§3.3），产出 ConditionGroup；
  `when` 的 key 须在开关注册表内（解析规则时须可访问 tuning_base 的 switches——
  解析函数加参数传入合法开关集合，或规则加载后统一校验，二选一，建议后者：
  manager 加载全部规则后统一校验 when 引用，报 RuleValidationError）；
- `parse_tuning_rule`：`default_rating` 枚举校验；`usable_conditions` 出现即报错提示更名；
  `not_contains` 出现即报错提示迁移（加入 legacy 检查）；
- `parse_tuning_base`：删 pvp 解析，增 switches 解析（key 格式/name 非空/重复校验）。

### 5.3 evaluator/judge.py

- 按 §3.5/§3.6 重写：删三处 keep_pvp 特判与 pvp_hit；条件组按开关过滤；
  四档循环 + default_rating 兜底；`_RANK` 增 NORMAL；
- `_eval_partial` 同步四档 + 开关过滤 + default_rating；
- 模块 docstring 重写（描述开关机制，删 PVP 叙述）。

### 5.4 evaluator/base.py

- `Rating.USABLE` → `Rating.NORMAL`，值 "能用" → "一般"；
- `JudgeResult` 删 `is_pvp` 字段、to_dict 分支与 docstring 相关句；
- `TuningJudge.__init__`：`self.keep_pvp` → `self.switches`，docstring 更新 config 形状。

### 5.5 UI

1. **base_config_page.py**：删「PVP 词条集合」「PVP 部位并库」两个表格；
   品阶门槛之下新增「装备评级」区块（只读展示四档名称与判定顺序、默认判定说明，
   即分级说明的落点；为后续自定义评级抽象留位）；再下新增「开关设定」表
   （两列 key/名称，添加行/删除选中行按钮，变更即校验即保存，沿用现有模式）；
2. **tuning_config_widget.py**：硬编码 `keep_pvp` 复选框改为按开关注册表动态渲染
   （每开关一个复选框，objectName=key）；`get/set_keep_pvp` 改 `get/set_switches`；
3. **main_window.py**：会话读写按 §4.3；传给判定器/工作流的配置改 switches dict；
4. **rule_settings_page.py**：玩法设定上方增「默认判定」下拉（四档，NoWheelComboBox）；
5. **part_pattern_page.py**：每部位列内部改为顶部 Tab：首词条、垃圾判定、一般判定、
   优秀判定、顶级判定；每 Tab 内支持多个条件组（添加/删除组）；
6. **condition_editor.py**：原语下拉只留 4 个（显示名见 §3.2 表）；include_first 对全原语可勾；
   条件组级增开关绑定编辑（选开关 key + 期望值，可留空=恒生效）；
7. **workflows/tuning_doc.py**：`start_run(..., keep_pvp)` 改传 `switches: dict[str, bool]`，
   文档行改为逐开关输出 `- 开关 保留PVP装备：是/否`（名称查注册表）。

### 5.6 全库清理 grep 关键字

`keep_pvp`、`is_pvp`、`pvp`、`USABLE`、`能用`、`not_contains`、`usable_conditions`——
src 与 tests 全部命中点逐一改造，不留旧语义死代码。

## 6. 存量规则迁移映射（行为保真）

通用步骤（5 个文件都做）：`not_contains`→`count_max(0)` 机械改写、
`usable_conditions`→`normal_conditions`、补 `default_rating: excellent`。
以下仅列 PVP 语义相关的增量改动。原则：**旧引擎 keep_pvp 覆盖层产生的行为才转开关条件；
规则固有要求（治疗系）不挂开关**。

### 6.1 huiyi_general / lieshi_big / lieshi_small（同构处理）

- `affix_pool` += `单体类奇术增伤`、`对玩家单位增效`；
- 冠胄 junk_conditions 追加：
  `when {keep_pvp: false}: count_min([单体类奇术增伤], 1)`
  （对应旧行为：off 时池外即垃圾；on 时 add_to_pool 容忍）；
- 胫甲 junk 中原 `not_contains[对首领单位增伤]` 替换为三个带开关组：
  1. `when {keep_pvp: false}: count_max([对首领单位增伤], 0)`（off：必须有对首领增）
  2. `when {keep_pvp: false}: count_min([对玩家单位增效], 1)`（off：出现对玩家增即垃圾，
     保真旧"池外即垃圾"行为——**保真补充条件**，用户原始描述未含，见 §8 风险 1）
  3. `when {keep_pvp: true}: count_max([对首领单位增伤, 对玩家单位增效], 0)`
     （on：两者至少其一，对应旧替换行为）
- 环的 `not_contains[全武学增效]`（huiyi/lieshi 两个）是流派固有要求，机械改写不挂开关。

### 6.2 heal_fire（治疗火拳）

- `affix_pool` += `对玩家单位增效`（单体奇术已在池内）；
- 冠胄 junk `not_contains[单体类奇术增伤]` = 固有要求（火拳冠胄必须有单体奇术），
  机械改写为 `count_max(0)`，**不挂开关**；
- 胫甲同 §6.1 的三组开关条件（旧引擎替换层对它同样生效过）。

### 6.3 heal_pure（治疗纯奶）

- `affix_pool` += `单体类奇术增伤`（对玩家增效已在池内）；
- 冠胄 junk 追加 `when {keep_pvp: false}: count_min([单体类奇术增伤], 1)`
  （旧 add_to_pool 容忍行为的保真）；
- 胫甲 junk `not_contains[对玩家单位增效]` = 固有要求（纯奶必须有对玩家增效），
  机械改写**不挂开关**；
- **注意**：`对首领单位增伤` 不入 heal_pure 池（保持池外垃圾，现状如此）。

### 6.4 迁移时以磁盘现状为准

用户曾手工调整规则 YAML（如 lieshi_small 冠胄一般档条件 not_contains↔contains_all 的语义翻转、
环档 usable 条件等），执行迁移时**必须逐文件读取当前磁盘内容做机械转换**，
不得凭本文档或历史快照回写任何条件内容。

## 7. 实施顺序建议

1. models.py + parsing.py（新 schema 全量落地，先不动 judge）；
2. 5 个规则 YAML + tuning_base.yaml 迁移（此时解析可通过）；
3. judge.py + base.py（引擎切换到开关语义，删 PVP 代码）；
4. tuning_doc.py / main_window.py / tuning_config_widget.py（配置流转与文档输出）；
5. rules_editor 五个页面改造；
6. 测试重写 + 全量回归。

每步 `py_compile` + 相关单测；最后全量 pytest（约 630 项）须全绿。

## 8. 风险与注意事项

1. **保真补充条件**（§6.1 第 2 组）：keep_pvp=off 且装备同时含对首领增+对玩家增时，
   旧行为=垃圾（对玩家增池外），用户原始三条描述下会漏判为非垃圾。已按保真处理加入；
   若用户认为该情形不算垃圾可删除该组（迁移后行为差异仅此一处）。
2. **excellent 档在顶级之前求值**：命中优秀条件的装备不再继续测顶级。存量规则 excellent
   档为空无影响；新写规则时注意条件互斥设计（在装备评级区块说明文案中提示）。
3. **开关删除防护**：开关被条件组引用时禁止删除（manager 校验），否则规则加载失败。
4. **会话兼容**：旧 session.json 的 `tuning.keep_pvp` 需回退读取一次，避免用户配置丢失。
5. **评级改名波及**：`Rating.USABLE`/"能用" 在 UI 配色、清理工作流、调律文档、测试断言中
   均有引用，须全库 grep 同步（§5.6）。
6. **测试文件**：至少涉及 tests/yysls/test_rule_loader.py（switches/when/四原语/default_rating
   的解析校验）、judge 相关测试（四档顺序、开关过滤、include_first 扩展、is_pvp 删除）、
   test_tuning_doc.py（开关行输出）、UI 测试若有 keep_pvp 引用。
7. 提交推送遵循 /submit 一次性语义：完成后不自动 commit/push，等用户指令。

## 9. 验收标准

- `keep_pvp` 在 src 下仅存在于：开关注册表默认数据、迁移兼容读取、规则 YAML 的 when 引用；
- 引擎（judge.py/base.py/models.py）中无任何 PVP 字样的专用逻辑；
- 5 个规则在 keep_pvp on/off 两种状态下对同一批装备的判定结果与改造前一致
  （除 §8.1 声明的唯一保真差异点——该点新行为更严格，与旧行为一致）；
- 基础配置页可增删开关并即时生效到主窗口复选框与规则条件编辑器；
- 全量 pytest 通过。
