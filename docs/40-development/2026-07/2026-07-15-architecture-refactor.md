# Dev Log: 架构文档重构与场景标识符规范化

> 日期：2026-07-15
> 涉及模块：`lvjiang/core/scene_registry.py`、`lvjiang/ui/region_editor/canvas.py`、`lvjiang/ui/region_editor/dialog.py`、`docs/`
> 关键词：文档重构、场景标识符、布局顺序、截图持久化、右键菜单

---

## 变更概览

| 类别 | 变更内容 |
|------|----------|
| 文档重命名 | `overview.md` → `main-window-state-flow.md` |
| 文档重命名 | `ocr-scenes.md` → `scene-region-editing.md` |
| 场景标识符 | `equip_detail` → `equip_weapon_detail` |
| 场景标识符 | `equip_tune` → `equip_tune_detail` |
| 新增场景 | `equip_armor_detail`（装备防具详情，9 字段） |
| 新增场景 | `bag_equip_detail`（装备背包详情，10 字段，Tab 第一列） |
| 布局顺序 | `config.json` 新增 `layouts` 数组管理顺序 |
| 截图存储 | 按布局+场景维度持久化到 `config/local/screenshots/` |
| Bug 修复 | 字段绑定状态刷新、OpenCV 中文路径 |

---

## 场景标识符变更

### 最终场景列表

| 场景 Key | 场景名称 | 字段数 | 优先级 |
|----------|----------|--------|--------|
| `bag_equip_detail` | 装备背包详情 | 10 | 1（Tab 第一列） |
| `equip_weapon_detail` | 装备武器详情 | 8 | 2 |
| `equip_armor_detail` | 装备防具详情 | 9 | 3 |
| `equip_tune_detail` | 装备调律详情 | 5 | 4 |

### 装备背包详情字段

```python
"bag_equip_detail": (
    "装备背包详情",
    [
        ("main_weapon", "主武器"),
        ("sub_weapon",  "副武器"),
        ("ring",        "环"),
        ("pendant",     "佩"),
        ("head",        "冠胄"),
        ("chest",       "胸甲"),
        ("leg",         "胫甲"),
        ("wrist",       "腕甲"),
        ("bow",         "弓箭"),
        ("arrow",       "射玦"),
    ],
),
```

### 装备防具详情字段

相比武器详情，`base_attr` 拆分为 `base_attr` 和 `base_attr_2`（防具有双基础属性）。

---

## 布局顺序管理

### 问题

原有实现从 `layouts/` 目录扫描文件，按字典序排列，无法维持用户创建顺序。

### 解决方案

在 `config.json` 中新增 `layouts` 数组：

```json
{
  "active_layout": "默认布局",
  "layouts": ["VIVO投屏方案", "测试布局", "默认布局"],
  "users": [...],
  "active_user": "蔡元君"
}
```

### 代码变更

| 方法 | 变更 |
|------|------|
| `list_layouts()` | 从 `layouts` 数组读取，不再扫描文件系统 |
| `new_layout()` | 追加到 `layouts` 数组 |
| `delete_layout()` | 从 `layouts` 数组移除 |

---

## 截图持久化

### 存储路径

```
config/local/screenshots/
├── 默认布局/
│   ├── bag_equip_detail.png
│   ├── equip_weapon_detail.png
│   ├── equip_armor_detail.png
│   └── equip_tune_detail.png
└── VIVO投屏方案/
    └── ...
```

### 关键函数

| 函数 | 说明 |
|------|------|
| `layout_screenshots_dir(layout_name)` | 返回布局截图目录路径 |
| `load_scene_screenshot(layout_name, scene_key)` | 加载场景截图（OpenCV 中文路径兼容） |
| `save_scene_screenshot(layout_name, scene_key, image)` | 保存场景截图 |
| `copy_screenshots(src_layout, dst_layout)` | 复制截图目录（另存为时用） |
| `delete_screenshots(layout_name)` | 删除截图目录 |

### OpenCV 中文路径兼容

Windows 上 `cv2.imwrite`/`cv2.imread` 不支持中文路径：

```python
# 保存：imencode + 文件写入
success, buf = cv2.imencode('.png', image)
if success:
    path.write_bytes(buf.tobytes())

# 读取：文件读取 + imdecode
data = path.read_bytes()
buf = np.frombuffer(data, dtype=np.uint8)
img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
```

---

## Bug 修复

### 1. 字段绑定状态不刷新

**现象**：创建区域并绑定字段后，右侧字段列表不显示已绑定状态。

**根因**：`dialog.py` 在 `_apply_layout_to_tabs` 中覆盖了 `scene_tab.py` 设置的 `on_region_changed` 回调，导致区域变化时只标记 dirty 而不刷新字段列表。

**修复**：`_on_any_region_changed` 同时刷新当前 Tab 的字段列表：

```python
def _on_any_region_changed(self):
    self._set_dirty(True)
    current = self._tab_widget.currentWidget()
    if hasattr(current, '_refresh_field_list'):
        current._refresh_field_list()
```

### 2. 刷新截图未前置校验

**现象**：未定位窗口时点击刷新截图，提示"刷新截图失败"，用户不知道原因。

**修复**：`_refresh_capture()` 返回 `(image, error_message)` 元组，未定位时返回明确提示"请先在主窗口定位窗口"。

### 3. 布局切换时截图残留

**现象**：切换到新布局后，旧布局的截图仍然显示。

**修复**：`_apply_layout_to_tabs()` 在无截图时调用 `canvas.clear_image()` 清除旧图片。

---

## 新增功能：右键菜单

### 功能

在已选中区域内右键弹出菜单：
- **复制区域**：创建相同位置和大小的新区域，提示绑定新字段，绑定成功后选中新区域（方便用户拖走）
- **删除区域**：删除选中区域，清除绑定状态，回到全局模式

### 实现

```python
def _show_context_menu(self, pos: QPointF):
    menu = QMenu(self)
    copy_action = menu.addAction("复制区域")
    delete_action = menu.addAction("删除区域")
    action = menu.exec(self.mapToGlobal(pos.toPoint()))
    if action == copy_action:
        self._copy_selected_region()
    elif action == delete_action:
        self.delete_selected()
```

### 交互逻辑

- 在已选中区域内右键 → 弹出菜单
- 在其他位置右键 → 保持原有的画布平移功能

---

## 配置迁移

所有变更均为**无兼容迁移**，不保留旧格式支持代码：

| 迁移项 | 旧 | 新 |
|--------|-----|-----|
| 布局文件场景键 | `equip_detail` | `equip_weapon_detail` |
| 布局文件场景键 | `equip_tune` | `equip_tune_detail` |
| 截图文件名 | `equip_detail.png` | `equip_weapon_detail.png` |
| 截图文件名 | `equip_tune.png` | `equip_tune_detail.png` |

---

## 教训

| 维度 | 教训 |
|------|------|
| 命名 | 场景标识符应统一后缀（`_detail`），便于理解和维护 |
| 顺序管理 | 需要维持用户创建顺序时，显式存储顺序数组优于扫描文件系统 |
| 回调覆盖 | 多层回调设定时注意不要意外覆盖，或采用链式回调设计 |
| 配置迁移 | 一次性迁移优于运行时兼容代码，减少维护负担 |
