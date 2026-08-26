/**
 * Worker 行为测试——零依赖，用 Node 内置 test runner 跑：
 *   node --test ops/stats-worker/test/
 *
 * 伪造 D1 的 prepare/bind/run/first/batch 接口，只断言「传给 SQL 的参数」
 * 和「返回的状态码」。不接真 sqlite：这里要验的是 Worker 的取值与分支逻辑
 * （谁的 install_id、哪个版本号、什么时候 503），不是 SQL 本身。
 * SQL 那半边由 queries/ 下的语句直接对 sqlite 跑验证。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import worker from "../src/index.js";

const INSTALL_A = "a".repeat(32);
const INSTALL_B = "b".repeat(32);

function fakeDB({ throwOn = null, batchCount = 0 } = {}) {
  const calls = [];
  const stmt = (sql) => ({
    bind(...args) {
      return {
        async run() {
          calls.push({ sql, args });
          if (throwOn && sql.includes(throwOn)) throw new Error("d1 boom");
          return { meta: { changes: 1 } };
        },
        async first() {
          calls.push({ sql, args });
          if (throwOn && sql.includes(throwOn)) throw new Error("d1 boom");
          return { n: batchCount };
        },
      };
    },
  });
  return {
    calls,
    db: { prepare: stmt, async batch(list) { return list; } },
  };
}

const heartbeat = (over = {}) => ({
  install_id: INSTALL_A, first_seen: "2026-01-01", day: "2026-08-26",
  app_version: "0.7.0", run_env: "desktop", os_name: "Linux",
  os_release: "6", arch: "x86_64", ui_language: "zh_CN", plugin: "yysls",
  ...over,
});

const event = (over = {}) => ({
  schema: "yysls.tuning_session", version: 1, install_id: INSTALL_A,
  date: "2026-08-26", part: "武器", level: 80, mode: "normal",
  active_rule: "none", game_config_customized: false,
  initial_affixes: [], rolls: [], stop_reason: "completed",
  total_rounds: 0, resets: 0, ...over,
});

function post(body) {
  return new Request("https://x/v1/report", {
    method: "POST", body: JSON.stringify(body),
  });
}

const envelope = (over = {}) => ({
  v: 1, app_version: "0.7.1", heartbeat: null,
  batches: [{ batch_id: "batch-1", events: [event()] }], ...over,
});

/** 取 roll_batch 插入语句绑定的 app_version（第 4 个参数）。 */
function batchInsert(calls) {
  return calls.find((c) => c.sql.includes("INSERT OR IGNORE INTO roll_batch"));
}

// ── ② app_version 来源 ────────────────────────────────────────

test("信封带 app_version 时，无心跳的批次也能归因到版本", async () => {
  const { db, calls } = fakeDB();
  const res = await worker.fetch(post(envelope()), { DB: db });
  assert.equal(res.status, 204);
  assert.equal(batchInsert(calls).args[3], "0.7.1");
});

test("信封缺 app_version 时回退到心跳", async () => {
  const { db, calls } = fakeDB();
  const body = envelope({ heartbeat: heartbeat() });
  delete body.app_version;
  await worker.fetch(post(body), { DB: db });
  assert.equal(batchInsert(calls).args[3], "0.7.0");
});

test("信封与心跳都没有版本号才记 unknown", async () => {
  const { db, calls } = fakeDB();
  const body = envelope();
  delete body.app_version;
  await worker.fetch(post(body), { DB: db });
  assert.equal(batchInsert(calls).args[3], "unknown");
});

test("畸形的信封版本号被忽略，不写进库", async () => {
  const { db, calls } = fakeDB();
  await worker.fetch(post(envelope({ app_version: "../../etc/passwd" })), { DB: db });
  assert.equal(batchInsert(calls).args[3], "unknown");
});

// ── ③ install_id 一致性 ───────────────────────────────────────

test("同一批次混入不同 install_id 时整批拒收", async () => {
  const { db, calls } = fakeDB();
  const body = envelope({
    batches: [{ batch_id: "batch-1",
                events: [event(), event({ install_id: INSTALL_B })] }],
  });
  const res = await worker.fetch(post(body), { DB: db });
  assert.equal(res.status, 204);          // 对客户端仍然静默
  assert.equal(batchInsert(calls), undefined);  // 但什么都没落库
});

test("install_id 一致的批次正常落库", async () => {
  const { db, calls } = fakeDB();
  const body = envelope({
    batches: [{ batch_id: "batch-1", events: [event(), event()] }],
  });
  await worker.fetch(post(body), { DB: db });
  assert.equal(batchInsert(calls).args[1], INSTALL_A);
  assert.equal(batchInsert(calls).args[5], 2);  // n_events
});

// ── ⑧ 事件类型 / 版本白名单 ───────────────────────────────────

test("未知事件类型被丢弃", async () => {
  const { db, calls } = fakeDB();
  const body = envelope({
    batches: [{ batch_id: "b", events: [event({ schema: "evil.event" })] }],
  });
  await worker.fetch(post(body), { DB: db });
  assert.equal(batchInsert(calls), undefined);
});

test("已知类型但未知 schema 版本被丢弃", async () => {
  const { db, calls } = fakeDB();
  const body = envelope({
    batches: [{ batch_id: "b", events: [event({ version: 2 })] }],
  });
  await worker.fetch(post(body), { DB: db });
  assert.equal(batchInsert(calls), undefined);
});

test("缺 version 字段被丢弃", async () => {
  const { db, calls } = fakeDB();
  const ev = event();
  delete ev.version;
  await worker.fetch(post(envelope({ batches: [{ batch_id: "b", events: [ev] }] })),
                     { DB: db });
  assert.equal(batchInsert(calls), undefined);
});

test("混合批次里只丢坏事件，好事件照常落库", async () => {
  const { db, calls } = fakeDB();
  const body = envelope({
    batches: [{ batch_id: "b",
                events: [event(), event({ schema: "evil.event" }), event()] }],
  });
  await worker.fetch(post(body), { DB: db });
  assert.equal(batchInsert(calls).args[5], 2);
});

// ── ④ 状态码语义 ──────────────────────────────────────────────

test("D1 异常返回 503，让客户端保留缓冲重试", async () => {
  const { db } = fakeDB({ throwOn: "INSERT OR IGNORE INTO roll_batch" });
  const res = await worker.fetch(post(envelope()), { DB: db });
  assert.equal(res.status, 503);
});

test("单 install 日批次上限是有意静默丢弃，仍回 204", async () => {
  const { db, calls } = fakeDB({ batchCount: 999 });
  const res = await worker.fetch(post(envelope()), { DB: db });
  assert.equal(res.status, 204);
  assert.equal(batchInsert(calls), undefined);
});

test("熔断开关回 204 且完全不碰数据库", async () => {
  const { db, calls } = fakeDB();
  const res = await worker.fetch(post(envelope()), { DB: db, DISABLED: "1" });
  assert.equal(res.status, 204);
  assert.equal(calls.length, 0);
});

test("forget 删除失败返回 503——不能让用户以为已删除", async () => {
  const db = { prepare: () => ({ bind: () => ({}) }),
               async batch() { throw new Error("d1 boom"); } };
  const req = new Request("https://x/v1/forget", {
    method: "POST", body: JSON.stringify({ install_id: INSTALL_A }),
  });
  assert.equal((await worker.fetch(req, { DB: db })).status, 503);
});
