# 场景扩展旧步骤与配置示例

> 归档日期：2026-10-09；这是整理日期，不代表原方案的提出或验证日期。
> 以下保留旧方案、排查或维护过程，不能作为当前用户操作或运行契约。

## 扩展新场景

### 1. 新建 Scene YAML

在 `config/system/scenes/` 下新建 YAML 文件：

```yaml
# config/system/scenes/transfer.yaml
key: transfer
name: 转律界面
regions:
  - key: cost
    name: 消耗材料
    type: attr
  - key: confirm_btn
    name: 确认
    type: func
    is_text: true
    is_clickable: true
points:
  - key: slider_handle
    name: 滑块手柄
```

### 2. 注册到布局配置

在 `config/system/scenes.yaml` 的 `scenes.<分组>.items` 中添加场景 key：

```yaml
# config/system/scenes.yaml
scenes:
  - bag_equip_detail
  - bag_item_detail
  - equip_weapon_detail
  - equip_armor_detail
  - equip_tune_detail
  - game_main_page
  - game_menu_page
  - general_control
  - transfer  # 新增
```

### 3. 在编辑器中配置坐标

打开场景管理 → 切换到新场景 Tab → 导入截图 → 在画布上放置 Region 和 Point 并调整坐标。
