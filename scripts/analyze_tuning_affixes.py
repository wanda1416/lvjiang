#!/usr/bin/env python3
"""
调律词条分布统计脚本

分析 logs/tuning 目录下的所有调律报告，统计：
1. 不同部位的词条分布
2. 第 N 个词条出现各种词条的概率
3. 给定首词条后，后续词条的条件概率
"""

import json
import re
from pathlib import Path
from collections import defaultdict, Counter
from typing import Dict, List, Tuple
import sys


# 正则表达式
RE_EQUIP_HEADER = re.compile(
    r'^## \d+\.\s+(.+?)\s+·\s+(主武器|副武器|环|佩|冠胄|胸甲|胫甲|腕甲)（(\d+)级\s+(金色|紫色|蓝色)）'
)
RE_INITIAL_AFFIXES = re.compile(r'^进入调律时词条（(\d+)/5）：')
RE_AFFIX_LINE = re.compile(r'^-\s+(.+?)\s+[\d.]+(?:%|\（[\d.]+%\）)?$')
RE_ROUND_NEW = re.compile(r'新词条「(.+?)\s+[\d.]+(?:%|\（[\d.]+%\）)?」')
RE_FINAL_RATING = re.compile(r'^最终评级：(.+?)：(顶级|优秀|一般|垃圾)')


def parse_tuning_file(filepath: Path) -> List[Dict]:
    """解析单个调律报告文件（优先 JSON 数据块，fallback 到 markdown 解析）"""
    try:
        content = filepath.read_text(encoding='utf-8')
    except Exception as e:
        print(f"警告：无法读取文件 {filepath}: {e}", file=sys.stderr)
        return []

    # 优先尝试 JSON 数据块
    records = _parse_json_block(content, filepath.name)
    if records is not None:
        return records

    # fallback：markdown 正则解析
    return _parse_markdown(content, filepath.name)


def _parse_json_block(content: str, filename: str) -> List[Dict] | None:
    """尝试从 HTML 注释中提取 JSON 数据块"""
    marker_start = '<!-- TUNING_DATA_JSON'
    marker_end = 'TUNING_DATA_JSON -->'
    idx_start = content.find(marker_start)
    if idx_start < 0:
        return None
    idx_end = content.find(marker_end, idx_start)
    if idx_end < 0:
        return None

    json_text = content[idx_start + len(marker_start):idx_end].strip()
    try:
        raw_records = json.loads(json_text)
    except json.JSONDecodeError:
        return None

    # 将 JSON 记录转换为分析脚本的统一格式
    # 部位名映射：type 字段 → 标准部位名
    _TYPE_TO_PART = {
        '环': '环', '佩': '佩', '冠胄': '冠胄', '胸甲': '胸甲',
        '胫甲': '胫甲', '腕甲': '腕甲', '主武器': '主武器',
        '副武器': '副武器',
    }
    _QUALITY_MAP = {
        'gold': '金色', 'purple': '紫色', 'blue': '蓝色',
    }

    records = []
    for rec in raw_records:
        etype = rec.get('type', '')
        part = _TYPE_TO_PART.get(etype, etype)
        quality_raw = rec.get('quality', '')
        rarity = _QUALITY_MAP.get(quality_raw, quality_raw)

        # 首词条
        initial = rec.get('initial_affixes') or []
        first_affix = None
        if initial:
            first_affix = initial[0].get('name')

        # 新词条（从 rounds 提取）
        new_affixes = []
        for r in (rec.get('rounds') or []):
            affix_text = r.get('new_affix', '')
            affix_name = extract_affix_name(affix_text)
            new_affixes.append(affix_name)

        # 组合词条列表
        all_affixes = []
        if first_affix:
            all_affixes.append(first_affix)
        all_affixes.extend(new_affixes)

        # 评级：从 final_rating 提取最高评级
        rating = None
        final_rating_str = rec.get('final_rating') or ''
        for r in ['顶级', '优秀', '一般', '垃圾']:
            if r in final_rating_str:
                rating = r
                break

        records.append({
            'name': rec.get('name', ''),
            'part': part,
            'level': rec.get('level'),
            'rarity': rarity,
            'affixes': all_affixes,
            'first_affix': first_affix,
            'new_affixes': new_affixes,
            'rating': rating,
            'file': filename,
        })
    return records


def _parse_markdown(content: str, filename: str) -> List[Dict]:
    """markdown 正则解析（旧报告 fallback）"""
    equipment_list = []
    lines = content.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 匹配装备标题
        m_header = RE_EQUIP_HEADER.match(line)
        if m_header:
            equip_name = m_header.group(1)
            part = m_header.group(2)
            level = int(m_header.group(3))
            rarity = m_header.group(4)

            first_affix = None  # 首词条
            new_affixes = []    # 调律过程中获得的新词条
            rating = None
            i += 1

            # 解析该装备的所有内容
            while i < len(lines):
                curr = lines[i].strip()

                # 遇到下一个装备标题或运行结束
                if curr.startswith('## ') or curr.startswith('# 调律说明'):
                    break

                # 初始词条 - 只提取首词条（第一个）
                m_init = RE_INITIAL_AFFIXES.match(curr)
                if m_init:
                    i += 1
                    # 只读取第一个词条作为首词条
                    if i < len(lines) and lines[i].strip().startswith('- '):
                        affix_line = lines[i].strip()
                        m_affix = RE_AFFIX_LINE.match(affix_line)
                        if m_affix:
                            first_affix = extract_affix_name(m_affix.group(1))
                        i += 1
                    # 跳过剩余的初始词条
                    while i < len(lines) and lines[i].strip().startswith('- '):
                        i += 1
                    continue

                # 新一轮 - 提取新词条
                if curr.startswith('第 ') and '轮' in curr:
                    m_round = RE_ROUND_NEW.search(curr)
                    if m_round:
                        new_affix = extract_affix_name(m_round.group(1))
                        new_affixes.append(new_affix)
                    i += 1
                    continue

                # 最终评级
                m_rating = RE_FINAL_RATING.match(curr)
                if m_rating:
                    rating = m_rating.group(2)

                i += 1

            # 组合所有词条：首词条 + 新词条
            all_affixes = []
            if first_affix:
                all_affixes.append(first_affix)
            all_affixes.extend(new_affixes)

            equipment_list.append({
                'name': equip_name,
                'part': part,
                'level': level,
                'rarity': rarity,
                'affixes': all_affixes,
                'first_affix': first_affix,
                'new_affixes': new_affixes,
                'rating': rating,
                'file': filename,
            })
        else:
            i += 1

    return equipment_list


def extract_affix_name(text: str) -> str:
    """从词条文本中提取纯名称（去掉数值和百分比）"""
    text = text.strip()
    # 匹配：名称 数值（百分比） 或 名称 数值
    m = re.match(r'^(.+?)\s+[\d.]+(?:\（[\d.]+%\）)?$', text)
    if m:
        return m.group(1).strip()
    return text


def analyze(records: List[Dict]):
    """统计分析"""
    # 按部位统计词条频率
    part_affix_counter = defaultdict(Counter)
    # 按位置统计词条频率
    position_affix_counter = defaultdict(Counter)
    # 按部位+位置统计
    part_position_affix_counter = defaultdict(lambda: defaultdict(Counter))
    # 首词条 -> 后续词条
    first_to_next = defaultdict(Counter)
    # 首词条 -> 位置N的词条
    first_to_position = defaultdict(lambda: defaultdict(Counter))
    # 各部位装备数量
    part_count = Counter()
    # 各评级数量
    rating_count = Counter()
    # 各品阶数量
    rarity_count = Counter()
    # 合并统计：忽略位置，直接统计所有调律新词条
    merged_new_affix_counter = Counter()

    for rec in records:
        part = rec['part']
        affixes = rec['affixes']
        new_affixes = rec.get('new_affixes', [])
        rating = rec['rating']
        rarity = rec['rarity']

        part_count[part] += 1
        if rating:
            rating_count[rating] += 1
        rarity_count[rarity] += 1

        # 合并统计：所有新词条
        for affix in new_affixes:
            merged_new_affix_counter[affix] += 1

        for idx, affix in enumerate(affixes):
            pos = idx + 1
            part_affix_counter[part][affix] += 1
            position_affix_counter[pos][affix] += 1
            part_position_affix_counter[part][pos][affix] += 1

            if idx == 0 and len(affixes) > 1:
                for later_idx in range(1, len(affixes)):
                    first_to_next[affix][affixes[later_idx]] += 1
                    first_to_position[affix][later_idx + 1][affixes[later_idx]] += 1

    return {
        'part_affix': part_affix_counter,
        'position_affix': position_affix_counter,
        'part_position_affix': part_position_affix_counter,
        'first_to_next': first_to_next,
        'first_to_position': first_to_position,
        'part_count': part_count,
        'rating_count': rating_count,
        'rarity_count': rarity_count,
        'merged_new_affix': merged_new_affix_counter,
    }


def print_report(stats: Dict, records: List[Dict]):
    """输出统计报告"""
    print("=" * 80)
    print("调律词条分布统计报告")
    print("=" * 80)
    print(f"\n总装备数：{len(records)}")

    # 基本信息
    print("\n" + "=" * 80)
    print("基本信息")
    print("=" * 80)

    print("\n【按部位统计】")
    for part in ['主武器', '副武器', '环', '佩', '冠胄', '胸甲', '胫甲', '腕甲']:
        count = stats['part_count'].get(part, 0)
        if count > 0:
            print(f"  {part:6s}: {count:4d} 件")

    print("\n【按品阶统计】")
    for rarity in ['金色', '紫色', '蓝色']:
        count = stats['rarity_count'].get(rarity, 0)
        if count > 0:
            print(f"  {rarity}: {count:4d} 件")

    print("\n【按评级统计】")
    for rating in ['顶级', '优秀', '一般', '垃圾']:
        count = stats['rating_count'].get(rating, 0)
        if count > 0:
            print(f"  {rating}: {count:4d} 件")

    # 合并统计：忽略位置，直接统计所有调律新词条
    print("\n" + "=" * 80)
    print("合并统计：调律新词条分布（忽略位置与部位）")
    print("=" * 80)
    
    merged_counter = stats.get('merged_new_affix', {})
    if merged_counter:
        total = sum(merged_counter.values())
        print(f"\n总新词条数：{total}")
        print("-" * 70)
        
        for affix, count in merged_counter.most_common(25):
            pct = count / total * 100
            bar = '█' * int(pct / 2)
            print(f"  {affix:20s}  {count:4d} 次  {pct:5.1f}%  {bar}")

    # 位置统计
    print("\n" + "=" * 80)
    print("各位置词条分布（所有部位合计）")
    print("=" * 80)

    for pos in range(1, 6):
        counter = stats['position_affix'].get(pos, {})
        if not counter:
            continue
        total = sum(counter.values())
        print(f"\n【位置 {pos}】（样本数：{total}）")
        print("-" * 70)

        for affix, count in counter.most_common(15):
            pct = count / total * 100
            bar = '█' * int(pct / 2)
            print(f"  {affix:20s}  {count:4d} 次  {pct:5.1f}%  {bar}")

    # 按部位的位置统计
    print("\n" + "=" * 80)
    print("各部位的位置 2 词条分布（给定首词条后）")
    print("=" * 80)

    for part in ['主武器', '副武器', '环', '佩', '冠胄', '胸甲', '胫甲', '腕甲']:
        if part not in stats['part_count']:
            continue

        print(f"\n{'=' * 70}")
        print(f"部位：{part}")
        print("=" * 70)

        part_pos = stats['part_position_affix'].get(part, {})
        pos2_counter = part_pos.get(2, {})
        if not pos2_counter:
            print("  无数据")
            continue

        total = sum(pos2_counter.values())
        print(f"位置 2 词条分布（样本数：{total}）")
        print("-" * 70)

        for affix, count in pos2_counter.most_common(10):
            pct = count / total * 100
            bar = '█' * int(pct / 2)
            print(f"  {affix:20s}  {count:4d} 次  {pct:5.1f}%  {bar}")

    # 首词条条件概率
    print("\n" + "=" * 80)
    print("首词条 → 后续词条条件概率（所有部位合计）")
    print("=" * 80)

    first_to_next = stats['first_to_next']
    for first_affix in sorted(first_to_next.keys()):
        counter = first_to_next[first_affix]
        total = sum(counter.values())
        if total < 5:
            continue

        print(f"\n【首词条：{first_affix}】（样本数：{total}）")
        print("-" * 70)

        for affix, count in counter.most_common(10):
            pct = count / total * 100
            bar = '█' * int(pct / 2)
            print(f"  {affix:20s}  {count:4d} 次  {pct:5.1f}%  {bar}")

    # 首词条 + 位置条件概率
    print("\n" + "=" * 80)
    print("首词条 → 各位置词条条件概率（详细版）")
    print("=" * 80)

    first_to_position = stats['first_to_position']
    for first_affix in sorted(first_to_position.keys()):
        pos_dict = first_to_position[first_affix]
        if not pos_dict:
            continue

        print(f"\n{'=' * 70}")
        print(f"首词条：{first_affix}")
        print("=" * 70)

        for pos in range(2, 6):
            counter = pos_dict.get(pos, {})
            if not counter:
                continue

            total = sum(counter.values())
            print(f"\n  位置 {pos}（样本数：{total}）")
            print("  " + "-" * 66)

            for affix, count in counter.most_common(8):
                pct = count / total * 100
                bar = '█' * int(pct / 2)
                print(f"    {affix:18s}  {count:4d} 次  {pct:5.1f}%  {bar}")


def main():
    if len(sys.argv) > 1:
        tuning_dir = Path(sys.argv[1])
    else:
        tuning_dir = Path(__file__).parent.parent / 'logs' / 'tuning'

    if not tuning_dir.exists():
        print(f"错误：目录不存在：{tuning_dir}", file=sys.stderr)
        sys.exit(1)

    md_files = list(tuning_dir.glob('调律说明_*.md'))
    if not md_files:
        print(f"错误：未找到调律报告文件：{tuning_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"找到 {len(md_files)} 个调律报告文件")

    all_records = []
    for f in md_files:
        records = parse_tuning_file(f)
        all_records.extend(records)

    if not all_records:
        print("错误：未解析到任何装备数据", file=sys.stderr)
        sys.exit(1)

    print(f"共解析到 {len(all_records)} 件装备\n")

    stats = analyze(all_records)
    
    # 输出到文件
    output_file = tuning_dir.parent / 'tuning_report.txt'
    import io
    with open(output_file, 'w', encoding='utf-8') as f:
        old_stdout = sys.stdout
        sys.stdout = f
        try:
            print_report(stats, all_records)
        finally:
            sys.stdout = old_stdout
    
    print(f"报告已保存到：{output_file}")


if __name__ == '__main__':
    main()
