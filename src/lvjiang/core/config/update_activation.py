"""安装后的只读缓存刷新；业务加载失败不阻断其它配置安装。"""
from __future__ import annotations

from pathlib import Path

from loguru import logger

from .resolver import get_resolver


def activate_configuration(_system: Path, changed: tuple[str, ...]) -> None:
    """由主线程调用；只刷新内容相关缓存，不写 active 值和用户参数。"""
    resolver = get_resolver()
    resolver.clear_read_cache()
    resolver.notify_changes(changed, refresh_content=False)
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
                try:
                    callback()
                except Exception as error:
                    logger.warning("配置已安装，{} 的缓存刷新失败，仅影响对应功能：{}", app_id, error)
