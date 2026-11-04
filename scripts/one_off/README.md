# 一次性验证脚本

本目录保存已经完成使命、但未来排查历史改造时仍可能需要的迁移或等价性验证。
它们不属于产品的持续运行契约，因此不会被默认 pytest 和发布 CI 收集。

- 只有明确需要复核对应历史改造时才手动运行子目录中的验证入口。
- 当前生产代码仍会执行的数据迁移、兼容读取和 schema 升级必须继续放在
  `tests/` 中做常驻回归，不能移到这里。
- 新的一次性迁移/重构对照不得放进 `tests/` 后再依赖 `skip` 或 marker 排除；
  应从一开始放在本目录，并在完成迁移后保留必要说明与冻结数据。

## DSL 解析器

[LALR 等价验证](lalr_parser/README.md) 对照指定 Git 基线的 Earley 语法与当前解析器。

## Profile 变更脚本

[示例验证](verify_profile_change_example.py) 手动读取 local 仓库的
`workflows/profile/example.wf` 与 `chengyinshi_limit.wf`，在独立临时客户端根和数据库中
验证事件、事务回滚、混合触发链及五个来源上限。上限参数只从实际脚本读取。
运行 `python scripts/one_off/verify_profile_change_example.py`；示例中的循环与异常
分支会有意产生错误日志，最终应输出 `PASS`。它不自动创建真实用户定义。

## 流派目标预置导入

[导入脚本](import_school_target_presets.py) 把外部调律器的 20 条「流派 → 目标词条条数」
展开成系统预置出装搭配。数据与映射内联在脚本里，生成结果写入
`config/system/yysls/gear_sets/`。口径（承音数值、弓玦按流派、套装按推荐、等级不落盘）
见脚本头注释。运行 `python scripts/one_off/import_school_target_presets.py`（需开发模式），
可重复执行覆盖已生成的条目。

## 网页端毕业率对拍

[graduation_web_parity.py](graduation_web_parity.py) 给网页端（`server/web`）的毕业率
实现算一份期望值：读取 `server/web/scripts/graduation-fixture.json` 里的合成装备与
基础属性（构造数据，不含用户信息），用 PC 端链路算出属性表、DPS 与毕业率，写进
`server/web/scripts/graduation-expected.json`。网页端
`server/web/scripts/check-graduation.ts` 用同一份用例跑 TS 实现并与之比对。

用途是证明「网页算出来的数和 PC 是同一条链路」，属于一次性等价性验证；日常由网页端
脚本自己跑（`node scripts/check-graduation.ts`）。模型或用例变更后重新生成：

```bash
python scripts/one_off/graduation_web_parity.py
```

## 废弃文件锁

[停机清理](cleanup_obsolete_locks.py) 仅用于本轮锁收敛后移除已废弃侧车。
退出所有律匠节点后运行 `python scripts/one_off/cleanup_obsolete_locks.py --config config`
盘点，再追加 `--apply` 删除。主节点或用户仍占用时拒绝清理；不改用户数据，
保留主节点及当前用户占用锁，不在客户端启动时自动执行。

## 角色属性有效性判定

[等价验证](compare_role_attr_validity.py) 对照 Git `HEAD` 的 `role_attrs.wf`，把
"外功区间完整 / 至少一门流派的属性攻击区间完整"两条判定，在 4 门派 ×
{缺 min / 缺 max / 都在 / 都缺} × 外功 4 态共 1024 组键存在组合下逐组比对
**取值与类型**。用于证明拆分出的 `attr_range_complete` / `attr_attack_has_school`
与原来的两条 `and/or` 表达式行为一致（语句数变了，AST 逐棵比对不适用）。

基线固定为重构前提交 `71a702ae`（最后一次仍带旧表达式的版本），不要用 `git HEAD`
——重构提交之后 HEAD 就是新版，再跑等于新版跟自己比。需要换基线时用 `--base <rev>`。

运行 `python scripts/one_off/compare_role_attr_validity.py`，全部一致时退出码为 0；
加 `--mutate-max-key` 会把新版的 `max_` 前缀改坏，脚本必须报差异——用于确认这套
比对有分辨率，不是恒真。

## 自动剧情匹配阈值迁移

[migrate_story_threshold.py](migrate_story_threshold.py) 把 0.15.7 及更早版本的
全局阈值（`tools/auto_plot/_settings.json`）搬进每个缺 `match_threshold` 的
配置文件；已经自带阈值的文件不动。0.15.8 起阈值随文件走，`_settings.json` 不再是
产品的一部分。

先干跑看清单，再写入：

```bash
python scripts/one_off/migrate_story_threshold.py
python scripts/one_off/migrate_story_threshold.py --apply --delete-legacy
```

## 工作流产出目录迁移

[migrate_output_to_logs.py](migrate_output_to_logs.py) 把工作流结果从
`config/session/output/` 搬到 `logs/output/`，同时把历史库里指向旧目录的
`*_path` 记录改成新路径。程序本身不需要它——历史库存的是相对客户端根目录的
路径，新旧目录都能打开——它是给用户**自行选择**使用的收拢工具，客户端不会自动
执行。

安全边界：默认干跑；先移文件后改数据库（中断可重跑收敛）；目标位置已有不同内容
的同名文件时不覆盖，源文件与对应记录一起留在原地；改写前把数据库备份到
`logs/one_off/migrate-output-<时间戳>/`。需要客户端未运行。

```bash
python scripts/one_off/migrate_output_to_logs.py                     # 干跑
python scripts/one_off/migrate_output_to_logs.py --apply             # 执行
python scripts/one_off/migrate_output_to_logs.py --root D:\lvjiang   # 指定客户端目录
```

## 历史路径分隔符规范化

[normalize_history_paths.py](normalize_history_paths.py) 把历史库里反斜杠写法的路径
统一成正斜杠：`logs\daily\…` → `logs/daily/…`。2026-10-14 之前生成端用
`str(path.relative_to(PROJECT_ROOT))`，Windows 下就是反斜杠；此后改成
`.as_posix()`，于是存量记录两种写法混杂。Windows 上都能打开，但回传包是跨端协议
（`core.history_transfer` 明确拒绝含 `\` 的名字），手机端也只认 posix。

只改 `config/session/*.db` 里以 `_path` 结尾的文本列，**不动任何文件**；只替换分隔符，
不改变路径指向；UNC 路径跳过；默认干跑；改写前把数据库备份到
`logs/one_off/normalize-paths-<时间戳>/`；可重复执行。需要客户端未运行。

```bash
python scripts/one_off/normalize_history_paths.py            # 干跑
python scripts/one_off/normalize_history_paths.py --apply    # 执行
```
