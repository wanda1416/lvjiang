"""Static coverage guard for user-facing Qt button styling."""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).parents[2] / "src"
# 由所在容器的样式表统一着色的小按钮：本身就是容器视觉的一部分，套通用
# 按钮样式会把它变成独立控件，反而破坏容器外观。静态扫描看不到容器规则，
# 只能在这里登记。
_INTENTIONAL_CONTAINER_STYLES = {
    ("src/lvjiang/ui/alert_panel.py", "self._close_btn"),
    # 标签 chip 内的「×」，由 QFrame#profileTagChip QPushButton 规则着色
    ("src/lvjiang/ui/profile/settings_dialog.py", "remove"),
}


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _name(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    return None


def _assignments(node: ast.Assign | ast.AnnAssign):
    return node.targets if isinstance(node, ast.Assign) else (node.target,)


def _function_button_coverage(path: Path, function: ast.AST):
    created: list[tuple[str, int]] = []
    styled: set[str] = set()
    dialog_boxes: list[tuple[str, int]] = []
    styled_boxes: set[str] = set()

    for node in ast.walk(function):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, ast.Call):
                constructor = _name(value.func)
                for target in _assignments(node):
                    target_name = _name(target)
                    if not target_name:
                        continue
                    if constructor == "QPushButton":
                        created.append((target_name, node.lineno))
                    elif constructor == "QDialogButtonBox":
                        dialog_boxes.append((target_name, node.lineno))
        if not isinstance(node, ast.Call):
            continue
        call_name = _name(node.func)
        if call_name == "apply_button_style":
            styled.update(filter(None, (_name(arg) for arg in node.args)))
        elif call_name and call_name.endswith(".setStyleSheet"):
            styled.add(call_name.removesuffix(".setStyleSheet"))
        elif call_name == "apply_dialog_button_box_style" and node.args:
            box_name = _name(node.args[0])
            if box_name:
                styled_boxes.add(box_name)

    return (
        [(name, line) for name, line in created if name not in styled],
        [(name, line) for name, line in dialog_boxes if name not in styled_boxes],
    )


def test_all_direct_qt_buttons_have_an_explicit_style():
    missing: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(_SRC.parent).as_posix()
        functions = (
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        for function in functions:
            buttons, boxes = _function_button_coverage(path, function)
            missing.extend(
                f"{relative}:{line}: {name}"
                for name, line in buttons
                if (relative, name) not in _INTENTIONAL_CONTAINER_STYLES
            )
            missing.extend(
                f"{relative}:{line}: {name} (QDialogButtonBox)"
                for name, line in boxes
            )

    assert not missing, "Buttons without an explicit style:\n" + "\n".join(missing)
