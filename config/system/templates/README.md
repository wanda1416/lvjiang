# 模板图

- `<name>.png`：要在画面里定位的小图（截图工具框选保存即可；带 alpha 时透明区按黑处理）
- 用户层覆盖：同名文件放 `config/local/templates/`。

录制画布宽高、匹配阈值和模板名统一保存在 Layout JSON 的 Region `template`
绑定中，`templates/` 目录不生成 sidecar JSON。

DSL：`find as $v by image "name" where confidence >= 0.85`，详见
`docs/30-architecture/32-grammar/04.4-image-template.md`。

布局模板优先通过绑定引用，避免脚本写死不同布局的目录名：
`find as $v by image [game_menu_page].[back]`。模板来源 Region 可以禁用；它仅负责
提供当前布局对应的模板和默认阈值，find 的搜索范围由语句前半部分独立决定。

场景编辑器也可从当前截图为 Region 截取模板，默认名称为
`<布局根>/<scene key>/<region key>`，并把 `name`、`min_score`、`record_w`、
`record_h` 绑定写入
该布局的 Region。DSL 用 `scan [scene].[r1, r2] as $v by image`
按顺序匹配这些绑定；匹配范围严格不超出各 Region。
