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
