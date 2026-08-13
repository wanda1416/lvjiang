"""跑脚本前的静态检查：脚本里的静态引用 vs 当前布局的绑定

DSL 只认 key，不认场景定义里的中文名 —— 把名字当 key 写（`[返回]` 而非
`[back]`）、key 拼错、布局漏绑一个按钮，这几类错误过去都要等执行到那一行
才暴露，而在改成硬报错之前更糟：静默空转，后续步骤在错误的页面上乱点。

故在执行前把脚本的全部静态引用与当前布局比一遍，一次性列出所有问题，
而不是跑到第几百行才炸一个。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .scene_scan import KIND_LABELS, RefUse


@dataclass(frozen=True)
class RefProblem:
    """一处校验不通过的引用"""
    ref: RefUse
    reason: str


def _bound_keys(layout, scene: str) -> dict[str, set[str]]:
    """取某场景在当前布局已绑定的各类 key"""
    return {
        "region": {r.key for r in layout.get_scene_regions(scene)},
        "point": {p.key for p in layout.get_scene_points(scene)},
        "arrow": {a.key for a in layout.get_scene_arrows(scene)},
        "panel": {p.key for p in layout.get_scene_panels(scene)},
    }


def _check_arrow_ends(layout, scene: str, arrow_key: str, points: set[str]) -> str | None:
    """校验方向两端的坐标点：起点必存，终点仅吸附态需存"""
    arrow = next((a for a in layout.get_scene_arrows(scene) if a.key == arrow_key), None)
    if arrow is None:
        return None
    if arrow.from_key not in points:
        return f"方向「{arrow_key}」的起点坐标点未绑定: {arrow.from_key}"
    if arrow.to_key and arrow.to_key not in points:
        return f"方向「{arrow_key}」的终点坐标点未绑定: {arrow.to_key}"
    return None


def check_refs(refs: list[RefUse], layout) -> list[RefProblem]:
    """逐条比对引用与布局绑定，返回全部问题（按传入顺序）

    kind 决定查哪类对象：
    - click_target 查 region+point+panel（click_any 的查找顺序）
    - drag_target 查 arrow+region（drag 的 SceneRef 分支）
    - drag_grid_target 查 panel+region（drag 的 PanelGridDrag 分支）
    - arrow 查 arrows、panel 查 panels、region 查 regions、point 查 points
    - scan 查 region+panel（单 key scan/recognize）
    key 为 None 的引用（动态 $var、整场景识别）只能校验到场景一级。
    """
    problems: list[RefProblem] = []
    cache: dict[str, dict[str, set[str]]] = {}
    for ref in refs:
        bound = cache.setdefault(ref.scene, _bound_keys(layout, ref.scene))
        if not any(bound.values()):
            problems.append(RefProblem(ref, "场景未绑定任何坐标"))
            continue
        if ref.key is None:
            continue
        if ref.kind == "click_target":
            ok = ref.key in bound["region"] or ref.key in bound["point"] or ref.key in bound["panel"]
        elif ref.kind == "drag_target":
            ok = ref.key in bound["arrow"] or ref.key in bound["region"]
        elif ref.kind == "drag_grid_target":
            ok = ref.key in bound["panel"] or ref.key in bound["region"]
        elif ref.kind == "arrow":
            ok = ref.key in bound["arrow"]
        elif ref.kind == "panel":
            ok = ref.key in bound["panel"]
        elif ref.kind == "scan":
            # 单 key scan/recognize：区域或面板任一绑定即可（运行时据此分派）
            ok = ref.key in bound["region"] or ref.key in bound["panel"]
        elif ref.kind == "point":
            ok = ref.key in bound["point"]
        else:
            ok = ref.key in bound["region"]
        if not ok:
            label = KIND_LABELS.get(ref.kind, ref.kind)
            problems.append(RefProblem(ref, f"{label}未绑定"))
            continue
        # 检查 arrow 端点
        if ref.kind in ("arrow", "drag_target") and ref.key in bound["arrow"]:
            reason = _check_arrow_ends(layout, ref.scene, ref.key, bound["point"])
            if reason:
                problems.append(RefProblem(ref, reason))
    return problems


def _where(ref: RefUse) -> str:
    """问题位置：有源文件则 文件名:行号，否则只报行号"""
    if ref.source and not ref.source.startswith("<"):
        return f"{Path(ref.source).name}:{ref.line_no}"
    return f"行 {ref.line_no}"


def format_problems(problems: list[RefProblem]) -> str:
    """拼成多行报错文本，每行一处问题"""
    lines = [f"静态检查未通过，脚本有 {len(problems)} 处引用在当前布局中找不到："]
    for p in problems:
        key = f".[{p.ref.key}]" if p.ref.key else ""
        lines.append(f"  {_where(p.ref)}  [{p.ref.scene}]{key} — {p.reason}")
    lines.append("请核对脚本里的 key 拼写（DSL 只认 key，不认中文名），或在场景布局编辑器中绑定")
    return "\n".join(lines)
