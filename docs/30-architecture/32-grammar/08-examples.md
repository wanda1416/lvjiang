# DSL 完整示例

## 目录

- [一、装备分析（if/else + 顺序执行）](#一装备分析ifelse--顺序执行)
- [二、调律决策树骨架（loop + if + goto + break）](#二调律决策树骨架loop--if--goto--break)
- [三、批量调律（for + list + call）](#三批量调律for--list--call)
- [四、重试 + 兜底 + 字典聚合（新语法协同）](#四重试--兜底--字典聚合新语法协同)
- [五、异常重试 + 用户介入（try/catch + confirm + pause）](#五异常重试--用户介入trycatch--confirm--pause)

## 一、装备分析（if/else + 顺序执行）

```
# 装备分析工作流（逐个点击扫描）

click [bag_equip_detail].[main_weapon]
wait @page_refresh_wait
scan [equip_weapon_detail] as $main_weapon_scan
eval $main_weapon = to_equipment($main_weapon_scan)
collect $main_weapon

click [bag_equip_detail].[sub_weapon]
wait @page_refresh_wait
scan [equip_weapon_detail] as $sub_weapon_scan
eval $sub_weapon = to_equipment($sub_weapon_scan)
collect $sub_weapon

# ... 其他部位类似
```

## 二、调律决策树骨架（loop + if + goto + break）

```
@tune_start
click [equip_tune_detail].[auto_add]
wait @step_interval
click [equip_tune_detail].[tune_btn]
wait @page_refresh_wait
scan [equip_tune_detail].[tune_affix, tune_tip] as $tune_result

if $tune_result.result equals "成功"
    log "调律成功"
    goto tune_done
end

if $tune_result.result contains "失败"
    loop 3
        click [equip_tune_detail].[retry]
        wait @step_interval
    end
    goto tune_start
end

log "未知结果，停止"
break

@tune_done
click [equip_tune_detail].[close_btn]
```

## 三、批量调律（for + list + call）

```
# 3x6 背包全槽位批量调律
eval $all_slots = [
    "bag_1_1", "bag_1_2", "bag_1_3", "bag_1_4", "bag_1_5", "bag_1_6",
    "bag_2_1", "bag_2_2", "bag_2_3", "bag_2_4", "bag_2_5", "bag_2_6",
    "bag_3_1", "bag_3_2", "bag_3_3", "bag_3_4", "bag_3_5", "bag_3_6"
]

import "subcall/single_tuning.wf"

for $slot in $all_slots
    eval $bag_slot = $slot
    call single_tuning()
end
```

## 四、重试 + 兜底 + 字典聚合（新语法协同）

```
# 重试扫描，最多 3 次，失败走兜底逻辑
eval $attempt = 0
eval $success = 0

loop while $attempt < 3
    eval $attempt = $attempt + 1
    try
        scan [target_scene] as $scan_result
        eval $success = 1
        break
    catch $err
        log concat("第 ", $attempt, " 次失败: ", $err)
        wait @retry_interval
    end
end

if $success equals 0
    log "扫描全部失败，执行兜底"
    goto fallback_handler
end

# 字典聚合：统计各分类数量
eval $counts = {}
for item in $items
    eval $cat = $item.category
    eval $has = has_key($counts, $cat)
    if $has
        eval $counts.$cat = $counts.$cat + 1
    else
        eval $counts.$cat = 1
    end
end

# 遍历结果
for k in keys($counts)
    log concat(k, ": ", $counts.$k)
end

@fallback_handler
log "兜底处理完成"
```

## 五、异常重试 + 用户介入（try/catch + confirm + pause）

```
# 重试扫描，失败时询问用户是否重试，最终兜底
eval $attempt = 0
eval $success = false

loop while $attempt < 3
    eval $attempt = $attempt + 1
    try
        scan [target_scene] as $result
        eval $success = true
        break
    catch $err
        eval $retry = confirm(concat("第 ", $attempt, " 次失败: ", $err, "\n是否重试？"))
        if not $retry
            break
        end
    end
end

if not $success
    eval pause("自动流程失败，请手动完成后点击确定")
end
```
