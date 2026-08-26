# 词条分布规律分析

调律出什么词条、数值落在哪、有没有保底——这些是**游戏的客观机制**，律匠只能
通过观测去逼近，猜不出来。本文讲怎么把观测数据变成结论，以及**这份数据在哪些
地方会骗人**。

目标是：把数据下到本地 → 一条命令 → 一份可读的报告。

## 边界

| 内容 | 去哪看 |
|------|--------|
| 收集了什么字段、隐私边界 | [`PRIVACY.md`](../../../PRIVACY.md)、[`08-feedback-and-issues.md`](../../60-userguide/08-feedback-and-issues.md) |
| 服务端表结构、运营指标（DAU/留存/版本分布） | [`30-architecture/06-telemetry.md`](../../30-architecture/06-telemetry.md) |
| 律匠**如何判定**装备好坏（评价规格） | [`10-tuning-rules/`](../10-tuning-rules/README.md) |
| 游戏调律/转律的客观机制描述 | [`01-equipment-system.md`](../01-equipment-system.md) |
| **从数据里发现机制**（本文） | 本目录 |

本文只管「怎么从数据推断机制」。推断出来的机制结论应当回写到
`01-equipment-system.md`，评价规格的调整则走 `10-tuning-rules/`——本文不是它们的
副本，也不该沉淀结论，只沉淀方法。

---

## 一、有哪三份数据

| 来源 | 覆盖 | 字段丰富度 | 拿到成本 |
|------|------|-----------|----------|
| **A. 本地遥测缓冲** `config/local/telemetry/spool/ready/*.ndjson` | 只有你自己这台机器、且尚未上报的部分 | 完整 | 零成本，文件就在本地 |
| **B. D1 生产库导出** | 所有同意上报的用户，90 天 | 完整 | 需要 wrangler 凭据 |
| **C. 调律说明文档** `logs/tuning/**/*.md` 里的 `TUNING_DATA_JSON` 块 | 只有你自己，但**保留全部历史** | 另一套字段 | 零成本 |

三份不是替代关系：

- **A 和 B 是同一个 schema**（`yysls.tuning_roll`，定义见
  [`schemas.py`](../../../src/lvjiang/apps/yysls/telemetry/schemas.py)），逐次
  roll 一条记录，带 `cap_pct` / `is_transferred` / `resets` / `active_rule` /
  `food` / `season`。这是**做分布分析该用的数据**。A 只是 B 的一小片，适合先
  把脚本跑通、确认输出长什么样，再去动生产数据。
- **C 是另一回事**：它按「一件装备」组织，带 `initial_affixes`（进入调律时的
  词条）、`rounds`（逐轮 food 与 new_affix）、`final_rating`、`stop_reason`。
  遥测里**没有**初始词条组合和最终评级（那是刻意的，完整五词条组合属于禁发
  数据），所以「给定首词条后，后续词条的条件概率」这类问题**只能用 C 回答**。

### A / B 的字段（`yysls.tuning_roll`）

一条记录 = 一次 roll 的产出。

| 字段 | 含义 | 分析时的注意 |
|------|------|-------------|
| `part` | 部位（weapon/ring/pendant/head/chest/leg/wrist） | 分层主维度 |
| `weapon_type` | 武器细分（剑/枪/…），非武器无此字段 | |
| `level` / `quality` | 装备等级 / 品阶 | `cap_pct` 的口径依赖 `level` |
| `food` | 材料（none/gold/purple/rainbow） | 分层主维度 |
| `slot` / `roll_index` / `resets` | 第几格 / 本件第几次调律 / 重置过几次 | 保底分析的核心 |
| `mode` | normal / force_tune / tune_full_recycle | 不同模式不可混算 |
| `active_rule` | 当时启用的规则集合，`+` 连接，`none` 表示未启用 | **不是决策者，是使用习惯的代理变量** |
| `affix` | 产出词条名 | 主结果 |
| `cap_pct` | 数值占该词条该等级上限的百分比 | 受 `game_config_customized` 影响 |
| `is_transferred` | 是否转律产物 | **机制不同，必须分开** |
| `season` | 赛季号 | 跨赛季机制可能变 |
| `game_config_customized` | 用户是否改过 `game_config.yaml` | cap 口径失真的标记 |

---

## 二、五分钟拿到一份报告

### 路径 A：先用自己的本地缓冲跑通

```bash
python scripts/analyze_telemetry_rolls.py config/local/telemetry/spool/ready
```

缓冲里的数据一旦上报成功就会被删除（见 [`spool.py`](../../../src/lvjiang/core/telemetry/spool.py)
的 `drop()`），所以这里通常只有很少几条。它的价值是**验证脚本能跑、输出格式
符合预期**，不是得结论。

### 路径 B：导出生产数据做正经分析

```bash
wrangler d1 execute lvjiang-stats --remote --json \
  --file=ops/stats-worker/queries/roll_export.sql > rolls.json

python scripts/analyze_telemetry_rolls.py rolls.json \
  -o report.md \
  --min-version 0.7.0 \
  --max-per-install 2000 \
  --target-affix 会心
```

脚本会自动展开 `roll_batch.payload`（JSON 数组）并把行上的
`install_id` / `day` / `app_version` 回填到每条事件上。

> `rolls.json` 含 `install_id`，是一份可关联的原始样本。放在本机分析目录，
> 不提交仓库、不随 issue 外发。

### 路径 C：分析自己的历史调律记录

```bash
python scripts/analyze_tuning_affixes.py logs/tuning
```

这个脚本比遥测分析器更早存在，回答的是 C 类问题：部位词条分布、第 N 个词条
的分布、**给定首词条后的条件概率**。它优先读 `TUNING_DATA_JSON` 块，读不到
才回退到 markdown 正则（兼容早期报告）。

### 常用参数

| 参数 | 作用 |
|------|------|
| `--min-version 0.7.0` | 剔除旧解析器产出的数据（见下节偏差 4） |
| `--since 2026-08-01` | 只看某日之后 |
| `--max-per-install 2000` | 每个 install 最多取 N 条，抑制重度用户主导（见偏差 1） |
| `--target-affix 会心` | 开启第 3 节保底检测 |
| `--top 15` | 每格列出多少个词条 |

---

## 三、报告怎么读

报告分五节，**顺序是有意的**：

**0. 样本体检** — 先看这节。头部集中度、版本分布、需要分流的样本量。这节不合格，
下面几节的数字就没有意义。脚本在单个 install 占比 ≥30% 时会直接打警告。

**1. P(词条 \| 部位, 材料)** — 主结果，带 Wilson 95% 置信区间。
**两格的区间重叠，就不能说它们不同。** 每格 n < 30 时只报计数不报比例。

**2. cap_pct 分布** — 看的是**形状**不是均值：均匀 → 数值在 [0, cap] 上等概率；
集中高位 → 存在下限保护；多峰 → 离散档位。报告里有分位数表和直方图。

**3. 保底检测** — 按 `roll_index` 分桶，**在「部位 × 材料」层内**比较（原因见
偏差 5）。只有同层内高桶的 CI 下界高于低桶的 CI 上界，才算「命中率随次数上升」
的证据；区间重叠就是**没有证据**，不是「趋势不明显」。

**4. 转律 vs 普通** — 单独对比，因为机制不同。

---

## 四、这份数据已知的系统性偏差

**这一节是本文存在的主要理由。** 上面的命令谁都会敲，下面这些坑不写下来，
每个人都会重新踩一遍，而且踩了不一定知道。

### 1. 少数重度用户主导样本

自愿上报 + 自动调律是长时间批量运行，几个重度用户就能贡献大部分事件。此时
「词条分布」很大程度是**这几个人的部位/材料使用习惯**，不是游戏机制。

- 报告第 0 节会给出头部集中度；
- 用 `--max-per-install N` 重跑，**看结论是否稳定**。不稳定就说明还没有可报的
  结论——这不是参数调不好，这是样本还不够。

### 2. 转律词条混入

`is_transferred=true` 的词条来自转律，与普通调律的出条机制不是一回事。混算会
同时污染词条分布和 `cap_pct` 分布。脚本在第 1、2 节默认排除，第 4 节单独对比。

### 3. 改过 game_config 的样本，cap_pct 口径失真

`cap_pct` = 数值 / 该词条该等级的 cap，而 cap 来自 `game_config.yaml`。用户在
local 层覆盖过这个文件时，分母就不是同一把尺子了。

`game_config_customized` 这个字段**就是为了让下游筛掉这部分样本而存在的**
（见 [`vocab.py`](../../../src/lvjiang/apps/yysls/telemetry/vocab.py) 的
`game_config_customized()`）。脚本在第 2 节排除、第 1 节保留——词条**名**不受
cap 影响。

### 4. 跨版本混算会把解析器 bug 读成游戏机制

词条名和数值来自 OCR。旧版本的解析 bug 会表现为「某个词条异常多」或
「某个数值段异常密集」，看起来完全像一个机制发现。

`app_version` 是**事后剔除坏版本数据的唯一抓手**（`schema.sql:55` 明写）。发现
任何反直觉的分布，第一件事是按 `app_version` 拆开看它是否只存在于某些版本。

### 5. 按 roll_index 分桶时，高桶和低桶不是同一批样本

高 `roll_index` 的记录只来自「前面都没中」的会话。这些会话的部位/材料构成与
低桶**不同**——愿意调到第 50 次的人，用的材料档次通常也不一样。不分层直接比，
读到的差异可能全部来自样本构成，与保底机制无关。

> `ops/stats-worker/queries/roll_pity_check.sql` 目前就是不分层的
> （直接 `GROUP BY roll_bucket`）。它可以当粗筛，**不能当结论**。
> 报告第 3 节做的是分层版本。

### 6. 停止规则会截断会话

自动调律在规则满足时停止，所以每个**正常结束**的会话，最后一次 roll 系统性地
是「命中」的；而材料耗尽或用户中断的会话没有这个终止命中。做「平均多少次出
目标」这类会话级统计时，两类会话不能当成同一回事。

### 7. `active_rule` 不是决策者，是使用习惯的代理

它记录的是「当时启用了哪些规则」，规则决定**何时停**，不决定**出什么**。但启用
不同规则的用户，本身在调不同的部位、用不同的材料。跨 `active_rule` 比较词条
分布，比的很可能是使用习惯而不是机制。

### 8. 样本是自愿上报的

只包含在首启弹窗里同意匿名统计的用户。**对外引用任何数字都必须写明这个口径**，
它不是装机量，也不是活跃用户量。

---

## 五、为什么不给 p 值

报告只给置信区间和计数，不给 p 值，这是刻意的：

几十个词条 × 7 个部位 × 4 种材料，格子数以千计。逐格做「是否偏离均匀」的检验，
即使机制完全均匀，也必然有成片的格子 p < 0.05。而这里**没有预注册的假设**可供
多重比较校正——真实的工作流程是「先看图，再针对看起来奇怪的格子做检验」，这个
顺序本身就让 p 值失去意义。

置信区间能直接回答该回答的问题：这个比例的不确定范围多大？两格是否可区分？
区间宽 = 样本不够，这个信息比一个「不显著」有用得多。

用的是 **Wilson 区间**而非正态近似：词条分布是「几十个词条、每个几个百分点」，
n 小 p 小，正态近似会给出负下界或过窄的区间，恰好是它最不该被使用的场景。

---

## 六、值得问的问题

按「能否用现有数据回答」排序，前几个是现成的：

1. **P(词条 \| 部位) 是均匀的吗？** 不同部位的词条池是否不同？→ 报告第 1 节
2. **材料（food）改变的是什么？** 是词条分布，还是只有 `cap_pct`？
   → 对比第 1 节各 food 列 vs 第 2 节按 food 分层
3. **`cap_pct` 的分布形状是什么？** 均匀 / 三角 / 离散档位？有下限保护吗？
   → 报告第 2 节直方图
4. **存在保底吗？** → 报告第 3 节（必须分层）
5. **转律的词条池与普通调律相同吗？** → 报告第 4 节
6. **`level` / `quality` 影响的是词条分布还是只有数值上限？**
   → 需要在第 1 节基础上加 level 分层
7. **给定首词条，后续词条的条件概率是否独立？**
   → 遥测数据答不了（没有词条组合），只能用 `logs/tuning`，见路径 C
8. **`slot`（第几格）之间有差异吗？** → 现有字段够，脚本暂未覆盖

7、8 两条目前没有现成输出。第 7 条**不应该**通过给遥测加字段来解决——完整词条
组合是明确的禁发数据（见 [`probe.py`](../../../src/lvjiang/apps/yysls/telemetry/probe.py)
的模块注释，那里解释了为什么不挂在 `tune_round_completed` 上）。

---

## 七、脚本索引

| 脚本 | 数据源 | 产出 |
|------|--------|------|
| [`scripts/analyze_telemetry_rolls.py`](../../../scripts/analyze_telemetry_rolls.py) | A 本地缓冲 / B D1 导出 | Markdown 报告（5 节） |
| [`scripts/analyze_tuning_affixes.py`](../../../scripts/analyze_tuning_affixes.py) | C `logs/tuning` | 终端报告：部位分布、第 N 词条分布、条件概率 |
| [`ops/stats-worker/queries/roll_export.sql`](../../../ops/stats-worker/queries/roll_export.sql) | D1 | 原始批次导出，供上面第一个脚本消费 |

两个分析脚本都**只用标准库**：运行期依赖里没有 numpy/pandas/scipy，分析脚本
不该引入只有它自己用的重型依赖。
