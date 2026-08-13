# 开发日志 2026-08-03

> 接续 08-02 DSL 语法扩展与 OCR 清洗架构。
> 本轮主题：**screenshot 指令 + OCR 画布可视化 + 批处理修复 + v0.1.1 发布**。

---

## 一、DSL screenshot 截图指令（`2545340`）

### 1.1 背景

工作流需要支持截取当前画面并保存，用于调试和分析。

### 1.2 改动

- 新增 `screenshot` 指令；
- 截图保存到 `logs/image/`；
- 文件名格式：`image_YYYYMMDD_HHMMSS_mmm.png`。

---

## 二、OCR 清洗规则简化（`1217f17`）

### 2.1 背景

原有 `patterns` 格式为 `list[dict]`，与 `replacements` 的 `dict` 格式不统一。

### 2.2 改动

- `patterns` 从 `list[dict]` 改为 `dict[str, str]` 格式；
- 符号统一和噪声删除用正则字符类；
- 规则编辑改为表格内联编辑（移除弹窗）。

---

## 三、OCR 对话框画布可视化（`b81c71d`）

### 3.1 背景

OCR 识别结果展示不够直观，需要支持缩放/平移和结果标注。

### 3.2 改动

- 新增 `OCRCanvas` 画布组件；
- 支持鼠标滚轮缩放（以鼠标位置为中心）；
- 右键拖拽平移；
- OCR 结果红色矩形标注 + 文字标签；
- 布局改为左右分割：画布 3/4 + 结果文本 1/4。

### 3.3 重命名

- `ocr_test_dialog.py` → `ocr_dialog.py`；
- `OCRTestDialog` → `OCRDialog`；
- `_open_ocr_test()` → `_open_ocr_dialog()`。

---

## 四、批处理修复

### 4.1 调律报告 resets 字段初始化（`d295053`）

- `resets` 字段始终初始化；
- 测试数据修正。

### 4.2 江湖工作流 goto 优化（`d6882c2`）

- goto 跳出双重循环；
- 批处理模块 lint 修复。

### 4.3 账号切换逻辑修复（`f78796c`）

- 比较 `prev_account` 与 `account`（游戏账号），而非 `tail`；
- 测试 mock 完善，防止调试图片写入。

---

## 五、v0.1.1 发布（`f62619e`）

### 5.1 版本号升级

- `pyproject.toml`: 0.1.0 → 0.1.1；
- `src/lvjiang/_version.py`: 0.1.0 → 0.1.1；
- `uv.lock`: 0.1.0 → 0.1.1；
- `android/app/build.gradle.kts`: 0.1.0 → 0.1.1, versionCode 11 → 12；
- `src/lvjiang/apps/yysls/__init__.py`: 窗口标题版本号更新。

### 5.2 发布文档

- 新增 `docs/50-releases/v0.1.1.md`；
- 主题：DSL 引擎增强 + OCR 清洗架构 + 批处理优化。

### 5.3 Tag 规范

- 删除旧的 `v0.1.0` tag；
- 创建 `0.1.0` 和 `0.1.1` tag（无 `v` 前缀，遵循 GitHub 规范）。

---

## 六、结果

- 本轮提交 7 commits；
- 全部改动已提交并推送至 `origin/master`；
- v0.1.1 发布完成。

---

## 七、关键设计决策（用户确认）

1. **screenshot 指令**：截取当前画面保存到 `logs/image/`。
2. **OCR patterns dict 格式**：与 replacements 统一。
3. **OCR 画布可视化**：缩放/平移 + 红色矩形标注。
4. **OCRDialog 重命名**：移除 "test" 标识。
5. **批处理账号切换**：比较 `account` 字段。
6. **Tag 规范**：无 `v` 前缀（`0.1.0` / `0.1.1`）。
