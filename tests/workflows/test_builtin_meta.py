"""内置函数元数据与本地脚本静态扫描。

元数据是管理界面与文档的共同数据源，漂移一次就会同时错两处，所以这里把
「元数据与真实签名自洽」当门禁跑。
"""

from pathlib import Path

from lvjiang.workflows.builtins import (
    describe_function,
    get_function,
    list_functions,
    signature_params,
    validate_function_meta,
)
from lvjiang.workflows.builtins.script_meta import (
    scan_directory,
    scan_script,
    script_problems,
)

SCRIPT = '''\
"""演示脚本"""
GROUP = "演示分组"

from lvjiang.workflows.builtins import builtin_func


@builtin_func(
    "local_double",
    group="覆盖分组",
    summary="翻倍",
    params={"value": "待翻倍的值"},
    returns="int",
)
def local_double(value: int) -> int:
    """翻倍

    细节说明
    """
    return value * 2


@builtin_func("needs_engine")
def needs_engine(_engine, name="x"):
    pass


def plain():
    pass
'''


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ─── 元数据门禁 ───────────────────────────────────────────


def test_all_builtin_functions_have_consistent_meta():
    problems = [problem for name in list_functions() for problem in validate_function_meta(name)]
    assert problems == []


def test_every_function_has_a_declared_group():
    groups = {describe_function(name).group for name in list_functions()}
    assert "其他" not in groups
    assert {"基础运算", "字符串处理", "图色"} <= groups


def test_signature_params_skip_injection_and_mark_varargs():
    specs = {spec.name: spec for spec in signature_params(get_function("append"))}
    assert "list_or_dict" in specs
    assert "*args" in specs


def test_meta_carries_param_meanings():
    specs = {spec.name: spec for spec in signature_params(get_function("app_start"))}
    assert specs["name"].meaning
    assert specs["timeout"].meaning


# ─── 脚本静态扫描 ─────────────────────────────────────────


def test_scan_script_reads_decorator_literals(tmp_path):
    found = {item.name: item for item in scan_script(_write(tmp_path / "demo.py", SCRIPT))}

    assert set(found) == {"local_double", "needs_engine"}
    doubled = found["local_double"]
    assert doubled.group == "覆盖分组"
    assert doubled.summary == "翻倍"
    assert doubled.returns == "int"
    assert doubled.params == {"value": "待翻倍的值"}
    assert doubled.line > 0
    assert doubled.injected is None

    injected = found["needs_engine"]
    # 未写 group 时继承模块级 GROUP
    assert injected.group == "演示分组"
    assert injected.injected == "engine"
    assert injected.param_specs == [("name", "'x'", "")]


def test_scan_script_ignores_undecorated_functions(tmp_path):
    names = {item.name for item in scan_script(_write(tmp_path / "demo.py", SCRIPT))}
    assert "plain" not in names


def test_scan_directory_skips_broken_files_but_reports_them(tmp_path):
    _write(tmp_path / "ok.py", SCRIPT)
    _write(tmp_path / "broken.py", "def broken(:\n")

    assert len(scan_directory(tmp_path)) == 2
    problems = script_problems(tmp_path)
    assert len(problems) == 1
    assert "broken.py" in problems[0]


def test_scan_directory_missing_root_is_empty(tmp_path):
    assert scan_directory(tmp_path / "nope") == []
    assert script_problems(tmp_path / "nope") == []
