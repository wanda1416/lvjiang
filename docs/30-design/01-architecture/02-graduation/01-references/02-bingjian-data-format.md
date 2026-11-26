# 装备数据外部交换格式（兵鉴 `bingjian-gear` v1）

本文记录律匠把装备数据导出到 `yysls-bingjian.com`（兵鉴）装备库时使用的 JSON 格式。
实现位于 `src/lvjiang/apps/yysls/ui/loadout/bingjian_export.py`，入口为
`export_bingjian()`；两个站点共用的部位词表与导出范围见
`src/lvjiang/apps/yysls/ui/loadout/equipment_exchange.py`。

该格式只用于外部数据交换，不是律匠本地仓储的备份格式，也不定义毕业率计算语义。
律匠自身的毕业率计算见 [毕业率计算引擎](../README.md)，leoq7 通道见
[leoq7 数据格式](01-leoq7-data-format.md)。

## 文件与编码

导出结果是 UTF-8（无 BOM）、两空格缩进、末尾换行的 `.json` 文件，直接送入兵鉴
「导入装备库」。站点侧有三种读取器按顺序尝试，本格式命中第二种：

| 顺序 | 站点读取器 | 识别特征 |
|---|---|---|
| 1 | leoq7 V2/V1 传输文本 | 以 `YYSLS_EQUIPMENT_EXPORT_V2` / `..._V1` 开头 |
| 2 | **兵鉴原生 JSON（本文）** | `format` 等于 `bingjian-gear` |
| 3 | 其他站点卡片文本 | 以 `\|` 开头的 Base64 负载 |

因此同一份装备数据既可以导成 V2 文本喂给兵鉴，也可以导成本文的 JSON；JSON 额外
保留装备名、紫装标记与"转"标记，是兵鉴的主通道。

## 顶层结构

```json
{
  "format": "bingjian-gear",
  "version": 1,
  "name": "角色名",
  "source": "律匠",
  "items": []
}
```

| 字段 | 类型 | 含义 |
|------|------|------|
| `format` | string | 固定 `bingjian-gear`，站点据此选中读取器 |
| `version` | integer | 当前为 `1`；站点对 `version > 1` 直接报错并要求刷新页面 |
| `name` | string | 导入后装备库的名称，律匠写角色名 |
| `source` | string | 来源标识，站点显示为「从「律匠」导出的文件」 |
| `items` | array | 装备条目列表 |

## 装备条目

```json
{
  "name": "踏雪含光",
  "slot": "weapon",
  "weapon": "剑",
  "purple": false,
  "chengyin": true,
  "initial": {"name": "最小外功攻击", "value": 114.1},
  "subs": [{"name": "会心率", "value": 15.2, "transmuted": true}],
  "pitch": {"name": "外功穿透", "value": 16.8}
}
```

| 字段 | 类型 | 含义 |
|------|------|------|
| `name` | string | 装备名；缺省时站点显示「第 N 件」 |
| `slot` | string | 部位 key，见下表 |
| `weapon` | string，仅武器 | 武器名，见下方词表；非武器不得出现 |
| `purple` | boolean | 是否紫装（决定站点按金装还是紫装查固有主属性） |
| `chengyin` | boolean | 是否承音 |
| `initial` | object，可选 | 首词条（律匠 `affix_1`） |
| `subs` | array，可选 | 其余词条（`affix_2`~`affix_5`，最多 4 条） |
| `pitch` | object，可选 | 定音词条 |

每条词条统一为 `{"name": 中文词条名, "value": 数值}`，可选 `"transmuted": true`
表示该条带「转」。站点用 `Number(value)` 读取数值，因此 `value` 必须是数字。

## 部位词表

| 律匠部位 | 导出 `slot` | 站点显示 |
|---|---|---|
| 主/副武器 | `weapon` | 武器 · （`weapon` 字段） |
| 环 | `ring` | 环 |
| 佩 | `pendant` | 佩 |
| 冠胄 | `head` | 冠胄 |
| 胸甲 | `chest` | 胸甲 |
| 胫甲 | `legs` | 胫甲 |
| 腕甲 | `hands` | 腕甲 |

武器名使用站点词表：`剑` / `枪` / `伞` / `扇` / `绳镖` / `双刀` / `陌刀` / `横刀` /
`手甲` / `鼓`（律匠的「舞绫鼓」写作「鼓」，「长剑」归一为「剑」，「唐刀」归一为
「横刀」，「拳甲」归一为「手甲」）。部位或武器名认不出时，律匠不导出该件装备，
而不是猜一个部位——错位的装备比少一件更难发现。

## 站点自己补的部分

- **装备固有主属性**（武器/环/佩的最小·最大外功）：兵鉴按部位与品质查表，不由本
  格式提供，因此低阶装备会按当前版本（115 阶）的表格计算；
- **防具定音类型**：冠胄/胸甲/胫甲/腕甲的定音在站点统一按「指定武学定音」处理，
  本格式只提供数值；
- 词条名不在站点词表里时，站点会保留空白并给出提示，不会阻断整批导入。

## 导出范围

- 已穿戴装备始终参与导出；
- 背包装备按装备页的等级筛选与词条筛选（全部 / 仅定音 / 仅满调律）；
- 模拟装备不导出（与 leoq7 通道一致）；
- 类型或词条数值缺失的装备与词条会被跳过，不写空值。
