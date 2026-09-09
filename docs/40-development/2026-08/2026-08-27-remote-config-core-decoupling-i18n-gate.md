# 开发日志 2026-08-27

> 接续 08-26 ui/ 分层重构、遥测按件粒度、stats-client、v0.7.0/v0.7.1 发布。
> 本轮主题：**`config/remote` 在线配置下发 + `content_version` 仲裁 + core 与插件解耦收尾 + i18n 遮蔽事故与一致性门禁 + 执行前静态校验只查可达过程 + session.json 结构梳理**。

---

## 一、配置合并策略改由插件自行注册（`73cc29f2`）

`REGISTRY_LIST_PATHS` / `PROTECTED_LIST_PATHS` 曾把 `yysls/tune_config.yaml`、`yysls/game_config.yaml` 的路径与语义硬编码进 `core/config/resolver.py`，core 因此认识了「`base_rules` 是登记表」「`weapon_types` 该按 name 判同一性」这类纯游戏领域知识——为后续 `config/remote` 加 `content_version` 仲裁埋了隐患：core 和插件各自维护一套对同一份配置的理解，容易对不上。

- `resolver.py` 两张表对 core 只保留自己的 `scenes.yaml`，新增 `register_registry_list_paths()` / `register_protected_list_paths()` 供插件运行时注册；
- `AppHooks` 新增 `config_policy_modules` 字段，按 `builtin_modules` / `telemetry_modules` 同款「import 即注册」处理；
- 新增 `apps/yysls/config/merge_policy.py` 承接原来两条 yysls 声明。

---

## 二、`content_version` + `config/remote` 在线下发层（`7d233775`）

分两块：先给参与下发的实体文件加内容版本号，再据此实现 remote 层。

### 2.1 `content_version`（`core/config/versioning.py`）

remote 与 system 谁为主的唯一判据。**不是「remote 优先」**：用户升了 App、system 带来 v5 的坐标，而远端还停在给旧版本热修的 v3，无脑覆盖会把配置静默回退，没有报错，只表现为「识别又坏了」。故恒要求 `remote.content_version > system.content_version` 才生效。

- 82 个文件补上初始值（scenes 25 + layouts 50 + tuning_rules 7），`scripts/add_content_version.py` 幂等生成，`--check` 供 CI；逐文件保持原行尾（桌面布局是 CRLF）与末尾换行，**全部 +1/-0 无格式抖动**；
- **system 侧缺失即拒绝 remote 替换（fail-safe）**：漏加字段的后果是「收不到在线更新」而不是「被远端悄悄接管」，前者能被发现；
- 开发模式经 `write_entity` 落盘时自动 +1（内容真变了才加）——这些文件全是 UI 编辑器写的，手写版本号存一次就没；
- 哪些目录参与由 versioning 注册表决定，core 只声明 `scenes`/`layouts`，`yysls/tuning_rules` 由插件经 `config_policy_modules` 注册。

### 2.2 remote 层（`core/config/remote.py` + resolver）

- `resolve_read` 改为 **local > remote（版本闸门）> system**；local 恒最高，用户改过的东西任何下发都不该盖掉；
- `enumerate_entities` 只计入过闸门的 remote 文件，避免列表里冒出读不到的条目；
- `allow_remote_new` 仅 `tuning_rules` 为真：规则管理器对未声明的规则是追加而非报错；`scenes`/`layouts` 新增要在 `scenes.yaml` 登记才有意义，而注册表走发版；
- **manifest 四道闸门**：schema_version / 客户端版本区间（防远端配置与代码 region key 对不上）/ 逐文件 sha256 + `.part` 原子替换 / 路径合法性（拒 `..`、绝对路径、盘符、反斜杠）；
- **撤回 = 从 manifest 移除**：manifest 全量声明，本地多出的一律删；
- 三段式线程拆分，逐条对应 `telemetry/reporter.py` 的硬约束——worker 绝不写 SessionStore；
- **不热切换，下次启动生效**：运行中的工作流读到半新半旧配置难查；
- 开关并入 `settings.network`（与公告 / 更新同层），否则「完全离线模式」这个总闸管不到在线配置。

暂不含设备端（Android `versionCode` 兼作配置解压 stamp，需单独设计）。

---

## 三、i18n 遮蔽事故与一致性门禁（`0980c35e`）

### 3.1 事故

`tr()` **以中文原文为 key** 查表（`i18n._build_translation_map` 里 `result[zh_value] = trans_value`），符号键只用来配对两个 yaml。于是**同一句中文挂在多个符号键下时，排在后面的静默胜出**。

`auto_strings` 是机器抽取的占位段，排在文件最后。其中 52 条 `en == zh` 的未翻译条目，正好盖住了前面写好的手工译文：

```
tr("自动调律")             → "自动调律"   本该是 "Auto Tuning"
tr("请至少选择一个调律规则") → 原文         本该是 "Please select at least one tuning rule"
```

译文早就写好了，只是被一段占位数据盖住。core 13 条、yysls 39 条。删除这 52 条遮蔽用的重复占位后，**英文覆盖率 879 → 930**。

### 3.2 门禁

现有 `test_i18n.py` 只覆盖机制，没有一条检查内容，这些问题才能一路静默漂移。新增 `tests/core/test_i18n_consistency.py`：两侧 key 集合必须对称；同一句中文不得有多种译法。

---

## 四、执行前静态校验只查可达过程（`17b8e953`）

用户执行「扫描备战装备」被拒绝执行，报：

```
page_detection.wf:52 [activity_jianghu].[haoling_of_week] - 区域未绑定
```

那是江湖号令活动页的区域，扫装备根本不去那个页面。

**成因**：`import` 按整文件平铺，`scan_equipped.wf` → `navigation.wf` → `page_detection.wf`，而后者是页面判断函数库，给游戏每个页面各备一个 `is_in_*_page()`。`collect_refs` 遍历 procs 全量，于是拿一个永远不会被调用的过程里的绑定缺失，把整个脚本挡在门外——`check_refs` 失败是 `raise` 不是 warning。

实测影响 7 个出厂脚本，多数校验范围超出一倍以上：

| 脚本 | 校验场景数 |
|------|-----------|
| `purchase_bugan` / `purchase_xinfa` | 11 → 4 |
| `scan_equipped` / `scan_unequipped` | 11 → 5 |
| `scan_wallet` | 10 → 4 |
| `daily_jianghu` | 13 → 10 |
| `weekly_baiye_freight` | 7 → 5 |

**改法：按用途分开范围，而不是一刀切**。新增 `reachable_procs()`，从顶层语句沿 `call` 求传递闭包（`CallProc.name` 恒为静态字符串，DSL 无按变量名调用过程的语法，调用图可完全静态求解）：

- **执行前闸门（默认 `reachable_only=True`）**：只查可达过程。拿不会执行的代码挡住用户是事故。
- **`validate_only` / CI 门禁（`False`）**：连没人调用的过程一起查，否则 `page_detection.wf` 这类库函数里的 key 拼错永远没人发现。

预检比执行更严，方向是安全的：只会「报了但其实不会炸」，不会「预检放过、上机仍炸」。

---

## 五、`session.json` 结构梳理（`ba4b7a3d`）

### 5.1 用户名无校验（会造成实际损坏）

`create_user` 只查空和重名，而用户名**直接当文件名**用（`users/{name}.json`），也会拼进 profile 告警去重的复合键（按 `:` 切分）。实测：

```
'../逃逸' → 落盘 users/../逃逸.json   逃出 users 目录
'a/b'     → 凭空建子目录
'含:冒号' → Windows 下存不了；告警键切分错位，有效记录被当过期删掉
```

加 `is_valid_username`：中文 + 字母数字下划线连字符，1~32 字。校验放在 `UserConfigManager`（产生用户名的唯一入口）而不是只放 UI——挡在这里才对所有调用方成立。

### 5.2 `yysls.json` 收进 session 的 `yysls` 节点

新增 `apps/yysls/config/session_node.py`：读优先 session 节点，节点不存在时回退旧的独立 `yysls.json`；写只写 session 节点（降级回老版本仍能读到自己的数据）。

**兼容判据是「节点在不在」而非「空不空」**——用 `None` 哨兵区分「从没迁移过」与「迁移过、内容被清空」；若按「空就回退」，用户清空配置后旧数据会自己爬回来。

`play_styles` 与 `graduation_session` 原先各抄了一份 tempfile + `os.replace` 样板且都是 load→改→save 的竞态写法，改走 `SessionStore.mutate_node` 后并发安全由它的文件锁统一保证，两文件净减 77 行。

---

## 六、`strip_focus_rect` 改用样式表，去掉析构期段错误的地雷（`01cd21c8`）

原实现是重写 `initStyleOption` 的 `QStyledItemDelegate` 子类。Qt 在视图析构收尾阶段仍会派发排队中的 paint 事件并回调 delegate 虚函数，而该虚函数是 **Python 重写**的，PyQt 重入 Python 时先抛 `wrapped C/C++ object ... has been deleted`，随后在 C++ 侧段错误。

**定位过程**（逐条都跑满 `tests/ui` 验证，不是推断）：

| 尝试 | 结果 |
|------|------|
| `strip_focus_rect` 改成 no-op | 不崩 3/3，确认元凶 |
| delegate 改挂 QApplication | 仍崩 5/5 |
| 改成全局共享单个实例 | 仍崩 5/5 |
| 换成 Qt 原生 `QStyledItemDelegate`（无 Python 重写） | 不崩 3/3 |

结论：问题不在 delegate 的所有权，而在「Qt 在收尾阶段回调 Python 重写的虚函数」这件事本身。故彻底不用 Python 虚函数，改用样式表 `QAbstractItemView { outline: 0; }`。

新旧两种写法的渲染结果经**像素级比对完全一致**，并另有一条「确实改变了渲染」的断言，防止两边都没画导致等价断言假通过。

**已知遗留**：`ui/reference/browser_panel.py` 的 `ThumbnailCheckboxDelegate` 是同一模式，理论上有相同风险；但它要画复选框，无法用样式表替代。

---

## 七、`default $var = {dict}` / `[list]` 存的是 AST 节点（`2bf67c55` 附带修复）

`_resolve_literal` 只拆 `VarRef`/`FieldAccess`，漏了 `Literal`，而 dict/list 字面量的标量元素在解析阶段就被包成了 `Literal` 节点。后果很隐蔽——**节点对象恒为真值**，`if $d.off` 写 `false` 也成立，开关型默认值全部失效且不报错。`eval` 走另一条求值路径本来就是对的，只有 `default` 中招。

这个 bug 不修就没法给 checkgroup 参数写安全兜底：批量执行读的是 `wf_configs` 里存过的配置，用户从没在日常页调过这个脚本时参数为空，遍历不报错，只是一个都不买，表现为「跑完了但什么都没做」。

同一提交把「自动购买不肝」的购买项开放为 checkgroup 参数（六个类目逐项可勾选，默认全选），营生改成 bool 参数。

---

## 八、通用层业务文案去耦（`9ad705fc`）

全部为客户端改动，未触碰 `ops/`、遥测 schema 或任何上报字段。

- **设置页收集说明改由插件声明**：原先写死「仅用于改进内置调律规则…」——那是燕云的说法，换个插件就是错的。现在读 `AppHooks.telemetry_disclosures`（与同意框同一份声明），未装插件时回退中性文案；
- **`parse_number` 不再用 `tr("万")` 做解析**：「万」是 OCR 从游戏画面读到的文本，恒为中文，与界面语言无关。走 `tr()` 意味着「把界面切成英文」会意外改变解析规则——目前两个语言文件都把它映射回「万」所以没出事，但那是巧合不是保证；
- 用 AST 剥离注释与 docstring 后复查，core/workflows/ui 里只剩两处真实业务词汇命中，一并改掉，复查现在为 0。

---

## 九、心跳字段改回 `plugin`，避免服务端静默丢弃（`01e35d8b`）

上游把心跳的 `plugin`（字符串）改成 `apps`（列表）并升到 schema v2，但 `ops/stats-worker` 没有同步。后果不是报错而是**静默丢数据**：

```js
index.js:90   if (!isEnum(hb.plugin, PLUGIN)) return null;
index.js:258  const hb = validateHeartbeat(...); if (hb) await upsert(...)
```

`hb.plugin` 变成 `undefined` → 整条心跳被判空跳过，但 worker 仍返回 200、批次照常入库；客户端只看 HTTP 状态码，据此按「已上报」记账，而心跳每 UTC 日只发一次 → **DAU/WAU/留存全部归零，且事后修好也补不回来**。两边测试当时都是全绿的：worker fixture 用的还是旧形状。

字段回退到 v1 原样，但**解耦成果保留**：取值仍读 `AppHooks.id`（框架登记的稳定 ID），不再靠 grep 模块路径里的 `.yysls.` 猜。

新增 `tests/core/test_heartbeat_worker_contract.py`：直接读 worker 源码断言字段名集合与枚举取值一致，只比会静默丢数据的形状。

---

## 十、DSL 文档拆分（`80f35d23`）

`05-control-flow.md` 原来 431 行、十个章节，把「块结构控制」「跳出顺序执行」「条件写法」三件事混在一处，按 03/04 已有惯例拆开为 05 总览 + 05.1 循环分支 / 05.2 流程跳转 / 05.3 条件表达式。

**条件表达式单独成篇**是因为它不属于任何一条指令——`if` / `loop while` / `loop until` 三者共用同一套写法。

`default` 从控制流**移回** `03.1-basic-commands.md`：它是赋值语句，和 `eval` 一组，跟分支循环没有关系。

---

## 十一、其他

- `c92a2f53` core 与 app 专属行为解耦；`da5e0de4` 批量从表头管理列；`5f0803d4` 单条目跳过生命周期；`fb43af31` 工作流对话框可立即停止；
- `cd3fd4df` 严格校验 `.wf` 文件头元数据；
- `00e1f0c6` 删除 `user_profile` 的两个死函数及其测试（净删 72 行）；
- `425e7aef` 恢复场景工具栏布局；`42de0bd3`/`69293483`/`7808c98b`/`6177de72` 若干 UI 尺寸与样式修正。

---

## 关键设计决策

1. **`content_version` 仲裁不是「remote 优先」**：恒要求 remote 严格大于 system，否则热修版本会把新版配置静默回退。
2. **system 侧缺 `content_version` 即拒绝 remote**：fail-safe 方向选「收不到更新」而非「被远端接管」。
3. **`workflows` 不进在线下发注册表**：scenes/layouts/tuning_rules 是数据，`.wf` 会被执行，下发它等于远程代码执行。
4. **执行前闸门只查可达过程，lint 查全部**：拿不会执行的代码挡住用户是事故；库函数里的拼写错误也必须有人发现。
5. **兼容判据用「节点在不在」而非「空不空」**：否则用户清空配置后旧数据会自己爬回来。
6. **不用 Python 重写的 Qt 虚函数**：Qt 在析构收尾阶段回调它会段错误，与所有权无关。
7. **数据键不过 `tr()`**：OCR 文本、参考图 label、配置枚举恒为中文，翻译它们会让界面语言意外改变解析规则。
