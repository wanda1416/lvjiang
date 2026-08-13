# Dev Log: 2026-07-18 输入/截图后端抽象化与 Android（scrcpy）接入

> 日期：2026-07-18
> 涉及模块：`lvjiang/core/`（input / capture / android）、`lvjiang/workflows/`、`lvjiang/ui/region_editor/`、`config/system/workflows/`、`tests/`
> 关键词：输入/截图后端抽象、ADB 纯命令后端、scrcpy H.264 视频流、by 子句短路识别、录制功能、front-matter 元数据、scene_editor 改名

> 说明：本篇为事后补写（原 07-17~20 无每日日志，仅 07-21 演进总结覆盖），依据当日 git 提交还原。

---

## 一、本日主线

当日 15 个提交，核心是**平台后端抽象化**：把输入与截图从「Windows 专用」抽象为可切换后端，并首次接入 Android（ADB + scrcpy）。同时 DSL 增加 by 子句短路识别、录制功能与 front-matter 元数据。

---

## 二、输入/截图后端抽象化（`2ebdd96`，25 files +2041/-467）

**背景：** 输入（SendInput/PostMessage）与截图（mss）实现散落在主流程，无法支持 Android 设备。

**方案：**

- 抽象出输入后端 / 截图后端接口，主流程面向接口编程；
- ADB 后端切换为**纯 adb 命令**实现（不依赖第三方库）；
- 为后续 scrcpy 视频流截图与 Android 执行端奠定架构基础。

配套修复（`48cceca`）：ADB 坐标系对齐、DSL 字段赋值求值、布局尺寸信息栏。

---

## 三、Android 截图：scrcpy H.264 视频流

### 3.1 core/adb → core/android + scrcpy 接入（`ed3255c`，15 files +780/-108）

- `core/adb` 模块重命名为 `core/android`（语义更准确）；
- 引入 **scrcpy H.264 视频流截图方案**：相比逐帧 `adb screencap`，视频流帧率更高、延迟更低。

### 3.2 scrcpy 流式截图 OCR 优化（`29581ea`）

针对流式帧做 OCR 识别优化（帧复用、按需识别）。

### 3.3 ADB 拖拽支持 hold（`a5ffe1c`）

ADB 拖拽支持 `hold` 长按语义，补充语法文档。

---

## 四、DSL by 子句短路识别（`6a4f194`，9 files +346/-41）

`scan` / `recognize` 新增 `by` 子句：按条件短路识别，命中即返回，避免全量扫描。

配套（`87cb2b3`）：补全 wf 元数据、修复 OCR 转律标记识别、by 子句改造、按钮初始状态修复。

---

## 五、录制功能与工作流元数据

### 5.1 录制功能 + DSL 坐标字面量扩展（`a65d6af`，11 files +502/-21）

新增操作录制功能；DSL 坐标字面量扩展（直接书写坐标点）。

### 5.2 front-matter 元数据语法（`6655349`，4 files +253/-4）

工作流文件支持 front-matter 元数据头；支持加载外部 `.wf`。

### 5.3 运行控制（`5d3a474`）

停止按钮响应修复 + 默认后台模式。

---

## 六、测试基线与编辑器收尾

- `6a41033`：配置 pytest 回归基线；
- `1943673`：合并 wf-scripts 手写脚本到 test_parser 回归（+121）；
- `cd9daa7`：区域编辑器画布编辑模式跳回原位修复；
- `3b1d065`：移除截图/画布比例一致性提示；
- `0b29f22`：**region_editor 重命名为 scene_editor**，窗口标题改为「场景管理」（18 files，术语对齐）。
