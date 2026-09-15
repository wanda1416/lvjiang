# 地图定义与地图管理

> Layer owner：`core/maps.py`（数据模型与读写）、`core/recognizers/heading.py`（小地图朝向解析）、`ui/map_manager/`（对话框）
> Feeds/Affects：场景注册表（每张地图自带一个 HUD 场景）、布局（HUD 实体坐标）、后续的导航状态机与 locomotion 原语
> Stability：第一版；导航运行时尚未接入

## 为什么要单独一层

界面场景描述的是"屏幕上固定位置的控件"，坐标系是屏幕；地图描述的是"一个玩法
的可导航世界"，坐标系是底图（世界）。把撤离点塞进界面场景，缩放一变距离就
没有意义。所以地图是独立实体，只在"入口"处引用界面场景。

## 一张地图的四类文件

```
maps/{key}.yaml                  地图定义（世界系：POI、导航模式、检测器、UI 引用）
maps/{key}/base.png              底图（不参与版本管理）
scenes/map_{key}.yaml            该地图专属 HUD 场景（创建时自动生成，分组"地图"）
layouts/{layout}/map_{key}.json  各布局坐标（场景编辑器标定；地图管理不碰坐标）
```

全部走 system / local / remote 三层：system 只读、"复制到本地"后修改、远程只新增。
`maps/*.yaml` 已注册为版本化实体。

### 小地图区域为什么随地图走

每个玩法的 HUD 布局都可能不同，所以不能指向某个固定场景的 `minimap`。每张地图
自带 `map_{key}` 场景，实体集固定：

| 视图 | 实体 | 类型 | 用途 |
|---|---|---|---|
| base | `minimap` | region | 小地图可视区域，朝向解析的裁剪窗口 |
| base | `minimap_center` | point | 箭头中心 |
| base | `open_map` | region | 打开大地图；桌面靠快捷键时不标坐标、只绑 `activation_key` |
| full | `full_map` | region | 大地图可视区域，"从当前画面截取"底图按它裁剪 |
| full | `close_map` | region | 关闭大地图 |

两个玩法 HUD 完全一样时，`ui.scene` 可以指向同一个场景。

## 地图定义

```yaml
key: duchenxu
name: 渡尘墟
navigation: { mode: closed_loop }      # closed_loop（无寻路）| pathfind（游戏寻路）
north_up: true
base_image: base.png
ui:
  scene: map_duchenxu
  minimap: minimap
  minimap_center: minimap_center
  open_map: open_map
  full_map: full_map
  close_map: close_map
detectors:
  player_heading:
    method: concave_arrow
    hsv_lower: [18, 90, 120]           # OpenCV HSV，H 0–180
    hsv_upper: [42, 255, 255]
    window_ratio: 0.5                  # 搜索窗口边长 / 小地图短边
    min_area: 12
pois:
  - { key: exit_a, kind: exit, name: 东撤离点, x: 0.71, y: 0.23 }
```

POI 坐标是**底图归一化坐标**。`kind` 只是标签（`exit` / `spawn` / `poi`…），导航
策略按它选目标。

## 小地图朝向解析（`recognizers/heading.py`）

小地图北朝上、箭头永远居中；箭头是黄色内凹（燕尾）形，尖端方向即朝向。算法只
依赖形状：

1. 小地图中心附近按 HSV 取色，取离中心最近且面积足够的连通块；
2. 轮廓凸包 + 凸性缺陷：燕尾缺口是最深缺陷，缺陷两端的凸包顶点是两翼，
   **两翼中点 → 离它最远的凸包点（尖端）** 就是朝向（基线是整个箭头长度）；
3. 缺陷不明显时退化为"质心 → 最远凸包点"，置信度 ≤ 0.5；找不到箭头返回
   `None`，调用方必须停止移动。

角度约定：罗盘方位角，0° = 屏幕上方（北），顺时针为正。合成箭头在 0–355°、三种
缩放下最大误差 2.1°。视角光锥不需要识别：移动时箭头 = 跑动方向，镜头朝向可由
"箭头方向 − 推杆角"反推（见需求文档）。

## 地图管理对话框

| 功能 | 落到 |
|---|---|
| 新建 / 复制到本地 / 删除 | `maps/{key}.yaml` + `scenes/map_{key}.yaml` + `scenes.yaml` 分组 |
| 导入底图（文件 / 从当前画面截取） | `maps/{key}/base.png`；截取时按当前布局的 `full_map` 裁剪，未标定用整帧并提示 |
| POI 标注 | 画布点击新增、点击标记选中、表格改 key/kind/name/x/y |
| HUD 场景绑定 | 下拉选场景与实体 key；"标定 HUD…"拉起场景编辑器定位到该场景 |
| 小地图朝向 | 载入截图 → 按 `minimap` 裁剪 → 识别 → 叠加图与角度；HSV/窗口参数随地图保存 |

原则：地图管理**不做区域标定**，所有屏幕坐标只有场景编辑器一个入口。

## 尚未实现

- 大地图非整图显示（玩家居中、可缩放）时的视口匹配与底图拼接；
- POI / 玩家位置在大地图上的检测器（当前只有朝向）；
- 导航状态机与 locomotion 原语；
- 远程下发底图（png 不在版本化实体内，需要时按模板目录的方式补）。
