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
const RE_ACTIVE_RULE = /^(none|[a-z0-9_]+(\+[a-z0-9_]+)*)$/;

const RUN_ENV = new Set(["desktop", "android"]);
const OS_NAME = new Set(["Windows", "Darwin", "Linux", "other"]);
const PLUGIN = new Set(["yysls", "none"]); // 新增插件必须同步扩充，否则拒收
const FOOD = new Set(["none", "gold", "purple", "rainbow"]);
const MODE = new Set(["normal", "force_tune", "tune_full_recycle"]);
const QUALITY = new Set(["blue", "purple", "gold"]);
const PART = new Set(["weapon", "ring", "pendant", "head", "chest", "leg", "wrist"]);
// weapon_type / affix 的取值随游戏配置增长（武器类型、词条池会随版本更新），
// 服务端没有游戏配置作为真源，没法像 part/food/mode 那样枚举穷尽。退而求其次
// 的格式闸门：真实的武器类型/词条名恒为纯中文短词，不含拉丁字母、数字、
// 标点或路径分隔符——这挡不住"凑巧是纯中文的伪造内容"，但能挡掉最常见的
// PII 形状（路径、邮箱、账号、ADB 序列号等几乎总带 ASCII/数字/符号）。
// 客户端一侧才是真正的枚举白名单（core/telemetry/schema.py + apps/yysls/
// telemetry/vocab.py 对 game_config.yaml 的实时枚举），这里只是纵深防御。
const RE_CJK_TERM = /^[一-鿿]{1,16}$/;

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

// ── 调律事件校验：单条不合法只丢弃这一条，不拖垮整个批次 ──

function validateRollEvent(e) {
  if (!e || typeof e !== "object") return null;
  if (e.schema !== "yysls.tuning_roll") return null;
  if (!isStr(e.install_id, RE_UUID_HEX)) return null;
  if (!isStr(e.date, RE_DATE)) return null;
  if (!isEnum(e.part, PART)) return null;
  if (e.weapon_type !== undefined && !isStr(e.weapon_type, RE_CJK_TERM, 16)) return null;
  if (!isInt(e.level, 1, 999)) return null;
  if (e.quality !== undefined && !isEnum(e.quality, QUALITY)) return null;
  if (!isEnum(e.food, FOOD)) return null;
  if (!isInt(e.slot, 1, 5)) return null;
  if (!isInt(e.roll_index, 1, 100000)) return null;
  if (!isInt(e.resets, 0, 100000)) return null;
  if (!isEnum(e.mode, MODE)) return null;
  if (!isStr(e.active_rule, RE_ACTIVE_RULE, 256)) return null;
  if (!isStr(e.affix, RE_CJK_TERM, 16)) return null;
  if (!isFloat(e.cap_pct, 0, 100)) return null;
  if (typeof e.is_transferred !== "boolean") return null;
  if (e.season !== undefined && !isInt(e.season, 0, 9999)) return null;
  if (typeof e.game_config_customized !== "boolean") return null;
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
      const events = rawEvents.map(validateRollEvent).filter(Boolean);
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
