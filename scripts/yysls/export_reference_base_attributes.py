#!/usr/bin/env python3
"""导出 115 阶预置基础属性（`base_attributes/*.yaml`）。

输入是社区计算表站点公开的前端资源：站点把每个流派的计算表转成 ``pack``
JSON（全部公式单元格 + DIY 表），在浏览器/Worker 里用 Excel 子集求值。本脚本
下载这些 pack，用仓库自己的公式求值器（``graduation.excel_formula``）算同一批
单元格，得到与那份计算表一致的面板值，而不是抓它的界面文字。

站点地址不进仓库：用 ``--site`` 或环境变量 ``LVJIANG_REFERENCE_SITE`` 在运行时
提供。落盘的数据只保留口径字段，不写来源站点、表版本或哈希。

口径（与 ``config/session/yysls/play_styles.json`` 一致）：

* 排除：8 件装备的固有主属性、40 条普通词条与定音、**装备套装**（DIY 表 T 列、
  即两件套）、**弓玦**（DIY 表 S 列）。
* 保留：等级/天赋/地图收集/突破、四条叠音（DIY「基础属性」6–9 行）、默认心法、
  武学天赋、武库（本系）。

用法::

    set LVJIANG_REFERENCE_SITE=https://...        # 或不设，用 --site 传入
    python scripts/yysls/export_reference_base_attributes.py --print
    python scripts/yysls/export_reference_base_attributes.py --write
    python scripts/yysls/export_reference_base_attributes.py --check   # 只比对

``--check`` 需要联网；离线回归由 ``tests/apps/yysls/core/test_base_attributes_reference.py``
用冻结的期望值守住。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from lvjiang.apps.yysls.core.graduation.excel_formula import (  # noqa: E402
    FormulaModel,
)
from lvjiang.core.config import dump_yaml  # noqa: E402

SITE_ENV = "LVJIANG_REFERENCE_SITE"
USER_AGENT = "lvjiang-base-attributes-export/1.0"
DEFAULT_SET = ""  # 空表示按站点规则自动选每个流派最新可用的那套
LEVEL = 115
STAGE = 2
PRESET_ID = "115_s2"

OUTPUT_DIR = ROOT / "config/system/yysls/base_attributes"

#: 站点流派 key → (仓库流派名, pack 里的流派标签, 默认心法)。
#: 心法取各流派默认装备方案里的那组「心法」（即新建方案时的默认搭配）；仓库目前
#: 没有心法选择概念，先固化默认，后续迭代再改为可选。
#: 牵丝·霖的计算表按「玩法」分两支（火拳奶 / 纯奶），默认那支只给两门，第四槽空着；
#: 空槽不计属性，所以这里补一门 四时无常 作为第四本，避免少一门心法的收益。
SCHOOLS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "MINGJIN_HONG": ("鸣金·虹", "鸣金虹", ("无名剑法", "月涌方圆", "千山法", "威猛歌")),
    "MINGJIN_YING": ("鸣金·影", "鸣金影", ("剑气纵横", "凝神章", "逐狼心经", "月涌方圆")),
    "MINGJIN_TING": ("鸣金·霆", "鸣金霆", ("月涌方圆", "浮梁一夜", "神断青锋", "明非通判")),
    "LIESHI_WEI": ("裂石·威", "裂石威", ("山河绝韵", "易水歌", "穿喉决", "抗造大法")),
    "LIESHI_JUN": ("裂石·钧", "裂石钧", ("霜天百夜", "孤忠不辞", "穿喉决", "易水歌")),
    "QIANSI_LIN": ("牵丝·霖", "牵丝霖", ("君臣药", "易水歌", "征人归", "四时无常")),
    "QIANSI_YI": ("牵丝·翊", "牵丝翊", ("相和歌", "风知意", "易水歌", "断石之构")),
    "QIANSI_YU": ("牵丝·玉", "牵丝玉", ("花上月令", "纵地摘星", "春雷篇", "断石之构")),
    "POZHU_CHEN": ("破竹·尘", "破竹尘", ("千营一呼", "绳舟行木", "断石之构", "易水歌")),
    "POZHU_FENG": ("破竹·风", "破竹风", ("忘川绝响", "心弥泥鱼", "断石之构", "易水歌")),
    "POZHU_YUAN": ("破竹·鸢", "破竹鸢", ("扶摇直上", "擒天势", "断石之构", "易水歌")),
    "POZHU_ZUN": ("破竹·樽", "破竹樽", ("一醉千秋", "飞仙醉言", "断石之构", "易水歌")),
}

#: DIY 表上为特定流派设的查表别名（「牵丝霖」没有独立的武学天赋行）。
TALENT_ALIAS = {"牵丝霖": "牵丝玉"}

#: 对心法表数值的人工修订：仅当表内仍是旧值时才替换。
INNER_WAY_FIXES: dict[str, dict[str, tuple[float, float]]] = {
    "无名剑法": {"大外": (121.3, 130.1)},
}

#: 「新建方案」默认带的武库选项。
DEFAULT_ARSENAL = "本系"

#: DIY 表单元格 → CombatAttributes 字段，顺序即 YAML 里的书写顺序。
CELL_FIELDS: tuple[tuple[str, str], ...] = (
    ("min_outer", "D4"), ("max_outer", "D5"),
    ("min_mingjin", "D13"), ("max_mingjin", "D14"),
    ("min_lieshi", "D15"), ("max_lieshi", "D16"),
    ("min_qiansi", "D17"), ("max_qiansi", "D18"),
    ("min_pozhu", "D19"), ("max_pozhu", "D20"),
    ("min_wuxiang", "D21"), ("max_wuxiang", "D22"),
    ("precision", ""), ("crit_rate", ""), ("intent_rate", ""),
    ("direct_crit", "D8"), ("direct_intent", "D11"),
    ("crit_dmg", "D9"), ("intent_dmg", "D12"),
    ("outer_pen", "D26"), ("outer_bonus", "D27"),
    ("mingjin_pen", "D28"), ("mingjin_bonus", "D29"),
    ("lieshi_pen", "D30"), ("lieshi_bonus", "D31"),
    ("qiansi_pen", "D32"), ("qiansi_bonus", "D33"),
    ("pozhu_pen", "D34"), ("pozhu_bonus", "D35"),
    ("wuxiang_pen", "D36"),
)

#: 三率在 DIY 表里都除过一个「判定抗性」才得到面板值，导出时反算回抗性前的值。
RESISTANCE_RATE = 2.8
ACCURACY_BASE = 0.65

#: 五维：公共底子（DIY 表 23–25 行 = 劲/敏/势）原样写进快照，
#: 体/御 计算表没有列，但同一底子下也是 328，一并补上，避免下游缺项。
DIM_CELLS = {"dim_jin": "B23", "dim_min": "B24", "dim_shi": "B25"}
DIM_FALLBACK = {"dim_ti": 328.0, "dim_yu": 328.0}

_ROUNDUP_RE = re.compile(r"^ROUNDUP\((.*),(\d+)\)$", re.S)


# ── 抓取 ──────────────────────────────────────────────


def _get(url: str, *, tries: int = 3) -> bytes:
    """下载单个资源；网络抖动重试，仍失败则交给调用方报错。"""
    last: Exception | None = None
    for attempt in range(tries):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            last = exc
            if attempt + 1 < tries:
                time.sleep(1.0 + attempt)
    raise SystemExit(f"下载失败 {url}：{last}")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inflate(data: bytes) -> dict:
    """pack 文件是 deflate-raw 压缩的 JSON。"""
    try:
        raw = zlib.decompress(data, -15)
    except zlib.error as exc:  # 站点改了打包方式时直接报错，不猜
        raise SystemExit(f"无法解压 pack：{exc}") from exc
    return json.loads(raw.decode("utf-8"))


class ReferenceSite:
    """定位站点的当前资源：入口 bundle → 计算表索引 → worker → 流派 pack。"""

    def __init__(self, site: str) -> None:
        self.site = site.rstrip("/")
        bundle, self.bundle_url = self._find_bundle()
        self.worker_url = self._find_worker(bundle)
        worker = _get(self.worker_url).decode("utf-8")
        self.worker_sha256 = _sha256(worker.encode("utf-8"))
        # 索引（每套计算表的流派版本、源表 sha256、是否可选）在入口 bundle 里，
        # 流派 pack 的动态 import 映射在 worker 里。
        self.indexes = self._parse_indexes(bundle)
        self.modules = self._parse_modules(worker)

    def _find_bundle(self) -> tuple[str, str]:
        html = _get(self.site + "/").decode("utf-8", "replace")
        match = re.search(r'src="(/assets/calc-[^"]+\.js)"', html)
        if match is None:
            raise SystemExit("站点首页结构变了：找不到 calc 入口脚本")
        url = self.site + match.group(1)
        return _get(url).decode("utf-8", "replace"), url

    def _find_worker(self, bundle: str) -> str:
        worker = re.search(r"/assets/(ownerCalcWorker-[\w.-]+\.js)", bundle)
        if worker is None:
            raise SystemExit("站点入口变了：找不到 ownerCalcWorker 脚本")
        return f"{self.site}/assets/{worker.group(1)}"

    @staticmethod
    def _parse_indexes(bundle: str) -> dict[str, dict]:
        """入口 bundle 里内联的每套计算表索引。"""
        result: dict[str, dict] = {}
        for match in re.finditer(r"JSON\.parse\('((?:[^'\\]|\\.)*)'\)", bundle):
            blob = match.group(1).replace("\\'", "'")
            if '"schools"' not in blob:
                continue
            try:
                index = json.loads(blob)
            except json.JSONDecodeError:
                continue
            set_id = index.get("set")
            if isinstance(set_id, str) and isinstance(index.get("schools"), list):
                result[set_id] = index
        if not result:
            raise SystemExit("站点 bundle 里没有找到计算表索引")
        return result

    @staticmethod
    def _parse_modules(worker: str) -> dict[str, dict[str, str]]:
        """每套计算表对应的动态 import 映射：{set: {school_key: module_path}}。"""
        by_var: dict[str, dict[str, str]] = {}
        pattern = re.compile(
            r"([A-Za-z_$][\w$]*)=\{(DIY:\(\)=>import\(\"\./[^\"]+\"\)"
            r"(?:,[A-Z_]+:\(\)=>import\(\"\./[^\"]+\"\))*)\}")
        for match in pattern.finditer(worker):
            entries = dict(re.findall(
                r"([A-Z_]+):\(\)=>import\(\"\./([^\"]+)\"\)", match.group(2)))
            if entries:
                by_var[match.group(1)] = entries
        sets: dict[str, dict[str, str]] = {}
        for set_id, var in dict(re.findall(
                r'"(1\d{2}-\d{4}-\d{2}-\d{2})"\s*:\s*([A-Za-z_$][\w$]*)', worker)).items():
            if var in by_var:
                sets[set_id] = by_var[var]
        if not sets:
            raise SystemExit("站点 worker 里没有找到流派 pack 映射")
        return sets

    def choose_set(self, school_key: str, preferred: str = "") -> str:
        """按站点规则选流派所在的计算表：优先非「可选」集，再退可选集。"""
        sets = [s for s in self.indexes if school_key in self.modules.get(s, {})]
        if preferred:
            if preferred in sets:
                return preferred
            raise SystemExit(f"站点没有 {preferred} 这一套 {school_key} 数据")
        if not sets:
            raise SystemExit(f"站点没有 {school_key} 的数据")
        sets.sort(reverse=True)
        for set_id in sets:
            if not self._is_optional(set_id, school_key):
                return set_id
        return sets[0]

    def _is_optional(self, set_id: str, school_key: str) -> bool:
        for entry in self.indexes[set_id]["schools"]:
            if entry.get("key") == school_key:
                return bool(entry.get("optional"))
        return False

    def school_meta(self, set_id: str, school_key: str) -> dict:
        for entry in self.indexes[set_id]["schools"]:
            if entry.get("key") == school_key:
                return entry
        return {}

    def load_pack(self, set_id: str, school_key: str) -> tuple[dict, str]:
        module = self.modules[set_id][school_key]
        stub = _get(f"{self.site}/assets/{module}").decode("utf-8", "replace")
        pack_name = re.search(r'new URL\("([^"]+)"', stub)
        if pack_name is None:
            raise SystemExit(f"{module} 里没有 pack 文件名")
        data = _get(f"{self.site}/assets/{pack_name.group(1)}")
        return _inflate(data), _sha256(data)


# ── 求值 ──────────────────────────────────────────────


def build_model(pack: dict) -> tuple[dict, dict[str, int]]:
    """pack 的 ``{v, f}`` 单元格 → 本仓库求值器的 ``{value, formula}`` 结构。

    求值器不支持 ``ROUNDUP``，这里把它换成内层表达式并记下小数位，算完再向上取整。
    """
    sheets: dict[str, dict] = {}
    roundups: dict[str, int] = {}
    for sheet_name, sheet in pack["model"]["sheets"].items():
        cells: dict[str, dict] = {}
        for coord, cell in sheet["cells"].items():
            entry: dict[str, Any] = {}
            if "v" in cell:
                entry["value"] = cell["v"]
            if "f" in cell:
                formula = str(cell["f"])
                rounded = _ROUNDUP_RE.match(formula)
                if rounded:
                    entry["formula"] = rounded.group(1)
                    roundups[f"{sheet_name}!{coord}"] = int(rounded.group(2))
                else:
                    entry["formula"] = formula
            if entry:
                cells[coord] = entry
        sheets[sheet_name] = {
            "cells": cells,
            "dimensions": {"max_row": int(sheet.get("maxRow") or 60)},
        }
    return {"sheets": sheets}, roundups


def _inner_way_cell(model: dict, inner_way: str, column: str) -> str | None:
    cells = model["sheets"]["心法"]["cells"]
    header = {
        cell["value"]: coord.rstrip("1")
        for coord, cell in cells.items()
        if coord.endswith("1") and isinstance(cell.get("value"), str)
    }
    target = header.get(column)
    if target is None:
        return None
    for row in range(2, 64):
        cell = cells.get(f"A{row}")
        if cell and cell.get("value") == inner_way:
            return f"{target}{row}"
    return None


def compute_stats(
    model: dict, roundups: dict[str, int], talent: str, inner_ways: tuple[str, ...],
) -> dict[str, float]:
    """按「无装备 / 无套装 / 无弓玦」的口径算出面板属性。"""
    overrides: dict[str, Any] = {}
    # 装备主属性由所选装备写入；这里没有装备 → 清零（叠音行 6–9 不属于装备主属性）。
    for row in (2, 3, 4, 5):
        overrides[f"基础属性!B{row}"] = 0
        overrides[f"基础属性!C{row}"] = 0
    # 40 条普通词条（K–R 列）与定音穿透合计：没有装备就没有。
    for column in "KLMNOPQR":
        for row in range(3, 25):
            overrides[f"DIY!{column}{row}"] = 0
    overrides["DIY!B26"] = 0
    overrides["DIY!S2"] = ""       # 弓玦：排除
    overrides["DIY!T2"] = ""       # 装备套装（两件套）：排除
    overrides["DIY!G9"] = DEFAULT_ARSENAL
    overrides["DIY!F14"] = TALENT_ALIAS.get(talent, talent)

    padded = list(inner_ways) + ["N/A"] * (4 - len(inner_ways))
    for index, inner_way in enumerate(padded[:4]):
        overrides[f"DIY!F{4 + index}"] = inner_way
    for inner_way, columns in INNER_WAY_FIXES.items():
        if inner_way not in inner_ways:
            continue
        for column, (old, new) in columns.items():
            cell = _inner_way_cell(model, inner_way, column)
            if cell is None:
                raise SystemExit(f"心法表里没有 {inner_way} 的「{column}」")
            current = model["sheets"]["心法"]["cells"][cell].get("value")
            if current == old:
                overrides[f"心法!{cell}"] = new

    formula_model = FormulaModel(model, overrides)

    def value(coord: str) -> float:
        address = f"DIY!{coord}"
        raw = float(formula_model.value(address) or 0)
        places = roundups.get(address)
        if places is not None:
            factor = 10 ** places
            return _round_up(raw, factor)
        return raw

    stats: dict[str, float] = {}
    for name, coord in CELL_FIELDS:
        if name == "precision":
            stats[name] = ACCURACY_BASE + (value("D6") - ACCURACY_BASE) * RESISTANCE_RATE
        elif name == "crit_rate":
            stats[name] = value("D7") * RESISTANCE_RATE
        elif name == "intent_rate":
            stats[name] = value("D10") * RESISTANCE_RATE
        else:
            stats[name] = value(coord)
    diy_cells = model["sheets"]["DIY"]["cells"]
    for name, coord in DIM_CELLS.items():
        raw = (diy_cells.get(coord) or {}).get("value", 0)
        stats[name] = float(raw) if isinstance(raw, (int, float)) else 0.0
    stats.update(DIM_FALLBACK)
    return {name: round(value, 4) for name, value in stats.items() if abs(value) > 1e-9}


def _round_up(value: float, factor: int) -> float:
    """Excel ``ROUNDUP(value, 小数位)``：away from zero，且吃掉浮点尾差。"""
    scaled = value * factor
    rounded = math.ceil(scaled - 1e-9) if scaled >= 0 else math.floor(scaled + 1e-9)
    return rounded / factor


# ── 落盘 ──────────────────────────────────────────────


def existing_preset_stats(path: Path) -> dict[str, float]:
    import yaml

    if not path.is_file():
        return {}
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    for preset in doc.get("presets") or []:
        if isinstance(preset, dict) and preset.get("id") == PRESET_ID:
            stats = preset.get("stats")
            if isinstance(stats, dict):
                return {str(k): float(v) for k, v in stats.items() if isinstance(v, (int, float))}
    return {}


def render_document(
    school: str, stats: dict[str, float], snapshot: dict, content_version: int,
) -> str:
    """落盘结构：只写口径快照，不写来源站点/版本/哈希等信息。"""
    ordered = {name: stats[name] for name, _ in CELL_FIELDS if name in stats}
    for name in (*DIM_CELLS, *DIM_FALLBACK):
        if name in stats:
            ordered[name] = stats[name]
    document = {
        "content_version": content_version,
        "school": school,
        "snapshot": snapshot,
        "presets": [
            {
                "id": PRESET_ID,
                "level": LEVEL,
                "stage": STAGE,
                "playstyle": None,
                "label": f"{LEVEL}级 {STAGE}阶段（默认）",
                "stats": ordered,
            },
        ],
    }
    return dump_yaml(document)


def _content_version(path: Path, stats: dict[str, float]) -> int:
    import yaml

    if not path.is_file():
        return 1
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return 1
    previous = doc.get("content_version")
    current = existing_preset_stats(path)
    same = current == stats
    if isinstance(previous, int) and previous > 0:
        return previous if same else previous + 1
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--print", dest="print_only", action="store_true", help="只打印结果")
    group.add_argument("--write", action="store_true", help="写入 config/system")
    group.add_argument("--check", action="store_true", help="与现有配置比对，不一致返回 1")
    parser.add_argument("--school", action="append", default=[], help="只处理指定流派（仓库名）")
    parser.add_argument("--set-id", default=DEFAULT_SET, help="固定使用某一套计算表")
    parser.add_argument("--site", default=os.environ.get(SITE_ENV, ""),
                        help=f"计算表站点地址；也可用环境变量 {SITE_ENV}")
    args = parser.parse_args(argv)

    site_address = args.site.strip()
    if not site_address:
        raise SystemExit(f"需要计算表站点地址：--site <url> 或设置环境变量 {SITE_ENV}")

    site = ReferenceSite(site_address)
    rows: list[tuple[str, dict[str, float], dict]] = []
    for key, (school, talent, inner_ways) in SCHOOLS.items():
        if args.school and school not in args.school:
            continue
        set_id = site.choose_set(key, args.set_id)
        meta = site.school_meta(set_id, key)
        diy_pack, _diy_sha = site.load_pack(set_id, "DIY")
        school_pack, _school_sha = site.load_pack(set_id, key)
        model, roundups = build_model(diy_pack)
        stats = compute_stats(model, roundups, talent, inner_ways)
        # 落盘只留口径（默认心法 / 武库 / 排除项），来源站点、表版本与哈希只在
        # 控制台输出里出现，不进随包数据。
        snapshot = {
            "inner_ways": list(inner_ways),
            "arsenal": DEFAULT_ARSENAL,
            "excludes": ["装备固有主属性", "词条与定音", "装备套装（两件套）", "弓玦"],
        }
        workbook = f"{meta.get('label', talent)} {meta.get('workbookVersion', '')}".strip()
        rows.append((school, stats, snapshot))
        if not school_pack.get("form"):
            raise SystemExit(f"{school} 的 pack 缺少 form 定义")
        print(f"[数据源] {school}: {set_id} / {workbook} / DIY "
              f"{diy_pack.get('workbookVersion', '')}", file=sys.stderr)

    drift: list[str] = []
    for school, stats, snapshot in rows:
        path = OUTPUT_DIR / f"{school}.yaml"
        if args.print_only:
            print(f"\n{school}")
            for name, value in stats.items():
                print(f"  {name}: {value}")
            continue
        if args.check:
            current = existing_preset_stats(path)
            if current != stats:
                diff = sorted(
                    f"{name}: 现值 {current.get(name)} → 参考 {value}"
                    for name, value in stats.items()
                    if abs((current.get(name) or 0.0) - value) > 1e-6
                )
                drift.append(f"{school}: " + ("; ".join(diff) or "条目缺失"))
            continue
        version = _content_version(path, stats)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(render_document(school, stats, snapshot, version))
        print(f"写入 {path.relative_to(ROOT)} (content_version {version})")

    if args.check:
        if drift:
            for line in drift:
                print(line)
            return 1
        print(f"与现有配置一致（{len(rows)} 个流派）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
