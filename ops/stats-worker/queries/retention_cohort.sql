-- 周 cohort 留存：样本量小时按天分组全是噪声，按周更稳。
-- 建议看"N 日内回访率"而不是"恰好第 7 天活跃"，同理更稳定。
-- cohort 人数 < 20 的格子不要看，噪声占主导。
SELECT strftime('%Y-%W', first_day) AS cohort_week,
       CAST(julianday(day) - julianday(first_day) AS INTEGER) AS day_n,
       COUNT(DISTINCT install_id) AS users
FROM daily
WHERE first_day >= date('now', '-90 day')
GROUP BY cohort_week, day_n
ORDER BY cohort_week, day_n;
