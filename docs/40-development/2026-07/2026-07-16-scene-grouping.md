# Dev Log: 2026-07-16 场景分组管理功能

> 日期：2026-07-16
> 涉及模块：`lvjiang/core/scene_loader.py`、`lvjiang/core/region_config.py`、`lvjiang/ui/region_editor/dialog.py`、`config/system/app.yaml`、`config/system/scenes/`
> 关键词：场景分组、二级 Tab、SceneRegistry、app.yaml 层级结构、PyQt6 bool 陷阱

---

## 一、本日完成

本日一个提交，主线是**场景分组管理功能完整实现**。

### 1.1 场景分组管理

随着场景不断增加（9+），需要分组管理。新增分组概念（如通用/调律/背包），作为场景的上一级组织。

#### 1.1.1 数据结构与配置

**app.yaml 新格式**：`layout_scenes` 从 flat list 改为 `group: [scenes]` 二级结构

```yaml
layout_scenes:
  group_1:
  - game_main_page
  - game_menu_page
  - general_control
  group_2:
  - equip_weapon_detail
  - equip_armor_detail
  - equip_tune_detail
  - equip_tune_result
  group_3:
  - bag_equip_detail
  - bag_item_detail
group_names:
  group_1: 通用
  group_2: 调律
  group_3: 背包
```

**约束**：
- 分组 key 创建后不可变，name 可重命名
- 非空分组不可删除，仅空分组可删
- 启动时校验至少存在一个分组，否则 RuntimeError

**向后兼容**：旧格式 flat list 自动迁移到 `default` 分组

#### 1.1.2 SceneRegistry 分组扩展

`scene_loader.py` 新增：
- `_groups`: dict[str, str] — group_key → group_name
- `_group_order`: list[str] — 分组顺序
- `_group_scenes`: dict[str, list[str]] — group_key → [scene_keys]
- CRUD 方法：`create_group`、`rename_group`、`delete_group`（非空抛异常）、`move_scene_to_group`
- 查询方法：`get_groups()`、`get_group_scenes()`、`get_scene_group()`
- `save_group_config()` 写入新格式到 app.yaml

#### 1.1.3 region_config 加载与缓存

`region_config.py` 新增：
- `_load_group_config()` 解析新格式
- 全局缓存：`SCENE_GROUPS_META`、`GROUP_SCENES`、`GROUP_ORDER`
- `sync_group_cache()`、`get_group_name()`、`get_scene_group()`
- 启动校验：`if not _registry.get_groups(): raise RuntimeError(...)`

#### 1.1.4 二级 Tab UI

`dialog.py` 改为二级 Tab 嵌套：
- 一级 Tab：`_group_tab_widget`（分组级别，可拖拽排序、右键菜单）
- 二级 Tab：`_group_tabs[group_key]`（场景级别，每个分组一个 QTabWidget）
- 顶部工具栏新增"创建分组"按钮
- 分组右键菜单：重命名 / 删除（非空禁用）
- 场景右键菜单：重命名 / 删除 / **更改分组**（子菜单）
- 场景创建时自动归属当前分组

### 1.2 场景重命名

- `equip_bag_detail` → `bag_equip_detail`（装备背包）
- `prop_bag_detail` → `bag_item_detail`（道具背包）
- 相关 workflow 和文档引用同步更新

### 1.3 Bug 修复

#### 1.3.1 PyQt6 QWidget bool 值陷阱

**问题**：分组功能实现后，场景 Tab 全部消失，只能看到分组 Tab。

**根因**：PyQt6 中 `bool(QWidget)` 对未显示的 widget 返回 False。`_rebuild_scene_tabs` 中 `if not scene_tab_widget:` 误判为 True，直接 return 跳过了场景 Tab 创建。

**修复**：所有 `if not scene_tab_widget:` 改为 `if scene_tab_widget is None:`（3 处）

#### 1.3.2 PyQt6 QAction 无 parentMenu 方法

**问题**：场景右键菜单"更改分组"报错 `AttributeError: 'QAction' object has no attribute 'parentMenu'`

**修复**：改用 `action.data() is not None` 判断是否属于"更改分组"子菜单（因为 move_action 都调用了 `setData(gk)`）

---

## 二、关键技术决策

### 2.1 二级 Tab 嵌套架构

```
_group_tab_widget (QTabWidget)
├── _group_tabs["group_1"] (QTabWidget)
│   ├── SceneTab("game_main_page")
│   ├── SceneTab("game_menu_page")
│   └── SceneTab("general_control")
├── _group_tabs["group_2"] (QTabWidget)
│   ├── SceneTab("equip_weapon_detail")
│   └── ...
└── _group_tabs["group_3"] (QTabWidget)
    └── ...
```

**理由**：分组作为独立 QTabWidget 嵌套，支持分组级别和场景级别的独立拖拽排序、独立右键菜单。

### 2.2 分组配置持久化

`save_group_config()` 写入新格式，同时处理场景顺序和分组结构：

```python
def save_group_config(self, path: Path):
    data = {"layout_scenes": {}, "group_names": {}}
    for gk in self._group_order:
        data["layout_scenes"][gk] = self._group_scenes.get(gk, [])
        data["group_names"][gk] = self._groups.get(gk, gk)
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False))
```

---

## 三、文件变更汇总

| 文件 | 操作 | 说明 |
|------|------|------|
| `lvjiang/core/scene_loader.py` | 修改 | 分组数据结构、CRUD、save_group_config |
| `lvjiang/core/region_config.py` | 修改 | 分组加载、缓存、启动校验 |
| `lvjiang/ui/region_editor/dialog.py` | 修改 | 二级 Tab UI、分组/场景 CRUD、右键菜单、PyQt6 bug 修复 |
| `config/system/app.yaml` | 修改 | 从 flat list 迁移为分组格式 |
| `config/system/scenes/bag_equip_detail.yaml` | 新增 | 原 equip_bag_detail 重命名 |
| `config/system/scenes/bag_item_detail.yaml` | 新增 | 原 prop_bag_detail 重命名 |
| `config/system/scenes/equip_bag_detail.yaml` | 删除 | 重命名为 bag_equip_detail |
| `config/system/scenes/prop_bag_detail.yaml` | 删除 | 重命名为 bag_item_detail |
| `config/system/workflows/*.wf` | 修改 | 场景引用更新 |
| `tests/test_parser.py` | 修改 | 场景名更新 |
| `docs/` | 修改 | 文档同步更新 |

---

## 四、PyQt6 经验总结

### 4.1 QWidget bool 值陷阱

**问题**：`bool(QWidget)` 在 PyQt6 中对未显示（`isVisible() == False`）的 widget 返回 False，即使 widget 对象有效存在。

**规则**：所有 QWidget 判空必须用 `if widget is None:`，**禁止**用 `if not widget:`。

### 4.2 QAction 无 parentMenu 方法

**问题**：PyQt6 的 QAction 没有 `parentMenu()` 方法（与 PySide6 不同）。

**替代方案**：通过 `action.data()` 区分不同子菜单的动作，设置唯一标识数据。
