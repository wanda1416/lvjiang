"""总词条 → 八件装备：配置驱动的精确容量分配，独立于 UI 和调律策略。"""
from __future__ import annotations

import copy
from collections import Counter, deque
from dataclasses import dataclass, field

from ...config.equipment_slots import SLOT_SPECS
from ..affix_cap import affix_cap_value
from ..combat.affix_rules import normal_affix_candidates
from ..equip_validator import (
    MAX_ATTRIBUTE_AFFIXES,
    MAX_DIVINE_AFFIXES,
    _categories,
    validate_combination_dict,
)
from ..tuning_rules.models import DYNAMIC_AFFIXES, dynamic_affix_map


@dataclass
class DistributionResult:
    equipment: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    locations: dict[str, list[str]] = field(default_factory=dict)

    @property
    def feasible(self) -> bool:
        return not self.errors


def native_name(name: str, attribute: str, game_config) -> str:
    """只折叠本属；外属保留具体名字，避免三种外属被误计为一条。"""
    mapped = dynamic_affix_map(attribute, game_config=game_config).get(name)
    return mapped if mapped in DYNAMIC_AFFIXES[:2] else name


def distribution_counts(equipment: dict[str, dict], attribute: str, game_config) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for equip in equipment.values():
        for i in range(1, 6):
            name = str((equip.get(f"affix_{i}") or {}).get("name") or "")
            if name:
                counts[native_name(name, attribute, game_config)] += 1
    return dict(counts)


@dataclass
class _Edge:
    target: int
    reverse: int
    capacity: int
    cost: int
    initial: int


class _Flow:
    """整容量最小费用流；最多 40 单位，负费用用于优先填满首词条。"""

    def __init__(self):
        self.graph: list[list[_Edge]] = []

    def node(self) -> int:
        self.graph.append([])
        return len(self.graph) - 1

    def edge(self, source: int, target: int, capacity: int, cost: int = 0) -> _Edge:
        forward = _Edge(target, len(self.graph[target]), capacity, cost, capacity)
        backward = _Edge(source, len(self.graph[source]), 0, -cost, 0)
        self.graph[source].append(forward)
        self.graph[target].append(backward)
        return forward

    def run(self, source: int, sink: int, wanted: int) -> int:
        sent = 0
        while sent < wanted:
            distance = [float("inf")] * len(self.graph)
            previous: dict[int, tuple[int, int]] = {}
            distance[source] = 0
            queue = deque([source])
            queued = {source}
            while queue:
                current = queue.popleft()
                queued.remove(current)
                for index, edge in enumerate(self.graph[current]):
                    value = distance[current] + edge.cost
                    if edge.capacity and value < distance[edge.target]:
                        distance[edge.target] = value
                        previous[edge.target] = (current, index)
                        if edge.target not in queued:
                            queued.add(edge.target)
                            queue.append(edge.target)
            if sink not in previous:
                break
            node = sink
            while node != source:
                parent, index = previous[node]
                edge = self.graph[parent][index]
                edge.capacity -= 1
                self.graph[node][edge.reverse].capacity += 1
                node = parent
            sent += 1
        return sent


#: 八件装备各有首词条，所以全套至少 8 条；上限 40 条。分配引擎的准入校验与
#: 编辑器里加号按钮的可用性都读这一份，避免两处各写一个 40 后悄悄走偏。
TOTAL_AFFIXES_MIN = 8
TOTAL_AFFIXES_MAX = 40


def distribute_affixes(
    counts: dict[str, int], templates: dict[str, dict], *, attribute: str,
    level: int, chengyin: bool, game_config,
) -> DistributionResult:
    """返回合法真实装备及可获取位置。不会修改 templates/counts。

    templates 仅提供槽位武器类型、套装及位置偏好；数值统一按所选等级口径生成。
    未满 TOTAL_AFFIXES_MAX 条可以返回部分普通词条，但八件装备必须各有合法首词条。
    """
    result = DistributionResult()
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in counts.values()):
        result.errors.append("词条数量必须为非负整数")
        return result
    wanted = sum(counts.values())
    if not TOTAL_AFFIXES_MIN <= wanted <= TOTAL_AFFIXES_MAX:
        result.errors.append(
            f"当前共 {wanted} 条；八件装备至少需要 {TOTAL_AFFIXES_MIN} 条首词条，"
            f"最多 {TOTAL_AFFIXES_MAX} 条")
        return result
    aliases = dynamic_affix_map(attribute, game_config=game_config)
    flow = _Flow()
    source, sink = flow.node(), flow.node()
    supply = {name: flow.node() for name, count in counts.items() if count}
    source_edges = {name: flow.edge(source, node, counts[name]) for name, node in supply.items()}
    attack_category, divine_categories = _categories()
    placements: list[tuple[str, str, bool, _Edge]] = []
    first_edges: dict[str, _Edge] = {}
    for spec in SLOT_SPECS:
        template = templates.get(spec.key) or {}
        kind = str(template.get("type") or ("" if spec.is_weapon else spec.part))
        group = game_config.get_type_to_group().get(kind)
        if not group:
            result.errors.append(f"{spec.label}尚未选择有效武器类型")
            return result
        first_names = game_config.get_first_affixes(group, level)
        normal_names = normal_affix_candidates({"type": kind, "level": level}, game_config)
        first_node, regular_node = flow.node(), flow.node()
        # 所有普通位置偏好的总费用小于 100，确保优先填满八个首词条。
        first_edges[spec.key] = flow.edge(first_node, sink, 1, -100)
        flow.edge(regular_node, sink, 4)
        attack_node, divine_node = flow.node(), flow.node()
        flow.edge(attack_node, regular_node, MAX_ATTRIBUTE_AFFIXES)
        flow.edge(divine_node, regular_node, MAX_DIVINE_AFFIXES)
        preferred = {str((template.get(f"affix_{i}") or {}).get("name") or "") for i in range(2, 6)}
        for is_first, candidates in ((True, first_names), (False, normal_names)):
            for name in candidates:
                matched = [key for key in supply if key == name or aliases.get(name) == key]
                if not matched or affix_cap_value(level, name, chengyin=chengyin, game_config=game_config) is None:
                    continue
                category = game_config.get_affix_category(name)
                target = (first_node if is_first else attack_node if category == attack_category
                          else divine_node if category in divine_categories else regular_node)
                gate = flow.node()
                old_first = str((template.get("affix_1") or {}).get("name") or "")
                cost = 0 if (name == old_first if is_first else name in preferred) else 1
                edge = flow.edge(gate, target, 1, cost)
                placements.append((spec.key, name, is_first, edge))
                for key in matched:
                    flow.edge(supply[key], gate, 1)
                    location = f"{spec.label}{'首词条' if is_first else '普通词条'}"
                    if location not in result.locations.setdefault(key, []):
                        result.locations[key].append(location)
        result.equipment[spec.key] = {
            "type": kind, "name": "模拟分配", "quality": "gold", "level": level,
            "original_level": level, "is_chengyin": chengyin,
            "equipment_set": str(template.get("equipment_set") or ""),
        }
    sent = flow.run(source, sink, wanted)
    if sent != wanted:
        left = "、".join(f"{name}缺 {edge.capacity} 个位置" for name, edge in source_edges.items() if edge.capacity)
        result.errors.append(f"无法满足部位、重复词条或神力限制：{left}")
    missing_first = [spec.label for spec in SLOT_SPECS if first_edges[spec.key].capacity]
    if missing_first:
        result.errors.append("缺少合法首词条：" + "、".join(missing_first))
    if result.errors:
        result.equipment.clear()
        return result
    for slot, name, is_first, edge in placements:
        if edge.capacity == edge.initial:
            continue
        equip = result.equipment[slot]
        index = 1 if is_first else next(i for i in range(2, 6) if f"affix_{i}" not in equip)
        caps = game_config.get_affix_caps(level, name) or {}
        equip[f"affix_{index}"] = {
            "name": name, "value": affix_cap_value(level, name, chengyin=chengyin, game_config=game_config),
            "unit": caps.get("unit") or None,
        }
    for slot, equip in result.equipment.items():
        problems = validate_combination_dict(equip)
        result.errors.extend(f"{slot}: {item.message}" for item in problems)
    if result.errors:
        result.equipment.clear()
    return result


def equipment_template(equipment: dict[str, dict]) -> dict[str, dict]:
    """保存白名单：禁止带入指纹、角色、扫描时间、转律状态等私人运行数据。"""
    return {
        slot: {
            "type": str(equip.get("type") or ""),
            "equipment_set": str(equip.get("equipment_set") or ""),
            **{f"affix_{i}": ({"name": str(affix["name"])}
                              if isinstance(affix := equip.get(f"affix_{i}"), dict) and affix.get("name") else None)
               for i in range(1, 6)},
        }
        for slot, equip in copy.deepcopy(equipment).items()
    }


def affix_summary(equipment: dict[str, dict], attribute: str, game_config) -> str:
    """把八件装备的词条压缩成简短描述，如「1剑增2首领增10大外」。

    词条先按词组配置的简称归并（缺简称时保留原名），再把通用属攻并入具体
    大属攻（1 大无相 + 2 大鸣金 → 3 大鸣金），最后按数量升序拼接；数量相同
    按词组声明序，保证同一套装备的描述稳定可复现。
    """
    counts: Counter[str] = Counter()
    for equip in equipment.values():
        for i in range(1, 6):
            name = str((equip.get(f"affix_{i}") or {}).get("name") or "")
            if name:
                counts[name] += 1
    if not counts:
        return ""
    order = {name: index for index, name in enumerate(game_config.get_normal_affix_names())}
    labels: dict[str, int] = {}
    label_order: dict[str, int] = {}
    for name, count in counts.items():
        label = _short_label(name, game_config)
        labels[label] = labels.get(label, 0) + count
        label_order[label] = order.get(name, len(order))
    _merge_generic_attribute_attack(labels, counts, attribute, game_config)
    return "".join(
        f"{count}{label}"
        for label, count in sorted(labels.items(), key=lambda item: (item[1], label_order[item[0]]))
    )


def _short_label(name: str, game_config) -> str:
    """词条的展示简称：词组配置里的别名，没配置就用原名。"""
    aliases = game_config.get_affix_aliases(name)
    return aliases[0] if aliases else name


def _merge_generic_attribute_attack(labels: dict[str, int], counts: Counter[str],
                                    attribute: str, game_config) -> None:
    """把通用属攻（最大无相攻击）并入同池的具体大属攻。

    无相在武器部位就是该流派的属攻，与具体属性攻击共用同一套上限；本属或
    唯一的大属攻才合并，多属攻并存时保持原样，不做猜测。
    """
    generic = next((name for name, key in
                    dynamic_affix_map(attribute, game_config=game_config, weapon=True).items()
                    if key == DYNAMIC_AFFIXES[0]), None)
    if not generic or generic not in counts:
        return
    dynamic = dynamic_affix_map(attribute, game_config=game_config)
    candidates = [name for name in counts if name != generic
                  and dynamic.get(name) in (DYNAMIC_AFFIXES[0], DYNAMIC_AFFIXES[2])]
    target = next((name for name in candidates if dynamic.get(name) == DYNAMIC_AFFIXES[0]), None)
    if target is None and len(candidates) == 1:
        target = candidates[0]
    if target is None:
        return
    generic_label = _short_label(generic, game_config)
    if generic_label not in labels:
        return
    target_label = _short_label(target, game_config)
    labels[target_label] = labels.get(target_label, 0) + labels.pop(generic_label)
