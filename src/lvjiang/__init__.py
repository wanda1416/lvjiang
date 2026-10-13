"""律匠客户端包。

私有管理控制台检出在主工程 ``admin/`` 下时，以 ``lvjiang.admin`` 子包的形式
并入同一个包命名空间（``admin/src/lvjiang/admin/``）。这里用 ``extend_path``
把搜索路径上其它同名 ``lvjiang`` 目录追加进 ``__path__``，dev 脚本的 ``--admin``
依赖它找到管理控制台入口。没有该检出时行为与普通包完全一致。
"""
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
