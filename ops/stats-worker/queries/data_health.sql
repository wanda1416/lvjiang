-- 每 install 每日调律量分位数：找异常灌注来源（量级离群但内容不可见）。
SELECT install_id, day, n_events
FROM install_day_rolls
WHERE day >= date('now', '-7 day')
ORDER BY n_events DESC
LIMIT 50;
