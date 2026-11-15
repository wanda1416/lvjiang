"""账号服务的错误类型。

单独一个模块是因为它被传输层与凭据层共用；放在 ``service`` 里会让这两层反向
依赖装配层，形成循环导入。
"""

from __future__ import annotations


class AccountError(Exception):
    """账号服务操作失败；message 是可直接展示的中文。"""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code
