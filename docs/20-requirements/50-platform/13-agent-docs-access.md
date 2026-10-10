# Agent 文档收录与配置访问梳理

> 状态：部分实现（2026-10-09，基线 v0.13.14）。公开领域目录、Agent 契约、文档包和访问接口已实现；用户指南全部业务定义的进一步拆分、Windows 安装及 WorkBuddy/游戏实机验收待完成。

## 1. 文档归属与发布范围

人和 AI 读取同一份正文，文档继续按内容归档，不因 AI 接入搬到 70-agent。
发行版与独立 ZIP 原样带上以下四层的全部 Markdown 文档，保持相对目录与正文：

| 目录 | 文档职责 | AI 使用方式 |
|------|----------|-------------|
| docs/10-game | 游戏机制、装备/武学/伤害事实与调律评价规格 | 理解装备和玩法，结合有效配置分析 |
| docs/30-architecture | 架构、领域模型、DSL 语法/命令/函数及执行语义 | 理解数据和计算口径，解释或指导脚本编写 |
| docs/60-userguide | 软件安装、连接、设置、运行、数据管理与排障 | 依据真实操作说明教用户使用律匠 |
| docs/70-agent | MCP 发现、调用契约、上下文、分析/生成工作流与接入示例 | 选择工具、解释副作用、处理接口返回 |

10-game 和 60-userguide 不再是导航存根，70-agent 不复制它们的正文。
DSL 说明直接使用 30-architecture/32-grammar 等既有文档，不另写一份简版。
其他文档层保持开发或需求职责，本次不打包。目录内新增 Markdown 章节自动纳入，
无需逐篇登记白名单；用户私人配置、日志、源码和凭据不属于静态文档。

阅读用户指南或 DSL 文档可用于教学，不改变 MCP 当前工具列表和执行能力。
动态数值、有效配置与能力状态通过实际接口核对，文档里的规划须与已实现行为区分。

## 2. 开发与发行一致

开发运行直接读取本实例 docs 下这四层，编辑正文立即生效，不要求先构建文档包。
发行构建原样复制到安装根 docs，并输出同一份独立文档 ZIP；不扁平化、不移动
机制到 Agent 层、不改写正文或相对链接。文档归属与目录结构只有一套。

```text
docs/
├── 10-game/
├── 30-architecture/
│   └── 32-grammar/
├── 60-userguide/
└── 70-agent/
    ├── 10-contracts/
    ├── 20-operations/
    ├── 30-examples/
    ├── schemas/tools.json      # 开发运行时生成，发行构建时输出
    └── manifest.json           # 发行构建生成清单与哈希
```

## 3. 文档发现与读取

list_docs、search_docs 支持 category=10-game、30-architecture、60-userguide、
70-agent，省略时跨四层查询。清单返回 id、title、category、相对 path 和哈希。
常用文档保留 entry、equipment、schools、damage、domain、rules 等快捷 ID，并
提供 userguide、architecture、dsl 入口。其他 ID 从相对路径生成，以冒号连接
目录，例如 30-architecture:32-grammar:01-basics；Agent 使用清单返回的 ID。

read_doc 与 lvjiang://docs/{doc_id} 资源提供相同正文，schema 来自实际 tool_catalog。
发行读取核对清单、目录与哈希，开发读取按相同四层发现，均不接受任意磁盘路径。
本机导出的地址和令牌是私人连接配置，不加入静态文档 ZIP。

## 4. 配置与数据访问清单

物理文件路径用于开发审计，不作为 MCP 任意文件参数。config 相对路径均指发行版
数据根；读取是领域对象查询，写入是专用字段补丁接口。所有用户可通过显式参数访问，
不绑定一个固定用户或设备，不要求逐项授予；不可用原因在调用时返回。

| 文件族/节点 | 可读范围 | 可写范围 | 落盘及约束 |
|-------------|----------|----------|-------------|
| docs/10-game、30-architecture、60-userguide、70-agent 内正文，及 70-agent 内 manifest/schema | 随包公开内容 | 无 | 官方只读资源；不接受清单外路径 |
| config/{system,remote,local}/yysls/game_config/basic.yaml | 游戏公共配置中计算所需值 | 无 | 经解析器返回有效值，不开放全局应用设置 |
| 同目录 seasons.yaml | 赛季和装备等级能力 | 无 | 不把静态文档赛季数值当实时真值 |
| 同目录 affixes.yaml | 词条上限、类别、别名及部位 | 无 | 计算与校验继续读取权威配置 |
| 同目录 equipment.yaml | 基础属性、套装、弓玦、武器类型 | 无 | 按主题查询，避免全文无界返回 |
| 同目录 martial_arts.yaml、schools.yaml、playstyles.yaml | 武学、流派、玩法及转律库 | 无 | 稳定 key 与展示名分离，组合无序匹配 |
| config/{system,remote,local}/yysls/tuning_rules/*.yaml | 有效规则、玩法、词条池、条件、来源及修订 | 新配置生成工具新建完整调律规则 | 只在 local 使用未占用 key；任何已有定义均不覆盖，不修改启停/顺序，不删除/重命名/移动，不启用隐藏/Beta 能力 |
| config/{system,remote,local}/yysls/base_groups/*.yaml | 有效基础、材料、行为与智能参数 | 新配置生成工具新建或派生完整基础组 | 只在 local 使用新 key；不改任何已有组，不含私人目标数据，不顺带设为默认 |
| config/{system,remote,local}/yysls/tune_config.yaml | 调律公共开关定义与品阶门槛 | 无 | switches 是开关注册表，不是用户开关值；新规则/组不修改该文件，全局门槛差异通过合法的新定义表达 |
| config/{system,remote,local}/yysls/graduation/**/*.json | 可用方案元数据与必要模型说明 | 无 | 分析调用现有模型；不提供原始公式编辑入口 |
| config/{system,remote,local}/yysls/damage_model/*.yaml、attr_model/**/*.yaml、gear_sets/*.yaml | 可计算方案所需模型摘要与来源 | 无 | 按已领域分析接口投影，不开放任意模型文件浏览 |
| config/session/users/{user}.json 的 workflow_params.auto_tuning | 指定用户的所选部位、规则/玩法、switches、base_group、smart_tuning_enabled 及运行选项 | 用户明确要求保存时修改上述公开字段 | 合并该节点；其他工作流、资料和 UI 状态不读写；字段补丁不等于整份替换 |
| config/session/users/{user}.loadouts.json | 指定用户的方案、装备和计算所需关联数据 | 仅扫描任务按原采集契约更新 | LoadoutRepository 查询与采集写入；不开放通用编辑、删除、自动换装或培养结果应用 |
| config/session/yysls/play_styles.json、attr_derivations.json 的方案关联条目 | 指定方案引用的基础属性与必要来源 | 扫描全部方案按既有可信度规则保存基础属性并维护其推导记录 | 由 save_play_style 等权威接口处理，基础属性引用关联回 loadouts JSON；不开放共享属性文件全文或任意编辑 |
| config/session 内 Agent 生成结果记录（results.json 与 tasks.json） | 指定用户的目标、建议、分析引用、配置引用和可启动状态 | 创建新生成结果及发布状态 | 与规则 YAML 分离；不包含凭据，禁止半套结果可启动；不随发行包分发 |
| config/session/profile.db | 领域分析必需字段，如现有适用的世界等级，注明来源与时间 | 无 | 通过现有仓储/领域接口；无任意 SQL，不当作实时材料库存 |
| config/session/tuning_history.db | 指定用户/目标任务的调律历史与结果 | 无 | 结构化分页；执行器按原生命周期正常写历史，不是 Agent 直接改 DB |
| 任务历史及任务产出 | 相关任务的状态、摘要、产出引用与必要错误 | 无 | 仅跟随任务 ID 查询；不读取全部日志目录 |
| config/session/session.json、interface.json、batch.json、users/{user}.session.json | 仅通过专用工具返回必需的用户/目标/任务状态 | 无整文件修改 | 凭据、账号、设备私有设定、AI 连接和其他任务数据不暴露 |
| config/{system,remote,local}/app.yaml、ocr.yaml、layouts.yaml、scenes.yaml | 只通过预检报告相关可用状态 | 无 | 不返回原始文件，不修改全局环境、OCR、场景或布局 |
| scenes/、layouts/、references/、workflows/ | 仅预检/状态的必要摘要 | 无 | 扫描通过限定 scan_all_loadouts 工具运行，不开放原文件、任意脚本参数或源码编辑 |
| config/remote.staging、遥测缓冲、完整日志、截图录像、头像、系统凭据库 | 无常规访问 | 无 | 不随 Agent 包分发，不通过搜索或错误正文间接泄露 |
| 其他未列文件/字段 | 无 | 无 | 默认拒绝，新增业务范围须补公开契约和服务端校验 |

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
结果不可启动。Agent 查询并展示生成结果，启动不自动覆盖原用户选择。明确设为默认才
更新 workflow_params.auto_tuning 的所属字段。

新定义通过权威管理器落 local；用户节点仍字段级合并，不直接编辑 YAML/JSON。
MCP 始终将新定义写 local，不因开发模式改写 system。开发软件可使用本实例的
local/session 数据；自动化测试必须隔离到临时根，不能污染真实用户文件或 DB。

## 5. 打包清单与验收产物

文档构建完整收录四个指定层的 Markdown，生成稳定 ID、标题、分类、原路径与哈希。
其他层的相对链接保留为维护参考，不能通过读取工具访问任意文件。
源码登记、示例真账号、私人配置和本机输出都不得进入清单。

首版必须交付并核对：

1. 安装器与便携版中的完整四层文档目录，以及同一生成结果的独立文档 ZIP。
2. manifest 和公开 schema，与运行服务的真实能力相符。
3. 开发与安装文档逐篇内容/路径一致；机制、操作与 DSL 内部导航保留原结构。
4. 文件集合核对及校验和；新增 ZIP 纳入发布产物，未生成时发布失败。
5. 从干净发行目录导出可接入配置，完成扫描、目标交流、培养分析、新配置生成及
   调用启动闭环；拒绝未开放文件与节点的回归结果。

知识包不包含本机配置快照。Agent 连接实际实例后读取有效配置、全部用户与实时目标状态，
如需要导出用户数据另立需求；本次静态文档打包不能成为私人数据导出入口。

## 6. 开发验收与维护门禁

文档保持原职责，机制、操作、架构和 Agent 契约各有唯一正文。既有需求、架构和发布文档保持原职责，未变化内容不批量刷新日期或基线。

字段清单与 schema 对照，接口定义与生成 schema 对照，服务可用状态与调用时限制
对照。测试覆盖越界、采集副作用、旧配置不变、新 key 冲突、成套发布、修订冲突、
local 保存、临时运行不保存及任务隔离；
只用临时数据。发行包验收不能用仓库存在的文件补齐漏打包内容。

执行详细验收以[主需求验收标准](12-agent-mcp.md#9-验收标准)为准。本清单随接口
范围变化调整；新增能力先补读取/修改范围与收录依据，再开放工具，不自动扩充目录权限。
