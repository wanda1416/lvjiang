"""任务历史与历史回传。

- :mod:`.daily`：任务/批量历史的 SQLite 持久化，以及逐任务日志与结果路径；
- :mod:`.transfer`：手机历史回传（声明式表范围、业务键去重、附件路径校验）。

这里与 profile 的变更账本无关：profile 的账本在 ``core/profile``（
``profile_history``），本包记的是「谁在什么时候跑了哪条任务」。
"""
