# 用户总览页面结构与上传下载

> 状态：已实现（2026-11-11，基线 v0.15.12）。

## 1. 背景与问题

用户总览的「可见结构」——有哪些分组、每组显示哪些列、列顺序、合计行开关、列宽——
此前散落在 `config/session/interface.json` 的两处节点里（`profile.overview_groups`、
`profile.overview_group_order`、`profile.overview_active_group` 与
`ui_state.profile_overview`）。`interface.json` 是**会话级界面状态**（窗口尺寸、当前页面、
批量草稿……），只在本机有效，于是：

- 换台机器登录同一账号，分组与列全没了，得重排一遍；
- 网页端看不到任何结构，只看到「定义」（keys）和「数值」（快照）；
- 列宽按下标数组存，增删列必须手工搬数组，用户名列宽还要横同步到所有分组。

而「定义」层（`profile.yaml`）早就有独立通道（`profile_definition` 表 + 专用路由）。
本需求补上对称的另一半。

## 2. 目标与非目标

目标：

1. 可见结构独立成 `config/session/profile.json`，与 `profile.yaml` 对称：yaml 管定义，
   json 管可见结构。
2. 用**两个独立按钮**（`上传页面结构` / `下载页面结构`）搬运它，**不作为常规同步项**。
3. 远端由别的设备写入时，上传前必须确认是否覆盖；下载必须两次确认。
4. 网页端只读展示结构。

非目标：

- 不做常规同步或自动合并（结构是整体替换，不做逐项 merge）。
- 不做网页端编辑：写入口只有客户端按钮。
- 不把活跃分组、窗口尺寸等本机视图状态纳入同步。

## 3. 存储

`config/session/profile.json`，与 `profile.yaml` 同目录、同生命周期（用户数据，随账号走）。

```json
{
  "version": 1,
  "metric": {"ui_scale": 1.25, "font_px": 13, "lang": "zh_CN"},
  "overview": {
    "group_order": ["默认", "物资"],
    "groups": {
      "默认": {
        "columns": ["niaoniao", "changmingyu"],
        "show_totals": true,
        "name_column_width": 114,
        "column_widths": {"niaoniao": 77, "changmingyu": 77}
      }
    }
  }
}
```

字段语义：

| 字段 | 说明 |
|---|---|
| `version` | 结构版本，当前为 1；缺失即按 v1 迁移/初始化 |
| `metric` | 列宽的度量环境：`ui_scale`（设备缩放比）、`font_px`（面板字体像素）、`lang`（仅诊断用） |
| `group_order` / `groups` | 分组顺序与内容；键在 `profile.yaml` 里已不存在的列**保留**，渲染层自行隐藏 |
| `name_column_width` | 用户名列宽，全局一份 |
| `column_widths` | 按列 key 的像素宽度，增删列天然对齐 |

四条语义决定：

1. **活跃分组不进文件**——它是「上次看哪个分组」的本机视图状态，切换页签的频率远高于
   结构编辑，留在 `interface.json` 的 `ui_state.profile_active_group`。
2. **列宽按 key 存**，不再按下标数组；用户名列宽只存一份，所有分组天然一致。
3. **文件不带机器信息**：写入来源由服务端按认证会话记录，文件里存 provenance 会在拷贝
   或编辑后失真，也会把写操作塞进下载路径。
4. **失效列保留**：定义里删掉 key 不能顺手清空总览配置。

写盘遵循独立 JSON 文件的统一规范（UTF-8、中文不转义、两空格缩进、末尾换行、原子替换），
并用跨进程锁串行化读改写。

## 4. 迁移（v1，一次性、幂等）

| 来源 | 去向 |
|---|---|
| `interface.json` → `profile.overview_groups` | `overview.groups`（`columns` / `show_totals`） |
| `interface.json` → `profile.overview_group_order` | `overview.group_order`（未记录的组按原规则追加在尾部） |
| `interface.json` → `ui_state.profile_overview` | `column_widths`（`columns[i] → widths[i]`，首项→`name_column_width`） |
| `interface.json` → `profile.overview_active_group` | `ui_state.profile_active_group`（不进 `profile.json`） |

- 只在文件缺失且确实存在旧数据时迁移；全新实例不凭空创建文件。
- 迁移前把旧结构写到 `config/session/_backup-<时间戳>/profile-layout-before.json`。
- 成功后删除上述四个旧节点，避免双源。
- `profile.json` 解析失败时保留原文件、记 error 日志、以默认结构展示，不静默重建。

## 5. 列宽折算与 clamp

```
font_ratio  = 当前 font_px / 文件 metric.font_px
scale_ratio = 当前 ui_scale / 文件 metric.ui_scale
width = clamp(round(raw * font_ratio * scale_ratio), lo, hi)

lo = 按当前字体实测的表头宽度 + 留白     # 任何语言/字体下列标题都不会被截断
hi = 480                                  # 挡住别机带来的离谱宽列
```

`raw <= 0`（无记录）时取默认 120 再 clamp。

**折算结果不回写文件**：只有用户自己拖动列宽才更新记录，否则 A 下载→按自己字体折算→
回写→B 再折算，宽度会在两台机器间反复漂移。

## 6. 上传 / 下载

两个按钮放在用户总览工具栏（「数据模型」右侧），**不进入常规同步**：不入 manifest，
不被启动同步或定时同步调用。

上传：

| 情形 | 行为 |
|---|---|
| 未登录 / 读不到令牌 | 提示先登录，不动作 |
| 本机没有结构文件 | 提示先在总览里添加分组 |
| 云端不存在 | 直接上传 |
| 云端存在、来源是本实例 | 直接覆盖 |
| 云端存在、来源是别的实例 | 弹确认框（显示来源设备、时间、组/列对比），默认**不覆盖** |
| 确认期间被改写（`expected_sha` 不符） | 提示「已被改写，请先下载或稍后重试」，不自动重试 |
| 手机端 | 禁用并说明「页面结构以 PC 为准，手机端只支持下载」 |

下载：

1. 第一次确认：来源设备、时间、组/列数对比；
2. 第二次确认：明确本机结构将被替换、备份目录位置；
3. 落地前先解析校验（JSON、版本、结构），失败时一个字节都不改；
4. 成功后把旧结构备份到 `_backup-<时间戳>/` 并热重载总览。

## 7. 服务端通道

- 表 `profile_layout`：`account_id` 主键 + `payload`（gzip 原样）+ `sha256` + `updated_at`
  + `origin_instance_id` / `origin_name` / `platform`。
- 路由 `POST /api/sync/profile/layout`：带 `payload_base64` 为写，否则为读；写入时
  `expected_sha` 不符返回 `409 {error:"conflict", layout:{…远端现行版…}}`。
- 来源由**认证会话**填写（`session.instance_id` + `devices.name`），不采信请求体。
- 写权限仅桌面端：`platform` 为 `android` 或 `web` 时返回 `403 readonly_platform`。
- 与常规同步隔离：不进 `manifest`，不被定义/账本同步调用。
- 但那算账户数据：`/api/sync/state` 的 `not_inited`、`/api/sync/clear`、
  `/api/account/export`、`/api/account/delete` 都必须覆盖它。

## 8. 网页展示

**「快照」子视图按页面结构绘制**（默认 `#/profile/snapshot`）：矩阵不再是「定义里全部键
平铺」，而是与 PC 总览对齐——

- 顶部是分组页签，顺序取 `group_order`，页签上标该组列数；
- 选中分组的表：第 0 列「用户名」，随后按 `columns` 顺序排列，列标题取定义里的 label；
  定义里已不存在的列**与 PC 一样隐藏**并给出计数；合计行仅在 `show_totals` 为真时渲染，
  口径同 PC（quota/regen/stock 累加数值、note 留空、空缺不计）；
- 保留「紧凑 / 舒适」「只看有值」「转置」三个查看开关；
- **只有没上传过页面结构时**才退回旧行为（一屏展示全部键、按 `profile.yaml` 顺序、
  可按类型与定义分组过滤），并在工具条上标明「未上传页面结构，按定义顺序展示」。

另有「结构」子视图（`#/profile/layout`）查看结构本身，只读：

- 顶部：来源设备与平台、更新时间、摘要前 8 位、度量环境；
- 汇总：分组数、列总数、定义里未被任何分组引用的键数、定义里已不存在的列数；
- 每组一行：组名、列数、合计行开关、用户名列宽、列顺序（标签 + 宽度，失效键标灰）；
- 未上传时提示「在客户端『用户总览 → 上传页面结构』传一次」。

## 9. 边界与验收

已实现并验证：

- 迁移：旧 `interface.json` → `profile.json`，含宽度数组→key 映射、顺序保持、失效列保留、
  活跃分组迁移、旧节点清理、备份目录生成、二次调用不再迁移；
- 无旧数据时不创建文件；`profile.json` 损坏时保留原文件并以默认结构展示；
- 分组增删改序、列增删替换排序、合计开关、列宽读写与旧实现行为等价（分组改名与列替换
  时列宽跟随）；
- 上传三个分支（无远端 / 同实例 / 异实例）、`409` 冲突再确认、本机无结构的拒绝；
- 下载两次确认后才落盘、备份存在、损坏 payload 不覆盖本地；
- 服务端：来源取会话、手机/网页 403、`409` 带远端现行版、只有结构时 `not_inited=false`、
  清空后结构消失；
- 网页端：有结构时快照按分组页签 + 组内列顺序绘制、合计行跟随 `show_totals`、失效列隐藏并
  计数；无结构时退回默认平铺视图；`npm run build`（tsc + vite）通过。

不在范围：结构的多端自动合并、网页端编辑、结构文件的加密。

## 10. 相关文档

- 存储契约：[Profile 共享模块](../../30-architecture/31-models/04-profile.md)
- 定义通道：[官方在线配置与会员内容](../../60-userguide/appendix-b-online-config.md)
- 用户操作：[2. 主界面与用户管理](../../60-userguide/02-main-interface.md)
