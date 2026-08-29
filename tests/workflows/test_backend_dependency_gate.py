"""通用工作流层不得依赖具体平台输入实现。"""

import ast
from pathlib import Path

WORKFLOWS_ROOT = Path(__file__).parents[2] / "src" / "lvjiang" / "workflows"
FORBIDDEN_PREFIXES = (
    "lvjiang.core.android",
    "lvjiang.core.desktop",
)


def _absolute_import(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package_parts = list(path.relative_to(WORKFLOWS_ROOT.parent.parent).parts[:-1])
    keep = len(package_parts) - node.level + 1
    prefix = package_parts[:keep]
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def test_workflows_do_not_import_platform_backend_implementations():
    violations: list[str] = []
    for path in WORKFLOWS_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                targets.append(_absolute_import(path, node))
            for target in targets:
                if target.startswith(FORBIDDEN_PREFIXES):
                    relative = path.relative_to(WORKFLOWS_ROOT.parent.parent)
                    violations.append(f"{relative}:{node.lineno} -> {target}")

    assert not violations, "通用工作流层依赖了平台具体实现:\n" + "\n".join(
        sorted(violations)
    )
