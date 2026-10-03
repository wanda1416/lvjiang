"""随包模型记录的源 Excel 必须真的在 `data/excel` 的对应等级目录下。

这些计算器表会被持续替换、改名和归档。而两条消费路径对"找不到源文件"的反应
完全不同：

- `test_damage_model` 用 `glob("*/<file>")` 后断言恰好 1 个命中，改名后**直接
  失败**，但报错只有 `('牵丝·玉.yaml', [])`，看不出是谁改了什么；
- `test_rotation_real_workbooks` 配不上就把那一组**静默跳过**，于是改名或归档
  会悄悄抽掉一个流派的轴对账覆盖，全绿但其实少测了。

所以这里单独守一道：谁的 `source.file` 失效、现在文件在哪，一次说清。
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_EXCEL_DIR = _ROOT / "data" / "excel"
_DAMAGE_DIR = _ROOT / "config" / "system" / "yysls" / "damage_model"
_GRADUATION_DIR = _ROOT / "config" / "system" / "yysls" / "graduation"


def _elsewhere(name: str) -> list[str]:
    """同名文件是不是被挪到了别处（最典型是归进「过期版本」子目录）。"""
    return [
        str(path.relative_to(_EXCEL_DIR))
        for path in _EXCEL_DIR.rglob(name)
    ]


def test_damage_models_point_at_an_existing_workbook() -> None:
    missing = []
    for path in sorted(_DAMAGE_DIR.glob("*.yaml")):
        name = yaml.safe_load(path.read_text(encoding="utf-8"))["source"]["file"]
        hits = list(_EXCEL_DIR.glob(f"*/{name}"))
        if len(hits) != 1:
            missing.append(
                f"{path.name}: source.file={name!r} 在 data/excel/*/ 下命中 "
                f"{len(hits)} 个；全树同名文件={_elsewhere(name) or '无'}")
    assert not missing, "\n".join(missing)


def test_graduation_schemes_point_at_an_existing_workbook() -> None:
    """轴对账按「<等级>级/<source.file>」配对，配不上会静默跳过整组。"""
    missing = []
    for path in sorted(_GRADUATION_DIR.glob("*/*.json")):
        scheme = json.loads(path.read_text(encoding="utf-8"))
        name = scheme.get("source", {}).get("file", "")
        level = scheme.get("model_level")
        if not name:
            missing.append(f"{path.name}: 没有记录 source.file")
            continue
        if not (_EXCEL_DIR / f"{level}级" / name).is_file():
            missing.append(
                f"{path.parent.name}/{path.name}: source.file={name!r} 不在 "
                f"data/excel/{level}级/ 下；全树同名文件="
                f"{_elsewhere(name) or '无'}")
    assert not missing, "\n".join(missing)
