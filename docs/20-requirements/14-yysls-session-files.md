# 燕云会话数据分文件存储

燕云插件的会话状态存放在 `config/session/yysls/`。固定登记的四个业务文件分别是
`play_styles.json`、`graduations.json`、`attr_loadout.json` 和
`attr_derivations.json`。文件内容直接是原 `session.json.yysls.<key>` 的值；
新增状态必须先在代码中登记文件名，未知键不自动生成文件。

启动燕云插件时，存储层读取 `_meta.json` 中的迁移版本。版本为 `1` 时只使用
新目录，不再检查旧节点。没有版本记录时，在 `SessionStore` 写锁内读取
`session.json.yysls`，校验所有子项，将它们写入对应文件，成功后删除旧节点，
最后写入版本记录。旧节点不存在时创建空文件并记录版本。

迁移中断时旧节点仍在，下次启动可重试。已经写入且内容与旧值相同的文件可
复用；内容冲突、旧节点形状错误或出现未知子项时停止迁移并保留原数据。
`play_styles` 与 `attr_derivations` 的联合更新使用可恢复事务，保存、删除、
重命名基础属性后两份状态保持一致。

目录锁、原子读写、版本校验和事务恢复由通用
`core.config.document_store.DocumentDirectoryStore` 提供；燕云适配器只登记上述
文件并实现旧 `session.json.yysls` 节点迁移。该抽取不改变磁盘格式和初始化顺序。
