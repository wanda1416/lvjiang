-- P(词条 | 部位, 材料)：在 roll_batch 的 JSON payload 上两层展开——
-- 外层 json_each 拆出每个会话（一件装备），内层再拆出该会话的逐轮产出。
-- 事件粒度是"一件装备一条"，词条序列在 $.rolls 数组里。
--
-- 按需加 WHERE app_version >= '0.7.0' 剔除旧版本解析器产出的数据。
-- 想做分层/置信区间/条件概率，用 roll_export.sql 拉到本地跑，SQL 里写不动。
SELECT
  json_extract(s.value, '$.part')  AS part,
  json_extract(r.value, '$.food')  AS food,
  json_extract(r.value, '$.affix') AS affix,
  COUNT(*) AS n
FROM roll_batch b,
     json_each(b.payload) s,
     json_each(json_extract(s.value, '$.rolls')) r
WHERE b.day >= date('now', '-30 day')
GROUP BY part, food, affix
ORDER BY part, food, n DESC;
