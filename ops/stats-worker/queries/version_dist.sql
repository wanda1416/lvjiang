-- 最近一天的版本分布（改天数看历史趋势自行调整 day 条件）
SELECT app_version, COUNT(*) AS n
FROM daily
WHERE day = (SELECT MAX(day) FROM daily)
GROUP BY app_version
ORDER BY n DESC;
