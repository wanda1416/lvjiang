-- 结束原因分布：规则判定得对不对的直接证据。
-- decided_recycle 占比过高 = 规则过严（好装备被回收）；
-- cannot_continue 占比过高 = 材料配置跟不上。
-- 这是按件上报才有的字段，逐轮粒度不记录会话怎么结束。
SELECT
  json_extract(s.value, '$.stop_reason')  AS stop_reason,
  json_extract(s.value, '$.final_rating') AS final_rating,
  COUNT(*) AS sessions
FROM roll_batch b, json_each(b.payload) s
WHERE b.day >= date('now', '-30 day')
GROUP BY stop_reason, final_rating
ORDER BY sessions DESC;
