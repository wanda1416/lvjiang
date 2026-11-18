# 模板图库方案（评审稿）

> 评审稿，**未实现**。记录本轮确定的设计取舍、已实测的现状数据与仍需拍板的未定项。
> 与代码冲突时以现状代码为准；本文只描述要建成什么样。

---

## 一、目标与非目标

**目标**：把模板图（`find` / `scan by image` 用的定位图）纳入可管理状态——看得清有哪些、
每张干什么用、改/删不会破坏布局与脚本；并按名模板补齐"录制尺寸"这个跨分辨率的必要信息。
落地只需新建一份索引并登记现有 `common/` 三张，**不涉及既有图片与绑定的迁移**。

**非目标**：不与参考图库合并数据模型（一个是分类器，一个是定位器）；不引入模板"空间/激活"
概念（`actives.space` 只作用于参考图）；不改 Layout 的 `Region.template` 结构。

## 二、概念与术语

模板图只有一个组织维度：**分组**（不再使用"命名空间"这个词）。

| 分组类型 | 判定 | 现有成员 | 含义与消费方式 |
|---|---|---|---|
| **布局分组** | 分组名与某个**根布局 key** 同名 | `android`、`desktop` | 属于该布局的模板；由场景编辑器截取并绑定到 Region，主要经 `scan [scene].[region] by image` 消费 |
| **普通分组** | 不以根布局 key 命名 | `common` | 跨布局共享、不绑定 Region；按名消费（`find ... by image "common/xxx"`）。**不选分组时快速截取默认落 `common`**，用户也可以新建任意普通分组 |

约束：**普通分组名不得与根布局 key 重名**（否则语义翻转、既有分组会突然变成布局分组）。
别名布局（`android_cast`、`desktop_fullscreen`）不作为分组——它们继承根布局的模板目录。

名字形态：`<分组>/<其余>`。

- 布局分组：`<其余>` 沿用现有约定 `<scene>/<region>`（场景编辑器生成，管理页只读展示绑定名）；
- 普通分组：`<其余>` 为一级自由名（`common/tracked_person_avatar`）。

## 三、存储与索引

图片仍是 `config/{system,local}/templates/<分组>/…​.png`（纯 PNG，无 sidecar）。新增同位置的
聚合索引：

```yaml
# config/system/templates/templates.yaml 与 config/local/templates/templates.yaml
version: 1
templates:
  common/tracked_person_avatar:
    record_w: 1280
    record_h: 720
  common/back_button:          # 快速截取默认落 common；也可新建自己的分组
    record_w: 2400
    record_h: 1260
    note: 关闭按钮
```

- **双层合并**走现成机制：`ConfigResolver.load_merged("templates/templates.yaml")`（system 基底 ← local
  dict 深合并、`__deleted__` 删除键）+ `save_merged`（开发写 system；用户写 local diff）。
- 需要在 `core/config/merge.py` 的 `DELETABLE_PATHS` 注册 `"templates/templates.yaml": ("templates",)`，
  否则删除模板条目会被"系统内容不可删"拦截（现在该表是空的）。
- 字段：`record_w`/`record_h`（**必需**，写入时采集）、`note`（可选）、`min_score`（可选，
  仅供按名引用时兜底；绑定存在时以 Layout 为准）。
- 已注册进版本管理：`register_versioned_file("templates/templates.yaml")`——顶层聚合配置带
  `content_version`，参与在线下发与修订判定（与 `ocr.yaml` 同一档：根 YAML 不进
  `content.json`，每次启动直接读取）；模板 PNG 与 `.wf` 同类，文件本身没有修订号，
  随整包修订下发。

## 四、元数据来源与运行期读取顺序

| 模板类型 | record 的权威 | 阈值权威 |
|---|---|---|
| 布局分组 + 有 Region 绑定 | Layout JSON 的 `Region.template` | 同上（现 13 处绑定全是 `0.8`） |
| 其余（普通分组 / 未绑定的布局分组） | `templates.yaml` | 索引 `min_score`（可选）→ 全局 `0.8` |

**单一来源规则**：有绑定的模板**不写** `templates.yaml`（避免同一份 record 两处维护）；绑定被
解除时，由场景编辑器把 record 搬进索引或直接删除图片。

`find ... by image "名"` 的 record 回落链（现状只有第一档为空 → 恒 1.0）：

1. `templates.yaml` 里的条目；
2. 同名 `Region.template` 绑定的 record（扫布局即可查到，名字本身就是
   `<布局根>/<scene>/<region>`）；
3. 都没有 → 0（按原始尺寸，维持现状）。

## 五、消费路径（以 scan 为主）

| 路径 | 写法 | 谁是权威 | 管理页如何呈现 |
|---|---|---|---|
| **场景引用（主）** | `scan [scene].[r1, r2] as $hit by image` | Layout 绑定 | 展示"被 `scene`/`region` 绑定"，并给出可复制的 `scan` 片段；不在此页编辑绑定 |
| **显式全名（次）** | `find [area] as $v by image "common/x"` | 脚本字面量 | 展示"脚本按名引用"（静态可查的列位置），给出 `find` 片段 |

参考图侧的 `recognize on group` 与这套完全无关；空间下拉在模板相关页签禁用。

## 六、界面

Tab 结构：**新增图像 / 参考图管理 / 参考图元数据 / 模板图管理**。

### 新增图像（右侧 toggle 切换目标）

一块画布（导入 / 粘贴 / 截图 + 框选），右侧顶部 `AutoWidthComboBox` 选择写入目标：

- **参考图**：框选 → 网格参数 → 执行切割 → 逐格填 label/分组/meta → 提交（现有 `GridPanel`，
  空间下拉在此模式生效）。
- **模板图**：
  - 只做**单图裁剪**：框选一个矩形 → 预览 → 填分组 + 名字 → 保存（不进入网格/切割结果流程）；
  - 网格参数、切割结果、逐格编辑全部隐藏（它们属于参考图工具）；
  - 记录尺寸自动取**当前截图的画布宽高**并只读展示（这是跨分辨率归一的前提）；
  - 保存前可"测试匹配"（当前截图 + 记录尺寸算实际分数）；
  - 保存后给可复制的用法片段；空间下拉禁用并提示"模板按分组存放，与图库空间无关"。

### 模板图管理

- 左：分组过滤（布局分组标「布局」、普通分组标「共享」）+ 名字搜索 + 缩略图流（system/local
  角标）；缩略图按原像素放大显示（模板多为 30–50 px）。
- 右：名字、像素尺寸、记录尺寸、来源层、引用列表（绑定 / 脚本按名 / 测试夹具）、推荐用法。
- 操作按**所有权**分档：
  - **布局分组**：只展示。截取/覆盖/删除都由场景管理负责（名字 = 布局/场景/Region，
    尺寸 = Layout 绑定），图库侧改它会同时破坏两条契约；
  - **普通分组**：允许重新截取覆盖、重命名（可在普通分组之间移动）、删除、复制名字/用法；
  - 任何分组都提供「复制名字」「复制用法」（布局分组给 `[scene].[region]` 形式的 `scan`
    片段，普通分组给 `find … by image "分组/名字"`）。
- 删除保守：有绑定 → 列出布局/场景/Region；有按名引用 → 提示脚本会失效；`role: sample`
  的标定样本永久保留（它是模板图，只是不参与匹配）。

## 七、校验与安全

- 分组名：非空、无路径分隔符、不以 `.` 开头；**不得与根布局 key 同名**（普通分组）。
- 模板名：`<分组>/<其余>`，`<其余>` 不含 `..` / 反斜杠；普通分组只允许一级。
- 写入层：开发模式→system，用户模式→local（PNG 与 `templates.yaml` 同层），复用
  `ConfigResolver` 与 `write_template`。
- **所有权**：布局分组的模板归场景管理，库层 `managed_by_scene()` 直接拒绝改名/删除
  （不靠界面自觉）；普通分组归图库管理。
- 删除：`templates/` 走现有 `delete_template`（用户模式只撤 local 影子、系统内容受保护）；
  索引条目按 `DELETABLE_PATHS` 白名单求解。

## 八、现状数据与初始化（无迁移）

实测（2026-11-18）：模板共 16 张——`android/` 7、`desktop/` 6、`common/` 3；13 处 Region 绑定
（`android` 7 + `desktop` 6），绑定里的 record 有五种取值（`2800×1260`、`2048×920`、`766×321`、
`1920×1080`、`542×250`），`min_score` 全为 `0.8`。

**没有数据要迁移**：13 张布局分组模板的 record 本来就在 Layout 绑定里（按第四节规则不写索引），
图片与绑定都不动。初始化只有一件事——新建 `config/system/templates/templates.yaml` 并登记 `common/` 下
现有的 3 张：

```yaml
version: 1
templates:
  common/tracked_person_avatar:      # 被本地脚本按名引用
    record_w: 2800
    record_h: 1260
  common/locked: {}                  # 算法标定样本，不需要尺寸
  common/unlock: {}
```

- `tracked_person_avatar` 的录制基准是 **android 2800×1260**（需求侧确认，与 `android/` 那 5 条
  绑定的 record 同值），因此在 2800 宽的设备上缩放恒为 1.0，与今天行为一致；换到别的画布宽时
  按 `当前宽 / 2800` 缩放——沿用与绑定模板同一套"UI 随画布等比例缩放"的假设，桌面端首次使用
  时需实机核对一次分数。
- `locked` / `unlock` **不需要尺寸**：它们是 `tests/yysls/test_equipment_lock_state.py` 直接读文件
  校验颜色分类阈值的**标定样本**，运行期按颜色比例判定、不做模板匹配，因此"跨分辨率归一"对它们
  没有意义。它们登记进索引只是为了"一张图一个去处"，管理页对它们**不提供补尺寸与测试匹配**，
  也不允许删除（测试依赖）。
- 索引条目缺 record 的模板，运行期行为与今天完全一致（按原始尺寸），管理页标「未登记录制尺寸」
  并在重新截取时自动补——即"登记"与"补尺寸"是两件事。

## 九、实施顺序（每步可独立回退）

1. **core**：`templates.yaml` 读写（resolver 合并 + `DELETABLE_PATHS` 注册）、
   `template_library` 原语（list / write / rename / remove / usage）、初始化文件
   （新建 `config/system/templates/templates.yaml` 并登记 `common/` 3 条空记录）、单测。
2. **运行期**：`find by image "名"` 的 record 回落链（索引 → 同名绑定 → 0）+ 回归测试。
3. **UI-新增**：目标 toggle + 模板图单图裁剪保存（含记录尺寸采集、测试匹配、用法片段）。
4. **UI-管理**：清单、分组过滤、引用展示、覆盖/重命名/删除保护。
5. **文档**：需求篇（`docs/20-requirements`）、3.3 用户指南、`32-grammar/04.4`（补"模板分组与
   record 来源"）、`37-vision/03-template-matching`（补按名模板的尺度差异）、
   `config/system/templates/README.md`、开发日志。

## 十、剩余待定

1. 布局分组里**截了但没绑到 Region** 的孤儿模板如何呈现：正常列出并标「未绑定」，
   还是收进「待绑定」视图？（不影响第 1、2 步的实现）

已经拍板、实现完毕的：`templates.yaml` 参与 `content_version` 与在线下发；`locked`/`unlock`
永久保留在 `common/`（`role: sample`，不参与匹配、不给尺寸）；布局分组只读、普通分组可改名
与删除、普通分组之间允许移动；不新增 `user/` 分组，未选分组时落 `common`。

## 十一、实现进度（2026-11-18）

已落地并验证：

1. **索引**：`config/system/templates/templates.yaml`（`content_version: 1`，注册为版本化聚合配置参与
   在线下发；local 键级覆盖 + `__deleted__`）；`DELETABLE_PATHS` 注册 `("templates",)`；
   种子为 `common/` 三张——`tracked_person_avatar` 记 `2800×1260`，`locked`/`unlock` 为
   `role: sample`；
2. **core**：`core/templates/library.py`（分组判定含"只认根布局"、索引读写、引用扫描、
   清单、改名/删除 + 所有权守卫 `managed_by_scene`）；`template_locator.delete_template`
   支持传入解析器；
3. **运行期**：`find … by image "名字"` 的尺寸回落链（索引 → 当前布局同名绑定 → 0）；
4. **UI**：`新增图像` 目标切换（参考图 / 模板图）、模板图单矩形裁剪 + 录制尺寸 + 测试匹配 +
   用法片段；`模板图管理`（分组过滤、来源层、引用、按所有权开放改名/删除）；图库空间控件在
   模板相关页签禁用；
5. **文档**：3.3「模板图」章节、`32-grammar/04.4`（按名模板与索引）、
   `37-vision/03`（按名回落）、`config/system/templates/README.md`；
6. **测试**：`tests/core/templates`（12 项）、`tests/workflows/test_find_image.py`（+3 项运行期）、
   `tests/ui/reference/test_template_management.py`（5 项）、i18n 双门禁（43 条新文案 × 11 语言）。

