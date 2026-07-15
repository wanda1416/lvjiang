# Dev Log: 场景定义 YAML 外部化

> 日期：2026-07-15
> 涉及模块：`lvjiang/core/scene_loader.py`、`lvjiang/core/region_config.py`、`lvjiang/constants.py`、`lvjiang/workflows/equip_analysis.py`、`config/system/app.yaml`、`config/system/scenes/`
> 关键词：YAML 外部化、SceneRegistry、FieldDef、场景加载顺序、type 过滤

---

## 变更概览

| 类别 | 变更内容 |
|------|----------|
| 架构重构 | 硬编码场景类 → YAML 配置文件驱动 |
| 新建模块 | `scene_loader.py`（FieldDef / SceneDef / SceneRegistry） |
| 新建配置 | `config/system/scenes/*.yaml`（5 个场景文件） |
| 新增机制 | `app.yaml` 中 `layout_scenes` 控制场景加载顺序 |
| 工作流优化 | `equip_analysis` 的 SKIP_FIELDS 硬编码 → `type == "func"` 过滤 |
| 删除代码 | `region_config.py` 中 160+ 行硬编码场景类全部移除 |

---

## 设计决策

### YAML 结构

每个场景一个文件，一级字段为 key/name/fields：

```yaml
key: equip_weapon_detail
name: 装备武器详情
fields:
  - key: equip_type
    name: 装备类型
    type: info
  - key: more_func
    name: 更多功能
    type: func
    is_text: false
    is_clickable: true
```

### 字段 type 枚举

| type | 含义 | 示例 |
|------|------|------|
| info | 信息字段 | equip_type, equip_level |
| attr | 属性字段 | base_attr, affix_* |
| func | 功能字段 | main_func, sub_func_* |
| slot | 槽位字段 | slot_main_weapon |
| material | 材料格 | material_1~7 |
| action | 动作按钮 | tune_btn, close_btn |

### 字段属性

- `is_text`（默认 true）：是否需要 OCR 文字识别
- `is_clickable`（默认 false）：是否可点击

### 场景加载顺序

由 `config/system/app.yaml` 的 `layout_scenes` 数组控制：

```yaml
layout_scenes:
  - equip_bag_detail
  - equip_weapon_detail
  - equip_armor_detail
  - equip_tune_detail
  - equip_tune_result
```

新增场景时只需新增 YAML 文件并在 `layout_scenes` 中追加 key。

---

## 核心模块

### SceneRegistry（scene_loader.py）

```python
class SceneRegistry:
    def __init__(self, scenes_dir, scene_order=None):
        # 扫描 YAML 文件加载全部场景
        # 按 scene_order 排序（未指定的追加到末尾）
    
    def get_scene(key) -> SceneDef | None
    def all_scene_keys() -> list[str]    # 按配置顺序
    def all_scenes() -> dict[str, SceneDef]  # 按配置顺序
```

### region_config.py 兼容接口

对外接口不变：
- `FIELD_GROUPS` — 从 SceneRegistry 自动构建
- `EQUIP_FIELDS` — 从 equip_weapon_detail 场景获取
- `get_scene_name()` / `get_scene_fields()` / `get_button_fields()`
- 新增 `get_field_defs()` — 返回完整 FieldDef 列表

---

## 文件变更汇总

| 文件 | 操作 |
|------|------|
| `lvjiang/constants.py` | 新增 `SYSTEM_SCENES_DIR` |
| `config/system/scenes/*.yaml` | 新建 5 个场景文件 |
| `lvjiang/core/scene_loader.py` | 新建（FieldDef + SceneDef + SceneRegistry） |
| `lvjiang/core/region_config.py` | 删除 160+ 行硬编码类，改为 SceneRegistry 驱动 |
| `lvjiang/workflows/equip_analysis.py` | SKIP_FIELDS → type 过滤 |
| `config/system/app.yaml` | 新增 `layout_scenes` 数组 |
