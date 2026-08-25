-- Tier 1（约 2000~5000 DAU，raw 表接近存储/查询瓶颈时启用）：
-- 每日物化调律事件到 agg_roll / agg_value，随后把 roll_batch 保留期
-- 从 90 天缩到 30 天——raw 变成短期纠错窗口，聚合表成为永久资产。
--
-- 用法：加进 Worker 的 scheduled() 钩子每日跑一次，或先手动跑：
--   wrangler d1 execute lvjiang-stats --remote --file=queries/rollup.sql

INSERT INTO agg_roll (period, app_major, part, weapon_type, level, quality,
                      food, slot, mode, active_rule, affix, is_transferred, n)
SELECT
  strftime('%Y-%m', b.day) AS period,
  substr(b.app_version, 1, instr(b.app_version || '.', '.', instr(b.app_version, '.') + 1) - 1) AS app_major,
  json_extract(e.value, '$.part'),
  COALESCE(json_extract(e.value, '$.weapon_type'), ''),
  json_extract(e.value, '$.level'),
  COALESCE(json_extract(e.value, '$.quality'), ''),
  json_extract(e.value, '$.food'),
  json_extract(e.value, '$.slot'),
  json_extract(e.value, '$.mode'),
  json_extract(e.value, '$.active_rule'),
  json_extract(e.value, '$.affix'),
  json_extract(e.value, '$.is_transferred'),
  COUNT(*)
FROM roll_batch b, json_each(b.payload) e
WHERE b.day = date('now', '-1 day')  -- 每日只物化前一天，避免重复累加
GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12
ON CONFLICT(period, app_major, part, weapon_type, level, quality, food, slot,
            mode, active_rule, affix, is_transferred)
DO UPDATE SET n = n + excluded.n;

INSERT INTO agg_value (period, app_major, part, level, food, affix, capb,
                       n, sum_cap, sum_cap_sq, min_cap, max_cap)
SELECT
  strftime('%Y-%m', b.day),
  substr(b.app_version, 1, instr(b.app_version || '.', '.', instr(b.app_version, '.') + 1) - 1),
  json_extract(e.value, '$.part'),
  json_extract(e.value, '$.level'),
  json_extract(e.value, '$.food'),
  json_extract(e.value, '$.affix'),
  CAST(json_extract(e.value, '$.cap_pct') / 5 AS INTEGER) * 5,
  COUNT(*),
  SUM(json_extract(e.value, '$.cap_pct')),
  SUM(json_extract(e.value, '$.cap_pct') * json_extract(e.value, '$.cap_pct')),
  MIN(json_extract(e.value, '$.cap_pct')),
  MAX(json_extract(e.value, '$.cap_pct'))
FROM roll_batch b, json_each(b.payload) e
WHERE b.day = date('now', '-1 day')
GROUP BY 1,2,3,4,5,6,7
ON CONFLICT(period, app_major, part, level, food, affix, capb)
DO UPDATE SET
  n = n + excluded.n,
  sum_cap = sum_cap + excluded.sum_cap,
  sum_cap_sq = sum_cap_sq + excluded.sum_cap_sq,
  min_cap = MIN(min_cap, excluded.min_cap),
  max_cap = MAX(max_cap, excluded.max_cap);
