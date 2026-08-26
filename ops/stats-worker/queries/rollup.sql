-- Tier 1（约 2000~5000 DAU，raw 表接近存储/查询瓶颈时启用）：
-- 每日物化调律事件到 agg_roll / agg_value，随后把 roll_batch 保留期
-- 从 90 天缩到 30 天——raw 变成短期纠错窗口，聚合表成为永久资产。
--
-- 用法：加进 Worker 的 scheduled() 钩子每日跑一次，或先手动跑：
--   wrangler d1 execute lvjiang-stats --remote --file=queries/rollup.sql
--
-- ⚠️ 事件粒度是「一件装备一条」：part/level/mode/active_rule 等在事件外层，
-- 而 affix/cap_pct/food/slot 在 rolls[] 数组的每一项里。所以必须两层
-- json_each：外层拆批次里的事件，内层拆事件里的逐轮产出。早先只有单层，
-- $.affix/$.food/$.slot/$.cap_pct 全部取到 NULL，启用即静默写入垃圾行。
--
-- app_major 取到次级版本号（'0.7.1' → '0.7'）。注意 SQLite 的 instr()
-- 只有两参数形式，没有 from-index 重载——早先写成 instr(x, '.', n) 会让
-- 整条语句直接报错而非退化。这里改用「截断后再找一次」再 rtrim 掉可能的
-- 尾点，'0.7.1'/'0.7'/'unknown' 三种形状都能正确处理。

INSERT INTO agg_roll (period, app_major, part, weapon_type, level, quality,
                      food, slot, mode, active_rule, affix, is_transferred, n)
WITH ev AS (
  SELECT
    strftime('%Y-%m', b.day) AS period,
    rtrim(substr(
      b.app_version || '..', 1,
      instr(b.app_version || '..', '.')
        + instr(substr(b.app_version || '..',
                       instr(b.app_version || '..', '.') + 1), '.') - 1), '.') AS app_major,
    e.value AS ev
  FROM roll_batch b, json_each(b.payload) e
  WHERE b.day = date('now', '-1 day')  -- 每日只物化前一天，避免重复累加
    -- payload 对存储层不透明，可能混有其它类型/版本的事件；字段语义按版本
    -- 变化，混着聚合会算出无意义的数。只物化认识的那一种。
    AND json_extract(e.value, '$.schema') = 'yysls.tuning_session'
    AND json_extract(e.value, '$.version') = 1
)
SELECT
  ev.period,
  ev.app_major,
  json_extract(ev.ev, '$.part'),
  COALESCE(json_extract(ev.ev, '$.weapon_type'), ''),
  json_extract(ev.ev, '$.level'),
  COALESCE(json_extract(ev.ev, '$.quality'), ''),
  json_extract(r.value, '$.food'),
  json_extract(r.value, '$.slot'),
  json_extract(ev.ev, '$.mode'),
  json_extract(ev.ev, '$.active_rule'),
  json_extract(r.value, '$.affix'),
  json_extract(r.value, '$.is_transferred'),
  COUNT(*)
FROM ev, json_each(json_extract(ev.ev, '$.rolls')) r
GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12
ON CONFLICT(period, app_major, part, weapon_type, level, quality, food, slot,
            mode, active_rule, affix, is_transferred)
DO UPDATE SET n = n + excluded.n;

-- cap_pct 是选填：算不出上限时客户端**省略该字段**（0 是合法取值，兜底成 0
-- 会在数值分布低位堆出一根假柱子，见 apps/yysls/telemetry/schemas.py）。
-- 这里必须显式排除 NULL——否则 CAST(NULL/5 AS INTEGER) 让 capb 变 NULL，
-- 撞上 agg_value 的 NOT NULL 主键列，整条 rollup 失败。
INSERT INTO agg_value (period, app_major, part, level, food, affix, capb,
                       n, sum_cap, sum_cap_sq, min_cap, max_cap)
WITH ev AS (
  SELECT
    strftime('%Y-%m', b.day) AS period,
    rtrim(substr(
      b.app_version || '..', 1,
      instr(b.app_version || '..', '.')
        + instr(substr(b.app_version || '..',
                       instr(b.app_version || '..', '.') + 1), '.') - 1), '.') AS app_major,
    e.value AS ev
  FROM roll_batch b, json_each(b.payload) e
  WHERE b.day = date('now', '-1 day')
    AND json_extract(e.value, '$.schema') = 'yysls.tuning_session'
    AND json_extract(e.value, '$.version') = 1
)
SELECT
  ev.period,
  ev.app_major,
  json_extract(ev.ev, '$.part'),
  json_extract(ev.ev, '$.level'),
  json_extract(r.value, '$.food'),
  json_extract(r.value, '$.affix'),
  CAST(json_extract(r.value, '$.cap_pct') / 5 AS INTEGER) * 5,
  COUNT(*),
  SUM(json_extract(r.value, '$.cap_pct')),
  SUM(json_extract(r.value, '$.cap_pct') * json_extract(r.value, '$.cap_pct')),
  MIN(json_extract(r.value, '$.cap_pct')),
  MAX(json_extract(r.value, '$.cap_pct'))
FROM ev, json_each(json_extract(ev.ev, '$.rolls')) r
WHERE json_extract(r.value, '$.cap_pct') IS NOT NULL
GROUP BY 1,2,3,4,5,6,7
ON CONFLICT(period, app_major, part, level, food, affix, capb)
DO UPDATE SET
  n = n + excluded.n,
  sum_cap = sum_cap + excluded.sum_cap,
  sum_cap_sq = sum_cap_sq + excluded.sum_cap_sq,
  min_cap = MIN(min_cap, excluded.min_cap),
  max_cap = MAX(max_cap, excluded.max_cap);
