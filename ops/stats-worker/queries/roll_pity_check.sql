-- 保底/软保底检测：按 roll_index 分桶看目标词条命中率是否随次数上升。
-- 把 '$YOUR_TARGET_AFFIX' 换成实际关心的词条名再跑。
SELECT
  CASE
    WHEN json_extract(e.value, '$.roll_index') <= 1 THEN '1'
    WHEN json_extract(e.value, '$.roll_index') <= 5 THEN '2-5'
    WHEN json_extract(e.value, '$.roll_index') <= 20 THEN '6-20'
    WHEN json_extract(e.value, '$.roll_index') <= 50 THEN '21-50'
    ELSE '51+'
  END AS roll_bucket,
  COUNT(*) AS n,
  SUM(CASE WHEN json_extract(e.value, '$.affix') = '$YOUR_TARGET_AFFIX' THEN 1 ELSE 0 END) AS hits
FROM roll_batch b, json_each(b.payload) e
WHERE b.day >= date('now', '-30 day')
GROUP BY roll_bucket
ORDER BY roll_bucket;
