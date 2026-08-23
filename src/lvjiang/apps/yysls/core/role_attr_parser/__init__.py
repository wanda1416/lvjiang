"""角色基础属性 OCR 数据转换器

将角色详情页 detail_1/detail_2 的 OCR 原始文本转换为标准基础属性字典。
"""

from .parser import (
    RoleAttrParser,
    get_role_attr_parser,
    merge_scroll_snapshots,
    parse_detail1,
    parse_detail2_attack,
    parse_detail2_attr_pen,
    parse_detail2_outer_pen,
)

__all__ = [
    "RoleAttrParser",
    "get_role_attr_parser",
    "merge_scroll_snapshots",
    "parse_detail1",
    "parse_detail2_attack",
    "parse_detail2_outer_pen",
    "parse_detail2_attr_pen",
]
