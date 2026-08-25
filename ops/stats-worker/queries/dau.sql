-- 用法：wrangler d1 execute lvjiang-stats --remote --file=queries/dau.sql
SELECT day, COUNT(*) AS dau
FROM daily
WHERE day >= date('now', '-30 day')
GROUP BY day
ORDER BY day;
