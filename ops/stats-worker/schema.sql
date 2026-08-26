-- 律匠匿名统计 —— D1 schema
--
-- 选型理由（详见方案讨论）：Cloudflare KV 免费额度 1000 writes/day，
-- 几百活跃用户就会打满且算不出留存；D1 免费额度 100k rows written/day，
-- 配合"一次调律会话存一行 payload"的粒度设计（而非一次 roll 一行），
-- 写入量约等于 DAU × 会话数，可以撑到约 4~5 万 DAU。
--
-- 隐私边界（结构性保证，不是运维承诺）：
--   * 没有任何 IP 列、国家/地区/运营商列，Worker 代码里也不读取
--     CF-Connecting-IP / request.cf；
--   * daily/installs 之外的表全部不含 install_id，可无限期保留；
--   * 所有维度列在写入时经 Worker 校验落在固定枚举/正则内，拒绝任何
--     不在名单里的取值——不是"存成 other"，是直接拒绝入库。

-- ── 通道 A：心跳（DAU/MAU/版本分布/端类型/留存） ──────────────

-- 每个安装一行：维度的"当前值" + 首见日
CREATE TABLE IF NOT EXISTS installs (
  install_id    TEXT PRIMARY KEY,   -- 客户端随机 UUIDv4（无机器指纹）
  first_day     TEXT NOT NULL,      -- 服务端首次见到的日期（不信客户端时钟）
  last_day      TEXT NOT NULL,
  app_version   TEXT NOT NULL,
  run_env       TEXT NOT NULL,      -- desktop | android（自动化目标，非"设备类型"）
  os_name       TEXT NOT NULL,
  os_release    TEXT NOT NULL,      -- 只存大版本号，不透传 build 号
  arch          TEXT NOT NULL,
  ui_lang       TEXT NOT NULL,
  plugin        TEXT NOT NULL
);

-- 每个安装每天一行；PK 即幂等键，同一天重复上报写 0 行
CREATE TABLE IF NOT EXISTS daily (
  day           TEXT NOT NULL,      -- 'YYYY-MM-DD'，按 UTC+8 固定偏移划分
  install_id    TEXT NOT NULL,
  first_day     TEXT NOT NULL,      -- 冗余：留存查询免 JOIN installs，省 rows read
  app_version   TEXT NOT NULL,
  run_env       TEXT NOT NULL,
  os_name       TEXT NOT NULL,
  os_release    TEXT NOT NULL,
  arch          TEXT NOT NULL,
  ui_lang       TEXT NOT NULL,
  plugin        TEXT NOT NULL,
  PRIMARY KEY (day, install_id)
) WITHOUT ROWID;

-- ── 通道 B：调律事件（改进内置调律规则） ─────────────────────

-- 一批事件存一行，payload 整体存 JSON 数组。事件粒度是"一件装备一条"
-- （含初始词条、逐轮产出序列、结束原因），一批默认 50 件。
-- payload 对服务端是不透明的：不做任何业务字段校验，只过结构闸门，
-- 见 src/index.js 的 sanitizeEvent。
-- 这是把写入配额压到最低的关键：写入量 ≈ DAU × 每日会话数，与单次
-- 会话里的 roll 数量无关。
CREATE TABLE IF NOT EXISTS roll_batch (
  batch_id      TEXT PRIMARY KEY,   -- 客户端生成 UUIDv4，天然幂等键
  install_id    TEXT NOT NULL,
  day           TEXT NOT NULL,      -- 服务端校验须落在 ±2 天内
  app_version   TEXT NOT NULL,      -- 解析器版本；事后剔除坏版本数据的唯一抓手
  plugin        TEXT NOT NULL,
  n_events      INTEGER NOT NULL,   -- 本批事件条数，用于异常检测
  payload       TEXT NOT NULL,      -- JSON 数组，每项一条调律事件
  received_at   TEXT NOT NULL       -- 服务端 ISO 时间戳
);
CREATE INDEX IF NOT EXISTS idx_roll_batch_day ON roll_batch (day);
CREATE INDEX IF NOT EXISTS idx_roll_batch_install ON roll_batch (install_id, day);

-- 每 install 每日的调律量摘要（不含词条明细），用于识别异常灌注来源
-- ——量级离群但看不出具体内容，是"能限流"和"不碰内容"之间的折中。
CREATE TABLE IF NOT EXISTS install_day_rolls (
  install_id    TEXT NOT NULL,
  day           TEXT NOT NULL,
  n_events      INTEGER NOT NULL,
  PRIMARY KEY (install_id, day)
) WITHOUT ROWID;

-- ── 聚合表（cron 每日物化，不含 install_id，可无限期保留） ─────
--
-- "无限期保留"与"不含 install_id"是捆绑的硬规则：一旦为了排查往这里
-- 加了 install_id，无限期保留就不再成立，必须同步改保留策略。

CREATE TABLE IF NOT EXISTS agg_dim (
  day   TEXT NOT NULL,
  dim   TEXT NOT NULL,   -- 'app_version' | 'run_env' | 'os_name' | 'plugin' | ...
  value TEXT NOT NULL,
  users INTEGER NOT NULL,
  PRIMARY KEY (day, dim, value)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS agg_retention (
  cohort_week TEXT NOT NULL,   -- 'YYYY-WW'，样本量小时按周 cohort 更稳
  day_n       INTEGER NOT NULL,
  users       INTEGER NOT NULL,
  PRIMARY KEY (cohort_week, day_n)
) WITHOUT ROWID;

-- 调律事件聚合：词条命中分布，回答 P(词条 | 部位, 等级, 材料, 规则, ...)
CREATE TABLE IF NOT EXISTS agg_roll (
  period      TEXT NOT NULL,   -- 'YYYY-MM'，按月不按日——写入量与用户数解耦
  app_major   TEXT NOT NULL,   -- '0.7' 粒度，坏版本剔除用
  part        TEXT NOT NULL,
  weapon_type TEXT NOT NULL DEFAULT '',
  level       INTEGER NOT NULL,
  quality     TEXT NOT NULL DEFAULT '',
  food        TEXT NOT NULL,
  slot        INTEGER NOT NULL,
  mode        TEXT NOT NULL,
  active_rule TEXT NOT NULL,
  affix       TEXT NOT NULL,
  is_transferred INTEGER NOT NULL,  -- 0/1
  n           INTEGER NOT NULL,
  PRIMARY KEY (period, app_major, part, weapon_type, level, quality,
               food, slot, mode, active_rule, affix, is_transferred)
) WITHOUT ROWID;

-- 调律事件聚合：数值分布，回答"这个词条能 roll 多高"
-- sum_cap/sum_cap_sq 让均值与方差可精确重算，不是分桶近似。
CREATE TABLE IF NOT EXISTS agg_value (
  period      TEXT NOT NULL,
  app_major   TEXT NOT NULL,
  part        TEXT NOT NULL,
  level       INTEGER NOT NULL,
  food        TEXT NOT NULL,
  affix       TEXT NOT NULL,
  capb        INTEGER NOT NULL,   -- cap_pct 向下取整到 5% 的分桶
  n           INTEGER NOT NULL,
  sum_cap     REAL NOT NULL,
  sum_cap_sq  REAL NOT NULL,
  min_cap     REAL NOT NULL,
  max_cap     REAL NOT NULL,
  PRIMARY KEY (period, app_major, part, level, food, affix, capb)
) WITHOUT ROWID;
