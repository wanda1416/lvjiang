# DSL 游戏相关函数

装备处理、背包遍历、玩家档案等游戏业务专属函数。

> 总览与速查表见 [06-functions.md](06-functions.md)。

## 目录

- [一、装备处理](#一装备处理)
  - [to_equipment — 装备解析](#to_equipment--装备解析)
  - [make_fingerprint — 装备指纹](#make_fingerprint--装备指纹)
  - [affix_cap / chengyin_cap — 词条上限查询](#affix_cap--chengyin_cap--词条上限查询)
  - [is_good_equip — 装备初筛](#is_good_equip--装备初筛)
  - [evaluate — 装备评估](#evaluate--装备评估)
- [二、背包遍历](#二背包遍历)
  - [check_scroll — 滚动校验](#check_scroll--滚动校验)
  - [notify_scroll — 记录指纹](#notify_scroll--记录指纹)
  - [scroll_advance — 推进状态](#scroll_advance--推进状态)
- [三、玩家档案函数](#三玩家档案函数)
- [四、综合示例](#四综合示例)

---

## 一、装备处理

### to_equipment — 装备解析

将 OCR 原始扫描结果解析为标准装备字典，支持链式字段访问。纯基于 OCR 文字分析，不依赖场景信息。

```
scan [equip_weapon_detail] as $scan_result
eval $main_weapon = to_equipment($scan_result)
collect $main_weapon
```

返回字典字段：`type`、`level`、`quality`、`base_attr`（嵌套 dict）、`affix_1`~`affix_5`（嵌套 dict）等。支持链式访问：

```
if $main_weapon.affix_1.value > 100
    log "首词条数值超过 100"
end
```

### make_fingerprint — 装备指纹

基于 type + level + quality + chengyin + 全部词条(name:value) 生成 MD5 前 8 位 hex 指纹。空数据返回空字符串。

```
eval $equip = to_equipment($scan)
eval $fp = make_fingerprint($equip)
```

### affix_cap / chengyin_cap — 词条上限查询

根据词条名和装备等级查询数值上限。`affix_cap` 返回上限值，`chengyin_cap` 返回承音值（上限的 94%）。自动映射词条名（如 "最大外功攻击" → "外功攻击"），找不到返回 0。

```
eval $equip = to_equipment($result)
eval $cap = affix_cap($equip.affix_1.name, $equip.level)
if $equip.affix_1.value > $cap
    log concat($equip.affix_1.name, " 超标")
end
```

### is_good_equip — 装备初筛

基于 OCR 扫描结果中的词条文本，检查是否包含 ≥ 2 条高价值词条（大外攻、会心、会意、三率、劲、敏、势、武学增效、首领增伤）。

```
scan [equip_weapon_detail] as $result
if is_good_equip($result)
    collect $result
end
```

### evaluate — 装备评估

使用当前流派规则（`config/system/rules/` 下第一个 .yaml）进行完整评估，返回评级结果字典。

```
eval $equip = to_equipment($scan)
eval $result = evaluate($equip)
if $result.rating equals "heirloom"
    log "传家宝！"
end
```

返回字段：`rating`（评级）、`disqualified`（是否取消资格）、`details`（详情列表）等。

---

## 二、背包遍历

用于 grid 滚动遍历场景的指纹管理三件套，配合 `check_scroll` → 处理 → `notify_scroll` → `scroll_advance` 流程使用。

### check_scroll — 滚动校验

比对 grid[1][1] 指纹与滚动管理器预期，返回偏移量：
- `"0"` — 正常
- `"1"` — 没有滚动
- `"-1"` — 滚动过头

```
eval $offset = check_scroll($fp)
```

### notify_scroll — 记录指纹

记录已处理装备的指纹。每行第一列（col=1）的指纹自动记录为行指纹。

```
eval notify_scroll($col, $row, $fp)
```

### scroll_advance — 推进状态

校验通过后调用，移除已滚出的行指纹。

```
eval scroll_advance()
```

---

## 三、玩家档案函数

访问 ProfileDB（玩家档案数据库）的内置函数，支持 quota/regen/stock 三模型。详细说明见 [09-data-channels.md](09-data-channels.md#七profile--玩家档案只读数据源)。

```dsl
# 读取配额
eval $remain = profile_get("niaoniao_of_week")

# 消耗体力（realtime regen，自动处理时间锚点）
eval $tili = profile_inc("tili", -900)

# 查询模型类型
eval $model = profile_model("tili")    # "regen"

# 批量获取
eval $all = profile_all()
```

---

## 四、综合示例

### 装备扫描 → 解析 → 评估 → 收集

```
scan [equip_weapon_detail] as $scan
eval $equip = to_equipment($scan)
eval $fp = make_fingerprint($equip)

# 指纹去重
if $fingerprints.$slot equals $fp
    log "重复装备，跳过"
else
    eval append($fingerprints, $slot, $fp)
end

# 评估
eval $result = evaluate($equip)
eval $total = count_nonempty($result)
log concat("评级: ", $result.rating, " 详情数: ", $total)
```

### 字典变量聚合

```
eval $summary = {}
eval $summary.total_affixes = count_nonempty($scan_result)
eval $summary.status = "done"
collect $summary
```
