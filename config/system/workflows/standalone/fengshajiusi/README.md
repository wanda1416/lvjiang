# 风沙酒肆 · 夜宿谜影（独立任务包）

一个可直接交付的独立任务包：**整个文件夹就是交付单元**，解压放到
`config/local/workflows/` 下即可使用，删除目录即完整卸载，不会改动任何全局
`scenes/`、`layouts/` 配置。

## 安装

把本目录整体放到下面这个位置（**目录名与层级必须保持不变**）：

```text
config/local/workflows/standalone/fengshajiusi/
```

之所以不随便放：包内入口脚本用 `import "standalone/fengshajiusi/desktop.wf"` 这种
**相对 workflows 根**的固定路径引用自己的平台过程文件，换目录名或层级会让 import 失败。
游戏启动后在任务列表「玩法」分组里看到 **风沙酒肆·夜宿谜影** 即可运行。

卸载：删掉 `config/local/workflows/standalone/fengshajiusi/` 整个目录。

## 目录内容

| 路径 | 说明 |
|------|------|
| `manifest.yaml` | 任务包标识与入口元信息（id `fengshajiusi`） |
| `fengsha_jiushi_night.wf` | 入口脚本：参数、词表、主流程与两端通用的过程 |
| `desktop.wf` / `android.wf` | 平台过程：键盘（W/A/S/D、Y/N、Space）与点按/摇杆 |
| `scenes/` | 私有场景 `fsjs_night`「风沙酒肆」（按阶段分 base / watch / settle / clue_dialog 四个视图） |
| `layouts/desktop|android/` | 两套实机标定的区域坐标 |

识别只在场景区域内做（不再整屏 OCR），所以跑之前**不要改窗口比例**：桌面布局按 16:9、
安卓布局按 20:9 标定；窗口/分辨率变了请按下面「重新标定」更新坐标。

## 使用前置

角色在客栈房间内（不必站在从拾香旁）。看到「躺下」提示即会被触发一次；躺下后等守夜
开始，连续接待 5 位访客，默认只放第 2、第 5 位。多人模式要等队友都躺下才会开始。

## 重新标定（换分辨率 / 换窗口大小时）

1. 打开「场景管理」，顶栏最右侧点「加载任务场景」，选择本任务包；
2. 布局下拉框选 `desktop`（或 `android`）→ 场景 `fsjs_night` → 对应视图；
3. 框出各区域（`quest_track`、`prompt_bar`、`open_btn`、`refuse_btn`、`lie_down`、
   `settle_title`、`again_btn`、`clue_tip`、`clue_check`、`clue_confirm`、`listen_btn`）；
4. 关闭窗口前保存；退出任务场景后主场景配置不受影响。

`listen_btn`（金色耳朵「听风辨位」）与 `clue_confirm`（弹窗「确认」）在桌面布局里是
「未启用」状态：PC 端没有这两个可点元素（对应动作是 Y/N 与 Space 键），并非漏标。
