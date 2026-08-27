"""通用层到具体 app 实现的依赖方向门禁。

core、workflows 与通用 ui 可以依赖 ``lvjiang.apps`` 提供的插件机制，
但不得静态或动态导入 ``lvjiang.apps.<具体 app>``。违规清单是迁移棘轮：
只能删除，不能增加。
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

SRC_ROOT = Path(__file__).parents[1] / "src"
GUARDED_ROOTS = (
    SRC_ROOT / "lvjiang" / "core",
    SRC_ROOT / "lvjiang" / "workflows",
    SRC_ROOT / "lvjiang" / "ui",
)

# ``(相对文件, 行号无关的导入目标)``。迁移完成后的目标是空集合。
KNOWN_VIOLATIONS: set[tuple[str, str]] = set()


def _module_package(path: Path) -> str:
    relative = path.relative_to(SRC_ROOT).with_suffix("")
    return ".".join(relative.parts[:-1])


def _resolve_import(path: Path, node: ast.ImportFrom) -> str:
    module = node.module or ""
    if not node.level:
        return module
    return importlib.util.resolve_name(
        "." * node.level + module,
        _module_package(path),
    )


def _specific_app_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        targets: list[str] = []
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            targets.append(_resolve_import(path, node))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "importlib"
            and node.func.attr == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            targets.append(node.args[0].value)
        imports.update(
            target for target in targets
            if target.startswith("lvjiang.apps.")
        )
    return imports


def test_specific_apps_do_not_leak_into_generic_layers():
    actual: set[tuple[str, str]] = set()
    for root in GUARDED_ROOTS:
        for path in root.rglob("*.py"):
            relative = path.relative_to(SRC_ROOT).as_posix()
            actual.update((relative, target) for target in _specific_app_imports(path))

    new = actual - KNOWN_VIOLATIONS
    removed = KNOWN_VIOLATIONS - actual
    assert not new, "通用层新增具体 app 依赖:\n" + "\n".join(map(str, sorted(new)))
    assert not removed, (
        "以下已知违规已经消失，请从 KNOWN_VIOLATIONS 删除以收紧棘轮:\n"
        + "\n".join(map(str, sorted(removed)))
    )
