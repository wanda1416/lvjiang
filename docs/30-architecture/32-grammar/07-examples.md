# DSL 完整示例

## 一、装备分析（if/else + 顺序执行）

```
# 装备分析工作流（逐个点击扫描）

click [bag_equip_detail].[main_weapon]
wait page_refresh_wait
scan [equip_weapon_detail] as $main_weapon_scan
eval $main_weapon = to_equipment($main_weapon_scan)
collect $main_weapon

click [bag_equip_detail].[sub_weapon]
wait page_refresh_wait
scan [equip_weapon_detail] as $sub_weapon_scan
eval $sub_weapon = to_equipment($sub_weapon_scan)
collect $sub_weapon

# ... 其他部位类似
```

## 二、调律决策树骨架（loop + if + goto + break）

```
@tune_start
click [equip_tune_detail].[auto_add]
wait step_interval
click [equip_tune_detail].[tune_btn]
wait page_refresh_wait
scan [equip_tune_result] as $tune_result

if $tune_result.result equals "成功"
    log "调律成功"
    goto tune_done
end

if $tune_result.result contains "失败"
    loop 3
        click [equip_tune_detail].[retry]
        wait step_interval
    end
    goto tune_start
end

log "未知结果，停止"
break

@tune_done
click [equip_tune_result].[close_btn]
```

## 三、批量调律（for + list + call）

```
# 3x6 背包全槽位批量调律
eval $all_slots = [
    "bag_1_1", "bag_1_2", "bag_1_3", "bag_1_4", "bag_1_5", "bag_1_6",
    "bag_2_1", "bag_2_2", "bag_2_3", "bag_2_4", "bag_2_5", "bag_2_6",
    "bag_3_1", "bag_3_2", "bag_3_3", "bag_3_4", "bag_3_5", "bag_3_6"
]

for $slot in $all_slots
    eval $bag_slot = $slot
    call "subcall/single_tuning.wf"
end
```
