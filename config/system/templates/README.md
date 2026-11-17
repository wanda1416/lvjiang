# 模板图

- `<name>.png`：要在画面里定位的小图（截图工具框选保存即可；带 alpha 时透明区按黑处理）
- 用户层覆盖：同名文件放 `config/local/templates/`。
- `common/tracked_person_avatar.png`：默认人物追踪头像，仅包含头像与深色底，
  不含动态距离、姓名或场景。桌面和 Android 引用同一模板；搜索范围由调用方限定在
  画面横向 `[0.25, 0.75]`，不搜索小地图。

布局分组（目录名等于根布局 key，如 `android` / `desktop`）的录制画布宽高、匹配阈值
保存在 Layout JSON 的 Region `template` 绑定中；**按名使用**的普通分组（如 `common`）
没有布局绑定，尺寸记在同目录的 `templates/templates.yaml`（用户覆盖写
`config/local/templates/templates.yaml`，键级合并）。`templates/` 目录不生成 sidecar JSON。

```yaml
# config/system/templates/templates.yaml
version: 1
templates:
  common/tracked_person_avatar:   # 按名模板：录制画布宽高，用于跨分辨率归一
    record_w: 2800
    record_h: 1260
  common/locked:                  # 标定样本：不做模板匹配，不需要尺寸
    role: sample
```

- `role: template`（默认，可省略）＝可被 `find` / `scan by image` 寻址的模板；
  `role: sample` ＝算法标定样本（如装备锁图标按颜色比例判定），不参与匹配，永久保留。
- 本文件是聚合配置（与分组目录同级，不在配置层根），带 `content_version`，参与在线下发；
  模板 PNG 与工作流同类，随整包修订下发。
- **所有权**：布局分组（目录名 = 根布局 key）的模板由场景编辑器截取/删除，图库管理只读；
  普通分组（如 `common`）由图库管理新建、改名、删除。
- 条目缺 `record_w`/`record_h` 表示"未登记尺寸"，运行期按原始像素匹配（同分辨率专用）。

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
