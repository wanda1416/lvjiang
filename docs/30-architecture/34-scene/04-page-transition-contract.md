# 页面结构与跳转契约

场景 YAML 声明页面结构和导航关系；DSL 的 `scene` 语句声明流程位置。
两者当前都不驱动页面检测、导航执行或调用栈。现有 `.wf` / Python 编排继续负责实际操作。

## 视图关系

`base` 是编辑器保留的首个视图，不代表所有其他视图的父页面，也不会自动叠加到其他视图。
一个场景可以包含多个独立页面；不同场景文件中的 TAB 也可以属于同一个页面。

| kind | 含义 | owner | 调用上下文 |
|---|---|---|---|
| page | 独立页面（也可作为默认页面内容） | 可选结构归属 | 打开时记录来源 |
| tab | 页面内的 TAB 内容 | 必填，所属页面/容器视图 | 与所属页面共享 |
| viewport | 同页的另一个滚动、翻页取景 | 必填，关联视图 | 与关联页面共享 |
| modal | 弹层 | 可选结构归属 | 打开时记录来源 |

`owner` 支持 `/view`、`scene/view`。结构归属不表示固定返回目标；多入口子页返回本次进入来源。
归属必须存在且不能形成循环。单视图场景可通过 `scene/base` 被关联。

```yaml
views:
- key: base
  name: 衣柜
  kind: page
- key: qingjing
  name: 情境
  kind: tab
  owner: /base
- key: chuanda
  name: 穿搭方案
  kind: page
- key: chuanda_page_2
  name: 方案第二屏
  kind: viewport
  owner: /chuanda
```

旧配置省略 `kind` 时兼容读取：基底解释为 page，非基底 `same_layer: true`（旧默认值）
解释为相对 `/base` 的 viewport，`false` 解释为 page。旧文件不自动批量重写；编辑器保存
明确关系后输出 `kind` / `owner`。旧 TAB 必须重新分类，不能通过布尔值自动识别。

## 转移目标和行为

```yaml
- key: settings
  to: game_settings             # 跨场景基底
  navigation: open
- key: other
  to: /other                   # 本场景视图
  navigation: switch
- key: back
  to: "@caller"               # 本次打开当前页面/弹层时的来源
```

空 `to` 表示不跳转。固定目标仍支持 `scene`、`scene/view`、`/view`；`@caller` 是保留目标，
不按场景 key 校验，不展开为猜测的固定目标。

| navigation | 语义 |
|---|---|
| open | 打开新页面上下文，记录来源场景及当时的视图 |
| switch | 同一页面上下文内切换内容，保留页面调用方 |
| replace | 替换当前页面内容，沿用其调用方，不新增返回层级 |
| 省略 | 行为尚未声明，不根据是否跨场景推断 |

`@caller` 自带返回含义，不再附加 `navigation`。固定跳转行为只能写在可点击且有固定目标的实体上。
`switch` 的源与目标必须归属于同一页面。`open` / `replace` 允许跨场景；页面文件边界不是返回层级。

例如“主页 → 设置游戏 → 设置画面 → 返回”，游戏到画面是 switch，返回目标仍为主页。
“情境 → 穿搭方案 → 返回”，打开方案是 open，返回本次进入的情境视图。
调用方未知时契约保留未知，不以静态入口列表猜测来源。DSL 过程调用方与页面调用方不是同一概念。

## 多视图控件和跨场景引用

实体的 `views` 表示出现位置，空列表表示基底；单归属仍兼容 `view`。布局坐标不跟随视图复制。

公共控件在使用位置解释 `@caller`：设置兑换码弹层引用 `general_control.cancel`，其返回的是
兑换码弹层的调用方。引用项参与跳转收集和校验，来源使用宿主场景及宿主视图。

引用可以覆盖导航语义，坐标、名称等仍由源实体提供：

```yaml
references:
- scene: general_control
  entity: confirm
  view: redeem_code             # 省略 to / navigation：继承源声明
- scene: general_control
  entity: cancel
  view: some_view
  to: ""                       # 显式覆盖为不跳转
  navigation: ""
```

继承源实体的固定相对目标 `/result` 时，仍以源场景解析；引用显式覆盖的相对目标以宿主场景解析。

## 全局入口和布局

```yaml
- key: open_settings
  name: 打开设置
  type: func
  is_clickable: true
  to: game_settings
  navigation: open
  available_from: ["*"]        # 示例；当前系统配置中没有全局入口实例
```

`available_from` 是场景 key 列表；空表示本地入口，`*` 表示全部一级场景。
全局边以实际来源视图建立声明上下文，保留 `definition_scene` 以定位真正的控件定义。
不为目标场景自身扩展入口，也不向子场景扩展。此处只描述页面范围，未建模输入焦点等运行时条件。

按键与禁用状态仍属于布局：桌面 `game_main_page.settings` 绑定 `/`、`game_main_page.bag` 绑定 `B`，
默认布局两者都禁用。快捷键专用区域几何尺寸为零，不代表新增可点击位置——
主页没有物理入口，设置的可点击入口只在 `game_menu_page.settings` 上。
这些配置不会安装全局键盘监听器或改变现有工作流。

静态关系查询未按布局过滤，展示的是各布局的关系合集；执行可用性仍以布局绑定和 `disabled` 为准。

## 编辑器与校验

- 跳转选择器顺序：不跳转、调用方、场景分组；固定目标附带行为选择和入口作用范围。
- 视图管理选择关系类型与关联视图，并显示入口、动态返回和共享调用上下文。
- 引用行可编辑宿主视图及跳转覆盖。
- 视图 key 改名同步关系和跳转引用；被引用的视图不能删除。
- 校验检查目标存在、关系存在/无环、行为合法、switch 同页、全局入口范围有效。
- “未声明入口”不等同实际不可达。基底与 viewport 不按缺少点击入口报警；滚动和快捷键也可能提供入口。

`collect_transitions`、`validate_transitions`、`entries_of_view`、`exits_of_view` 和
`find_unreachable_views` 继续提供只读 API。完整契约校验尚不作为加载或保存的全局门禁；
视图管理保存关系时会检查当前视图的关系是否合法。
