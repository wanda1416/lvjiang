-- 手动删除某个 install_id 的全部记录（正常应走 /v1/forget 接口，
-- 这份 SQL 仅供接口不可用时的应急操作）：
-- wrangler d1 execute lvjiang-stats --remote --file=queries/forget.sql \
--   --binding install_id=<要删除的 install_id>
-- wrangler 不支持命令行传参替换，请手动把下面的 ?1 替换成实际 ID 再执行。
DELETE FROM installs WHERE install_id = '?1';
DELETE FROM daily WHERE install_id = '?1';
DELETE FROM roll_batch WHERE install_id = '?1';
DELETE FROM install_day_rolls WHERE install_id = '?1';
