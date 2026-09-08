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
**名称**（key 退到 tooltip 与行数据），与本场景原生定义一眼区分。

灰的含义是「这些字段属于源场景」，**不是「整行不能动」**：

- 坐标、类型、名字、禁用状态只读，要改去源场景改
- **归属视图可改**：双击引用行开一个只有视图清单的小对话框。它是本场景
  自己的数据，改不了的话加错视图之后只能删掉重加
- 判据是 `Region.is_reference`（即 `source_scene` 非空）

### 一次引一批

一次要引十几个 area 是常态（通用控件、公共弹窗），逐条开对话框不现实。所以
`SceneAreaReferenceBatchPicker` 把**筛选**与**选取**分开：

- 上面三个下拉（分组 / 场景 / 视图，视图默认「全部视图」）只管缩小范围
- 下面的多选列表才是选取，区域与坐标混排——两者本质都是 area，
  `add_scene_reference` 也一视同仁，按类型拆成两处只会让人来回切
- 归属视图对本次选中的全部条目共用；个别要挪的，加完双击那一行改

批量添加**逐条独立**：一条撞名不回滚其余的，失败的汇总成一条提示。为一条
同名的全部回滚，用户还得自己找出是哪条。

### 新增之后画布要跟上

引用坐标是布局**加载期**展开的，新加一条不会自己出现在画布上——列表里有、
画布上没有，看着就像加失败了。所以 `expand_one_reference()` 从
`_expand_scene_references` 里拆了出来，编辑器新增引用后只补展开这几条
（`SceneEditorDialog._on_scene_references_added`），**不整份重载**：重载会丢掉
画布上还没保存的改动。

补展开前先把画布上的现状取回 `Layout`，否则随后的 `set_regions` 会把未保存的
改动盖掉。源场景在本布局里没标坐标时展开不出东西，引用行仍以「○ 未放置」示人，
与其他未绑定的实体一致。

`SceneRegistry.add_scene_reference` / `remove_scene_reference` /
`update_scene_reference_views` 是持久化入口，在写盘前重跑全部校验——UI 表单会先
拦一道，但导入和测试替身仍可能绕过。

## 8. 源实体的删除与改名

引用只存 `(源场景, 实体 key)`，坐标运行期从源场景转读。所以源实体一旦变动，
引用要么跟着走，要么必须拦下来。规则是一句话：**引用跟着源实体走，源实体
没了才拦。**

| 对源实体的操作 | 行为 |
|---|---|
| 改 key | `retarget_references()` 同步改指，引用照常有效 |
| 跨场景迁移 | 同上，连场景一起改指（迁移不是删除，实体还在） |
| 删除 | `_reject_if_referenced()` **拒绝**，并报出引用方场景名 |

删除为什么是拦而不是级联删：源定义没了，引用就成了悬空声明——加载期会在内存
里被丢掉，于是那个场景**静静少了一个实体**，直到某条 `.wf` 跑到
`click [场景].[实体]` 才炸。级联删更糟：用户在 A 场景点了删除，B 场景的定义
跟着没了，而他根本不知道 B 引用过它。

报错消息带上引用方的场景名（不只是数量）——光说「有引用」等于让人自己去十几个
场景里翻。要真想删，先去那几个场景移除引用。

校验在 `SceneRegistry.remove_region_from_scene` / `remove_point_from_scene` /
`remove_panel_from_scene` 三个持久化入口里，不在 UI：UI 表单会先拦一道，但导入
和测试替身仍可能绕过。

## 9. 已知未覆盖

源场景在当前布局里没给该实体标坐标时，展开期只打 warning，不阻断。硬性的
「引用项必须在每套布局都有坐标」校验等编辑器接入时一并做。
