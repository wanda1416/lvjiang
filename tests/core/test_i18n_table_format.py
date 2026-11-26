"""翻译表格式门禁：所有词表条目都必须单行、且整份能被 YAML 解析。

抽词表的生成器遇到长文案会写出跨行条目（显式键 `? …` / `: …`，或把普通标量折行），
一句话摊成两三行，diff 与人工核对都很费劲。换行要写成 ``"第一行\\n第二行"`` 这种
双引号单行条目——YAML 会把 ``\\n`` 解释成换行，key 与源码字面量仍然相等。

判定逻辑直接用归一脚本的实现（单一来源）：``scripts/one_off/normalize_i18n_newlines.py``
的 ``rewrite()`` 返回「这个文件还有几条跨行条目」，这里要求全部为 0。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
I18N_DIR = REPO / "config" / "i18n"
NORMALIZER = REPO / "scripts" / "one_off" / "normalize_i18n_newlines.py"


def _load_normalizer():
    spec = importlib.util.spec_from_file_location("normalize_i18n_newlines", NORMALIZER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_TABLES = sorted(I18N_DIR.rglob("*.yaml"))


def test_translation_tables_exist() -> None:
    """目录结构变了要立刻知道，否则下面两条会“零文件全绿”。"""
    assert len(_TABLES) > 200


@pytest.mark.parametrize("path", _TABLES, ids=lambda p: p.relative_to(I18N_DIR).as_posix())
def test_table_parses(path: Path) -> None:
    """整份词表必须能被 YAML 解析。

    值以 ``{`` 开头却漏了引号之类的错误，只会在该语言的用户启动时炸开，这里提前拦。
    """
    try:
        yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        pytest.fail(f"{path.relative_to(I18N_DIR)} 无法解析：{str(exc).splitlines()[0]}")


def test_no_multiline_entries() -> None:
    """一条词条只能占一行；换行用 ``\\n`` 转义写在双引号里。"""
    module = _load_normalizer()
    offenders = {}
    for path in _TABLES:
        lines = path.read_text(encoding="utf-8").splitlines()
        _, count = module.rewrite(lines)
        if count:
            offenders[path.relative_to(I18N_DIR).as_posix()] = count
    assert not offenders, (
        f"这些词表还有跨行条目：{offenders}——跑 "
        f"`python scripts/one_off/normalize_i18n_newlines.py --apply` 归一。")
