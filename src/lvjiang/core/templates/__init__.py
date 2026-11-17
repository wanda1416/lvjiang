"""模板图库（``find`` / ``scan by image`` 用的定位图）。

与参考图库（``core.reference_db``）完全分离：那里是分类器，这里是定位器。
目录即分组，录制尺寸与角色存在合并索引 ``templates.yaml`` 里。
"""

from .library import (
    INDEX_REL,
    ROLE_SAMPLE,
    ROLE_TEMPLATE,
    ROLES,
    TemplateBinding,
    TemplateInfo,
    bindings_by_name,
    drop_entry,
    group_of,
    image_names,
    is_layout_group,
    load_index,
    managed_by_scene,
    remove_template,
    rename_template,
    root_layout_groups,
    script_refs,
    set_entry,
    template_info,
    templates,
)

__all__ = [
    "INDEX_REL",
    "ROLES",
    "ROLE_SAMPLE",
    "ROLE_TEMPLATE",
    "TemplateBinding",
    "TemplateInfo",
    "bindings_by_name",
    "drop_entry",
    "group_of",
    "image_names",
    "is_layout_group",
    "managed_by_scene",
    "load_index",
    "remove_template",
    "rename_template",
    "root_layout_groups",
    "script_refs",
    "set_entry",
    "template_info",
    "templates",
]
