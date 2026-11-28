"""独立任务包（Standalone Task Package）发现、校验、合并与作用域管理。

独立任务包是放在 ``workflows/`` 下任意位置的文件夹，内部包含 ``manifest.yaml``、
入口 ``.wf`` 脚本，以及与主配置结构完全一致的 ``scenes/``、``layouts/``（及可选
``templates/``）。

核心约束：
1. 普通任务（同级无 ``manifest.yaml``）完全走原有路径，零开销、零行为变更。
2. 独立任务包内 ``scenes/`` 的分组 key 与场景 key 不允许与全局主场景重名，
   运行启动时直接报错中止，强制开发者使用独特前缀区分。
3. 独立任务包内 ``layouts/`` 的布局 key（如 ``desktop`` / ``android``）允许且
   必然与全局同名合并，确保用户选择桌面或安卓布局时能无缝加载任务私有场景。
4. 场景管理加载任务场景时只切到该任务包根（纯切根、不合并全局）；任务运行期
   采用线程级 ContextVar 合并加载并在 ``finally`` 中释放，不污染全局单例与下一任务。
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import yaml
from loguru import logger

from ..core.config.resolver import (
    LAYER_LOCAL,
    LAYER_SYSTEM,
    ConfigResolver,
    OverlayConfigResolver,
    get_root_resolver,
    use_resolver,
)
from ..core.layout.config import load_layout_doc
from ..core.layout.models import Layout
from ..core.layout.screenshots import get_screenshots_root
from ..core.layout.storage import _resolve_layout_entry, load_layout_by_key
from ..core.scene.config import load_scene_manifest
from ..core.scene.definition import SceneRegistry
from ..core.scene.registry import get_registry, use_scene_registry
from .errors import WorkflowUserError
from .file_tree import WORKFLOWS_DIR

MANIFEST_FILENAME = "manifest.yaml"
_PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fff-]+$")
_active_runtime_package_id: ContextVar[str | None] = ContextVar(
    "active_task_package_id", default=None,
)


@dataclass(frozen=True)
class TaskPackage:
    """已解析的独立任务包描述。"""

    id: str
    name: str
    entry: str
    rel_dir: str
    system_dir: Path | None
    local_dir: Path | None
    version: str = ""
    author: str = ""
    min_app_version: str = ""
    description: str = ""
    layer: str = LAYER_SYSTEM

    @property
    def primary_dir(self) -> Path:
        if self.local_dir is not None and (self.local_dir / MANIFEST_FILENAME).is_file():
            return self.local_dir
        if self.system_dir is not None:
            return self.system_dir
        assert self.local_dir is not None
        return self.local_dir

    @property
    def entry_path(self) -> Path:
        if self.local_dir is not None:
            local_entry = self.local_dir / self.entry
            if local_entry.is_file():
                return local_entry
        if self.system_dir is not None:
            system_entry = self.system_dir / self.entry
            if system_entry.is_file():
                return system_entry
        return self.primary_dir / self.entry

    @property
    def has_scenes(self) -> bool:
        return any(
            d is not None and (d / "scenes").is_dir()
            for d in (self.local_dir, self.system_dir)
        )

    @property
    def has_layouts(self) -> bool:
        return any(
            d is not None and (d / "layouts").is_dir()
            for d in (self.local_dir, self.system_dir)
        )

    @property
    def has_templates(self) -> bool:
        return any(
            d is not None and (d / "templates").is_dir()
            for d in (self.local_dir, self.system_dir)
        )


def _validate_entry_path(entry: str, pkg_dir: Path) -> str:
    """校验 manifest.yaml 中的 entry 字段为包目录内的相对 .wf 路径。"""
    cleaned = str(entry or "").strip().replace("\\", "/")
    if not cleaned:
        raise WorkflowUserError(f"任务包 {pkg_dir.name} 的 manifest.yaml 缺少 entry 字段")
    rel = Path(cleaned)
    if rel.is_absolute() or ".." in rel.parts:
        raise WorkflowUserError(
            f"任务包 {pkg_dir.name} 的 entry 路径非法（禁止绝对路径或 ..）: {entry}"
        )
    if rel.suffix.lower() != ".wf":
        raise WorkflowUserError(
            f"任务包 {pkg_dir.name} 的 entry 必须指向 .wf 文件: {entry}"
        )
    resolved_base = pkg_dir.resolve()
    resolved_target = (pkg_dir / rel).resolve()
    try:
        resolved_target.relative_to(resolved_base)
    except ValueError as exc:
        raise WorkflowUserError(
            f"任务包 {pkg_dir.name} 的 entry 越出包目录: {entry}"
        ) from exc
    return rel.as_posix()


def _parse_manifest(manifest_path: Path) -> dict:
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise WorkflowUserError(f"读取任务包清单失败 {manifest_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkflowUserError(f"任务包清单格式无效（必须是 YAML 映射）: {manifest_path}")
    return raw


def load_task_package_dir(
    pkg_dir: Path,
    *,
    rel_dir: str = "",
    system_dir: Path | None = None,
    local_dir: Path | None = None,
) -> TaskPackage:
    """从指定目录（或配对的 system/local 目录）加载 TaskPackage 元信息。"""
    manifest_dir = (
        local_dir
        if local_dir is not None and (local_dir / MANIFEST_FILENAME).is_file()
        else system_dir
        if system_dir is not None and (system_dir / MANIFEST_FILENAME).is_file()
        else pkg_dir
    )
    manifest_path = manifest_dir / MANIFEST_FILENAME
    data = _parse_manifest(manifest_path)

    pkg_id = str(data.get("id") or manifest_dir.name).strip()
    if not pkg_id or _PACKAGE_ID_RE.fullmatch(pkg_id) is None:
        raise WorkflowUserError(f"任务包 id 非法: {pkg_id!r}（位置: {manifest_path}）")

    name = str(data.get("name") or pkg_id).strip()
    default_entry = f"{manifest_dir.name}.wf"
    entry_raw = data.get("entry") or default_entry
    entry = _validate_entry_path(str(entry_raw), manifest_dir)

    layer = (
        LAYER_LOCAL
        if local_dir is not None and (local_dir / MANIFEST_FILENAME).is_file()
        else LAYER_SYSTEM
    )
    return TaskPackage(
        id=pkg_id,
        name=name,
        entry=entry,
        rel_dir=rel_dir,
        system_dir=system_dir,
        local_dir=local_dir,
        version=str(data.get("version") or "").strip(),
        author=str(data.get("author") or "").strip(),
        min_app_version=str(data.get("min_app_version") or "").strip(),
        description=str(data.get("description") or "").strip(),
        layer=layer,
    )


def _workflows_rel_package_dir(dir_path: Path, root: ConfigResolver) -> str | None:
    resolved = dir_path.resolve()
    for base in (root.local_dir, root.system_dir):
        wf_root = (base / WORKFLOWS_DIR).resolve()
        try:
            rel = resolved.relative_to(wf_root)
        except (ValueError, OSError):
            continue
        if rel != Path("."):
            return rel.as_posix()
    return None


def find_task_package_for_wf(wf_path: Path) -> TaskPackage | None:
    """探测 .wf 是否属于独立任务包（支持包根及子目录脚本，无 manifest.yaml 时快速返回 None）。"""
    resolved = Path(wf_path).resolve()
    parent = resolved.parent
    root = get_root_resolver()

    rel_dir = _workflows_rel_package_dir(parent, root)
    if rel_dir is not None:
        # 从当前目录向上逐层查到 workflows/ 根之下，兼容 entry 位于子目录的场景
        parts = Path(rel_dir).parts
        for depth in range(len(parts), 0, -1):
            cand_rel = Path(*parts[:depth]).as_posix()
            sys_dir = root.system_dir / WORKFLOWS_DIR / cand_rel
            loc_dir = root.local_dir / WORKFLOWS_DIR / cand_rel
            has_sys = (sys_dir / MANIFEST_FILENAME).is_file()
            has_loc = (loc_dir / MANIFEST_FILENAME).is_file()
            if has_sys or has_loc:
                return load_task_package_dir(
                    loc_dir if has_loc else sys_dir,
                    rel_dir=cand_rel,
                    system_dir=sys_dir if sys_dir.is_dir() else None,
                    local_dir=loc_dir if loc_dir.is_dir() else None,
                )
        return None

    # 非标准 workflows 目录（例如测试临时目录或外部绝对路径）：向上最多查 4 层
    cur = parent
    for _ in range(4):
        if (cur / MANIFEST_FILENAME).is_file():
            return load_task_package_dir(cur, rel_dir="", system_dir=None, local_dir=cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def discover_task_packages(root: ConfigResolver | None = None) -> list[TaskPackage]:
    """扫描 system 与 local 的 workflows/ 目录，返回全部有效独立任务包。"""
    resolver = root or get_root_resolver()
    rel_dirs: set[str] = set()
    for base in (resolver.system_dir, resolver.local_dir):
        wf_root = base / WORKFLOWS_DIR
        if not wf_root.is_dir():
            continue
        for manifest in wf_root.rglob(MANIFEST_FILENAME):
            if not manifest.is_file():
                continue
            try:
                rel = manifest.parent.relative_to(wf_root).as_posix()
            except ValueError:
                continue
            if rel in ("", "."):
                continue
            # 跳过下划线开头的内部目录
            if any(part.startswith("_") for part in rel.split("/")):
                continue
            rel_dirs.add(rel)

    packages: list[TaskPackage] = []
    for rel in sorted(rel_dirs):
        sys_dir = resolver.system_dir / WORKFLOWS_DIR / rel
        loc_dir = resolver.local_dir / WORKFLOWS_DIR / rel
        try:
            pkg = load_task_package_dir(
                loc_dir if (loc_dir / MANIFEST_FILENAME).is_file() else sys_dir,
                rel_dir=rel,
                system_dir=sys_dir if sys_dir.is_dir() else None,
                local_dir=loc_dir if loc_dir.is_dir() else None,
            )
            packages.append(pkg)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"跳过无效任务包 {rel}: {exc}")
    return packages


def create_package_resolver(
    pkg: TaskPackage,
    root: ConfigResolver | None = None,
) -> ConfigResolver:
    """为独立任务包构造隔离的 ConfigResolver。

    - 若任务包来源于 system（无论 local 是否已有影子），system_dir 指向 system 包目录，
      local_dir 指向对应的 local 影子目录，完整保留「开发写 system、用户写 local」语义。
    - 若任务包仅存在于 local（第三方放入或外部目录），system_dir 与 local_dir 均指向该目录，
      编辑直接原地写盘，不触碰全局 content.json。
    """
    base = root or get_root_resolver()
    if pkg.system_dir is not None and pkg.rel_dir:
        local_shadow = base.local_dir / WORKFLOWS_DIR / pkg.rel_dir
        return ConfigResolver(
            system_dir=pkg.system_dir,
            local_dir=local_shadow,
            dev_mode=base.is_dev_mode(),
            track_content=False,
        )
    target = pkg.primary_dir
    return ConfigResolver(
        system_dir=target,
        local_dir=target,
        dev_mode=False,
        track_content=False,
    )


def package_screenshots_dir(pkg: TaskPackage) -> Path:
    """独立任务包专属的截图隔离目录（位于 session/screenshots/packages/<id>）。

    目录名刻意不带下划线：``config/session/.gitignore`` 用 ``_*/`` 挡掉本地备份
    目录，而包内标定底图和全局 ``screenshots/<布局>/`` 一样是要入库的资产，不能被
    那条规则一起忽略掉。
    """
    return get_screenshots_root() / "packages" / pkg.id


def build_package_registry(
    pkg: TaskPackage,
    pkg_resolver: ConfigResolver,
) -> SceneRegistry:
    """为独立任务包构造纯包内 SceneRegistry（不含全局主场景）。"""
    manifest = load_scene_manifest(pkg_resolver) if pkg.has_scenes else None
    return SceneRegistry(
        resolver=pkg_resolver,
        scene_order=manifest.order if manifest else None,
        group_config=manifest.groups if manifest else None,
        group_names=manifest.group_names if manifest else None,
        disabled_scenes=manifest.disabled if manifest else None,
        group_layers=manifest.group_layers if manifest else None,
        scene_layers=manifest.scene_layers if manifest else None,
    )


def validate_package_no_collisions(
    base_registry: SceneRegistry,
    pkg_registry: SceneRegistry,
    pkg: TaskPackage,
) -> None:
    """校验任务包的分组 key 与场景 key 不与全局主场景重名，冲突直接报错。"""
    base_groups = {gk for gk, _ in base_registry.get_groups()}
    pkg_groups = {gk for gk, _ in pkg_registry.get_groups()}
    dup_groups = sorted(base_groups & pkg_groups)

    base_scenes = set(base_registry.all_scene_keys())
    pkg_scenes = set(pkg_registry.all_scene_keys())
    dup_scenes = sorted(base_scenes & pkg_scenes)

    if not dup_groups and not dup_scenes:
        return

    parts: list[str] = []
    if dup_groups:
        parts.append(f"重复分组: {', '.join(dup_groups)}")
    if dup_scenes:
        parts.append(f"重复场景: {', '.join(dup_scenes)}")
    detail = "；".join(parts)
    raise WorkflowUserError(
        f"任务包「{pkg.name}」({pkg.id}) 与全局主场景存在重名冲突（{detail}）。"
        "请为任务包的分组与场景使用独特前缀以区分系统预置。"
    )


def build_merged_scene_registry(
    base_registry: SceneRegistry,
    pkg_registry: SceneRegistry,
) -> SceneRegistry:
    """构造「全局主场景 + 任务包场景」的合并 SceneRegistry 视图（不修改原实例）。"""
    merged = SceneRegistry.__new__(SceneRegistry)
    merged._resolver = base_registry._resolver
    merged._scenes = {**base_registry._scenes, **pkg_registry._scenes}
    merged._file_baselines = {**base_registry._file_baselines, **pkg_registry._file_baselines}
    merged._order = [*base_registry._order, *pkg_registry._order]
    merged._groups = {**base_registry._groups, **pkg_registry._groups}
    merged._group_order = [*base_registry._group_order, *pkg_registry._group_order]
    merged._group_scenes = {
        **{k: list(v) for k, v in base_registry._group_scenes.items()},
        **{k: list(v) for k, v in pkg_registry._group_scenes.items()},
    }
    merged._disabled_scenes = set(base_registry._disabled_scenes) | set(pkg_registry._disabled_scenes)
    merged._group_layers = {**base_registry._group_layers, **pkg_registry._group_layers}
    merged._scene_layers = {**base_registry._scene_layers, **pkg_registry._scene_layers}
    merged._initial_group_names = {
        **base_registry._initial_group_names,
        **pkg_registry._initial_group_names,
    }
    return merged


def _resolve_root_layout_key(layout_key: str, root: ConfigResolver) -> str:
    """将别名布局（如 desktop_fullscreen / android_cast）解析为根布局 key。"""
    doc = load_layout_doc(root).get("layouts", {})
    resolved = _resolve_layout_entry(doc, layout_key)
    if resolved is not None:
        return resolved[1]
    return layout_key


def merge_package_layout(
    pkg: TaskPackage,
    pkg_resolver: ConfigResolver,
    pkg_registry: SceneRegistry,
    base_layout: Layout | None,
    *,
    root: ConfigResolver | None = None,
) -> Layout | None:
    """将任务包中对应当前布局（支持同名根布局与别名回退）的场景绑定合并到布局副本。"""
    if base_layout is None or not pkg.has_layouts:
        return base_layout

    base_root = root or get_root_resolver()
    root_layout_key = _resolve_root_layout_key(base_layout.key, base_root)

    # 先按精确布局 key 查（如任务包单独配了 android_cast），再回退到根布局 key（如 android / desktop）
    candidates = [base_layout.key]
    if root_layout_key != base_layout.key:
        candidates.append(root_layout_key)

    pkg_layout: Layout | None = None
    with use_resolver(pkg_resolver), use_scene_registry(pkg_registry):
        for cand in candidates:
            loaded = load_layout_by_key(cand)
            if loaded is not None:
                pkg_layout = loaded
                break

    if pkg_layout is None:
        logger.debug(
            f"任务包「{pkg.name}」({pkg.id}) 未包含布局 {candidates}，保持原布局运行"
        )
        return base_layout

    # 浅拷贝基础布局并合并任务包场景，绝不修改原始 base_layout
    return Layout(
        key=base_layout.key,
        name=base_layout.name,
        desc=base_layout.desc,
        canvas=base_layout.canvas,
        aspect=base_layout.aspect,
        aspect_tolerance=base_layout.aspect_tolerance,
        regions={**base_layout.regions, **pkg_layout.regions},
        points={**base_layout.points, **pkg_layout.points},
        arrows={**base_layout.arrows, **pkg_layout.arrows},
        panels={**base_layout.panels, **pkg_layout.panels},
        crop_canvases={**base_layout.crop_canvases, **pkg_layout.crop_canvases},
        subscene_refs={**base_layout.subscene_refs, **pkg_layout.subscene_refs},
    )


@contextmanager
def task_package_runtime_scope(
    wf_path: Path,
    base_layout: Layout | None,
) -> Iterator[Layout | None]:
    """任务运行期独立包合并上下文。

    - 普通任务（无 manifest.yaml）：直接 yield base_layout，零改动。
    - 独立任务包：校验无分组/场景冲突 → 合并场景与同名布局 → 挂载模板叠加解析器 →
      退出时在 finally 中彻底释放，不污染下一个任务。
    """
    pkg = find_task_package_for_wf(wf_path)
    if pkg is None:
        yield base_layout
        return
    # 同一任务包内部嵌套执行子脚本时直接复用已合并作用域，避免与自身冲突
    if _active_runtime_package_id.get() == pkg.id:
        yield base_layout
        return

    base_root = get_root_resolver()
    base_registry = get_registry()
    pkg_resolver = create_package_resolver(pkg, base_root)
    pkg_registry = build_package_registry(pkg, pkg_resolver)

    validate_package_no_collisions(base_registry, pkg_registry, pkg)
    merged_registry = build_merged_scene_registry(base_registry, pkg_registry)
    merged_layout = merge_package_layout(
        pkg, pkg_resolver, pkg_registry, base_layout, root=base_root,
    )
    overlay_resolver: ConfigResolver | OverlayConfigResolver = (
        OverlayConfigResolver(base_root, pkg_resolver)
        if pkg.has_templates
        else base_root
    )

    logger.info(
        f"已合并加载独立任务包「{pkg.name}」({pkg.id}): "
        f"+{len(pkg_registry.all_scene_keys())} 场景"
    )
    pkg_token = _active_runtime_package_id.set(pkg.id)
    with use_resolver(overlay_resolver), use_scene_registry(merged_registry):
        try:
            yield merged_layout
        finally:
            _active_runtime_package_id.reset(pkg_token)
            logger.info(f"已释放独立任务包「{pkg.name}」({pkg.id}) 运行作用域")
