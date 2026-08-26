-- 把原始调律批次导出到本地做离线分析（配合 --json 重定向到文件）。
--
--   wrangler d1 execute lvjiang-stats --remote --json \
--     --file=ops/stats-worker/queries/roll_export.sql > rolls.json
--   python scripts/analyze_telemetry_rolls.py rolls.json -o report.md
--
-- 与同目录其余查询的分工：那些是"在服务端算完，只取结论"，适合一周看
-- 一两次的运营数字；这条是"把原始事件搬到本地慢慢挖"，适合研究词条分布
-- 规律——分层、置信区间、重抽样这类分析在 SQL 里写会很难读，也很难复核。
--
-- 导出的是含 install_id 的原始数据，落到本地就等同于一份可关联的样本：
-- 只放在本机分析目录，不要提交进仓库，也不要随 issue 附件外发。
-- 方法论与偏差清单见 docs/10-game/20-affix-analysis/README.md。
--
-- 时间窗按需改；roll_batch 本身只保留 90 天（见 schema.sql 的清理钩子）。
SELECT install_id, day, app_version, plugin, n_events, payload
FROM roll_batch
WHERE day >= date('now', '-30 day')
ORDER BY day;
