"""场景/布局目录迁移 v2：跨层规划，原件留档，按固定计划恢复。

不发现插件、不初始化 registry；所有输入均来自显式的两个配置根。
.migrations/content-split-v2 内的记录同时保存原字节和计划，不能删除后重试。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import yaml
from loguru import logger

from ..fs_util import atomic_write_bytes
from ..json_files import formatted_json
from .merge import merge_doc

SCENES_META_NAME = "_meta.json"
LAYOUTS_META_NAME = "_meta.json"
FALLBACK_GROUP = "general"
_RECORDS = ".migrations/content-split-v2"
_PATHS = ("scenes.*.items", "scenes.*.disabled")


class ContentSplitConflict(ValueError):
    """原值受到保护；这些场景不得静默使用系统默认内容。"""

    def __init__(self, message: str, scenes=()):
        super().__init__(message)
        self.scenes = set(scenes)


def _encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _decode(raw: str) -> bytes:
    return base64.b64decode(raw)


def _yaml(raw: bytes) -> dict:
    data = yaml.safe_load(raw.decode("utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("配置顶层必须是映射")
    return data


def _dump(data: dict) -> bytes:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode("utf-8")


def _write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), prefix=".content-split-")


def _commit_write(path: Path, raw: bytes) -> None:
    atomic_write_bytes(path, raw, prefix=".content-split-")


class _Plan:
    def __init__(self, roots: dict[str, Path]):
        self.roots = roots
        self.originals: dict[str, str | None] = {}
        self.writes: dict[str, str] = {}
        self.deletes: list[str] = []
        self.scenes: set[str] = set()

    def key(self, layer: str, rel: str) -> str:
        return f"{layer}/{rel}"

    def read(self, layer: str, rel: str) -> bytes | None:
        key = self.key(layer, rel)
        if key not in self.originals:
            path = self.roots[layer] / rel
            self.originals[key] = _encode(path.read_bytes()) if path.is_file() else None
        raw = self.originals[key]
        return _decode(raw) if raw is not None else None

    def put(self, layer: str, rel: str, raw: bytes, *, replace=False):
        key = self.key(layer, rel)
        old = self.read(layer, rel)
        if old is not None and old != raw and not replace:
            raise ContentSplitConflict(
                f"迁移目标已存在，拒绝覆盖：{self.roots[layer] / rel}", self.scenes
            )
        if key in self.writes and self.writes[key] != _encode(raw):
            raise ContentSplitConflict(f"同一目标有不同来源：{rel}", self.scenes)
        self.writes[key] = _encode(raw)

    def move(self, layer: str, old: str, new: str):
        raw = self.read(layer, old)
        assert raw is not None
        self.put(layer, new, raw)
        self.deletes.append(self.key(layer, old))

    def record(self) -> dict:
        return dict(
            version=2,
            state="pending",
            originals=self.originals,
            writes=self.writes,
            deletes=self.deletes,
            scenes=sorted(self.scenes),
        )


def _tree_groups(root: Path) -> dict:
    from ..scene.config import load_scene_manifest
    from .resolver import ConfigResolver

    manifest = load_scene_manifest(
        ConfigResolver(
            system_dir=root, local_dir=root, dev_mode=False, track_content=False
        )
    )
    return {
        key: {"name": manifest.group_names[key], "items": items}
        for key, items in manifest.groups.items()
        if (root / "scenes" / key).is_dir()
    }


def _owners(groups: dict) -> dict[str, str]:
    owners: dict[str, str] = {}
    for group, data in groups.items():
        for scene in data.get("items") or []:
            if scene in owners and owners[scene] != group:
                raise ContentSplitConflict(f"场景归属不唯一：{scene}", [scene])
            owners[scene] = group
    return owners


def _build_plan(roots: dict[str, Path]) -> _Plan:
    plan = _Plan(roots)
    old: dict[str, dict] = {}
    trees = {layer: _tree_groups(root) for layer, root in roots.items()}
    for layer, root in roots.items():
        raw = plan.read(layer, "scenes.yaml")
        old[layer] = _yaml(raw).get("scenes", {}) if raw is not None else {}
        if not isinstance(old[layer], dict):
            raise ValueError(f"{root / 'scenes.yaml'} 缺少 scenes 映射")
        plan.scenes.update(
            p.stem
            for p in (root / "layouts").glob("*/*.json")
            if not p.stem.startswith("_")
        )
        for path in (root / "scenes").glob("*.yaml"):
            data = _yaml(path.read_bytes())
            if "key" in data:  # 严格验证场景身份；分组定义没有 key 字段
                if data["key"] != path.stem:
                    raise ContentSplitConflict(
                        f"场景身份与文件名不符：{path}", [path.stem]
                    )
                plan.scenes.add(path.stem)
    base = old.get("system") or trees.get("system", {})
    effective = {"scenes": base}
    if "local" in roots:
        effective = merge_doc(effective, {"scenes": old.get("local", {})}, _PATHS)
    groups = effective["scenes"]
    owners = _owners(groups)
    for scene, base_group in _owners(base).items():
        owners.setdefault(scene, base_group)
    # 当前目录归属也是事实源，local 同路径影子不改变系统场景归属。
    for tree in trees.values():
        for scene, tree_group in _owners(tree).items():
            if scene in owners and owners[scene] != tree_group:
                raise ContentSplitConflict(
                    f"旧登记与当前目录归属冲突：{scene}", plan.scenes | {scene}
                )
            owners[scene] = tree_group
    pending = set(plan.scenes)
    for layer, root in roots.items():
        flat_scenes = []
        for path in sorted((root / "scenes").glob("*.yaml")):
            if "key" in _yaml(path.read_bytes()):
                flat_scenes.append(path)
        # 规划全部场景移动后再生成分组定义，允许分组与场景同名。
        for path in flat_scenes:
            group = owners.get(path.stem)
            if group is None:
                raise ContentSplitConflict(
                    f"场景没有可证明的分组，原件保留：{path}", pending
                )
            plan.move(
                layer, path.relative_to(root).as_posix(), f"scenes/{group}/{path.name}"
            )
        for path in sorted((root / "layouts").glob("*/*.json")):
            if path.stem.startswith("_"):
                continue
            group = owners.get(path.stem)
            if group is None:
                raise ContentSplitConflict(
                    f"坐标没有可证明的场景归属，原件保留：{path}", pending
                )
            plan.move(
                layer,
                path.relative_to(root).as_posix(),
                f"layouts/{path.parent.name}/{group}/{path.name}",
            )
        # 旧错误版本没有来源记录：不猜测非当前分组下的哪份坐标应当生效。
        for path in sorted((root / "layouts").glob("*/*/*.json")):
            group = owners.get(path.stem)
            if group and path.parent.name != group:
                raise ContentSplitConflict(
                    f"发现可能迁错的坐标；请核对后选择原件：{path} -> {group}/",
                    pending | {path.stem},
                )
        for group, data in old[layer].items():
            if not isinstance(data, dict) or group.startswith("__"):
                continue
            rel = f"scenes/{group}.yaml"
            if "name" in data:
                # 该位置可能仍是刚规划移走的同名旧场景，原字节已在记录中。
                plan.put(
                    layer,
                    rel,
                    _dump({"name": data["name"]}),
                    replace=plan.key(layer, rel) in plan.deletes,
                )
            if "disabled" in data:
                desired = set(groups.get(group, {}).get("disabled") or [])
                for scene in groups.get(group, {}).get("items") or []:
                    if owners.get(scene) != group:
                        continue
                    target = f"scenes/{group}/{scene}.yaml"
                    key = plan.key(layer, target)
                    raw = (
                        _decode(plan.writes[key])
                        if key in plan.writes
                        else plan.read(layer, target)
                    )
                    if raw is None and layer == "local":
                        raw = plan.read("system", target)
                    if raw is None:
                        continue
                    doc = _yaml(raw)
                    disabled = scene in desired
                    if bool(doc.get("disabled")) != disabled:
                        doc["disabled"] = disabled
                        # 已规划的移动可派生 disabled，但原字节仍保留在 originals。
                        plan.writes.pop(key, None)
                        plan.put(layer, target, _dump(doc), replace=True)
        if plan.read(layer, "scenes.yaml") is not None:
            meta = []
            for group, data in old[layer].items():
                if not isinstance(data, dict) or group.startswith("__"):
                    continue
                items = data.get("items") or []
                if isinstance(items, dict):
                    items = list(items.get("__order__") or []) + list(
                        items.get("__added__") or []
                    )
                meta.append({"key": group, "scenes": list(dict.fromkeys(items))})
            plan.put(
                layer,
                "scenes/_meta.json",
                formatted_json({"groups": meta}).encode(),
                replace=True,
            )
            plan.deletes.append(plan.key(layer, "scenes.yaml"))
        raw = plan.read(layer, "layouts.yaml")
        if raw is not None:
            entries = _yaml(raw).get("layouts")
            if not isinstance(entries, dict):
                raise ValueError(f"{root / 'layouts.yaml'} 缺少 layouts 映射")
            keys = []
            for key, entry in entries.items():
                if key.startswith("__"):
                    continue
                if not isinstance(entry, dict) or not entry:
                    raise ValueError(f"布局定义无效：{key}")
                keys.append(key)
                plan.put(layer, f"layouts/{key}.yaml", _dump(entry))
            plan.put(
                layer,
                "layouts/_meta.json",
                formatted_json({"layouts": keys}).encode(),
                replace=True,
            )
            plan.deletes.append(plan.key(layer, "layouts.yaml"))
    return plan


def _apply_record(roots: dict[str, Path], path: Path, record: dict) -> None:
    def target(key):
        layer, rel = key.split("/", 1)
        return roots[layer] / rel

    originals, writes = record["originals"], record["writes"]
    # 开始落盘前核验所有输入；重跑允许本计划已经写入/移除的结果。
    for key, original in originals.items():
        file = target(key)
        now = _encode(file.read_bytes()) if file.is_file() else None
        allowed = [original]
        if key in writes:
            allowed.append(writes[key])
        if key in record["deletes"]:
            allowed.append(None)
        if now not in allowed:
            raise ContentSplitConflict(
                f"迁移期间文件已变化，拒绝覆盖：{file}", record["scenes"]
            )
    for key, raw in writes.items():
        file = target(key)
        payload = _decode(raw)
        if not file.is_file() or file.read_bytes() != payload:
            _commit_write(file, payload)
    for key in record["deletes"]:
        if key not in writes:
            target(key).unlink(missing_ok=True)
    record["state"] = "complete"
    _commit_write(path, formatted_json(record).encode())


def ensure_content_split(system_root: Path, local_root: Path) -> bool:
    from .content import config_write_lock, configuration_lock

    roots = {"system": Path(system_root)}
    if Path(local_root) != Path(system_root):
        roots["local"] = Path(local_root)
    records = Path(local_root) / _RECORDS
    # 无旧路径/未完成记录时不建目录、不加文件锁，保持只读实例的常规读取无写入。
    pending = [
        p
        for p in sorted(records.glob("*.json"))
        if json.loads(p.read_text(encoding="utf-8"))["state"] == "pending"
    ]
    if not pending and not any(needs_migration(r) for r in roots.values()):
        # 仍检查错误版本遗留的异组坐标，不能以完成标记掩盖冲突。
        _build_plan(roots)
        return False
    with (
        configuration_lock(Path(local_root).parent),
        config_write_lock(Path(local_root)),
    ):
        for path in pending:
            _apply_record(roots, path, json.loads(path.read_text(encoding="utf-8")))
        plan = _build_plan(roots)
        if not plan.writes and not plan.deletes:
            return bool(pending)
        records.mkdir(parents=True, exist_ok=True)
        path = records / f"{len(list(records.glob('*.json'))) + 1:06d}.json"
        record = plan.record()
        _commit_write(path, formatted_json(record).encode())
        _apply_record(roots, path, record)
        return True


def migrate_layer(root: Path) -> bool:
    return ensure_content_split(root, root)


def needs_migration(root: Path) -> bool:
    root = Path(root)
    if (root / "scenes.yaml").is_file() or (root / "layouts.yaml").is_file():
        return True
    if any(not p.stem.startswith("_") for p in (root / "layouts").glob("*/*.json")):
        return True
    return any("key" in _yaml(p.read_bytes()) for p in (root / "scenes").glob("*.yaml"))


def prepare_content_split(resolver) -> None:
    """组合根入口：冲突只隔离受影响场景；失败不静默使用系统定义/坐标。"""
    resolver.content_split_blocked_scenes = set()
    try:
        ensure_content_split(resolver.system_dir, resolver.local_dir)
    except ContentSplitConflict as error:
        resolver.content_split_blocked_scenes = error.scenes
        logger.error("场景/布局迁移未完成，相关场景不可执行，原件已保留：{}", error)


def repair_misplaced_coordinate(resolver, source: str, target: str) -> None:
    """用户明确选择的一份旧迁错坐标；不覆盖目标，原字节进入同一恢复记录。"""
    from pathlib import PurePosixPath

    from ..scene.config import load_scene_manifest
    from .content import config_write_lock, configuration_lock

    old, new = PurePosixPath(source), PurePosixPath(target)
    if any(p.is_absolute() or ".." in p.parts or "\\" in str(p) for p in (old, new)):
        raise ValueError("修复路径必须是 local 内的相对路径")
    if (
        len(old.parts) != 4
        or len(new.parts) != 4
        or old.parts[0] != "layouts"
        or new.parts[:2] != old.parts[:2]
        or new.name != old.name
        or old.suffix != ".json"
    ):
        raise ValueError("只允许同布局、同场景的坐标改正分组")
    manifest = load_scene_manifest(resolver)
    group = next(
        (g for g, scenes in manifest.groups.items() if old.stem in scenes), None
    )
    if group is None or new.parts[2] != group:
        raise ValueError("目标不是该场景的有效分组")
    roots = {"system": resolver.system_dir, "local": resolver.local_dir}
    with configuration_lock(resolver.config_dir), config_write_lock(resolver.local_dir):
        plan = _Plan(roots)
        plan.scenes.add(old.stem)
        plan.move("local", source, target)
        records = resolver.local_dir / _RECORDS
        records.mkdir(parents=True, exist_ok=True)
        path = records / f"{len(list(records.glob('*.json'))) + 1:06d}.json"
        record = plan.record()
        _commit_write(path, formatted_json(record).encode())
        _apply_record(roots, path, record)
