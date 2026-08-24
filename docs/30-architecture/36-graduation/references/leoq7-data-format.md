# 装备数据外部交换格式（V2）

本文记录律匠把装备数据导出到 `yysls.leoq7.com` 时使用的 V2 传输格式。
实现位于 `src/lvjiang/apps/yysls/ui/loadout/leoq7_export.py`，入口为 `export_leoq7()`。

该格式只用于外部数据交换，不是律匠本地仓储的备份格式，也不定义毕业率计算语义。
律匠自身的毕业率计算见 [毕业率计算引擎](../README.md)。

## 传输文本

导出结果是两行文本：

```text
YYSLS_EQUIPMENT_EXPORT_V2
<编码后的负载>
```

首行是固定格式标识，第二行是负载。负载的编码链为：

```text
JSON → UTF-8 字节 → XOR → Base64
```

## 顶层负载

解码后的 JSON 结构：

```json
{
  "kind": "yysls-equipment-export",
  "schemaVersion": 2,
  "roleName": "角色名",
  "items": []
}
```

| 字段 | 类型 | 含义 |
|------|------|------|
| `kind` | string | 固定为 `yysls-equipment-export` |
| `schemaVersion` | integer | 当前为 `2` |
| `roleName` | string | 角色名称 |
| `items` | array | 装备条目列表 |

## 装备条目

```json
{
  "equipmentKey": "EQUIPMENT_WEAPON_SWORD",
  "equipmentName": "踏雪含光",
  "type": "weapon",
  "firstTuning": [{"key": "MAX_EXTERNAL_ATTACK", "value": 114.1}],
  "secondaryTuning": [{"key": "JIN", "value": 72.2}],
  "pitch": [{"key": "EXTERNAL_PENETRATION", "value": 16.8}],
  "isChengyin": true
}
```

| 字段 | 类型 | 含义 |
|------|------|------|
| `equipmentKey` | string | 装备类型标识，如剑、环、胸甲 |
| `equipmentName` | string，可选 | 装备名称，OCR 未取到时省略 |
| `type` | string | 槽位类型：`weapon` / `ring` / `pendant` / `head` / `cloth` / `legs` / `wrist` |
| `firstTuning` | array | 首词条（对应 `affix_1`） |
| `secondaryTuning` | array | 其余普通词条，最多 4 条 |
| `pitch` | array | 定音词条 |
| `isChengyin` | boolean | 是否为承音装备 |

槽位命名与律匠内部不完全一致：胸甲为 `cloth`，胫甲为 `legs`，主副武器统一为 `weapon`。

## 词条值

每条属性统一使用 `{key, value}`：

```json
{"key": "MAX_EXTERNAL_ATTACK", "value": 114.1}
```

- `key` 为外部格式定义的英文属性标识，`value` 统一转为浮点数；
- 无法映射到外部标识的词条不会进入导出结果；
- 定音词条只在武器、环、佩三个槽位判定，命中后写入 `pitch`，否则该词条归入 `secondaryTuning`。

完整的装备类型与词条映射表以 `leoq7_export.py` 中的常量为准。

## 导出范围

- 已穿戴装备始终参与导出；
- 背包装备可按等级筛选，并可选择全部 / 仅定音 / 仅满调律；
- OCR 无法确定装备类型或词条数值时，相应装备或词条会被跳过。
