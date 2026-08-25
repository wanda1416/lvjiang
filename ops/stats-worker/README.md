# 律匠匿名统计服务端（Cloudflare Workers + D1）

对应客户端实现：`src/lvjiang/core/telemetry/` + `src/lvjiang/apps/yysls/telemetry/`。
详细方案见项目 `PRIVACY.md` 与开发过程中的方案讨论（存档于 `.claude/plans/`）。

代码公开是刻意的：这里没有任何秘密（`database_id` 不是凭据），"你可以自己
看服务器存了什么"是这个项目最省力也最有说服力的信任杠杆。

## 部署

```bash
cd ops/stats-worker
wrangler d1 create lvjiang-stats          # 记下返回的 database_id
# 把 database_id 填进 wrangler.toml
wrangler d1 execute lvjiang-stats --remote --file=schema.sql
wrangler deploy
```

本地联调：

```bash
wrangler d1 execute lvjiang-stats --local --file=schema.sql
wrangler dev
# 另一个终端：
LVJIANG_TELEMETRY_URL=http://127.0.0.1:8787/v1/report uv run python -m lvjiang
```

## 已上线

生产地址：`https://lvjiang-stats.wyxj.net`，绑的是 `wyxj.net` 这个已有域名下的
自定义子域名（`wrangler.toml` 里 `custom_domain = true` 的写法——这个语法会
让 Cloudflare 自动创建/管理 DNS 记录，比传统 `routes` 通配符语法省一步手动
建 DNS 记录）。`*.workers.dev` 默认域名已随之停用，客户端 `transport.py` 的
`DEFAULT_TELEMETRY_URL` 已同步指向新地址。

## 建议但未在代码里强制的运维项

1. **Cloudflare 控制台加一条 WAF Rate Limiting 规则**，路径匹配
   `/v1/*`，按 IP 限流（例如 10 分钟 20 次）。这一层完全在 Cloudflare
   边缘完成，Worker 代码里从未读取过 IP，不构成额外的隐私处理。
2. **不要开启 Bot Fight Mode**——它会给非浏览器客户端发挑战，正好把
   合法用户全部挡掉。
3. **必要时用 `wrangler deploy --var DISABLED:1` 一键熔断**，不需要
   重新发布代码。已实测有效：熔断期间请求仍返回 204，但不写入 D1。

## 已知的服务端校验边界

`part`/`food`/`mode`/`quality`/`run_env`/`os_name`/`plugin` 是服务端能穷举的
封闭枚举，不在名单内直接丢弃该条。`affix`/`weapon_type` 做不到——它们的
取值随游戏配置增长（词条池、武器类型会随版本更新），服务端没有
`game_config.yaml` 作为真源，没法像其余字段那样枚举穷尽。退而求其次的
格式闸门是「必须是 1~16 个纯中文字符」（`RE_CJK_TERM`），这挡得住路径、
邮箱、ADB 序列号这类几乎总带 ASCII/数字/符号的 PII 形状，**但挡不住一个
纯中文的伪造字符串**（例如故意填「角色名叫张三」）——已用真实请求验证过
这一点会被存下来。

真正的枚举白名单在客户端一侧（`core/telemetry/schema.py` +
`apps/yysls/telemetry/vocab.py` 对 `game_config.yaml` 的实时枚举），只要
用的是本项目分发的客户端就不会触发这个边界；服务端这道闸门是纵深防御，
挡的是绕过客户端直接发请求的情况，不是第二道完整的枚举校验。

## 文件说明

| 文件 | 用途 |
|---|---|
| `src/index.js` | Worker 主体：`/v1/report` `/v1/forget` + 每日过期清理 |
| `schema.sql` | D1 表结构，含选型理由与隐私边界的完整注释 |
| `wrangler.toml` | 部署配置 |
| `queries/*.sql` | 日常查数用的只读 SQL，见 `queries/README.md` |
