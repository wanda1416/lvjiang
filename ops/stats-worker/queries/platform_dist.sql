-- 端类型 / 系统分布
SELECT run_env, os_name, COUNT(*) AS n
FROM daily
WHERE day = (SELECT MAX(day) FROM daily)
GROUP BY run_env, os_name
ORDER BY n DESC;
