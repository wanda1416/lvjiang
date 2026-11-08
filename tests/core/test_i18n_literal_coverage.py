"""tr() 文案必须真的能查到译文 —— 逐个随包语言核对。

真实事故：新增一句 ``tr("…")`` 后没人往词表补条目，运行时既不报错也不写日志，
界面静默回退中文。内容一致性测试随 f41d6d16a 删除后这类缺口再无人发现，累计
67 条（核心 28 / 插件 39）在全部 11 个非中文界面显示中文，直到 2026-11-08 才
按本文件的规则补齐。

同一轮排查还暴露两个同源缺陷，这里一并守门：

- ``是: Yes`` 裸写：YAML 1.1 把 Yes/No 解析成布尔，而配对器只认字符串，条目
  静默失效（10 个新语言核心层的「是」因此一直是死条目）。
- 旧标签值自带冒号（``材料: 'Materials:'``）：``tr_label()`` 再补一次冒号，
  界面显示成 ``Materials:: ``。

词表自身的死键、zh/en 键集对齐见 ``test_i18n_stale_keys.py``。
"""
from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import pytest
import yaml

from lvjiang import i18n
from lvjiang.i18n import init_i18n, load_app_i18n

ROOT = Path(__file__).resolve().parents[2]
I18N_DIR = ROOT / "config" / "i18n"

#: 冒号是排版符号，由 tr_label() 追加，词表值不应再带
_TRAILING_COLONS = (":", "：")


def _shipped_languages() -> list[str]:
    """随包语言 = config/i18n/*.yaml 去掉原文语言 zh_CN。

    新增语言文件后自动纳入覆盖检查，不需要改这里。
    """
    return sorted(path.stem for path in I18N_DIR.glob("*.yaml") if path.stem != "zh_CN")


def _app_dirs() -> list[Path]:
    apps_dir = I18N_DIR / "apps"
    if not apps_dir.is_dir():
        return []
    return sorted(path for path in apps_dir.iterdir() if path.is_dir())


@lru_cache(maxsize=None)
def _translations_for(language: str) -> dict[str, str]:
    """按运行时入口装载（核心层 + 全部插件层）后真正生效的扁平查表。

    故意走 init_i18n/load_app_i18n 而不是自己重写配对逻辑：这里要守的是
    “用户切到该语言后 tr() 能不能查到”，包括插件层覆盖和加载顺序。
    """
    init_i18n(language)
    for app_dir in _app_dirs():
        load_app_i18n(app_dir.name)
    return dict(i18n._translations)


@lru_cache(maxsize=1)
def _tr_literals() -> dict[str, str]:
    """源码里全部 ``tr("字面量")`` / ``tr_label("字面量")`` / ``tr_in("字面量", lang)``
    的原文 → 首个出处。

    只认字面量：``tr(变量)`` 无法静态核对，由调用点自己保证（例如游戏配置
    显示名走词表里的具体条目）。``tr_in`` 的第一参数同样是原文，故一并纳入——
    它按目标语言查表，缺条目时同样静默回退中文，缺口性质完全一样。
    """
    found: dict[str, str] = {}
    for path in sorted((ROOT / "src").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # 语法错误的文件由 ruff/mypy 拦，这里不误报
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name not in ("tr", "tr_label", "tr_in") or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.setdefault(first.value, str(path.relative_to(ROOT)))
    return found


def _string_leaves(language: str) -> Iterator[tuple[str, object]]:
    """该语言全部模块文件里的叶子值（跳过多模块文件的 meta 段）。"""
    files = sorted((I18N_DIR / language).glob("*.yaml"))
    files += [I18N_DIR / f"{language}.yaml"]
    for path in files:
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        yield from _walk(data, path)


def _walk(node: object, path: Path, trail: tuple[str, ...] = ()) -> Iterator[tuple[str, object]]:
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if not trail and key == "meta":
            continue
        where = f"{path.relative_to(ROOT).as_posix()}::{'/'.join(trail + (str(key),))}"
        if isinstance(value, dict):
            yield from _walk(value, path, trail + (str(key),))
        else:
            yield where, value


def _key_paths(path: Path) -> set[tuple[str, ...]]:
    """词表文件的键路径集合（跳过多模块文件顶层的 meta 段）。"""
    return _paths(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def _paths(node: object, trail: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    if not isinstance(node, dict):
        return set()
    found: set[tuple[str, ...]] = set()
    for key, value in node.items():
        if not trail and key == "meta":
            continue
        if isinstance(value, dict):
            found |= _paths(value, trail + (str(key),))
        else:
            found.add(trail + (str(key),))
    return found


@pytest.fixture(autouse=True)
def _restore_i18n_state() -> Iterator[None]:
    """init_i18n 会改模块级全局，用例结束后还原，避免污染其它用例。"""
    saved_language = i18n._current_language
    saved_translations = dict(i18n._translations)
    saved_apps = set(i18n._loaded_app_i18n)
    try:
        yield
    finally:
        i18n._current_language = saved_language
        i18n._translations.clear()
        i18n._translations.update(saved_translations)
        i18n._loaded_app_i18n.clear()
        i18n._loaded_app_i18n.update(saved_apps)


@pytest.mark.parametrize("language", _shipped_languages())
def test_every_tr_literal_has_a_translation(language: str) -> None:
    """tr() 的每句字面量都必须能在该语言查到译文。

    门槛固定为 0（不是棘轮）：查不到就是非中文界面出现中文，用户可直接看到。
    新增文案时同批补 zh_CN 自映射、en_US 译文和各语言条目。
    """
    table = _translations_for(language)
    missing = {text: where for text, where in _tr_literals().items() if text not in table}
    detail = sorted(f"{text!r} @ {where}" for text, where in missing.items())
    assert not missing, (
        f"{language} 有 {len(missing)} 条 tr() 文案查不到译文（会回退中文）：\n"
        + "\n".join(detail[:20])
    )


@pytest.mark.parametrize("language", _shipped_languages())
def test_translation_values_are_strings(language: str) -> None:
    """词表叶子必须是字符串。

    ``是: Yes`` 这种裸写在 YAML 1.1 里是布尔 True，配对器只认 ``isinstance(str)``，
    于是条目静默失效——不报错、不告警，只是界面回退中文。
    """
    offenders = [where for where, value in _string_leaves(language)
                 if not isinstance(value, str)]
    assert not offenders, (
        f"{language} 有 {len(offenders)} 条词表值不是字符串（多半是 Yes/No/On/Off "
        f"裸写被解析成布尔，需加引号）：{offenders[:10]}"
    )


@pytest.mark.parametrize("language", _shipped_languages())
def test_language_tables_cover_every_en_key(language: str) -> None:
    """en_US 的每个键路径都必须以同一段落出现在该语言里。

    配对是纯路径配对：段落名或键路径对不上就整条跳过，既不报错也不记日志。
    真实事故：10 个新语言的插件表把 zh/en 的 ``yysls`` 段（709 条游戏术语）
    整段生成进了 ``auto_strings`` 段，235 条在 10 个语言里静默回退中文，
    而两边的条目总数看起来还都对得上。

    反向不要求相等：该语言多出来的键只是死条目，不在这里拦。
    """
    problems: list[str] = []
    for en_file in sorted((I18N_DIR / "en_US").glob("*.yaml")):
        target = I18N_DIR / language / en_file.name
        if not target.exists():
            problems.append(f"{en_file.name}: 该语言缺此模块文件")
            continue
        missing = _key_paths(en_file) - _key_paths(target)
        if missing:
            examples = sorted("/".join(path) for path in missing)[:3]
            problems.append(f"{en_file.name}: 少 {len(missing)} 个键路径（如 {examples}）")
    apps_root = I18N_DIR / "apps"
    for app_en in sorted(apps_root.glob("*/en_US.yaml")) if apps_root.is_dir() else []:
        target = app_en.parent / f"{language}.yaml"
        if not target.exists():  # 插件可以只随包部分语言，缺失即不承诺覆盖
            continue
        missing = _key_paths(app_en) - _key_paths(target)
        if missing:
            examples = sorted("/".join(path) for path in missing)[:3]
            problems.append(
                f"apps/{app_en.parent.name}: 少 {len(missing)} 个键路径（如 {examples}）")
    assert not problems, problems


@pytest.mark.parametrize("language", _shipped_languages())
def test_app_fallback_values_match_en(language: str) -> None:
    """英文回退语言的插件词表必须与 en_US 逐条相同（放行非 ASCII 的本地化值）。

    真实事故：10 个新语言的插件表停在 dd4ca53f 修正官方译名之前的英文快照，
    336 条术语与 en 分叉——同一游戏概念在不同界面语言下叫法不同：
    ``Dingyin`` / ``Attunement``、``Cap Value`` / ``Maximum``、
    ``Loadout`` / ``Equipment Builds``。这些语言本来就以英文回退，值不一致
    只说明复制品腐烂，不是本地化。
    """
    en_file = I18N_DIR / "apps" / "yysls" / "en_US.yaml"
    target = en_file.parent / f"{language}.yaml"
    if not target.exists():  # 插件可以只随包部分语言
        return
    en_values = _flat_values(en_file)
    drifted = {
        "/".join(path): (value, en_values[path])
        for path, value in _flat_values(target).items()
        if value.isascii() and path in en_values and en_values[path] != value
    }
    assert not drifted, (
        f"{language} 插件层有 {len(drifted)} 条英文值与 en_US 不一致"
        f"（应随 en 一起改，不要各存一份）：{sorted(drifted)[:8]}")


def _flat_values(path: Path) -> dict[tuple[str, ...], str]:
    """词表文件的 {键路径: 字符串值}（跳过多模块文件顶层 meta）。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    values: dict[tuple[str, ...], str] = {}

    def walk(node: object, trail: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if not trail and key == "meta":
                continue
            if isinstance(value, dict):
                walk(value, trail + (str(key),))
            elif isinstance(value, str):
                values[trail + (str(key),)] = value

    walk(data, ())
    return values


@pytest.mark.parametrize("language", _shipped_languages())
def test_literal_translations_do_not_end_with_colon(language: str) -> None:
    """文案的译文不应自带冒号，否则 tr_label() 会拼出双冒号。

    只检查真的会被 tr() 命中的键：历史自动抽取留下的 ``基础规则_单选`` 之类死
    条目不在调用路径上，不在这里扩大范围。
    """
    literals = _tr_literals()
    table = _translations_for(language)
    offenders = sorted(
        f"{text!r} -> {value!r}"
        for text, value in table.items()
        if text in literals
        and not text.rstrip().endswith(_TRAILING_COLONS)
        and value.rstrip().endswith(_TRAILING_COLONS)
    )
    assert not offenders, (
        f"{language} 有 {len(offenders)} 条译文自带冒号（tr_label 会补第二个）："
        f"{offenders[:10]}"
    )
