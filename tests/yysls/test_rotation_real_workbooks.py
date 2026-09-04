"""真实毕业率计算器 Excel 的对账

解析出的轴总伤与 DPS 必须与已编译方案的 reference 完全一致——这是判断
解析口径对不对的唯一硬标准（期望列内部已含次数，多乘一次就会翻倍）。

Excel 不随仓库分发，缺失时整组跳过。放路径的环境变量：LVJIANG_EXCEL_DIR，
未设置时回落到项目内的 data/excel 目录。
"""

import json
import os
from pathlib import Path

import pytest

from lvjiang.apps.yysls.core.graduation.rotation import parse_rotation
from lvjiang.constants import DATA_DIR, PROJECT_ROOT
from tests.case_matrix import case_matrix

_DEFAULT_DIR = DATA_DIR / "excel"
_SCHEME_DIR = PROJECT_ROOT / "config" / "system" / "yysls" / "graduation"


def _excel_dir() -> Path:
    return Path(os.environ.get("LVJIANG_EXCEL_DIR") or _DEFAULT_DIR)


def _pairs() -> list[tuple[Path, dict]]:
    """(Excel 路径, 方案 JSON) —— 按方案里记录的源文件名配对"""
    directory = _excel_dir()
    if not directory.is_dir():
        return []
    pairs = []
    for scheme_path in sorted(_SCHEME_DIR.glob("*.json")):
        scheme = json.loads(scheme_path.read_text(encoding="utf-8"))
        excel = directory / scheme.get("source", {}).get("file", "")
        if excel.is_file():
            pairs.append((excel, scheme))
    return pairs


_PAIRS = _pairs()
pytestmark = pytest.mark.skipif(
    not _PAIRS,
    reason=f"未找到原始 Excel（设 LVJIANG_EXCEL_DIR 指向目录，默认 {_DEFAULT_DIR}）",
)


@case_matrix(
    "excel,scheme", _PAIRS, ids=[s["school"] for _, s in _PAIRS] or None)
class TestReconcile:
    def test_total_damage_matches_scheme(self, excel, scheme):
        rot = parse_rotation(excel)
        assert rot.total_damage == pytest.approx(
            scheme["reference"]["total_damage"], abs=1.0)

    def test_dps_matches_scheme(self, excel, scheme):
        rot = parse_rotation(excel)
        assert rot.dps == pytest.approx(scheme["reference"]["dps"], abs=1.0)

    def test_skill_shares_sum_to_100(self, excel, scheme):
        by = parse_rotation(excel).by_skill()
        assert sum(s.share for s in by) == pytest.approx(100.0)

    def test_axis_is_non_trivial(self, excel, scheme):
        """每条轴都应有多行多技能——解析退化成一行时这条会失败。"""
        rot = parse_rotation(excel)
        assert len(rot.hits) >= 50
        assert len(rot.by_skill()) >= 5
        assert rot.combat_time > 0
