"""上机前预检：类实现工作流的坐标引用校验（AST 静态抽取，不执行）

`preflight_device_workflows.py` 覆盖的是 DSL 脚本 —— 引擎的静态引用收集器只认
DSL 语法。类实现（auto_tuning / single_tuning）在 Python 里用
`self.click_region("场景", "key")` 这类字面量调用引坐标，是那条路径的盲区。

这里用 AST 抽出调用点（`self.CONST` 形式的参数经类属性求值），再与设备端布局
比对。局限：场景参数非字面量时（如 `click_region(GRID_SCENE, slot)` 里的循环
变量 slot）整条跳过，只能校验到场景一级 —— 报「缺失」前务必确认引用的真实场景，
否则会误判（曾把 more_func 判成缺失，它其实绑在动态取到的 detail 场景上）。

用法：
    .venv\\Scripts\\python.exe -X utf8 scripts/manual-tests/preflight_class_refs.py
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lvjiang.core.ondevice.plugins import ensure_loaded  # noqa: E402
from lvjiang.core.ondevice.workflow_runner import (  # noqa: E402
    _default_layout_name,
    _load_layout,
)

# 方法名 → (场景参数位, region/key 参数位或 None, kind)
CALLS = {
    "click_region": (0, 1, "region"),
    "ocr_scene": (0, 1, "region_list"),
    "ocr_scene_by": (0, 1, "region_list"),
    "click_panel": (0, 1, "panel"),
    "scan_panel": (0, 1, "panel"),
}


def _lit(node, cls):
    """求值参数：字符串字面量 / self.CONST / 字符串列表"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        return getattr(cls, node.attr, None)
    if isinstance(node, ast.List):
        vals = [_lit(e, cls) for e in node.elts]
        return [v for v in vals if isinstance(v, str)]
    return None


def collect(path: Path, cls) -> list[tuple[str, str, str, int]]:
    """返回 [(scene, key, kind, lineno)]，无法静态求值的跳过"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                and fn.value.id == "self" and fn.attr in CALLS):
            continue
        si, ki, kind = CALLS[fn.attr]
        if len(node.args) <= si:
            continue
        scene = _lit(node.args[si], cls)
        if not isinstance(scene, str):
            continue
        keys = _lit(node.args[ki], cls) if len(node.args) > ki else None
        if kind == "region_list":
            for k in (keys if isinstance(keys, list) else []):
                out.append((scene, k, "region", node.lineno))
        elif isinstance(keys, str):
            out.append((scene, keys, "region" if kind == "region" else kind,
                        node.lineno))
        else:
            out.append((scene, "", "scene_only", node.lineno))
    return out


def main() -> int:
    ensure_loaded()
    from lvjiang.workflows.implementations import get_workflow_class

    layout = _load_layout(_default_layout_name())
    bad = 0
    for wid in ("auto_tuning", "single_tuning"):
        cls = get_workflow_class(wid)
        path = Path(sys.modules[cls.__module__].__file__)
        refs = collect(path, cls)
        print(f"\n=== {wid} ({path.name}) 抽到 {len(refs)} 处引用 ===")
        seen = set()
        for scene, key, kind, line in refs:
            if (scene, key, kind) in seen:
                continue
            seen.add((scene, key, kind))
            regions = {r.key for r in layout.get_scene_regions(scene)}
            points = {p.key for p in layout.get_scene_points(scene)}
            panels = {p.key for p in layout.get_scene_panels(scene)}
            if not (regions or points or panels):
                print(f"  [缺] 行{line} [{scene}] — 场景未绑定任何坐标")
                bad += 1
                continue
            if kind == "scene_only":
                continue
            ok = (key in regions or key in points) if kind == "region" \
                else key in panels
            if not ok:
                print(f"  [缺] 行{line} [{scene}].[{key}] — {kind} 未绑定")
                bad += 1
        print(f"  去重后 {len(seen)} 组，缺失见上（无输出即全绑定）")
    print(f"\n合计缺失 {bad} 处")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
