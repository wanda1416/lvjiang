# 律匠发布流程 — AI Agent 操作手册

本文档描述律匠（lvjiang）项目的标准发布流程。AI Agent 在执行 `/release` 或用户要求发布新版本时，应严格按以下步骤操作。

---

## 前置条件

- 所有功能代码已提交并推送到 master 分支
- CI 全量通过（ruff + mypy + pytest）
- 当前工作区无未提交的变更（除待发布的版本号变更外）

---

## 标准发布流程

默认使用 GitHub Actions 发布。功能与发布准备提交合并进 `master` 后，只需在
`master` 当前发布提交上创建并推送语义化版本标签；`.github/workflows/release.yml`
会在隔离的 Windows runner 中完成校验、测试、打包和 GitHub Release 发布。

### 第一步：对比功能差异

对比当前 master 与上一个发布版本之间的 commit 差异，梳理新增功能、修复、重构等内容。

```powershell
# 查看上一个发布版本的 tag 或 commit
git log --oneline docs/50-releases/v*.md | Select-Object -First 5

# 对比上一个版本到当前的所有变更（以 0.1.1 为例）
git log 0.1.1..HEAD --oneline
```

### 第二步：编写发布声明

在 `docs/50-releases/` 目录下创建新版本发布文档（如 `v0.1.2.md`），格式参考已有发布文档。
文档首个一级标题必须以 `vX.Y.Z` 开头；自动发布会直接使用该行作为 GitHub
Release 标题，确保发布页面与历史版本维持一致。

**必须包含的章节：**
- ⚠️ 不兼容变动与升级指引（**放在最前面**；本版本无则明确写「本版本无不兼容变动」）
- ✨ 新增功能
- 🔧 修复
- 📦 重构
- 📚 文档
- 📝 变更统计（commit 数 + 测试用例数）
- ⚠️ 免责声明
- 📄 许可证

**⚠️ 不兼容变动章节（最重要，优先级高于其余所有章节）：**

用户升级后第一时间需要知道的是「我原来的东西还能不能用、要不要动手改」。这一节写不全，用户就会在升级后遇到无法自行解释的异常，且往往归因为「新版本坏了」。

**每条必须写清三件事**，缺一不可：

1. **现象** — 用户会观察到什么（而不是内部实现改了什么）
2. **原因** — 一句话说明为什么变
3. **如何调整** — 用户要做的具体操作；若无需操作要明确写「无需操作，自动迁移」

**必须逐项排查的不兼容来源**（对照 `git diff <上一 tag>..HEAD` 逐类确认）：

| 类别 | 排查方式 | 典型后果 |
|------|---------|---------|
| 默认值变更 | `git log -p -- src/lvjiang/core/config/models.py` | 存量用户保留旧值、与新文档不符 |
| 配置结构 / 存储位置迁移 | 搜索本周期新增的迁移函数、`session.json` 节点变化 | 旧配置读不到，设置像是「丢了」 |
| 系统配置语义变更 | `git diff <tag>..HEAD -- config/system` | 同样的输入产出不同结果 |
| 校验变严 | 新增的 raise / 校验函数 | 原本能跑的用户内容被拒 |
| DSL / `.wf` 语法与内置函数 | `git diff <tag>..HEAD -- src/lvjiang/workflows` | 用户自写脚本报错 |
| 键名 / 场景 / 区域重命名 | 布局与场景 yaml 的 key 改名 | 引用旧名的脚本或本地覆盖失效 |
| 快捷键 / 交互默认值 | `HotkeyConfig` 等默认值 | 文档说 A、实际还是 B |
| 数据落盘格式 | profile / 遥测 / 报告字段 | 历史数据读不出或被服务端丢弃 |

**注意 config 分层的三个坑**（这类问题在发布说明里不写清，用户永远排查不出来）：

- `config/local` **无条件**优先于 system。用户自己改过 / 重画过的那份配置，系统修正**到不了他**。凡是修了系统配置的，都要提示「若你改过这项，需要手动同步或删除本地覆盖」。
- `config/remote` 只在 `content_version` **严格大于** system 时才顶替。改了系统内容却不 bump 版本号，已下发过的用户拿不到修正。
- **发布流程严禁自动或批量提升 `content_version`。** 应用发版、修改了 `config/system`、递增 Android `versionCode`，都不代表要创建新的远端下发代次；三者之间不存在自动联动。发布 Agent 只能检查并报告当前版本，不能因为“文件有改动”就修改该字段。只有开发者明确决定发布一代远端配置，并在编辑器中主动点击「提升至 vN / 提升」时，才允许改变 `content_version`。
- **`content_version` 不再自动 +1**（0.9 起）。开发模式的普通保存只**保留**原版本号，提升必须在编辑器里显式操作：场景编辑器点「提升至 vN」再保存，调律规则点 key 行的「提升」按钮。这条以前是自动的，现在纯人工，本轮改过、要走在线下发的配置**逐个确认版本号**。
- 开发模式下若线上版本正顶替某个文件，普通保存写进 system 也不会生效（版本号没超过线上那份）。编辑器保存后会明确提示「尚未生效」，看到就去点提升。
- Android 的 `versionCode` 是配置解压 stamp，不递增则设备上仍是旧配置。

**🔧 修复章节的收录原则：**

只收录「上一版本已存在、本版本修复」的问题。如果某个 bug 是本版本引入并在同一版本内修复的（即用户从未在上一版本中遇到过），则**不写入发布声明**——它属于开发过程的内部修正，不是用户可感知的变更。

判断方法：追溯 fix commit 对应的引入 commit，如果引入 commit 在上一个版本 tag **之后**，则该 fix 不收录；如果引入 commit 在上一个版本 tag **之前或就是**上一个版本，则收录。

> 注意：这条排除原则**只适用于 🔧 修复章节**。不兼容变动一节按「相对上一发布版本的净差异」写——本周期内引入又调整过的破坏性变更，只要最终状态与上一版本不一致，仍要写。

### 第三步：更新版本号

**需要更新的文件（不可遗漏）：**

| 文件 | 字段 | 说明 |
|------|------|------|
| `pyproject.toml` | `version = "X.Y.Z"` | Python 包版本号 |
| `android/app/build.gradle.kts` | `versionName = "X.Y.Z"` | Android APK 版本名 |
| `android/app/build.gradle.kts` | `versionCode = N` | Android 内部版本号（递增整数，改了 config/system 或布局文件必须 +1） |
| `src/lvjiang/_version.py` | `__version__ = "X.Y.Z"` | 手动改成待发布版本号，与 `pyproject.toml` 一致 |

> **关于 `_version.py`：** 这个文件手动维护并提交。约定是**进入新版本开发时就把三处
> 版本号一起改成待发布版本**（`chore: start X.Y.Z development`），这样开发环境、源码
> 安装和 Android 侧读到的都是真实版本，而不是一个占位值。打包时
> `packaging/inject_version.py` 仍会以 `pyproject.toml` 为准**二次覆写**一次，作为
> 手改遗漏的兜底；两边不一致会打印警告，并以 `pyproject.toml` 为准。

### 第四步：提交并合并发布准备

在打标签之前提交版本号与发布说明，并确保该提交已进入远端 `master`：

```powershell
git add pyproject.toml uv.lock android/app/build.gradle.kts src/lvjiang/_version.py docs/50-releases/
git commit -m "chore(release): prepare vX.Y.Z"
git push
```

如果版本号在开发之初就已经改好，这一步 `_version.py` 不会有变更，属正常情况。

### 第五步：推送标签，自动打包发布（推荐）

确保本地位于已经推送的 `master` 发布提交，然后执行：

```powershell
git tag X.Y.Z
git push origin X.Y.Z
```

Release 工作流会依次完成：

1. 校验标签严格为 `X.Y.Z`，并与 `pyproject.toml` 版本一致；
2. 确认 `docs/50-releases/vX.Y.Z.md` 存在、一级标题以 `vX.Y.Z` 开头，且标签提交属于远端 `master`；
3. 检查配置 `content_version` 完整性，运行 Ruff、mypy 与全量 pytest；
4. 执行 `packaging\\package.bat`，并强制检查 ZIP 和 Inno Setup 安装器均已生成；
5. 生成 `SHA256SUMS.txt`，保存 Actions artifact；
6. 创建 GitHub Release，使用版本发布文档的一级标题和正文，并上传三个产物。

任何一步失败都不会发布 GitHub Release。临时故障可直接重新运行该 workflow；
需要修改代码时，只能在 Release 尚未发布的前提下删除失败标签，修复并合并后再
重新创建。已经发布的标签禁止移动，后续修复应递增补丁版本。

### 第六步：本地打包（仅用于排障）

运行打包脚本，生成 Windows 发布包：

```powershell
packaging\package.bat
```

打包产物：
- `dist/lvjiang/lvjiang.exe` — 可执行文件
- `dist/lvjiang-vX.Y.Z-win64.zip` — 发布压缩包（便携版）
- `dist/lvjiang-vX.Y.Z-win64-setup.exe` — Windows 安装包（推荐）

打包脚本会自动：
1. 从 `pyproject.toml` 读取版本号注入到 `src/lvjiang/_version.py`
2. 调用 PyInstaller 构建
3. 复制配置、ADB、scrcpy 等运行时依赖
4. 压缩为 zip
5. 调用 Inno Setup 生成安装包（需安装 [Inno Setup 6](https://jrsoftware.org/isdl.php)）

> **注意：** 如果未安装 Inno Setup，本地脚本会跳过安装器构建；GitHub Release
> 工作流会把安装器缺失视为失败。打包会按 `pyproject.toml` 覆写
> `src/lvjiang/_version.py`——版本号已经改对时这是空操作；如果产生了 diff，说明
> 之前漏改，应当把这个变更一并提交。

---

## 版本号规范

遵循语义化版本（Semantic Versioning）：`MAJOR.MINOR.PATCH`

- **MAJOR**：不兼容的 API 变更
- **MINOR**：向后兼容的功能新增
- **PATCH**：向后兼容的问题修复

---

## 检查清单

发布前逐项确认：

- [ ] `pyproject.toml` 版本号已更新
- [ ] `src/lvjiang/_version.py` 版本号已更新（通常在进入本版本开发时就已改好）
- [ ] `android/app/build.gradle.kts` versionName 已更新
- [ ] `android/app/build.gradle.kts` versionCode 已递增（如有 config/布局变更）
- [ ] `docs/50-releases/vX.Y.Z.md` 发布文档已编写
- [ ] **不兼容变动已逐项排查**（对照上表八类），每条都写了「现象 / 原因 / 如何调整」；确认无不兼容时也已明确写出
- [ ] 已确认本次是否有开发者**明确安排的远端配置下发**：没有则保持所有 `content_version` 原值不动；有则仅核对开发者已在编辑器里显式提升的目标文件。发布 Agent 不得自行提升；会被 `config/local` 遮蔽的内容已在发布说明里提示用户
- [ ] `python scripts/add_content_version.py --check` 通过（存量/新增文件的版本字段齐全）
- [ ] `uv.lock` 变更已纳入提交（如有）
- [ ] 所有变更已提交并推送
- [ ] 发布提交已进入远端 `master`
- [ ] `X.Y.Z` 标签与 `pyproject.toml` 一致并已推送
- [ ] GitHub Release 工作流中的配置检查、Ruff、mypy 和 pytest 全部通过
- [ ] 云端 `packaging/package.bat` 打包成功，版本注入校验通过
- [ ] GitHub Release 已发布，ZIP、安装器和 `SHA256SUMS.txt` 已上传

---

## 常见错误

1. **遗漏 Android 版本号**：`android/app/build.gradle.kts` 的 versionName 和 versionCode 必须同步更新
2. **`_version.py` 与 `pyproject.toml` 不一致**：三处版本号应在进入新版本开发时一起改；
   打包脚本的二次覆写只是兜底，靠它会让开发期的日志、统计和「关于」对话框显示上一版本号
3. **versionCode 未递增**：Android 设备通过 versionCode 判断是否需要重新解压配置，不递增会导致设备上仍使用旧配置
4. **未运行 CI**：发布前必须确保 ruff + mypy + pytest 全量通过
