# 跨场景 area 引用

> **状态**：已实现（模型 / 加载 / 展开 / 保存过滤 / 校验 / 编辑器 UI）。

## 1. 解决什么

同一块屏幕区域此前会在多个场景里各标一次。实测 `general_control.blank_area`
与 `equip_tune_detail.tune_tip` 就是同一条底部提示：

| 布局 | 中心偏差 |
|------|---------|
| 桌面布局 | dx 1.1% / dy 1.3% |
| 默认布局 | dx 0.5% / dy 2.3%（h 已漂到近两倍） |

一份坐标标两遍，校准了一个就会忘掉另一个。跨场景引用让一块区域**只有一处
坐标真源**，其他场景转读。

顺带解决 DSL 的表达：原先退出调律页的重置确认要写
`click [general_control].[confirm]`——退出控件不属于当前场景，读脚本时看不出
它和调律页的关系。现在可以写 `click [equip_tune_detail].[confirm]`。

## 2. 与 SubsceneRefDef 的区别

**这是两件不同的事，实现时不要混用。**

| | `SubsceneRefDef` | `SceneRefDef`（本文） |
|---|---|---|
| 语义 | 几何嵌套 | 别名透传 |
| 布局侧 | 有自己的外框（`SubsceneRef` 带 x/y/w/h） | 无外框 |
| 坐标 | 相对外框，取屏幕坐标要变换 | 画布归一化，**零变换** |
| 可引用 | `type: subscene` 的场景 | **只能是一级场景** |
| 典型 | `jianghu_card` 摆进 `activity_jianghu` | `general_control.confirm` 接进 `equip_tune_detail` |

「只引用一级场景」这条约束的意义就在最后一行：一级场景的实体坐标本就同属一套
画布归一化，原样搬过来即可。允许引用子场景就得回答「引用项跟不跟外框走、跟哪个
外框走」，那是一整类说不清的问题。

## 3. Schema

```yaml
# config/system/scenes/equip_tune_detail.yaml
references:
- scene: general_control      # 源场景，必须 type: scene
  entity: confirm             # 源实体 key
  view: reset_confirm         # 在本场景哪个视图可见，省略 = 基底视图
- scene: general_control
  entity: cancel
  view: reset_confirm
```

**引用名恒等于源实体 key，不支持重命名**——少一个概念。代价是同一场景无法同时
引用两个同名实体（`general_control.confirm` 和 `general_action.confirm`），
真遇到再加 `as:`，现在不预留。

## 4. 校验规则

解析期（`_load_scene`，写错即该场景加载失败）：

1. `scene` / `entity` 必填
2. 不能自引用
3. 子场景不能声明 references
4. 引用名与本场景任何原生 region/point/panel/subscene_ref **不得同名** ——
   同名直接报错，**绝不静默覆盖**：在 RPA 里「点错地方」的代价太高

加载后（`_drop_invalid_references`，需要全部场景就绪，**丢弃并记 error 而非抛异常**
——单个场景写错不该让整个注册表加载失败，那会连带 UI 起不来）：

5. 源场景存在，且是一级场景（`type: scene`）
6. 源实体在源场景中存在
7. **不能引用引用**：源必须是原生定义，禁止传递

## 5. 运行期：加载展开，保存过滤

**展开在加载期做**（`layout_manager._expand_scene_references`），把源场景的
`Region` / `Point` 原样复制进本场景的坐标表，标上 `source_scene`。

好处是**运行期零改动**：`Layout.get_scene_regions` 仍是纯字典查表，
`click [equip_tune_detail].[confirm]` 自然就通了，`click_region` / `click_any` /
引擎的 `_validate_refs_bound` 一行都不用改。

代价是源场景改坐标后要重新加载布局——本来就要重载。

**保存必须过滤。** 引用项写回布局 JSON 就把引用烘死成拷贝，源场景再改坐标也不
同步，正好毁掉这个特性的全部意义，而且**完全静默**：保存一次配置就污染了，看起来
一切正常。两道闸都要在：

- `Region.to_dict()` / `Point.to_dict()` 无条件 `pop("source_scene")`
- `save_layout` 写盘前按 `source_scene` 非空过滤

`tests/core/test_scene_cross_references.py` 对两道闸都有用例，其中保存那条走的是
真实的 `save_layout` 落盘再读文件。

## 6. 区域和坐标都可引用

`Region` 和 `Point` 本质都是 area，属性同构，**都可以被引用**。引用声明不区分
类型，展开时按源实体的实际类型落进 `regions` 或 `points` 表，各归各的。

因此两个面板的表格列也是同一套：名称 / Key / 类型 / 含文本 / 可点击 / 按键 /
禁用 / 跳转 / 来源。

> **视图过滤必须算上引用项**（`get_view_visible_keys`）。过滤是按场景定义算的，
> 漏掉 `references` 的后果很隐蔽：右侧列表里有这一项，画布上却不画它——因为
> 画布按可见 key 集合过滤，而那个集合没包含引用。

## 7. 编辑器

区域面板表格增加「跳转」「来源」两列。引用行**整行置灰**，「来源」列显示源场景
key，与本场景原生定义一眼区分。

- 引用项**只读**：不显示禁用复选框、不进编辑弹窗。坐标要改得去源场景改
- 只提供「+ 引用区域」/「+ 引用坐标」和「移除引用」这几个操作
- 候选列表自动排除子场景，以及 key 已被本场景占用的实体（同名会抢 key）
- 判据是 `Region.is_reference`（即 `source_scene` 非空）

`SceneRegistry.add_scene_reference` / `remove_scene_reference` 是持久化入口，
在写盘前重跑全部校验——UI 表单会先拦一道，但导入和测试替身仍可能绕过。

## 7. 已知未覆盖

源场景在当前布局里没给该实体标坐标时，展开期只打 warning，不阻断。硬性的
「引用项必须在每套布局都有坐标」校验等编辑器接入时一并做。
