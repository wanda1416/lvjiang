# Earley → LALR 历史等价验证

这是手动审计工具，不进入默认 pytest 或运行期。运行环境需要项目依赖与 Git。

```bash
python scripts/one_off/lalr_parser/verify_equivalence.py --baseline-ref b469be6b
```

指定改造前的提交，工具读取该提交的原始 Earley 语法，与工作区解析器比较：

- system 及存在时的 local 工作流；文件只读，不执行游戏操作。
- 工作流测试中的直接 `parse_text` 常量片段，含非法输入。
- AST 全部 dataclass 字段、值类型及源位置；非法输入的拒绝和语法错误行列。

工具复用现行预处理和 transformer。此次改造只新增方向与按钮规则的 token 适配，旧规则回调未改变；未来若改动这些共享代码，必须重新审计比较方法，不能把本工具当作任意版本间的完整验证。

解析器建表不计入逐文件解析时间。不打印 local 工作流内容，不写用户配置。片段差异只报告类别，需要本地复现检查。
