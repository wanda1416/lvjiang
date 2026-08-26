# 匿名统计：数据库设计与分析

律匠的匿名统计分客户端（`src/lvjiang/core/telemetry/` +
`src/lvjiang/apps/yysls/telemetry/`）与服务端（`ops/stats-worker/`，
Cloudflare Workers + D1）两侧。本文档只讲**服务端数据落地之后长什么样、
以后怎么查**；用户可见的收集范围与隐私边界见 [`PRIVACY.md`](../../PRIVACY.md)，
客户端的字段白名单机制与采集点见 `core/telemetry/schema.py` 和
`apps/yysls/workflows/implementations/auto_tuning.py` 的模块注释。

生产地址：`https://lvjiang-stats.wyxj.net`（Custom Domain，绑在
`wyxj.net` 这个已有域名下；`*.workers.dev` 默认域名已停用）。

---

## 一、为什么是 D1 不是 KV

Cloudflare KV 免费额度 1000 writes/day——几百活跃用户就会打满，之后所有
上报静默丢失，而作者在数据上看到的是"DAU 停止增长"（最坏的一种失败：
它伪装成一个业务结论）。且 KV 算不出留存（需要同一 ID 跨多天记录做
cohort）。D1 免费额度 100k rows written/day、5M rows read/day、5GB
存储，同样零运维，四项指标（DAU/MAU/版本分布/留存）都能算。

## 二、写入粒度：一件装备一条事件，一批 50 条存一行

这是整个 schema 设计里最关键的一个决策。D1 按**行**计费（含二级索引的
写入），若按"一次 roll 一行"：500 DAU × 每人 200 次调律/天 = 10 万
行/天，正好卡线；5000 DAU 直接超额度，且**没有超额降级**——写满当天就
直接拒绝写入。

采集粒度定在**一件装备一条事件**（进调律页面到离开，含初始词条、逐轮产出
序列、结束原因），批次再把 50 条事件打成一行 `roll_batch`。

写入量因此是 `DAU × ceil(每日件数 ÷ 50) × 4 行`（一批 = `roll_batch` 1 行 +
它的 2 个二级索引 + `install_day_rolls` 1 行），**与调律件数成正比**，不是
与会话数成正比。按客户端每次启动最多发 4 批算，每人每天上限约 18 行（含
心跳 2 行），免费额度 100k rows/day 对应约 **5,500 DAU**；轻度用户（每天 1 批）
约 1.6 万 DAU。

> 早先这里写的是"与单次会话里调律多少次无关，可以撑到约 4~5 万 DAU"。那是
> 按"一次会话一行"估的，与实际实现（每 50 条事件一行）不符，高估了约一个
> 数量级。选 D1 而非 KV 的结论不受影响——KV 是 1000 writes/day，差距仍在
> 三个数量级以上——但容量规划要按上面的数字来。

**因此现在（几百用户）不需要任何预聚合**——探索阶段还不知道哪些维度
重要，预聚合是有损投影，一旦做了就再也拆不开；该做的是先把"建不回来
的东西"（原始数据）攒起来，"随时能加的东西"（聚合表）留到约
2000~5000 DAU、raw 表接近存储/查询瓶颈时再做（见 `queries/rollup.sql`
的 Tier 1 方案）。

## 三、表结构

完整定义与逐列注释见 [`ops/stats-worker/schema.sql`](../../ops/stats-worker/schema.sql)。这里只给关系概览：

| 表 | 一行代表什么 | 保留期 | 含 install_id |
|---|---|---|---|
| `installs` | 一个安装的"当前值"（首见日/末次活跃日/版本…） | `last_day` 超 180 天清除 | 是 |
| `daily` | 一个安装某一天的心跳（`PRIMARY KEY (day, install_id)`） | 90 天 | 是 |
| `roll_batch` | 一次调律会话的全部事件，`payload` 是 JSON 数组 | 90 天 | 是 |
| `install_day_rolls` | 一个安装某天的调律量摘要（**不含内容**，只有计数） | 90 天 | 是 |
| `agg_dim` | 某天某维度某取值的用户数（DAU/版本分布等的物化结果） | **无限期** | 否 |
| `agg_retention` | 周 cohort 留存 | 无限期 | 否 |
| `agg_roll` / `agg_value` | 调律事件的聚合计数/数值分布（Tier 1 才启用） | 无限期 | 否 |

**"无限期保留"与"不含 install_id"是捆绑的硬规则**：聚合表之所以能无限期
留，正是因为它已经无法关联到任何一次安装。哪天为了排查问题往聚合表里
加了 install_id，无限期保留就不再成立，必须同步改保留策略——这条不是
建议，是设计约束。

心跳与调律事件用**同一个端点** `POST /v1/report` 一次性提交（见
`src/index.js` 的 `handleReport`），批次内每条事件独立校验，单条不合法
只丢那一条，不拖垮整个批次；批次本身以客户端生成的 `batch_id` 为幂等键
（`INSERT OR IGNORE`），重复上报零写入。

## 四、服务端校验边界（诚实记录，不是自我表扬）

心跳字段（`run_env`/`os_name`/`plugin` 等）仍是服务端能穷举的封闭枚举。

**调律事件则刻意不做业务校验**：服务端不知道调律有哪些字段。业务枚举的
真源是客户端的 `game_config.yaml`（词条池随游戏版本增长），服务端没有也
不该有；抄一份过去的代价是客户端每加一个字段都要同步改服务端并重新部署，
而在同步完成之前新字段完全裸奔。取而代之的是**与字段无关的结构闸门**
（`sanitizeEvent`）：嵌套深度、键名格式、数组长度、数值有限性，以及字符串
必须落在"ascii token"或"1~16 个纯中文字符"两种形状之一。这样任何未来字段
自动受同一套约束保护，比逐字段枚举覆盖面更广。

它挡得住路径/邮箱/账号这类几乎总带斜杠、@、空格的 PII 形状，**挡不住一个
纯中文的伪造短串**（例如故意填"角色名叫张三"）——这一点已用真实请求
验证过会被存下来，详见 `ops/stats-worker/README.md`「已知的服务端校验
边界」。真正的枚举白名单在客户端一侧（`schema.py` + `vocab.py` 对
`game_config.yaml` 的实时枚举），只要走的是本项目分发的客户端就不会
触发这条边界；服务端这道闸门是防绕过客户端直接发请求的纵深防御，不是
第二道完整枚举。

## 五、怎么查

`ops/stats-worker/queries/*.sql` 是现成的只读查询，用法见该目录
[README](../../ops/stats-worker/queries/README.md)：

```bash
wrangler d1 execute lvjiang-stats --remote --file=ops/stats-worker/queries/dau.sql
```

个人开发者一周看一两次数字，没有建网页看板——那需要管密钥、管鉴权，
本身也是一个攻击面，CLI 已经够用。

### 每条查询回答什么

| 文件 | 回答什么 |
|---|---|
| `dau.sql` / `mau.sql` | 日活/月活 |
| `version_dist.sql` / `platform_dist.sql` | 版本分布、端类型（desktop/android）与系统分布 |
| `retention_cohort.sql` | 周 cohort 留存 |
| `roll_affix_prob.sql` | P(词条 \| 部位, 材料)——直接对 `roll_batch.payload` 用 `json_each` 展开查，Tier 0 阶段不需要预聚合 |
| `roll_pity_check.sql` | 按 `roll_index` 分桶看命中率是否随次数上升——检测保底/软保底机制 |
| `data_health.sql` | 每 install 每日调律量分位数，找量级离群的异常灌注来源 |
| `roll_export.sql` | 把原始批次导出到本地做离线分析（配合 `--json`） |
| `forget.sql` | `/v1/forget` 接口不可用时的应急手动删除 |

上面这些是**运营视角**（这个月有多少人在用）。**研究视角**——调律到底怎么出
词条、有没有保底、`cap_pct` 是什么分布——SQL 写起来很难读也很难复核，走
`roll_export.sql` 导出到本地，用 `scripts/analyze_telemetry_rolls.py` 生成
报告；方法论与这份数据已知的系统性偏差见
[`docs/10-game/20-affix-analysis/`](../10-game/20-affix-analysis/README.md)。

### 读数字时必须记住的两条

1. **样本量小时按天分组全是噪声**——几百活跃用户，`retention_cohort.sql`
   按周 cohort 已经是让步；`cohort 人数 < 20` 的格子直接不看。
2. **对外公布任何数字都要注明口径**：只统计选择参与的用户（首启弹窗
   opt-in），不是全部装机量。

## 六、紧急处置

`env.DISABLED` 环境变量设为 `"1"` 时，Worker 直接返回 204、不碰 D1——
客户端完全无感（响应码与正常成功一致，见 `transport.py` 的"响应体完全
忽略"设计）。已实测有效：

```bash
wrangler deploy --var DISABLED:1   # 止血
wrangler deploy --var DISABLED:0   # 恢复
```

不需要回滚代码，也不需要等 CI。

### 状态码语义

响应恒为 **204 / 400 / 404 / 405 / 503** 且**无 body**——客户端永远没有
东西可解析，这个通道不可能被用来下发指令（`transport.py` 连响应体都不读）。

| 码 | 含义 | 客户端行为 |
|---|---|---|
| 204 | 已受理，**或有意静默丢弃**（熔断、单 install 日批次上限） | drop 本地缓冲 |
| 400 | 信封畸形（超长 / 非 JSON / `v` 不为 1） | drop |
| 503 | 非预期内部异常（D1 写失败 / 配额耗尽），**数据没落库** | 保留缓冲，下次启动重试 |

204 与 503 的分界是刻意的：熔断和日批次上限属于「收到了，但按设计不存」，
客户端重试多少次都不会被接受，必须让它丢掉缓冲；而 D1 写失败属于「本该存
却没存」，回 204 等于收下之后悄悄扔掉、客户端还以为成功。`/v1/forget` 同理
——删除失败必须回 503，回 204 等于告诉用户「已删除」而实际没删。
