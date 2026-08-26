# stats-client：仅本机运行的统计控制台

配好一个只读 Cloudflare API Token 后一条命令启动的本地网页：展示用户量/
活跃度这类运营指标，也展示调律分析的汇总结论。数据增量拉到本地 SQLite
缓存，页面基于本地缓存跑，Cloudflare 不可用时历史数据仍然能看。

替代的是"每次都手敲 `wrangler d1 execute` + 手动导出 `rolls.json` 再跑
`scripts/analyze_telemetry_rolls.py`"这套流程——本工具不取代那个脚本，
是把它包起来、加上取数和用户指标两块它没有的能力，见下面「与
`scripts/analyze_telemetry_rolls.py` 的关系」。

定位是**个人本机工具**，不做多用户鉴权、不对外部署。这与
`ops/stats-worker/queries/README.md` 里"不建立需要鉴权的网页看板"的既有
决定并不冲突——那条针对的是对外看板，这里是本机单进程，默认只监听
`127.0.0.1`。

## 快速开始

```bash
cd ops/stats-client
uv sync --extra dev   # 首次；建独立虚拟环境，不影响主项目
uv run stats-client   # 默认监听 127.0.0.1:8765，自动打开浏览器
```

浏览器会先跳到 `/setup`：填一个**只有 `Account.D1:Read` 权限**的 API
Token（Cloudflare 控制台 → My Profile → API Tokens → Create Token，
Permissions 选 `Account` / `D1` / `Read`）、Account ID；Database ID 已按
`ops/stats-worker/wrangler.toml` 预填，不用改。保存时会用一条
`SELECT 1` 校验凭据可用。

- `--port` 换端口，`--no-browser` 启动后不自动开浏览器。
- `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` 环境变量优先于页面填写的值。
- `STATS_CLIENT_DATA_DIR` 环境变量覆盖 `config.json` 自身的落脚位置（默认
  `ops/stats-client/data/`）；配置页里的"本地数据目录"字段控制的是**缓存库**
  放哪，不影响 `config.json` 自己放哪——这是解"先有鸡还是先有蛋"必须要有
  的一个固定锚点。

## 目录结构

```
src/stats_client/
  app.py            FastAPI 入口 + `stats-client` console-script
  config.py         首次配置向导 / 凭据存取（token 走系统密钥环，见下）
  cloudflare.py     Cloudflare D1 REST Query API 的只读客户端（stdlib urllib）
  database.py       本地 SQLite 建表（remote_* 前缀 = 远端镜像）
  sync.py           增量同步编排：daily/roll_batch/install_day_rolls 三种节奏
  metrics_user.py   DAU/WAU/MAU/版本/平台/留存——查询对象是本地缓存
  analysis_bridge.py 薄封装：sys.path 注入后 import 仓库根的
                    scripts/telemetry_analysis，把本地 roll_batch 缓存喂给它
  vocab.py          读 vocab/telemetry_vocab.json 规范枚举快照
                    （部位/词条候选值），不 import lvjiang，见下
  deps.py           FastAPI 依赖：cfg / sqlite 连接 / D1Client
  routes/           总览 / 调律分析 / 数据与同步 / 首次配置
templates/          Jinja2
static/             CSS + 本地打包的 ECharts（不接 CDN，离线也能看缓存报告）
vocab/              主项目导出的规范枚举快照（进 git，见下）
data/               本地缓存库 + 配置/token（gitignore，见下）
tests/              纯逻辑测试全部伪造 D1Client，不碰真实网络
```

## 与 `scripts/analyze_telemetry_rolls.py` 的关系

分析引擎在仓库根的 `scripts/telemetry_analysis/`（纯标准库，不依赖
FastAPI 等重依赖），`scripts/analyze_telemetry_rolls.py` 现在只是调用它的
薄 CLI 壳，命令行行为完全不变。`analysis_bridge.py` 在运行时把
`scripts/` 目录插进 `sys.path` 后直接 `import telemetry_analysis`——不
subprocess 调用 CLI，也不把 `scripts/` 打包成正式依赖（项目的可安装包
边界只到 `src/lvjiang/`，`scripts/` 故意留在边界外，这里用运行时路径
注入维持这个边界）。

"调律分析"页的完整报告与 `python scripts/analyze_telemetry_rolls.py`
跑出来的报告应当**逐字一致**——同一份代码、同一批数据。日常小差异只可能
来自数据源不同（本地缓存 vs. 手动导出的 JSON 快照）。

同一个页面上方是"槽位条件查询"交互表单：终态重建口径（按 `resets` 重建
每件装备最终留下的槽位词条，见 `scripts/telemetry_analysis/slots.py`
模块文档），可以叠加"部位""首词条""给定某格=某词条"任意组合筛选，回答
"腿甲、首词条为「劲」时第 2-5 格出现什么"或"第 2 格出现「会心」以后，
第 3 格及以后的分布"这类问题——这类查询组合太多，写不进固定报告章节，
CLI 侧对应 `--slot-part`/`--slot-first-affix`/`--given-slot`/
`--given-affix`/`--target-slots` 参数（`--help` 查看）。

## 部位/词条候选值从哪来

"槽位条件查询"表单的部位/首词条下拉不是从本地缓存"碰巧出现过的值"里
现取的——样本少时那样会漏掉大半可能取值，用户没法知道该填什么去查一个
还没见过的组合。候选值来自
`vocab/telemetry_vocab.json`：燕云十六声部位/词条的规范枚举快照，由
`scripts/export_yysls_vocab.py`（主项目 venv 跑，需要 `import lvjiang`）
生成，`vocab.py` 只读这份 JSON。放在 `ops/stats-client/vocab/` 而不是
主项目 `config/system/` 下——它是 stats-client 私有的构建产物，不经
`ConfigResolver` 的 system/local 合并，混进配置分层目录只会让人误以为
它参与合并语义、可以被 local 覆盖。

**为什么不直接 `import lvjiang.apps.yysls`**：实测会经
`apps/yysls/__init__.py → i18n` 强制拉 PyQt6（i18n 模块要用它做翻译）。
stats-client 是零 GUI 依赖的独立轻量 venv，不该为了几个下拉框的候选值
多装一个几十上百 MB 的 Qt 库——两边都用同一份 `game_config.yaml` 作单一
真源，只是快照决定"谁来读解析后的结果"。

`game_config.yaml` 改了（新词条、新武器类型……）要记得重新导出：

```bash
python scripts/export_yysls_vocab.py   # 用主项目 venv 跑
```

忘记的话主项目 `tests/yysls/test_telemetry_vocab_export.py` 会红——它把
导出逻辑重新跑一遍在内存里跟已提交的 JSON 比对，不用只靠人记着。

## 本地缓存与增量同步

三张远端表镜像成本地表（`remote_daily`/`remote_roll_batch`/
`remote_install_day_rolls`），节奏不同：

- **`remote_daily`**：Worker 端对 `(day, install_id)` 是 `INSERT ...
  ON CONFLICT DO NOTHING`，同一天的行写入后不再变，keyset 游标即可，只留
  1 天回看窗口做防御性冗余（已用 `ops/stats-worker/src/index.js` 的
  `upsertHeartbeat` 核对）。
- **`remote_roll_batch`**：写入后不再变，但存在延迟上报（本地缓冲、断网
  重连后补发），不能按 day 增量，用 `(received_at, batch_id)` 复合 keyset
  游标。
- **`remote_install_day_rolls`**：当天 `n_events` 会持续累加，最近 3 天
  整窗口重拉 UPSERT，更早日期只追加。

刻意**不长期镜像 `installs` 表**——`remote_daily` 已经冗余存了
`app_version`/`run_env`/`os_name` 等维度列（源头 `schema.sql` 的注释：
"冗余：留存查询免 JOIN installs"），本地做维度分布/留存不需要
`installs`。唯一需要的"当前累计安装数"存进 `remote_scalar`，每次同步用
一条 `SELECT COUNT(*) FROM installs` 刷新，不落地任何 install 级别的行。

每一页数据落库和游标前进在同一个 sqlite 事务里提交，崩溃/中断后重跑不会
出现"游标已前进但数据没写完"。

## 隐私与安全边界

- 只需要 `D1:Read` 权限的 Token；Token 不下发浏览器、不写日志、不进报告。
- 默认只监听 `127.0.0.1`，不做鉴权——单机单用户工具。
- Token 默认写系统密钥环（`keyring`），不支持密钥环的平台退化为
  `data/token.secret`（chmod 0600）；两种情况下都不出现在 `config.json`。
- 本地缓存（`data/stats-cache.sqlite3`）含 `install_id`，与
  `ops/stats-worker/queries/roll_export.sql` 的隐私提示同等对待：只留
  本机、不进仓库、不外发（`data/` 已在 `.gitignore` 里）。
- "数据与同步"页提供一键清空本地缓存；远端不受影响，下次同步按首次同步
  逻辑从 90 天保留窗口重新拉。

## 测试

```bash
cd ops/stats-client
uv run pytest tests/ -v
```

全部用伪造的 `D1Client`（内存里模拟远端三张表），不碰真实网络。冒烟测试
（`tests/test_app_smoke.py`）用 FastAPI `TestClient` 把整个应用真的跑起来，
`/sync/run` 通过依赖覆盖换成假实现，确认没有意外发出真实请求。

`scripts/telemetry_analysis` 的拆分正确性由"拆分前后 CLI 输出逐字节
diff 一致"保证，不在这里重复测——那是拆分那次改动自己的验收标准。

## 已知的第一阶段范围（MVP）

覆盖：配置向导 + 只读 Token 校验、自动增量同步、SQLite 缓存、
DAU/WAU/MAU/版本/平台/留存、调律样本体检 + 现有分析小节等价覆盖、
Markdown 报告导出、同步状态/错误展示。

不在这一阶段：交互式筛选联动、报告快照与前后对比、结构化"结论卡"（可信度
等级 / 依据 / 限制）、参数组合结果缓存、重度用户平衡抽样的可视化、时间窗
差异检测。这些留给后续迭代——分析引擎已经拆到 `scripts/telemetry_analysis/`，
往上加"数据算完、只是没结构化展示"这类能力时不需要再动分析逻辑本身。
