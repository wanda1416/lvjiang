"""清单文件加载：``config/system/yysls/lingering.yaml`` → :class:`LingerCatalog`。

所有地区合在一份文件里（``regions`` 按地区 id 索引），内容随版本下发。用户的
增删改写在 local 层的**同名文件**里（``config/local/yysls/lingering.yaml``，
见 :mod:`.overrides`），合并语义由 ``merge_policy`` 登记。读取不缓存：页面按需
打开，一份文件的解析成本可以忽略。

失败边界：整份文件解析失败时页面提示「清单未安装」并写一条 error 日志，而不是弹
异常——清单是外部内容，坏掉时页面还能打开、能看到提示。
"""
from __future__ import annotations

from loguru import logger

from .....core.config.resolver import ConfigResolver, get_resolver
from .models import LingerCatalog, catalog_from_doc

#: 清单文件（相对配置根，system/local 同名合并）
LINGER_FILE = "yysls/lingering.yaml"


def load_catalog(resolver: ConfigResolver | None = None) -> LingerCatalog:
    """读取整份清单；文件缺失或写坏时返回空目录，由页面提示「清单未安装」。"""
    resolver = resolver or get_resolver()
    try:
        doc = resolver.load_merged(LINGER_FILE)
    except FileNotFoundError:
        logger.warning(f"袅袅之音清单不存在：{LINGER_FILE}")
        return LingerCatalog(())
    except Exception as exc:
        logger.error(f"袅袅之音清单 {LINGER_FILE} 无法读取：{exc}")
        return LingerCatalog(())
    try:
        return catalog_from_doc(doc, source_name=LINGER_FILE)
    except Exception as exc:  # 内容写坏了：整份丢弃，页面给提示
        logger.error(f"袅袅之音清单 {LINGER_FILE} 解析失败：{exc}")
        return LingerCatalog(())
