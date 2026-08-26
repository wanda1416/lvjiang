/**
 * 律匠匿名统计 —— Cloudflare Worker
 *
 * 隐私约束（结构性，不是承诺）：
 *   - 整个文件不出现 CF-Connecting-IP / request.cf 的任何读取；
 *   - 所有维度字段落库前经白名单校验，不在名单内的值一律拒绝该条
 *     （不是"存成 other"）；
 *   - 响应恒为 204/400/404/405 且无 body，客户端永远没有东西可解析，
 *     这个通道不可能被用来下发指令。
 *
 * 端点：
 *   POST /v1/report   上报心跳 + 调律批次
 *   POST /v1/forget    删除某个 install_id 的全部记录
 *   其余              404
 *
 * 防滥用分层（详见项目内 PRIVACY.md / 方案文档）：
 *   1. schema 本身（PK + 枚举白名单）是主防线；
 *   2. Cloudflare 控制台配置一条 WAF Rate Limiting 规则按 IP 限流
 *      （不在本文件代码里，那一层根本不读 IP，由 Cloudflare 边缘完成）；
 *   3. 本文件内的请求体积/结构闸门；
 *   4. env.DISABLED=="1" 时的零状态熔断开关。
 */

const MAX_BODY_BYTES = 256 * 1024;
const MAX_BATCHES = 5;
const MAX_EVENTS_PER_BATCH = 2000;
const MAX_ROLLS_PER_INSTALL_PER_DAY = 20; // 批次数上限，非事件条数

// ── 枚举白名单（与客户端 schema 定义严格对齐，改动需双边同步） ──

const RE_UUID_HEX = /^[0-9a-f]{32}$/;
const RE_UUID4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const RE_DATE = /^\d{4}-\d{2}-\d{2}$/;
const RE_VERSION = /^[0-9A-Za-z.\-+]{1,32}$/;
const RE_OS_RELEASE = /^[0-9A-Za-z._-]{1,16}$/;
const RE_ARCH = /^[A-Za-z0-9_]{1,20}$/;

const RUN_ENV = new Set(["desktop", "android"]);
const OS_NAME = new Set(["Windows", "Darwin", "Linux", "other"]);
const PLUGIN = new Set(["yysls", "none"]); // 新增插件必须同步扩充，否则拒收
// weapon_type / affix 的取值随游戏配置增长（武器类型、词条池会随版本更新），
// 服务端没有游戏配置作为真源，没法像 part/food/mode 那样枚举穷尽。退而求其次
// 的格式闸门：真实的武器类型/词条名恒为纯中文短词，不含拉丁字母、数字、
// 标点或路径分隔符——这挡不住"凑巧是纯中文的伪造内容"，但能挡掉最常见的
// PII 形状（路径、邮箱、账号、ADB 序列号等几乎总带 ASCII/数字/符号）。
// 客户端一侧才是真正的枚举白名单（core/telemetry/schema.py + apps/yysls/
// telemetry/vocab.py 对 game_config.yaml 的实时枚举），这里只是纵深防御。

function isStr(v, re, maxLen) {
  return typeof v === "string" && v.length <= (maxLen || 64) && (!re || re.test(v));
}
function isEnum(v, set) {
  return typeof v === "string" && set.has(v);
}
function isInt(v, min, max) {
  return Number.isInteger(v) && (min === undefined || v >= min) && (max === undefined || v <= max);
}
function isFloat(v, min, max) {
  return typeof v === "number" && Number.isFinite(v)
    && (min === undefined || v >= min) && (max === undefined || v <= max);
}

function todayUtc8() {
  return new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10);
}

function jsonResponse(status) {
  return new Response(null, { status, headers: { "Cache-Control": "no-store" } });
}

// ── 心跳校验：不合法直接丢弃整条心跳（不落库），但不影响批次处理 ──

function validateHeartbeat(hb) {
  if (!hb || typeof hb !== "object") return null;
  if (!isStr(hb.install_id, RE_UUID_HEX)) return null;
  if (!isStr(hb.first_seen, RE_DATE)) return null;
  if (!isStr(hb.app_version, RE_VERSION)) return null;
  if (!isEnum(hb.run_env, RUN_ENV)) return null;
  if (!isEnum(hb.os_name, OS_NAME)) return null;
  if (hb.os_release !== undefined && !isStr(hb.os_release, RE_OS_RELEASE)) return null;
  if (!isStr(hb.arch, RE_ARCH)) return null;
  if (!isStr(hb.ui_language, /^[a-z]{2}_[A-Z]{2}$/)) return null;
  if (!isEnum(hb.plugin, PLUGIN)) return null;
  return {
    install_id: hb.install_id,
    first_seen: hb.first_seen,
    app_version: hb.app_version,
    run_env: hb.run_env,
    os_name: hb.os_name,
    os_release: hb.os_release || "unknown",
    arch: hb.arch,
    ui_lang: hb.ui_language,
    plugin: hb.plugin,
  };
}

// ── 事件校验：只看形状与安全性，**不认识调律** ──
//
// 服务端刻意不知道调律有哪些字段。业务枚举的真源是客户端的 game_config.yaml
// （词条池随游戏版本增长），服务端没有也不该有这份真源；把 part/food/affix
// 抄一份到这里的代价是：客户端每加一个字段都得同步改服务端并重新部署，而在
// 同步完成之前，新字段是完全裸奔的。
//
// 改成通用闸门之后，任何未来字段自动受同一套约束保护，PII 纵深防御反而比
// 逐字段枚举更强——原先只有被枚举到的那些字段受保护。业务语义的白名单仍在
// 客户端（schema.py + vocab.py 对 game_config.yaml 的实时枚举），那才是主防线；
// 这里是防绕过客户端直接发请求的纵深防御。
//
// 分析在本地做：拉原始 JSON 下来跑，见 queries/roll_export.sql。

const MAX_EVENT_DEPTH = 4;
const MAX_KEYS_PER_OBJECT = 32;
const MAX_ARRAY_ITEMS = 64;
const MAX_NUMBER_ABS = 1e9;

const RE_EVENT_KEY = /^[a-z][a-z0-9_]{0,31}$/;
// 字符串只允许两种形状，任一不满足即拒绝该条事件：
//   ascii token —— 枚举 key、install_id、日期、版本号、active_rule 等
//   CJK 词条    —— 词条名、武器类型；长度上限与旧版逐字段校验保持一致
// 两者都不含空格、斜杠、@，挡得住路径/邮箱/账号这类几乎总带这些字符的 PII
// 形状。挡不住一个纯中文的伪造短串（例如"角色名叫张三"）——这条限制与旧版
// 相同，已记录在 docs/30-architecture/06-telemetry.md「服务端校验边界」。
const RE_ASCII_TOKEN = /^[0-9A-Za-z_.+\-]{1,128}$/;
const RE_CJK_TERM = /^[一-鿿]{1,16}$/;

function isSafeString(v) {
  return RE_ASCII_TOKEN.test(v) || RE_CJK_TERM.test(v);
}

/** 递归校验任意 JSON 值的形状；不合规返回 false。 */
function isSafeValue(v, depth) {
  if (depth > MAX_EVENT_DEPTH) return false;
  if (v === null || typeof v === "boolean") return true;
  if (typeof v === "number") {
    return Number.isFinite(v) && Math.abs(v) <= MAX_NUMBER_ABS;
  }
  if (typeof v === "string") return isSafeString(v);
  if (Array.isArray(v)) {
    if (v.length > MAX_ARRAY_ITEMS) return false;
    return v.every((item) => isSafeValue(item, depth + 1));
  }
  if (typeof v === "object") {
    const keys = Object.keys(v);
    if (keys.length > MAX_KEYS_PER_OBJECT) return false;
    return keys.every(
      (k) => RE_EVENT_KEY.test(k) && isSafeValue(v[k], depth + 1));
  }
  return false;
}

/**
 * 单条事件的结构性校验。不合法只丢弃这一条，不拖垮整个批次。
 * 只强制两个存储层自身需要的字段：schema（用于事后按类型筛选）与
 * install_id（行的归属键）——这两个不是调律语义，是存储语义。
 */
function sanitizeEvent(e) {
  if (!e || typeof e !== "object" || Array.isArray(e)) return null;
  if (!isStr(e.schema, RE_ASCII_TOKEN, 64)) return null;
  if (!isStr(e.install_id, RE_UUID_HEX)) return null;
  if (!isSafeValue(e, 0)) return null;
  return e;
}

async function upsertHeartbeat(db, hb) {
  const day = todayUtc8();
  const ins = await db.prepare(
    `INSERT INTO daily (day, install_id, first_day, app_version, run_env,
                        os_name, os_release, arch, ui_lang, plugin)
     VALUES (?1, ?2, COALESCE((SELECT first_day FROM installs WHERE install_id = ?2), ?1),
             ?3, ?4, ?5, ?6, ?7, ?8, ?9)
     ON CONFLICT(day, install_id) DO NOTHING`
  ).bind(day, hb.install_id, hb.app_version, hb.run_env, hb.os_name,
         hb.os_release, hb.arch, hb.ui_lang, hb.plugin).run();

  if (ins.meta.changes === 0) return; // 今天已经记过，installs 的"当前值"也不必再写

  await db.prepare(
    `INSERT INTO installs (install_id, first_day, last_day, app_version, run_env,
                           os_name, os_release, arch, ui_lang, plugin)
     VALUES (?1, ?2, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
     ON CONFLICT(install_id) DO UPDATE SET
       last_day = excluded.last_day, app_version = excluded.app_version,
       run_env = excluded.run_env, os_release = excluded.os_release,
       ui_lang = excluded.ui_lang, plugin = excluded.plugin`
  ).bind(hb.install_id, day, hb.app_version, hb.run_env, hb.os_name,
         hb.os_release, hb.arch, hb.ui_lang, hb.plugin).run();
}

async function insertBatch(db, batchId, installId, appVersion, plugin, events) {
  const day = todayUtc8();

  // 单 install 每日批次数上限——超过静默丢弃，不报错（客户端不该因此重试）。
  const countRow = await db.prepare(
    `SELECT COUNT(*) AS n FROM roll_batch WHERE install_id = ?1 AND day = ?2`
  ).bind(installId, day).first();
  if ((countRow && countRow.n) >= MAX_ROLLS_PER_INSTALL_PER_DAY) return;

  const ins = await db.prepare(
    `INSERT OR IGNORE INTO roll_batch
       (batch_id, install_id, day, app_version, plugin, n_events, payload, received_at)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)`
  ).bind(batchId, installId, day, appVersion, plugin, events.length,
         JSON.stringify(events), new Date().toISOString()).run();
  if (ins.meta.changes === 0) return; // 重复批次，天然幂等

  await db.prepare(
    `INSERT INTO install_day_rolls (install_id, day, n_events)
     VALUES (?1, ?2, ?3)
     ON CONFLICT(install_id, day) DO UPDATE SET n_events = n_events + excluded.n_events`
  ).bind(installId, day, events.length).run();
}

async function handleReport(request, env) {
  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) return jsonResponse(400);

  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    return jsonResponse(400);
  }
  if (!body || body.v !== 1) return jsonResponse(400);

  try {
    const hb = validateHeartbeat(body.heartbeat);
    if (hb) await upsertHeartbeat(env.DB, hb);

    const batches = Array.isArray(body.batches) ? body.batches.slice(0, MAX_BATCHES) : [];
    for (const b of batches) {
      if (!b || !isStr(b.batch_id, RE_UUID4) && !isStr(b.batch_id, /^[0-9a-zA-Z_-]{1,64}$/)) continue;
      const rawEvents = Array.isArray(b.events) ? b.events.slice(0, MAX_EVENTS_PER_BATCH) : [];
      const events = rawEvents.map(sanitizeEvent).filter(Boolean);
      if (events.length === 0) continue;
      const installId = events[0].install_id;
      const appVersion = hb ? hb.app_version : "unknown";
      await insertBatch(env.DB, b.batch_id, installId, appVersion, "yysls", events);
    }
  } catch (e) {
    // 任何内部异常（含 D1 配额耗尽）都不能让客户端感知到，也不能诱发重试。
    console.error("report_failed"); // 只打固定错误码，绝不打请求内容
  }
  return jsonResponse(204);
}

async function handleForget(request, env) {
  let body;
  try {
    body = JSON.parse(await request.text());
  } catch {
    return jsonResponse(400);
  }
  const installId = body && body.install_id;
  if (!isStr(installId, RE_UUID_HEX)) return jsonResponse(400);

  try {
    await env.DB.batch([
      env.DB.prepare(`DELETE FROM installs WHERE install_id = ?1`).bind(installId),
      env.DB.prepare(`DELETE FROM daily WHERE install_id = ?1`).bind(installId),
      env.DB.prepare(`DELETE FROM roll_batch WHERE install_id = ?1`).bind(installId),
      env.DB.prepare(`DELETE FROM install_day_rolls WHERE install_id = ?1`).bind(installId),
    ]);
  } catch (e) {
    console.error("forget_failed");
  }
  return jsonResponse(204);
}

async function purgeExpired(env) {
  const cutoff90 = new Date(Date.now() - 90 * 86400 * 1000).toISOString().slice(0, 10);
  const cutoff180 = new Date(Date.now() - 180 * 86400 * 1000).toISOString().slice(0, 10);
  await env.DB.batch([
    env.DB.prepare(`DELETE FROM daily WHERE day < ?1`).bind(cutoff90),
    env.DB.prepare(`DELETE FROM roll_batch WHERE day < ?1`).bind(cutoff90),
    env.DB.prepare(`DELETE FROM install_day_rolls WHERE day < ?1`).bind(cutoff90),
    env.DB.prepare(`DELETE FROM installs WHERE last_day < ?1`).bind(cutoff180),
  ]);
}

export default {
  async fetch(request, env) {
    if (env.DISABLED === "1") return jsonResponse(204); // 一键止血开关

    if (request.method !== "POST") return jsonResponse(405);
    const url = new URL(request.url);
    if (url.pathname === "/v1/report") return handleReport(request, env);
    if (url.pathname === "/v1/forget") return handleForget(request, env);
    return jsonResponse(404);
  },

  async scheduled(_event, env, ctx) {
    ctx.waitUntil(purgeExpired(env));
    // 聚合表（agg_dim/agg_retention/agg_roll/agg_value）的物化写入见
    // queries/rollup.sql —— Tier 0（现在，几百用户）阶段查询期临时展开
    // 即可，不需要每日物化；Tier 1（约 2000~5000 DAU）时再启用。
  },
};
