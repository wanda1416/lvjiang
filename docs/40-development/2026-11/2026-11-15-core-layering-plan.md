# Dev Log: core 根目录分层方案（评审稿）

> 日期：2026-11-15
> 涉及模块：`src/lvjiang/core/`（36 个根模块，6236 行）
> 关键词：目录分层、模块归属、import 迁移、评审稿

> 本文是**方案**，不是已完成的事实。落地前需要确认包名与批次；落地后应把结论
> 合并进 `docs/30-architecture/30-overview/06-session-data-map.md` 的相关表格。

---

## 1. 为什么现在提

`core/` 下已经有 `config/` `sync/` `profile/` `account/` `batch/` `android/`
`layout/` `scene/` `ondevice/` 等 20 个子包，但**根目录仍平铺着 36 个模块 6236 行**，
里面既有被所有层依赖的原语（`paths.py` 56 行、`json_files.py` 15 行、
`fs_util.py` 257 行），也有完整业务域（`task_cli.py` 824 行、`daily_history.py`
537 行、`user_config.py` 476 行）。同一层级混放导致两个具体后果：

- 新人不知道 `user_config.py`（用户资料）和 `config/users.py`（会话名册）不是一回事；
- 想给「历史」找代码时，`daily_history.py`（根）与 `history_transfer.py`（根）
  和 `sync/`（子包）在视觉上毫无关系。

本轮已先拆了五个最痛的子系统（profile 仓储、config 合并、快照同步、参考图库、
账号凭据与传输、批量编排），`core/` 的其它部分尚未动。

## 2. 判定规则

一条模块进不进子包，只看两条，两条都满足才搬：

1. **同域**：同一业务域有 ≥2 个模块，且它们互相引用或被同一批消费者使用；
2. **边界明确**：搬走后包内不需要反向 import 包外的业务模块（原语除外）。

据此，明确**留在根目录**的两类：

| 类别 | 模块 | 理由 |
|---|---|---|
| 原语（任何层都可依赖） | `paths` `json_files` `fs_util` `timing` `versioning` `version_info` `platforms` `access` `app_events` `plugin_runtime` `log_files` `crash_handler` | 被 ≥20 个模块引用，搬动只增加 import 长度，不增加任何信息 |
| 应用外壳（唯一入口） | `app_controller` `task_cli` `param_require` `task_params` `offline_bundle` | 它们是「命令行 / 窗口 / 打包」的入口与参数桥，彼此无语义共性，凑成子包反而掩盖了「应用从哪进来」 |

## 3. 目标目录（提案）

| 批次 | 新子包 | 迁入模块 | 迁移后根目录模块数 |
|---|---|---|---|
| B1 | `core/history/` | `daily_history.py`(537)、`history_transfer.py`(257) | 36 → 34 |
| B2 | `core/users/` | `user_config.py`(476)、`user_notes.py`(215)、`user_avatars.py`(69) | 34 → 31 |
| B3 | `core/updates/` | `update.py`(198)、`update_bundle.py`(210)、`announcement.py`(323)、`preset_sources.py`(109)、`system_preset.py`(152) | 31 → 26 |
| B4 | `core/qrcode/` + `core/about/` | 二维码四件套（`qr_image`/`qr_cache`/`group_qrcode`/`support_qrcode`）→ `core/qrcode/{image,cache,group,support}.py`；`about`/`public_links` → `core/about/{content,links}.py` | 26 → 18 |

四批之后根目录只剩 18 个模块：14 个原语 + 4 个外壳，每屏能看完。
`core/history/` 的命名与 `core/profile/history_query.py`、`ProfileDB` 的账本不冲突：
前者是**任务/批量历史的持久化**，后者是 profile 数值账本。

## 4. 每批的迁移方式（四批完全同构）

1. 建包（`__init__.py` 只放包说明，不放 re-export）；
2. `git mv` 逐模块进包，**同一次提交里改完全仓 import**（不留兼容 shim：
   `AGENTS.md` 2.7 不默认保留兼容，这些模块都是内部 API）；
3. 门禁：
   - `python -m ruff check src tests scripts`
   - `python -m mypy`
   - 逐模块 import smoke（本轮已证明它能抓到 mypy 抓不到的断链：某模块被
     `from .x import NAME` 引用了不存在的名字）
   - 该域相关测试 + 依赖方测试（如 B1 需跑 `tests/core/test_daily_history.py`、
     `tests/core/test_history_transfer.py`、`tests/ui/test_daily_history_dialog.py`、
     `tests/core/ondevice`、`tests/ui/mobile`）
4. 更新架构文档里写到旧路径的地方（`06-session-data-map.md` 的咽喉模块列、
   `31-models/*` 的模块表）。

每批一次提交，信息模板：`refactor(core): <域> 模块收进 core/<包>`，
说明「搬了哪些、为什么留原语在根目录、验证了哪些」。

## 5. 不在本方案内

- **不动原语与外壳**（见 §2 表格），也不给它们再造一层 `core/primitives/`：
  多一层目录只让 `from ...core.paths` 变成 `from ...core.primitives.paths`，
  几十处 import 变长而无任何语义增益；
- **不合并任何模块**（例如把 `qr_cache`/`qr_image`/`group_qrcode` 并成一个文件）：
  它们是独立落点，合并会把缓存策略与图片编码绑在一起；
- **不动 `offline_bundle.py`**：它同时是打包入口与安装逻辑，2026-11 的
  `config/session` 分层文档以它为咽喉，搬动收益低；
- 不做 `.gitattributes` 的 `*.py text eol=lf` 全局约定：仓库里仍有 CRLF 的
  `.py` 文件，加规则会让它们全部显示为已修改。真要统一，需要一笔专门的
  「行尾归一」提交，与本方案无关。

## 6. 风险与代价

| 风险 | 缓解 |
|---|---|
| 改 import 时漏改（尤其是字符串形式的动态导入与测试里的 `monkeypatch.setattr("lvjiang.core.xxx.yyy")` 路径串） | 全仓搜索模块名字面量，不只依赖 ruff；import smoke 兜底 |
| 某些模块被 `docs/` 或 `.wf` 脚本按路径引用 | 落批前先 `grep` 模块名 |
| 四批串行时间成本 | 每批独立可回退；B1/B2 价值最高（历史与用户是最高频定位目标），B3/B4 可延后 |
