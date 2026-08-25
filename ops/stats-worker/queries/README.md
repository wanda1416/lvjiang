# 统计查询脚本

个人开发者一周看一两次数字，用 `wrangler d1 execute` 直接跑即可，不建立
需要鉴权、需要维护密钥的网页看板。

```bash
wrangler d1 execute lvjiang-stats --remote --file=queries/dau.sql
wrangler d1 execute lvjiang-stats --remote --file=queries/retention_cohort.sql
```

## 怎么读这些数字

- **样本量小时按周 cohort、看"N 日内回访率"，不看单日留存**——几百活跃用户
  按天分组每格是个位数，全是噪声。`cohort 人数 < 20` 的格子直接忽略。
- **`roll_affix_prob.sql` / `roll_pity_check.sql` 现在直接查 `roll_batch`
  的原始 JSON**（`json_each`），不需要预聚合表——Tier 0（现在，几百用户）
  阶段写入量本就很低（约 DAU × 会话数，见 schema.sql 顶部注释），预聚合
  解决的是配额问题，而配额问题现在不存在。等 raw 表接近存储/查询瓶颈
  （约 2000~5000 DAU）再启用 `rollup.sql` 做每日物化。
- **对外公布任何数字都要注明口径**：只统计同意上报的用户，且默认开启
  被改回 opt-in 弹窗之后，样本可能明显小于总装机量。

## 保留期

- `daily` / `roll_batch` / `install_day_rolls`：90 天，Worker 的
  `scheduled` 钩子每日清理，与 PRIVACY.md 里的承诺一一对应。
- `installs`：`last_day` 超过 180 天未活动即清除。
- `agg_*`：无限期保留——因为不含 `install_id`，已经无法关联到任何安装。
  这条是硬规则，不要为了排查问题往聚合表加 install_id，加了就必须同步
  改保留策略。
