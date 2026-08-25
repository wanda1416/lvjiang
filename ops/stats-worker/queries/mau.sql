SELECT COUNT(DISTINCT install_id) AS mau
FROM daily
WHERE day >= date('now', '-30 day');
