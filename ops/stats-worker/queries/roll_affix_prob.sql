-- P(词条 | 部位, 等级, 材料)：Tier 0 阶段直接在 roll_batch 的 JSON payload
-- 上展开查询，不需要预聚合（写入量本就很低，见方案文档）。
-- 按需加 WHERE app_version >= '0.7.0' 剔除旧版本解析器产出的数据。
SELECT
  json_extract(e.value, '$.part')  AS part,
  json_extract(e.value, '$.food')  AS food,
  json_extract(e.value, '$.affix') AS affix,
  COUNT(*) AS n
FROM roll_batch b, json_each(b.payload) e
WHERE b.day >= date('now', '-30 day')
GROUP BY part, food, affix
ORDER BY part, food, n DESC;
