"""候选校验与安装后的只读缓存刷新；不重置会话或覆盖编辑草稿。"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import get_ident

from .resolver import ConfigResolver, get_resolver
from .runtime_snapshot import using_configuration
from .update_validation import validate_documents


@contextmanager
def _fail_on_errors():
    from loguru import logger
    errors = []
    thread = get_ident()
    sink = logger.add(lambda message: errors.append(str(message)), level="ERROR",
                      filter=lambda record: record["thread"].id == thread)
    try:
        yield
        if errors:
            raise ValueError("候选配置无法完整加载:\n" + "\n".join(errors[:10]))
    finally:
        logger.remove(sink)


def validate_candidate(system: Path, local: Path, app_ids: tuple[str, ...]) -> None:
    from ...apps import load_app
    from ...workflows.engine import WorkflowEngine
    from ...workflows.metadata import parse_metadata_file
    from ..layout_config import load_layout_entries
    from ..layout_manager import load_layout_by_key
    from ..scene_config import load_scene_manifest
    from ..scene_registry import get_registry
    from .models import parse_delay_params
    from .plans import Plan

    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
    with using_configuration(resolver), _fail_on_errors():
        patterns = tuple(pattern for app_id in app_ids for pattern in load_app(app_id).offline_configuration_patterns)
        validate_documents(system, patterns)
        for app_id in app_ids:
            for callback in load_app(app_id).configuration_validation_callbacks:
                callback()
        manifest = load_scene_manifest(resolver)
        registry = get_registry()
        if any(key not in registry.all_scenes() for key in manifest.order):
            raise ValueError("场景登记表引用缺失或无效的场景定义")
        from ..maps import MapManager
        from ..reference_db import ReferenceDatabase
        maps = MapManager(resolver, registry)
        for key in maps.list_keys():
            item = maps.load(key)
            maps.validate_bindings(item)
            if maps.base_image_path(item) is None:
                raise ValueError(f"地图底图缺失或摘要不匹配: {key}")
        for name in resolver.enumerate_entities("references", "*.yaml"):
            space = Path(name).stem
            database = ReferenceDatabase(
                system_dir=system / "references" / space, local_dir=local / "references" / space,
                system_yaml=system / "references" / name, local_yaml=local / "references" / name,
                system_spaces_dir=system / "references", local_spaces_dir=local / "references",
                session_path=system.parent / "validation-session.json")
            for entry in database.entries:
                if not database.image_path(entry.file).is_file():
                    raise ValueError(f"参考图资源缺失: {space}/{entry.file}")
        layouts = load_layout_entries(resolver)
        app = resolver.load_merged("app.yaml")
        plans = [Plan.from_dict(value) for value in app.get("plans", [])]
        for plan in plans:
            if plan is None:
                raise ValueError("运行方案定义无效")
            if not plan.layout:
                continue
            if plan.layout not in layouts:
                raise ValueError(f"运行方案引用不存在的布局: {plan.layout}")
            layout = load_layout_by_key(plan.layout)
            if layout is None:
                raise ValueError(f"布局加载失败: {plan.layout}")
            for regions in layout.regions.values():
                for region in regions:
                    if region.template and resolver.resolve_read(f"templates/{region.template.name}.png") is None:
                        raise ValueError(f"布局模板资源缺失: {region.template.name}")
            engine = WorkflowEngine(capture=None, ocr=None, input_ctrl=None, layout=layout,
                                    delay_params=parse_delay_params(app.get("delay_params", {})), run_env=plan.env)
            for name in resolver.enumerate_entity_tree("workflows", "*.wf"):
                if any(part.startswith("_") for part in Path(name).parts):
                    continue
                path = resolver.resolve_read(f"workflows/{name}")
                if path is None:
                    continue
                metadata = parse_metadata_file(path)
                if not metadata.get("env") or plan.env in metadata["env"]:
                    engine.validate_only(path)


def activate_configuration(_system: Path, changed: tuple[str, ...]) -> None:
    """由主线程调用；只刷新内容相关缓存，不写 active 值和用户参数。"""
    resolver = get_resolver()
    resolver.clear_read_cache()
    resolver.notify_changes(changed, strict=True)
    if "ocr.yaml" in changed:
        from ..ocr_cleaner import OCRCleaner
        OCRCleaner.reset_instance()
    if any(name.startswith("templates/") for name in changed):
        from ..recognizers.template_locator import get_template_store
        get_template_store().invalidate()
    from ...apps import get_registered_app_ids, load_app
    for app_id in get_registered_app_ids():
        if any(name.startswith(app_id + "/") for name in changed):
            for callback in load_app(app_id).configuration_reload_callbacks:
                callback()
