-- 保底/软保底粗筛：按轮次序号分桶看目标词条命中率是否随次数上升。
-- 轮次序号 = $.rolls 数组下标 +1（本件第几轮，跨重置连续累加）。
-- 把 '$YOUR_TARGET_AFFIX' 换成实际关心的词条名再跑。
--
-- ⚠️ 这条查询**不分层**，只能当粗筛，不能当结论：高轮次桶只包含"前面都没中"
-- 的会话，它们的部位/材料构成与低桶不同，读到的差异可能全部来自样本构成。
-- 分层版本在 scripts/analyze_telemetry_rolls.py 第 3 节，见
-- docs/10-game/20-affix-analysis/README.md 偏差 5。
SELECT
  CASE
    WHEN r.key + 1 <= 1  THEN '1'
    WHEN r.key + 1 <= 5  THEN '2-5'
    WHEN r.key + 1 <= 20 THEN '6-20'
    WHEN r.key + 1 <= 50 THEN '21-50'
    ELSE '51+'
  END AS roll_bucket,
  COUNT(*) AS n,
  SUM(CASE WHEN json_extract(r.value, '$.affix') = '$YOUR_TARGET_AFFIX'
           THEN 1 ELSE 0 END) AS hits
FROM roll_batch b,
     json_each(b.payload) s,
     json_each(json_extract(s.value, '$.rolls')) r
WHERE b.day >= date('now', '-30 day')
GROUP BY roll_bucket
ORDER BY roll_bucket;
