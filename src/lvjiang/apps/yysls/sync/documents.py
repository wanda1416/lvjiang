"""yysls 领域文档声明——经 ``AppHooks.sync_document_modules`` import 时注册。

core.sync 不认识插件领域词汇；出装（备战方案 + 装备池）、基础属性集和袅袅之音
记录是 yysls 的领域文件，kind 与落点由本模块声明：

- ``loadouts``：用户级，``users/<用户名>.loadouts.json``，按整文档覆盖同步；
- ``play_styles``：账户级，``yysls/play_styles.json``，与角色无关、只存一份，
  网页端算毕业率要用的基础属性集就取自它；
- ``linger``：用户级，``users/<用户名>.linger.json``，袅袅之音清单的已得记录。
  kind 名不带 ``ing``，云端按 ``(账号, 用户名, linger)`` 存，一旦有数据不可改名。

三者都见 core/sync/engine.py 的文档通道。
"""
from __future__ import annotations

from ....core.sync.registry import (
    register_session_document,
    register_user_document_kind,
)
from ..core.linger.store import DOCUMENT_KIND as LINGER_KIND
from ..core.linger.store import DOCUMENT_SUFFIX as LINGER_SUFFIX

register_user_document_kind("loadouts", ".loadouts.json")
register_session_document("play_styles", "yysls/play_styles.json")
register_user_document_kind(LINGER_KIND, LINGER_SUFFIX)

