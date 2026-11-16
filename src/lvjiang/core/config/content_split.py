"""scenes.yaml / layouts.yaml → 目录化结构（拆分迁移）。

目标结构（每个配置层根目录独立成立，层间零合并）::

    <layer>/scenes/_meta.json          # {"groups": [{"key", "scenes": [...]}]}
    <layer>/scenes/{group}.yaml        # 分组定义（name）
    <layer>/scenes/{group}/{scene}.yaml
    <layer>/layouts/_meta.json         # {"layouts": [key, ...]}
    <layer>/layouts/{key}.yaml         # 布局清单（entry 映射）
    <layer>/layouts/{key}/{group}/{scene}.json

迁移必须严格两阶段，防止「场景 key 与分组 key 相同」时的整文件覆盖：
分组定义文件位于 ``scenes/{key}.yaml``，与旧平铺场景文件同一路径——若某场景
key 恰好等于某分组 key，先建分组文件就会把场景覆盖掉。因此：

- 阶段一：只把平铺场景文件移入 ``scenes/{group}/`` 子目录，**不新建任何文件**；
  目标已存在即报错停止（绝不覆盖）。
- 阶段二：移动全部完成后才生成分组定义 / ``_meta.json`` / 布局清单，
  最后删除旧聚合文件。布局侧同一纪律（先移坐标 JSON 入分组子目录）。

幂等：两个聚合文件都不存在时返回 False，不做任何事。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml
from loguru import logger

SCENES_META_NAME = "_meta.json"
LAYOUTS_META_NAME = "_meta.json"
#: 无分组信息可依时的兜底分组（迁移兜底 / 加载兜底一致）
FALLBACK_GROUP = "general"


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"迁移前解析失败 {path}: {e}") from None
    return data if isinstance(data, dict) else {}


def _read_scene_groups(scenes_yaml: Path) -> dict[str, dict]:
    """旧 scenes.yaml v2 的 scenes 映射：key → {name, items, disabled}"""
    doc = _load_yaml(scenes_yaml)
    groups = doc.get("scenes")
    if not isinstance(groups, dict):
        raise ValueError(f"{scenes_yaml} 缺少 scenes 映射，无法迁移")
    return groups


def _scene_to_group(groups: dict[str, dict]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for group_key, group in groups.items():
        items = group.get("items") or [] if isinstance(group, dict) else []
        for scene_key in items:
            mapping[str(scene_key)] = str(group_key)
    return mapping


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def migrate_layer(root: Path) -> bool:
    """迁移单个配置层根目录；发生迁移返回 True，无需迁移返回 False。

    Raises:
        ValueError: 聚合文件缺失映射、或移动目标已存在（绝不覆盖）。
    """
    root = Path(root)
    scenes_yaml = root / "scenes.yaml"
    layouts_yaml = root / "layouts.yaml"
    if not scenes_yaml.is_file() and not layouts_yaml.is_file():
        return False

    scene_groups = _read_scene_groups(scenes_yaml) if scenes_yaml.is_file() else {}
    scene_owner = _scene_to_group(scene_groups)
    group_keys = list(scene_groups)
    disabled: set[str] = set()
    for group in scene_groups.values():
        if isinstance(group, dict):
            disabled.update(group.get("disabled") or [])

    scenes_dir = root / "scenes"
    layouts_dir = root / "layouts"
    scenes_dir.mkdir(parents=True, exist_ok=True)

    # ── 阶段一：移动平铺场景文件入分组子目录（不新建任何文件） ──
    flat_scene_files = sorted(
        p for p in scenes_dir.glob("*.yaml") if p.is_file())
    for path in flat_scene_files:
        scene_key = path.stem
        group_key = scene_owner.get(scene_key)
        if group_key is None:
            group_key = group_keys[0] if group_keys else FALLBACK_GROUP
            logger.warning(
                f"[迁移] 场景 {scene_key} 未登记进 scenes.yaml 分组，"
                f"归入兜底分组 {group_key}")
        target = scenes_dir / group_key / path.name
        if target.exists():
            raise ValueError(
                f"[迁移] 目标已存在，拒绝覆盖：{target}；"
                f"请先人工处理该冲突再迁移")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
        logger.info(f"[迁移] 场景文件已入组: {path.name} -> scenes/{group_key}/")

    # 旧 scenes.yaml 里的 disabled 落到场景文件自身（阶段二之前补写）
    for scene_key in sorted(disabled):
        group_key = scene_owner.get(scene_key)
        if group_key is None:
            continue
        path = scenes_dir / group_key / f"{scene_key}.yaml"
        if not path.is_file():
            continue
        data = _load_yaml(path)
        if data.get("disabled"):
            continue
        data["disabled"] = True
        _write_text(path, yaml.safe_dump(
            data, allow_unicode=True, default_flow_style=False, sort_keys=False))

    # ── 阶段二：生成分组定义 / _meta.json / 布局清单 ──
    # 分组清单 = scenes.yaml 登记顺序 + 未登记但有文件的目录（按名追加）
    meta_groups: list[str] = list(group_keys)
    for path in scenes_dir.iterdir():
        if path.is_dir() and path.name not in meta_groups \
                and not path.name.startswith(("_", ".")):
            meta_groups.append(path.name)
            logger.warning(f"[迁移] 发现未登记的分组目录，追加到末尾: {path.name}")

    meta_doc: dict = {"groups": []}
    for group_key in meta_groups:
        group_dir = scenes_dir / group_key
        members = sorted(
            p.stem for p in group_dir.glob("*.yaml")
            if p.is_file()) if group_dir.is_dir() else []
        registered = scene_groups.get(group_key)
        name = (registered or {}).get("name", group_key) \
            if isinstance(registered, dict) else group_key
        # 顺序以 scenes.yaml 登记为准，目录里多出的文件追加在末尾
        ordered = [s for s in (registered or {}).get("items") or []
                   if s in members] if isinstance(registered, dict) else []
        ordered.extend(s for s in members if s not in ordered)
        meta_doc["groups"].append({"key": group_key, "scenes": ordered})
        group_file = scenes_dir / f"{group_key}.yaml"
        if group_file.exists():
            raise ValueError(
                f"[迁移] 分组定义目标已存在，拒绝覆盖：{group_file}")
        _write_text(group_file, yaml.safe_dump(
            {"name": name}, allow_unicode=True, default_flow_style=False,
            sort_keys=False))
    _write_text(
        scenes_dir / SCENES_META_NAME,
        json.dumps(meta_doc, ensure_ascii=False, indent=2) + "\n")

    # ── 布局：先移坐标 JSON 入分组子目录，再写清单与 _meta.yaml 纪律同上 ──
    layout_doc = _load_yaml(layouts_yaml) if layouts_yaml.is_file() else {}
    layout_entries = layout_doc.get("layouts")
    if layouts_yaml.is_file() and not isinstance(layout_entries, dict):
        raise ValueError(f"{layouts_yaml} 缺少 layouts 映射，无法迁移")
    # 聚合 diff 的元键（__deleted__ 等）不是布局条目。DELETABLE_PATHS 从未
    # 放行 layouts 顶层删除，这里出现 __deleted__ 只可能是空占位，跳过即可。
    layout_entries = {
        key: value for key, value in (layout_entries or {}).items()
        if not str(key).startswith("__")}
    layout_keys = list(layout_entries)
    if layouts_dir.is_dir():
        for layout_dir in sorted(p for p in layouts_dir.iterdir() if p.is_dir()):
            if layout_dir.name.startswith(("_", ".")):
                continue
            for json_path in sorted(layout_dir.glob("*.json")):
                scene_key = json_path.stem
                if scene_key.startswith("_"):
                    continue
                group_key = scene_owner.get(scene_key)
                if group_key is None:
                    group_key = meta_groups[0] if meta_groups else FALLBACK_GROUP
                    logger.warning(
                        f"[迁移] 布局坐标 {json_path.name} 的场景未登记分组，"
                        f"归入兜底分组 {group_key}")
                target = layout_dir / group_key / json_path.name
                if target.exists():
                    raise ValueError(
                        f"[迁移] 目标已存在，拒绝覆盖：{target}")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(json_path), str(target))
            # 清掉因移动而变空的目录层级残留（只删空目录，不碰别的文件）
            for sub in sorted(layout_dir.glob("*"), reverse=True):
                if sub.is_dir() and not any(sub.iterdir()):
                    sub.rmdir()

    for layout_key, entry in layout_entries.items():
        layout_file = layouts_dir / f"{layout_key}.yaml"
        if not isinstance(entry, dict) or not entry:
            raise ValueError(f"布局 {layout_key} 的定义无效，无法迁移")
        if layout_file.exists():
            raise ValueError(
                f"[迁移] 布局清单目标已存在，拒绝覆盖：{layout_file}")
        _write_text(layout_file, yaml.safe_dump(
            entry, allow_unicode=True, default_flow_style=False,
            sort_keys=False))
    if layout_keys:
        _write_text(
            layouts_dir / LAYOUTS_META_NAME,
            json.dumps({"layouts": layout_keys}, ensure_ascii=False,
                       indent=2) + "\n")

    scenes_yaml.unlink(missing_ok=True)
    layouts_yaml.unlink(missing_ok=True)
    logger.info(f"[迁移] {root} 已切换为目录化结构（scenes/layouts 拆分完成）")
    return True


def needs_migration(root: Path) -> bool:
    root = Path(root)
    return (root / "scenes.yaml").is_file() or (root / "layouts.yaml").is_file()


def ensure_content_split(system_root: Path, local_root: Path) -> bool:
    """运行时迁移入口：对 system 与 local 两层执行拆分；任一层迁移返回 True。

    幂等且可重复执行；失败即抛出，调用方自行决定是否阻断启动——半迁移状态
    （场景已入组但聚合文件还在）重跑会从阶段二继续，不会重复移动。
    """
    changed = False
    for label, root in (("system", system_root), ("local", local_root)):
        if not needs_migration(root):
            continue
        logger.info(f"[迁移] 检测到旧聚合配置，开始拆分: {label} ({root})")
        changed = migrate_layer(root) or changed
    return changed
