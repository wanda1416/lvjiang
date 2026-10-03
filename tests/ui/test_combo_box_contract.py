"""生产 UI 不得绕过统一的下拉框宽度策略。"""

from __future__ import annotations

import ast
from pathlib import Path


def test_production_ui_has_no_raw_combo_box_construction() -> None:
    root = Path(__file__).parents[2] / "src" / "lvjiang"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        # premium 是部署时注入、由独立私有仓库维护的扩展，不属于本仓库
        # 的生产源码门禁范围。
        if path.name == "combo_box.py" or "apps/premium" in path.as_posix():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = (
                    node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute)
                    else ""
                )
                if name == "QComboBox":
                    violations.append(f"{path.relative_to(root)}:{node.lineno}")
            elif isinstance(node, ast.ClassDef):
                if any(
                    (isinstance(base, ast.Name) and base.id == "QComboBox")
                    or (isinstance(base, ast.Attribute) and base.attr == "QComboBox")
                    for base in node.bases
                ):
                    violations.append(f"{path.relative_to(root)}:{node.lineno}")
    assert violations == [], (
        "请使用 lvjiang.ui.combo_box.AutoWidthComboBox：" + ", ".join(violations)
    )
