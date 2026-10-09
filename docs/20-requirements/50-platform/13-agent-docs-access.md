# Agent 文档收录与配置访问梳理

> 状态：部分实现（2026-10-09，基线 v0.13.14）。公开领域目录、Agent 契约、文档包和访问接口已实现；用户指南全部业务定义的进一步拆分、Windows 安装及 WorkBuddy/游戏实机验收待完成。

## 1. 清单用途与分类

本清单支撑[外部 Agent MCP 接入](12-agent-mcp.md)，以用户安装发行版后能查询、
扫描方案、分析装备培养、生成全新配置并一键启动现有自动调律为范围。
目录仅决定知识归属；运行权限由接口
执行，不由文件位于哪个目录决定。

文档处理分四种：收录是将一份权威正文移至公开共享目录后打包；拆分是把业务语义
与界面/维护内容分离后只收录前者；转述契约是从既有实现与规格整理公开操作说明，
不复制内部设计；排除是不进入首版文档包。未列入收录清单的内容默认排除。

公开领域正文已迁入 10-public，维护登记已迁入 90-internal，旧路径保留导航。
Agent 操作契约已建立并可打包；用户指南中未拆走的旧语义仍需后续归并，不整体入包。

## 2. 现有文档梳理

### 2.1 游戏领域文档

来源目录为 docs/10-game，下表按每篇列出处理。目标目录统一使用 10-public
保存共享正文、90-internal 保存实现登记，实施时保持每份正文唯一。

| 当前文档 | 处理 | 收录内容与维护边界 |
|----------|------|--------------------|
| README.md | 拆分索引 | 为公开正文建立独立索引；开发分析和未创建规划不进入包内导航 |
| 01-equipment-system.md | 拆分后收录 | 装备、调律、重置、转律、承音与合法性；源码链接移出，具体数值通过有效配置核对 |
| 02-school-system.md | 拆分后收录 | 流派、武学、玩法关系及无序组合语义；固定名单和过期推荐按配置核对 |
| 03-damage-mechanics.md | 拆分后收录 | 属性作用与计算口径；与计算引擎核对，内部实现和来源工程细节不入包 |
| 04-tuning-mechanics.md | 重建公开索引 | 包内只导向机制、评价与操作契约，维护层去向留在仓库索引 |
| 05-ui-pages-and-relations.md | 排除 | UI 页面和自动化状态细节不用于首版配置启动；前置状态通过预检接口返回 |
| 06-mechanics-conventions.md | 拆分 | A6～A8 的合法性及 B1～B3 的身份语义并入公开正文；数值事实查询公共模型，源码位置/容差/维护登记留 90-internal |
| 10-tuning-rules/README.md | 拆分后收录 | 评级、最高潜力、转律模拟与熔断的共同语义；动态规则清单由接口返回 |
| 10-tuning-rules/01-huiyi.md | 收录前核对 | 会意规则的评价语义；用当前有效规则判断，正文不替代 YAML |
| 10-tuning-rules/02-huixin-big.md | 收录前核对 | 会心大外规则语义，保留与其他规则的实际差异 |
| 10-tuning-rules/03-huixin-small.md | 收录前核对 | 会心小外规则语义 |
| 10-tuning-rules/04-heal-pure.md | 收录前核对 | 纯奶规则语义；可评级不等于可使用所有毕业率分析 |
| 10-tuning-rules/05-heal-fire.md | 收录前核对 | 火拳治疗规则语义；适用模型由能力接口说明 |
| 10-tuning-rules/06-weiwei-dawang.md | 收录前核对 | 专属规则语义；不将其他 Beta 规则按相似名字猜成相同能力 |

规则说明保持共享正文；实际词条池、玩法、条件与默认值通过规则查询取得。
若正文与发行时配置有差异，先修正正文再收录，不能在文档包中静默形成第二套规则。

### 2.2 用户指南逐篇归属

来源目录为 docs/60-userguide。此目录仍面向 UI 用户；“拆分”指把共同业务定义
迁入共享正文或公开契约，原指南保留点击步骤和引用，不永久维护两份全文。

| 当前文档 | 处理 | Agent 需要的部分 |
|----------|------|------------------|
| README.md | 排除，另建入口 | 不用完整用户指南目录充当 Agent 导航 |
| 01-quick-start.md | 转述契约 | 已安装实例、连接前提和可用目标；下载、加群及安装截图排除 |
| 02-main-interface.md | 转述契约 | 智能调律页接入导出、生成结果与一键启动；当前用户与执行用户/目标的区别；通用布局与热键排除 |
| 03.01-base-config.md | 转述契约 | 只说明执行所需环境前提；连接凭据与全局设置不开放 |
| 03.02-scene-management.md | 排除 | 场景编辑不在首版范围 |
| 03.03-gallery-management.md | 排除 | 图库编辑不开放；缺失必需字段由启动预检返回 |
| 03.04-batch-tasks.md | 排除 | 首版不开放批量配置、调度或通用任务启动 |
| 04.01-tuning-config-overview.md | 拆分 | 规则组、流派规则、用户选择及编辑态归属 |
| 04.02-default-config.md | 拆分 | 材料/处置含义；预置参数实例由有效配置查询，不复制整张默认表 |
| 04.03-behavior-config.md | 拆分 | 三行为点、判定范围、匹配顺序、缺料、重置耗尽、锁定和回收 |
| 04.04-school-rules.md | 拆分 | 评级与动态词条共同语义，具体规则正文回归 10-game |
| 04.05-game-config.md | 拆分 | 有效配置、覆盖层、玩法身份；游戏机制修改与 UI 编辑步骤排除 |
| 04.06-running-tuning.md | 拆分 | 启动前提、参数、状态、结果及异常；保留原 UI 操作步骤在指南 |
| 05.01-equipment-data-overview.md | 拆分 | 备战方案、装备库、共享引用、真实/模拟身份及数据完整性 |
| 05.02-equipment-data-acquisition.md | 拆分 | 全部方案扫描入口、授权、覆盖范围、数据写入副作用、采集时间及逐方案失败；现有指南缺少的全部方案说明需补齐 |
| 05.03-equipment-management.md | 拆分 | 查询筛选和对象身份；编辑、删除、应用和装备写回排除 |
| 05.04-base-attributes-and-graduation.md | 拆分 | 基础属性、方案、毕业率、敏感度及可计算前提；编辑步骤排除 |
| 05.05-school-rule-filtering.md | 拆分 | 规则过滤与毕业率方案的区别、候选范围 |
| 05.06-optimal-equipment-combination.md | 拆分 | 搜索前提、候选、培养假设、真实/派生结果；应用方案和实验性能力排除 |
| 05.07-build-calculator.md | 排除 | 总词条分配及出装编辑不是首版所需；以后开放时再加入对应契约 |
| 06-workflows.md | 转述契约 | 自动调律与全部备战方案扫描的运行控制，其他日常任务与脚本清单排除 |
| 07-faq.md | 拆分 | 调律启动、数据不足、材料异常的可行动提示；通用 UI 问答排除 |
| 08-feedback-and-issues.md | 转述契约 | 错误引用与脱敏边界；联系方式、群二维码不入包 |
| 09-mobile-device.md | 转述契约 | 桌面所连接 Android 目标的状态与限制；手机安装和标定编辑排除 |
| 10-script-authoring.md | 排除 | 不开放脚本编写、任意代码及 DSL 执行 |

### 2.3 需求、架构及其他目录

| 来源 | 处理 | 用途 |
|------|------|------|
| 20-requirements/10-tuning/01-auto-tuning.md、02-tuning-management.md、03-smart-tuning.md | 转述契约，不打包原文 | 提炼现有流程、观察模式、未知放行、判断点和历史语义 |
| 20-requirements/10-tuning/04-transmute-simulation.md | 转述培养分析契约 | 转律建议与同名数值培养对照、资格及结果可信度；不开放转律写回 |
| 20-requirements/20-equipment/01-loadout-management.md、03-combat-attribute-sources.md | 转述必要语义 | 全部方案扫描及可见网格限制、写入与部分失败；数据归属、稳定引用及评分来源 |
| 20-requirements/40-profile/01-graduation-rate.md | 转述契约 | 计算前提、口径和不支持情况 |
| 20-requirements/50-platform/04-execution-targets.md、05-concurrent-target-execution.md | 转述契约 | 显式目标、占用和现有授权等级 |
| 20-requirements/60-editor/02-task-history.md | 转述必要语义 | 任务 ID、历史查询与产出引用 |
| 其他 20-requirements，包括本文及 MCP 需求 | 排除 | 开发需求和规划不作为发行版能力说明 |
| 30-architecture/31-models/05-martial-arts-and-playstyles.md | 转述领域语义 | 武学无序匹配与实际装备槽位 |
| 30-architecture/36-graduation/01-data-flow.md、06-operations.md、08-scoring-layer.md | 转述计算契约 | 输入、培养假设、合法性、结果口径与限制，编译器实现不入包 |
| 30-architecture/39-runtime/01-tuning-history.md、03-concurrent-execution.md | 转述查询/控制契约 | 任务隔离、实际产出和状态，不开放数据库实现 |
| 其他 30-architecture，尤其 32-grammar | 排除 | 内部设计和 DSL 不属于首版调用面 |
| 00-meta、40-development、50-releases | 排除 | 待办、旧结论和发布记录不用于推断当前能力 |
| packaging、scripts、tests、ops、AGENTS.md | 排除 | 构建/开发指令不能成为外部 Agent 的运行授权 |
| 仓库 README、LICENSE、PRIVACY | 分别处理 | README 不整体收录；许可证保留原发行位置，必要隐私条款提供固定说明或明确引用 |

## 3. 目标目录与文档责任

以下目录已建立；用户指南的逐篇表同时记录后续归并计划，不能将计划视为正文已全部拆分。

```text
docs/
├── 10-game/
│   ├── 10-public/           # 机制、伤害口径、评价规格；人和 Agent 共读
│   └── 90-internal/         # 写死项的源码位置、开发验证和维护登记
├── 20-requirements/         # 需求与验收，不入 Agent 包
├── 30-architecture/         # 实现设计，不入 Agent 包
├── 40-development/          # 过程记录，不入 Agent 包
├── 50-releases/             # 保持现有发布路径
├── 60-userguide/            # 人的界面操作指南，不整体入 Agent 包
└── 70-agent/
    ├── README.md
    ├── 10-contracts/
    ├── 20-operations/
    └── 30-examples/
```

70-agent 的正文主题至少包括：对象与状态归属、权限与有效配置、计算口径、
任务生命周期、扫描采集、培养分析、全新基础组/调律规则的生成及校验、
配置结果发布、调律预检/一键启动/控制、结果复盘及示例。
工具字段和 schema 从公开接口生成，人工正文只解释业务含义、前提和副作用。

操作正文还须提供：用户在智能调律页导出 MCP 接入配置、在外部 Agent 中交流
流派与目标、触发扫描、阅读对应领域说明、取得培养/换装建议、生成配置并回到
律匠一键启动的示例。接入配置格式和规则生成 schema 纳入文档包；本机导出的
实际路径/授权材料和私人目标不纳入静态包。

默认入口不加载全部领域文档。Agent 按任务检索与读取需要的章节；可用玩法、
规则与数值上限读取有效配置。共享正文发生变化时更新引用，不在 Agent 层复制。
历史开发日志保留当时事实；正文迁移后的有效引用和文档检查随实施更新。

构建清单将 10-public 与 70-agent 映射到发行版 agent/docs 下的 domain、contracts、
operations 和 examples，另输出从实际接口生成的 schemas。构建中复制属于生成
产物，不形成另一份手工维护正文；不将构建结果提交到源码文档目录。

## 4. 配置与数据访问清单

物理文件路径用于开发审计，不作为 MCP 任意文件参数。config 相对路径均指发行版
数据根；授权读取是领域对象查询，授权写入是专用字段补丁接口。

| 文件族/节点 | 可读范围 | 可写范围 | 落盘及约束 |
|-------------|----------|----------|-------------|
| agent/manifest.json、README.md、清单内 docs 与 schemas | 随包公开内容 | 无 | 官方只读资源；不接受清单外路径 |
| config/{system,remote,local}/yysls/game_config/basic.yaml | 游戏公共配置中计算所需值 | 无 | 经解析器返回有效值，不开放全局应用设置 |
| 同目录 seasons.yaml | 赛季和装备等级能力 | 无 | 不把静态文档赛季数值当实时真值 |
| 同目录 affixes.yaml | 词条上限、类别、别名及部位 | 无 | 计算与校验继续读取权威配置 |
| 同目录 equipment.yaml | 基础属性、套装、弓玦、武器类型 | 无 | 按主题查询，避免全文无界返回 |
| 同目录 martial_arts.yaml、schools.yaml、playstyles.yaml | 武学、流派、玩法及转律库 | 无 | 稳定 key 与展示名分离，组合无序匹配 |
| config/{system,remote,local}/yysls/tuning_rules/*.yaml | 有效规则、玩法、词条池、条件、来源及修订 | 新配置生成授权下新建完整调律规则 | 只在 local 使用未占用 key；任何已有定义均不覆盖，不修改启停/顺序，不删除/重命名/移动，不启用隐藏/Beta 能力 |
| config/{system,remote,local}/yysls/base_groups/*.yaml | 有效基础、材料、行为与智能参数 | 新配置生成授权下新建或派生完整基础组 | 只在 local 使用新 key；不改任何已有组，不含私人目标数据，不顺带设为默认 |
| config/{system,remote,local}/yysls/tune_config.yaml | 调律公共开关定义与品阶门槛 | 无 | switches 是开关注册表，不是用户开关值；新规则/组不修改该文件，全局门槛差异通过合法的新定义表达 |
| config/{system,remote,local}/yysls/graduation/**/*.json | 可用方案元数据与必要模型说明 | 无 | 分析调用现有模型；不提供原始公式编辑入口 |
| config/{system,remote,local}/yysls/damage_model/*.yaml、attr_model/**/*.yaml、gear_sets/*.yaml | 可计算方案所需模型摘要与来源 | 无 | 按已授权分析接口投影，不开放任意模型文件浏览 |
| config/session/users/{user}.json 的 workflow_params.auto_tuning | 授权用户的所选部位、规则/玩法、switches、base_group、smart_tuning_enabled 及运行选项 | 用户参数授权下修改上述公开字段 | 合并该节点；其他工作流、资料和 UI 状态不读写；字段补丁不等于整份替换 |
| config/session/users/{user}.loadouts.json | 授权用户的方案、装备和计算所需关联数据 | 仅授权扫描任务按原采集契约更新 | LoadoutRepository 查询与采集写入；不开放通用编辑、删除、自动换装或培养结果应用 |
| config/session/yysls/play_styles.json、attr_derivations.json 的方案关联条目 | 授权方案引用的基础属性与必要来源 | 扫描全部方案按既有可信度规则保存基础属性并维护其推导记录 | 由 save_play_style 等权威接口处理，基础属性引用关联回 loadouts JSON；不开放共享属性文件全文或任意编辑 |
| config/session 内 Agent 生成结果记录（results.json 与 tasks.json） | 授权用户的目标、建议、分析引用、配置引用和可启动状态 | 创建新生成结果及发布状态 | 与规则 YAML 分离；不包含凭据，禁止半套结果可启动；不随发行包分发 |
| config/session/profile.db | 授权分析必需字段，如现有适用的世界等级，注明来源与时间 | 无 | 通过现有仓储/领域接口；无任意 SQL，不当作实时材料库存 |
| config/session/tuning_history.db | 授权用户/目标任务的调律历史与结果 | 无 | 结构化分页；执行器按原生命周期正常写历史，不是 Agent 直接改 DB |
| 任务历史及任务产出 | 授权任务的状态、摘要、产出引用与必要错误 | 无 | 仅跟随任务 ID 查询；不读取全部日志目录 |
| config/session/session.json、interface.json、batch.json、users/{user}.session.json | 仅通过专用工具返回必需的用户/目标/任务状态 | 无整文件修改 | 凭据、账号、设备私有设定、AI 连接和其他任务数据不暴露 |
| config/{system,remote,local}/app.yaml、ocr.yaml、layouts.yaml、scenes.yaml | 只通过预检报告相关可用状态 | 无 | 不返回原始文件，不修改全局环境、OCR、场景或布局 |
| scenes/、layouts/、references/、workflows/ | 仅预检/状态的必要摘要 | 无 | 扫描通过限定 scan_all_loadouts 工具运行，不开放原文件、任意脚本参数或源码编辑 |
| config/remote.staging、遥测缓冲、完整日志、截图录像、头像、系统凭据库 | 无常规访问 | 无 | 不随 Agent 包分发，不通过搜索或错误正文间接泄露 |
| 其他未列文件/字段 | 无 | 无 | 默认拒绝，新增业务范围须补公开契约和服务端授权 |

### 4.1 用户调律节点可写字段

首版字段候选来自 default_auto_tuning_config，公开前分别校验语义：

- selected_slots、rules 的既有 enabled/playstyles 选择、switches、base_group、
  smart_tuning_enabled：用户调律目标与设置。
- skip_locked_equipment、use_stone_cache、initial_stone_check_enabled、
  initial_stone_min_count、validate_stone_cache：现有保护与库存检查设置。
- pc_background_scroll、scroll_strategy、skip_start、target_cell、min_level：
  仅在实际目标支持时允许；位置参数必须与有效背包范围匹配，不作为任意点击接口。
- skip_tuning 是现有测试性跳过实际调律开关，首版只读并在预检中提示，
  不让 Agent 擅自启用或把它宣称为完整无副作用的模拟模式。

未知字段拒绝，明确空值及清除语义；rules 和 smart_tuning_enabled 等嵌套映射
按指定对象合并，不能覆盖未修改的规则/组选择。规则管理层的 disabled 不等于
某用户 rules 中的 enabled，不能通过用户选择重新启用已禁用公共规则。

### 4.2 新配置生成与来源层

Agent 每次新的生成请求创建新的基础组与调律规则。可从既有配置派生内容，
但不能覆盖 system、remote 或 local 中的任何已有 key；名称相同也不能隐式覆盖。
重复同一幂等请求返回已生成结果，主动重做则创建新版本，旧结果保留且仍能核对。

新基础组字段为材料、扫描处理、调律处理、smart_tuning 及既有门槛，具体字段
由 TuningGroup schema 限定；新规则使用 TuningRule schema，包括合法词条池、
目标玩法、部位条件与评级定义。新对象身份字段由创建接口分配/校验，不提升
已有 content_version，不修改公共游戏配置或 switches 注册表来迁就生成结果。

规则选择、部位与基础组关联等本次参数一并保存到生成结果，私人用户名、方案 ID、
真实装备和对话目标不进入规则 YAML/key/文件名。先整体校验，再成套发布；失败
结果不可启动。UI 列出可启动结果，启动不自动覆盖原用户选择。明确设为默认才
更新 workflow_params.auto_tuning 的所属字段。

新定义通过权威管理器落 local；用户节点仍字段级合并，不直接编辑 YAML/JSON。
MCP 的发行版写入边界不能被 LVJIANG_DEV_MODE 或 .git 检测改变；开发环境验证
应隔离到临时发行数据根，不能写项目 system/local/session 或真实 DB。

## 5. 打包清单与验收产物

文档构建使用明确收录清单：源文档、稳定 ID、输出路径、主题和关联能力。
源文件存在不意味着自动收录；相对链接不意味着授权更多文件；必要附件逐项登记。
源码登记、示例真账号、私人配置和本机输出都不得进入清单。

首版必须交付并核对：

1. 安装器与便携版中的完整 agent 目录，以及同一生成结果的独立文档 ZIP。
2. manifest 和公开 schema，与运行服务的真实能力相符。
3. 打包后的链接/锚点/附件检查报告；无对仓库目录的必需依赖。
4. 文件集合核对及校验和；新增 ZIP 纳入发布产物，未生成时发布失败。
5. 从干净发行目录导出可接入配置，完成扫描、目标交流、培养分析、新配置生成及
   一键启动闭环；拒绝未授权文件与节点的回归结果。

知识包不包含本机配置快照。Agent 连接实际实例后读取有效配置和授权范围，
如需要导出用户数据另立需求；本次静态文档打包不能成为私人数据导出入口。

## 6. 开发验收与维护门禁

正文拆分后，公开目录无必需的源码/开发文档链接；用户指南继续可读且共享事实
只有一份。既有需求、架构和发布文档保持原职责，未变化内容不批量刷新日期或基线。

字段清单与 schema 对照，接口定义与生成 schema 对照，服务返回权限与实际授权
对照。测试覆盖越界、采集副作用、旧配置不变、新 key 冲突、成套发布、修订冲突、
local 保存、临时运行不保存及任务隔离；
只用临时数据。发行包验收不能用仓库存在的文件补齐漏打包内容。

执行详细验收以[主需求验收标准](12-agent-mcp.md#9-验收标准)为准。本清单随接口
范围变化调整；新增能力先补读取/修改范围与收录依据，再开放工具，不自动扩充目录权限。
