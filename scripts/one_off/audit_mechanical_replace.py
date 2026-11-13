"""机械替换审计：扫 873a8e3d / 0f264b44 里的 `["k"]`→`.k` 替换，
找出对「仍是 dict 的对象」做了属性访问的地方。

方法：对两个提交改动过的每个运行时函数，
1) 取 HEAD 版本源码；
2) 找 `.attr` 访问点；
3) 回溯该变量来源（函数返回注解 / 赋值处的构造）判断是 dataclass 还是 dict；
4) dict 却用属性访问 → 报问题；dataclass 却用下标 → 也报。
"""
from __future__ import annotations

import re
import subprocess

FILES = [
    "src/lvjiang/core/profile/snapshot_sync.py",
    "src/lvjiang/core/profile/repository.py",
    "src/lvjiang/core/profile/service.py",
    "src/lvjiang/core/profile/engine.py",
    "src/lvjiang/core/profile/regen.py",
    "src/lvjiang/core/profile/history_query.py",
    "src/lvjiang/core/profile/key_rename.py",
    "src/lvjiang/core/profile/triggers.py",
    "src/lvjiang/core/profile/sync.py",
    "src/lvjiang/ui/profile/history_dialog.py",
    "src/lvjiang/ui/profile/tab.py",
    "src/lvjiang/ui/profile/cell_editing.py",
    "src/lvjiang/ui/profile/cell_formatting.py",
    "src/lvjiang/workflows/builtins/profile.py",
    "src/lvjiang/core/profile/rows.py",
]

DATACLASSES: dict[str, set[str]] = {}   # 类名 → 字段集
DICT_FACTORIES: set[str] = set()        # 返回 list[dict]/dict 的函数名


def head_text(path: str) -> str:
    out = subprocess.run(["git", "show", f"HEAD:{path}"],
                         capture_output=True)
    return out.stdout.decode("utf-8", errors="replace") if out.returncode == 0 else ""


def collect_dataclasses(text: str) -> None:
    current: str | None = None
    for line in text.split("\n"):
        m = re.match(r"@dataclass", line)
        if m:
            current = "pending"
            continue
        if current == "pending":
            m = re.match(r"class (\w+)", line)
            if m:
                current = m.group(1)
                DATACLASSES.setdefault(current, set())
            continue
        if current and current != "pending":
            m = re.match(r"\s{4}(\w+)\s*:", line)
            if m and not line.strip().startswith(("def ", "#")):
                DATACLASSES[current].add(m.group(1))


def collect_dict_factories(text: str) -> None:
    for match in re.finditer(r"def (\w+)\([^)]*\)\s*->\s*(list\[dict\]|dict\b)", text):
        DICT_FACTORIES.add(match.group(1))


problems: list[str] = []
checked = 0

for path in FILES:
    text = head_text(path)
    if not text:
        continue
    collect_dataclasses(text)
    collect_dict_factories(text)

for path in FILES:
    text = head_text(path)
    if not text:
        continue
    lines = text.split("\n")
    # 变量 → 类型 的局部推断（赋值自已知工厂/构造）
    var_type: dict[str, str] = {}
    for index, line in enumerate(lines):  # noqa: B007
        # x = factory(...)
        for fname in DICT_FACTORIES:
            m = re.search(rf"(\w+)\s*=\s*(?:\w+\.)?{fname}\(", line)
            if m:
                var_type[m.group(1)] = "dict"
        # x = SomeDataclass(...)
        for cls in DATACLASSES:
            m = re.search(rf"(\w+)\s*=\s*(?:\w+\.)?{cls}\(", line)
            if m:
                var_type[m.group(1)] = cls
        # for item in list_of_dict_factory(...)
        for fname in DICT_FACTORIES:
            m = re.search(rf"for\s+(\w+)\s+in\s+(?:[\w.]+)?(?:{fname})\(", line)
            if m:
                var_type[m.group(1)] = "dict"
        # 已知 dict 字面量构造：{"k": ...}
        m = re.match(r"\s*(\w+)\s*=\s*\{", line)
        if m and re.search(r'"\w+":', line):
            var_type[m.group(1)] = "dict"

    for index, line in enumerate(lines):  # noqa: B007
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # dict 变量的属性访问：var.attr
        for var, kind in var_type.items():
            if kind == "dict":
                for m in re.finditer(rf"(?<![\w.]){var}\.(\w+)", line):
                    attr = m.group(1)
                    if attr in {"get", "keys", "values", "items", "append", "pop",
                                "setdefault", "update", "copy"}:
                        continue
                    problems.append(
                        f"{path}:{index + 1}  dict 变量 `{var}` 用了属性访问 .{attr}"
                        f"  ← {stripped[:90]}")
        # dataclass 变量的下标访问：var["attr"]
        for var, kind in var_type.items():
            if kind in DATACLASSES:
                for _m in re.finditer(rf"(?<![\w.]){var}\[", line):
                    problems.append(
                        f"{path}:{index + 1}  dataclass `{kind}` 变量 `{var}` 用了下标访问"
                        f"  ← {stripped[:90]}")
        checked += 1

print(f"检查行数: {checked}，已知 dataclass: {sorted(DATACLASSES)}")
print(f"已知 dict 工厂: {sorted(DICT_FACTORIES)}")
print(f"\n发现 {len(problems)} 处可疑访问：")
for item in problems:
    print(" ", item)
