"""yysls 领域文档声明——经 ``AppHooks.sync_document_modules`` import 时注册。

core.sync 不认识插件领域词汇；出装（备战方案 + 装备池）和基础属性集是
yysls 的领域文件，kind 与落点由本模块声明：

- ``loadouts``：用户级，``users/<用户名>.loadouts.json``，按整文档覆盖同步；
- ``play_styles``：账户级，``yysls/play_styles.json``，与角色无关、只存一份，
  网页端算毕业率要用的基础属性集就取自它。

两者都见 core/sync/engine.py 的文档通道。
"""
from __future__ import annotations

from ....core.sync.registry import (
    register_session_document,
    register_user_document_kind,
)

register_user_document_kind("loadouts", ".loadouts.json")
register_session_document("play_styles", "yysls/play_styles.json")
