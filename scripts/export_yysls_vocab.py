#!/usr/bin/env python3
"""导出燕云十六声部位/词条的规范枚举到 JSON。

单一真源仍是 `config/system/yysls/game_config.yaml` + 解析它的
`lvjiang.apps.yysls.config.ConfigManager` / `telemetry/vocab.py`——这个
脚本不重新实现那套派生逻辑（别名归并、定音/普通词条池划分等），只是把
已经算好的结果落一份地，给不方便依赖完整 `lvjiang` 包的工具读
（``ops/stats-client`` 是当前唯一用户：它是独立 venv 的轻量网页，不该为
了几个下拉框的候选值拖进 PyQt6/mss/rapidocr 这些运行期依赖）。

输出落在 `ops/stats-client/vocab/`，不放 `config/system/` 下——它是
stats-client 私有的构建产物，不经 `ConfigResolver` 的 system/local 合并，
放进配置分层目录只会让人误以为它参与合并、可以被 local 覆盖。

``game_config.yaml`` 改了以后（新增词条、新武器类型……）重新跑一遍：

    python scripts/export_yysls_vocab.py

这个脚本要用主项目的 venv 跑（需要 import `lvjiang`），跟
`scripts/telemetry_analysis/` 刻意保持"只用标准库"不是一回事——两者
职责不同：那边是分析引擎，这边是配置快照导出。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from lvjiang.apps.yysls.config import get_game_config  # noqa: E402
from lvjiang.apps.yysls.telemetry import vocab  # noqa: E402

_OUT_PATH = _REPO_ROOT / "ops/stats-client/vocab/telemetry_vocab.json"


def _part_labels() -> dict[str, str]:
    """从 ``get_type_to_group()``（装备 type 中文名 → 分组 key）反推
    分组 key → 中文标签。武器组特殊处理：``type_to_group`` 里武器组有
    十来个具体武器型号（剑/枪/伞……）都映射到 ``"weapon"``，标签要用
    通用的"武器"，不能取到某一个具体型号的名字。
    """
    labels: dict[str, str] = {}
    for zh_name, code in get_game_config().get_type_to_group().items():
        if code == "weapon":
            labels["weapon"] = "武器"
        elif code not in labels:
            labels[code] = zh_name
    return labels


def _normal_affixes_by_part(part_labels: dict[str, str]) -> dict[str, list[str]]:
    """部位（ascii code）→ 该部位能出现的普通词条名列表。

    ``ConfigManager.get_affix_parts()`` 返回的是词条可出现的部位（中文名，
    未单独配置时缺省全部七部位），这里按词条正向遍历、按部位反向分桶，
    再用 ``part_labels`` 反查表把中文部位名转成 ascii code——跟遥测事件
    的 ``part`` 字段对齐，调用方不用在网页层再做一次中文/ascii 转换。
    """
    gc = get_game_config()
    zh_to_code = {zh: code for code, zh in part_labels.items()}
    by_part: dict[str, list[str]] = {code: [] for code in part_labels}
    for affix in gc.get_normal_affix_names():
        for zh_part in gc.get_affix_parts(affix):
            code = zh_to_code.get(zh_part)
            if code:
                by_part[code].append(affix)
    return by_part


def build_vocab() -> dict:
    """算出完整快照（不落盘）——``main()`` 用来写文件，
    ``tests/yysls/test_telemetry_vocab_export.py`` 用它重新算一份跟
    已提交的 JSON 比对，忘记重新导出时测试会红而不是让下游读到过期数据。
    """
    gc = get_game_config()
    part_labels = _part_labels()
    return {
        "_generated_by": "scripts/export_yysls_vocab.py",
        "_source": "config/system/yysls/game_config.yaml",
        "parts": list(vocab.part_choices()),
        "part_labels": part_labels,
        "qualities": list(vocab.quality_choices()),
        "foods": list(vocab.food_choices()),
        "modes": list(vocab.mode_choices()),
        "weapon_types": gc.get_weapon_types(),
        "normal_affixes": gc.get_normal_affix_names(),
        "dingyin_affixes": gc.get_dingyin_affix_names(),
        "normal_affixes_by_part": _normal_affixes_by_part(part_labels),
    }


def main() -> int:
    data = build_vocab()
    _OUT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"写入 {_OUT_PATH}："
         f"{len(data['parts'])} 个部位、{len(data['normal_affixes'])} 个普通词条、"
         f"{len(data['dingyin_affixes'])} 个定音词条、{len(data['weapon_types'])} 种武器",
         file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
