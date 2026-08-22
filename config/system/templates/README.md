# 模板图（`find … by image` 用）

- `<name>.png`：要在画面里定位的小图（截图工具框选保存即可；带 alpha 时透明区按黑处理）
- `<name>.json`（可选）：`{"recordW": 2400, "recordH": 1080}` 录制时的画布宽高，
  运行时按 `当前画布宽 / recordW` 缩放模板再匹配（±10% 各试一次，1.0 兜底）。
  不给则只按原尺寸 ±10% 匹配。
- 用户层覆盖：同名文件放 `config/local/templates/`。

DSL：`find as $v by image "name" where confidence >= 0.85`，详见
`docs/30-architecture/32-grammar/04.3-find.md`。
