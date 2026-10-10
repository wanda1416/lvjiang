# 毕业率导入验证与缓存契约

> 最后更新：2026-10-09（基线 v0.13.14）。

## 导入边界

运行时只读取模型 JSON，不直接读写 Excel。导入的身份、等级和版本字段由 [JSON 模型契约](03-json-model.md) 定义；同名同等级再次导入创建新版本。

## 验证对账

### 导入时自动验证

`_compile_v2()` 在编译完成后执行双重验证：

1. **Excel 缓存对账**：用 `FormulaModel` 直接求值的结果与 Excel 保存的缓存值对比
2. **编译结果对账**：用 `ProgramRuntime` 以满值输入执行的结果与 `FormulaModel` 结果对比

两者容差均为 `max(1e-6, abs(expected) * 1e-10)`。任一验证失败都会阻断导入。


## 缓存管理

模型注册表缓存实体清单，模型加载缓存按实体路径保存 JSON。以下场景需要清除缓存：

- 覆写方案 JSON 文件后
- 重新导入 Excel 后

```python
from lvjiang.apps.yysls.core.graduation import invalidate_graduation_cache
invalidate_graduation_cache()
```

UI 的方案导入流程会自动调用此函数。

## 扩展契约

不支持的公式函数必须明确报错，不以旧缓存值替代计算；新增函数同时维护公式求值、编译和运行时语义，并通过实际模型对账。

用户导入步骤见 [游戏配置指南](../../60-userguide/04.05-game-config.md#从-excel-导入毕业率方案)。批量命令与开发对账记录见 [开发归档](../../40-development/2026-10/2026-10-09-graduation-maintenance.md)。
