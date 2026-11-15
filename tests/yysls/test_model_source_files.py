"""随包模型记录的源 Excel 必须能找到。

模型和表格会持续替换、改名和归档。这里只守一件事：``source.file`` 还能不能在
``data/excel`` 下找到，而不是要求它必须停在等级目录的根上——归档到「过期版本」
是正常维护动作，不应把每次数据导入都变成一次测试修复。

毕业率模型按身份只校验最新版本：历史版本允许指向已归档的旧表，运行时也不会
再选中它们。
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_EXCEL_DIR = _ROOT / "data" / "excel"
_DAMAGE_DIR = _ROOT / "config" / "system" / "yysls" / "damage_model"
_GRADUATION_DIR = _ROOT / "config" / "system" / "yysls" / "graduation"


def _workbook_hits(name: str) -> list[Path]:
    """同名源表可能同时存在于等级目录和「过期版本」子目录。"""
    return sorted(_EXCEL_DIR.rglob(name))


def test_damage_models_point_at_an_existing_workbook() -> None:
    missing = []
    for path in sorted(_DAMAGE_DIR.glob("*.yaml")):
        name = yaml.safe_load(path.read_text(encoding="utf-8"))["source"]["file"]
        if not _workbook_hits(name):
            missing.append(
                f"{path.name}: source.file={name!r} 在 data/excel 下找不到")
    assert not missing, "\n".join(missing)


def test_latest_graduation_schemes_point_at_an_existing_workbook() -> None:
    """只校验每个身份的最新模型；旧版本可以指向「过期版本」里的历史表。"""
    latest: dict[tuple[str, str, int], tuple[int, Path, dict]] = {}
    for path in sorted(_GRADUATION_DIR.glob("*/*.json")):
        scheme = json.loads(path.read_text(encoding="utf-8"))
        school = scheme.get("school")
        name = scheme.get("scheme")
        level = scheme.get("model_level")
        version = scheme.get("model_version")
        if not (isinstance(school, str) and isinstance(name, str)
                and isinstance(level, int) and isinstance(version, int)):
            continue
        key = (school, name, level)
        if key not in latest or version > latest[key][0]:
            latest[key] = (version, path, scheme)
    missing = []
    for (_school, _scheme, _level), (_version, path, scheme) in sorted(
            latest.items()):
        file_name = scheme.get("source", {}).get("file", "")
        if not file_name:
            missing.append(f"{path.name}: 没有记录 source.file")
            continue
        if not _workbook_hits(file_name):
            missing.append(
                f"{path.parent.name}/{path.name}: source.file={file_name!r} "
                "在 data/excel 下找不到")
    assert not missing, "\n".join(missing)
