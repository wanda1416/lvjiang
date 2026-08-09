# 律匠发布流程 — AI Agent 操作手册

本文档描述律匠（lvjiang）项目的标准发布流程。AI Agent 在执行 `/release` 或用户要求发布新版本时，应严格按以下步骤操作。

---

## 前置条件

- 所有功能代码已提交并推送到 master 分支
- CI 全量通过（ruff + mypy + pytest）
- 当前工作区无未提交的变更（除待发布的版本号变更外）

---

## 标准发布流程

### 第一步：对比功能差异

对比当前 master 与上一个发布版本之间的 commit 差异，梳理新增功能、修复、重构等内容。

```powershell
# 查看上一个发布版本的 tag 或 commit
git log --oneline docs/50-releases/v*.md | Select-Object -First 5

# 对比上一个版本到当前的所有变更（以 v0.1.1 为例）
git log v0.1.1..HEAD --oneline
```

### 第二步：编写发布声明

在 `docs/50-releases/` 目录下创建新版本发布文档（如 `v0.1.2.md`），格式参考已有发布文档。

**必须包含的章节：**
- ✨ 新增功能
- 🔧 修复
- 📦 重构
- 📚 文档
- 📝 变更统计（commit 数 + 测试用例数）
- ⚠️ 免责声明
- 📄 许可证

### 第三步：更新版本号

**需要更新的文件（不可遗漏）：**

| 文件 | 字段 | 说明 |
|------|------|------|
| `pyproject.toml` | `version = "X.Y.Z"` | Python 包版本号 |
| `android/app/build.gradle.kts` | `versionName = "X.Y.Z"` | Android APK 版本名 |
| `android/app/build.gradle.kts` | `versionCode = N` | Android 内部版本号（递增整数，改了 config/system 或布局文件必须 +1） |

> **注意：** `src/lvjiang/_version.py` 由 `package.bat` 打包时自动注入，**无需手动修改**。

### 第四步：打包

运行打包脚本，生成 Windows 发布包：

```powershell
packaging\package.bat
```

打包产物：
- `dist/lvjiang/lvjiang.exe` — 可执行文件
- `dist/lvjiang-win64.zip` — 发布压缩包（便携版）
- `dist/lvjiang-win64-setup.exe` — Windows 安装包（推荐）

打包脚本会自动：
1. 从 `pyproject.toml` 读取版本号注入到 `src/lvjiang/_version.py`
2. 调用 PyInstaller 构建
3. 复制配置、ADB、scrcpy 等运行时依赖
4. 压缩为 zip
5. 调用 Inno Setup 生成安装包（需安装 [Inno Setup 6](https://jrsoftware.org/isdl.php)）

> **注意：** 如果未安装 Inno Setup，脚本会跳过安装包构建，仅生成 zip。

### 第五步：提交

打包成功后，提交所有发布相关变更：

```powershell
git add pyproject.toml android/app/build.gradle.kts docs/50-releases/vX.Y.Z.md src/lvjiang/_version.py uv.lock
git commit -m "chore: bump version to X.Y.Z + 发布文档"
```

> **注意：** `packaging/package.bat` 打包后会产生以下文件变更，必须一并提交：
> - `src/lvjiang/_version.py` — 版本号注入（从 pyproject.toml 读取写入）
> - `uv.lock` — 依赖锁文件可能因 uv run pyinstaller 而更新

### 第六步：推送

```powershell
git push
```

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
- [ ] `android/app/build.gradle.kts` versionName 已更新
- [ ] `android/app/build.gradle.kts` versionCode 已递增（如有 config/布局变更）
- [ ] `docs/50-releases/vX.Y.Z.md` 发布文档已编写
- [ ] `packaging/package.bat` 打包成功
- [ ] `dist/lvjiang-win64.zip` 已生成
- [ ] `dist/lvjiang-win64-setup.exe` 已生成
- [ ] `src/lvjiang/_version.py` 版本号已注入（打包脚本自动完成）
- [ ] `uv.lock` 变更已纳入提交（如有）
- [ ] 所有变更已提交并推送

---

## 常见错误

1. **遗漏 Android 版本号**：`android/app/build.gradle.kts` 的 versionName 和 versionCode 必须同步更新
2. **手动修改 _version.py**：此文件由打包脚本自动注入，手动修改会在下次打包时被覆盖
3. **versionCode 未递增**：Android 设备通过 versionCode 判断是否需要重新解压配置，不递增会导致设备上仍使用旧配置
4. **未运行 CI**：发布前必须确保 ruff + mypy + pytest 全量通过
