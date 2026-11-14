"""通用层到插件的依赖方向门禁。

主模块 = ``src/lvjiang`` 下除插件命名空间 ``lvjiang/apps/**`` 以外的全部代码
（core、workflows、ui、i18n、app.py、__main__.py、constants.py……）。

1. 导入门禁：主模块不得静态或动态导入 ``lvjiang.apps`` 及其任何子模块。插件
   宿主（加载器、注册表、``AppHooks`` 契约）在 ``lvjiang.core.plugins``；
   ``lvjiang.apps`` 只是插件命名空间包。
2. 字面量门禁：主模块的字符串字面量（不含 docstring）不得出现插件 ID、插件
   显示名、插件注册的代码工作流 ID，以及历史上出现过的插件私有概念。插件需要
   宿主配合的地方必须通过 hook 在加载时声明，而不是让宿主认识它。

违规清单是迁移棘轮：只能删除，不能增加。
"""

from __future__ import annotations

import ast
import importlib.util
import re
from functools import lru_cache
from pathlib import Path

SRC_ROOT = Path(__file__).parents[1] / "src"
PACKAGE_ROOT = SRC_ROOT / "lvjiang"
APPS_ROOT = PACKAGE_ROOT / "apps"

# ``(相对文件, 行号无关的导入目标)``。迁移完成后的目标是空集合。
KNOWN_VIOLATIONS: set[tuple[str, str]] = set()

#: 插件 hook 推导不出来、但曾被宿主写死过的插件概念（脚本 ID、宿主属性名、
#: 外置插件的工作流 ID、游戏名简称）。新增插件概念被发现写进宿主时补到这里。
EXTRA_PLUGIN_TERMS = frozenset({
    "scan_all_loadouts",
    "_tuning_management",
    "auto_gather",
    "燕云",
})


def _guarded_files() -> list[Path]:
    return sorted(path for path in PACKAGE_ROOT.rglob("*.py") if APPS_ROOT not in path.parents)


def _module_package(path: Path) -> str:
    """相对导入的锚点包：``__init__.py`` 是包自身，普通模块是其所在包。"""
    parts = path.relative_to(SRC_ROOT).with_suffix("").parts
    return ".".join(parts[:-1])


def _resolve_import(path: Path, node: ast.ImportFrom) -> str:
    module = node.module or ""
    if not node.level:
        return module
    return importlib.util.resolve_name(
        "." * node.level + module,
        _module_package(path),
    )


def _is_dynamic_import(node: ast.Call) -> bool:
    """``importlib.import_module(...)``、裸 ``import_module(...)`` 与 ``__import__(...)``。

    只认属性调用会漏掉 ``from importlib import import_module`` 之后的裸调用，
    而那正是绕过门禁最省事的写法。
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        return (isinstance(func.value, ast.Name)
                and func.value.id == "importlib"
                and func.attr == "import_module")
    return isinstance(func, ast.Name) and func.id in ("import_module", "__import__")


def _is_apps_module(target: str) -> bool:
    return target == "lvjiang.apps" or target.startswith("lvjiang.apps.")


def _app_imports(path: Path, source: str | None = None) -> set[str]:
    text = path.read_text(encoding="utf-8") if source is None else source
    tree = ast.parse(text, filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        targets: list[str] = []
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_import(path, node)
            targets.append(base)
            # ``from lvjiang import apps`` 同样是导入 apps 包
            targets.extend(f"{base}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and _is_dynamic_import(node):
            # 动态导入同样要拦：普通 import 检查看不到字符串形式的模块路径。
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                targets.append(first.value)
        imports.update(target for target in targets if _is_apps_module(target))
    return imports


def test_generic_layers_do_not_import_plugin_namespace():
    actual: set[tuple[str, str]] = set()
    for path in _guarded_files():
        relative = path.relative_to(SRC_ROOT).as_posix()
        actual.update((relative, target) for target in _app_imports(path))

    new = actual - KNOWN_VIOLATIONS
    removed = KNOWN_VIOLATIONS - actual
    assert not new, "主模块新增对 lvjiang.apps 的依赖:\n" + "\n".join(map(str, sorted(new)))
    assert not removed, (
        "以下已知违规已经消失，请从 KNOWN_VIOLATIONS 删除以收紧棘轮:\n"
        + "\n".join(map(str, sorted(removed)))
    )


@lru_cache(maxsize=1)
def _plugin_terms() -> frozenset[str]:
    """内置插件自己声明出来的概念：插件 ID、显示名、注册的代码工作流 ID。"""
    from lvjiang.core.plugins import builtin_app_ids, load_app

    terms: set[str] = set(EXTRA_PLUGIN_TERMS)
    for app_id in builtin_app_ids():
        hooks = load_app(app_id)
        terms.update(term for term in (app_id, hooks.id, hooks.name) if term)
        terms.update(hooks.workflow_implementations)
    return frozenset(terms)


def _string_literals(tree: ast.AST) -> list[ast.Constant]:
    docstrings = {
        id(node.value) for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_generic_layers_hold_no_plugin_literals():
    terms = _plugin_terms()
    pattern = re.compile(
        r"(?<![A-Za-z0-9_])(" + "|".join(map(re.escape, sorted(terms, key=len, reverse=True)))
        + r")(?![A-Za-z0-9_])")
    offenders: list[str] = []
    for path in _guarded_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _string_literals(tree):
            match = pattern.search(node.value)
            if match:
                offenders.append(
                    f"{path.relative_to(SRC_ROOT).as_posix()}:{node.lineno}: {match.group(1)!r}")
    assert not offenders, (
        "主模块字面量里出现了插件概念；请改为由插件通过 hook 在加载时声明：\n"
        + "\n".join(offenders))


def test_literal_gate_catches_known_shapes(tmp_path):
    """门禁自检：典型的反向依赖写法都能被识别。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        '"""文档里提到 yysls 不算。"""\n'
        'if flow_id == "scan_all_loadouts":\n'
        '    getattr(engine, "_tuning_management", None)\n',
        encoding="utf-8")
    tree = ast.parse(sample.read_text(encoding="utf-8"))
    values = [node.value for node in _string_literals(tree)]
    assert values == ["scan_all_loadouts", "_tuning_management"]

    package_file = PACKAGE_ROOT / "core" / "fake_module.py"
    for source, expected in (
        ("from lvjiang import apps\n", {"lvjiang.apps"}),
        ("from ..apps import load_app\n", {"lvjiang.apps", "lvjiang.apps.load_app"}),
        ("import importlib\nimportlib.import_module('lvjiang.apps.demo')\n", {"lvjiang.apps.demo"}),
        ("__import__('lvjiang.apps')\n", {"lvjiang.apps"}),
        ("from lvjiang.core.plugins import load_app\n", set()),
    ):
        assert _app_imports(package_file, source) == expected, source
