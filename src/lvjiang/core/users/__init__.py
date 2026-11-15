"""按用户隔离的资料、笔记与头像。

- :mod:`.document`：``users/{名}.json`` 用户资料文档（用户、元数据、属性、
  工作流参数、按用户隔离的界面偏好）；
- :mod:`.notes`：``users/{名}.notes.json`` 笔记；
- :mod:`.avatars`：``avatars/avatar_<16hex>.png`` 内容寻址的头像库。

命名说明：``users/document.py`` 指的是「单个用户的资料文档」，与
``core/config/document_store.py``（多文档目录 + 事务恢复）不是一层东西；
与 ``core/profile``（Profile 数值与账本）更是两个域。
"""
