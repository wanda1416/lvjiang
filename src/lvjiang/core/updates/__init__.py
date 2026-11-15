"""版本与下发：检查更新、更新包、公告、官方预置。

- :mod:`.release`：GitHub 发布信息与版本比较（发布接口 + 跳过版本）；
- :mod:`.bundle`：更新包（成员索引、摘要、路径校验）；
- :mod:`.announcement`：公告清单的拉取与「已看过」标记；
- :mod:`.preset_sources`：预置基底的来源清单与树归一；
- :mod:`.system_preset`：官方预置的构建与安装（config/system 的初始内容）。

这一组只处理「会随上游变化的东西」；本机运行态与用户资产不在这里。
"""
