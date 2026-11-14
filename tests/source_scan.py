"""源码静态扫描的共享缓存。

i18n 字面量覆盖、死键检查、插件依赖门禁都要把 ``src/`` 下全部 ``.py`` 解析成
AST 再遍历；各自扫一遍时每个 xdist worker 会重复解析同一批文件好几次。这里按
文件路径缓存解析结果，同一进程内只解析一次。

缓存的 AST 由多个测试共享，调用方只能读，不能修改节点。
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


@lru_cache(maxsize=None)
def source_files(root: Path = SRC_ROOT) -> tuple[Path, ...]:
    """``root`` 下全部 ``.py``，按路径排序。"""
    return tuple(sorted(root.resolve().rglob("*.py")))


@lru_cache(maxsize=None)
def parsed(path: Path) -> ast.Module | None:
    """解析单个文件；语法错误返回 ``None``（交给 ruff/mypy 报，扫描方不误报）。"""
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return None


@lru_cache(maxsize=None)
def string_constants(path: Path) -> frozenset[str]:
    """文件里全部字符串常量（含 docstring）。"""
    tree = parsed(path)
    if tree is None:
        return frozenset()
    return frozenset(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str))
