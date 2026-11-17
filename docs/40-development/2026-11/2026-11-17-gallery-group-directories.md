# 开发日志 2026-11-17（一）

> 本轮主题：**参考图库图片目录回归「按分组命名」，写入侧跟随分组落盘**。

---

## 一、背景与决定

`2026-08-17` 把材料图库的图片从分类文件夹重组为 `bucket_0/4/8/C` 哈希分桶，
分组语义只留在 YAML 的 `meta.group` 里。实际使用中目录不再有信息量：

- 手游空间 45 张图分散在 `bucket_0/4/8/C`，看一眼目录无法判断分组；
- 同一空间的端游反而一直是分组命名，两个空间表现不一致；
- 「多分组」曾被当作不按分组落盘的理由，但多分组完全可以用单独挂载/多个分组
  字段表达，不需要牺牲目录可读性。

决定（用户确认）：**回退到按分组命名的目录**，并让写入侧跟随分组，而不是随机分桶。

## 二、数据迁移

- `config/system/references/手游/` 45 张图按各自 `meta.group` 移到
  `调律材料/6`、`装备培养/6`、`货币资产/25`、`普通道具/3`、`增益道具/1`、
  `定音转律/4`，删除 4 个空的 `bucket_*` 目录；
- `手游.yaml` 未改动——`file` 字段只存裸文件名，`meta.group` 本来就正确；
- 校验：用 `ReferenceDatabase` 隔离 session 加载，45/45 条目 `image_path()`
  命中真实文件且所在目录名 == `meta.group`。

## 三、写入侧规则

- `add_entry`：校验分组 → 图片落「第一个分组」对应目录，未分组落 `default/`；
- `update_entry`：分组变了就把**可写层**里的图片挪进新目录；用户模式改系统条目
  只写 local 影子，系统图片原地不动（`image_path()` 仍按文件名找到它）；
- 分组名即目录名，因此新增 `validate_group_names()`：
  - 拒绝保留名 `default`（大小写不敏感，Windows 目录本来不区分大小写）——
    它是未分组的兜底目录，当成普通分组会出现「分组与未分组同目录」；
  - 拒绝以 `.` 开头、含路径分隔符或通配符的名字——图片会落到目录扫描看不到的
    位置或非法路径上；
  - 校验在写图片之前完成，失败不留半写状态；界面（单条保存、批量应用、切割提交）
    统一调用同一函数并给出提示，避免规则分两套。
- `_discover_buckets`/`buckets` 更名为 `_discover_image_dirs`/`image_dirs`：
  目录扫描语义不变（空间下一级子目录即图片目录），只是名字不再暗示「哈希桶」。

## 四、影响面

- `recognize` 链路完全不受影响：`ReferenceMatcher` 仍通过 `db.image_path()` 取图，
  目录名对它是透明的；
- 数据文件只动了图片所在目录，YAML 与 `templates/` 均未改动；
- 端游历史错位一条（`3C45D25D.png`「心法心得」：图片在 `货币资产/`、`meta.group`
  是 `普通道具`，旧随机分桶留下的）按「YAML 是分组权威、目录只是位置」移到
  `普通道具/`，YAML 未改，识别分组语义不变。两个空间现均为「目录 == 第一分组」。

## 五、测试

`tests/core/test_reference_db_overlay.py` 新增 `TestGroupDirectories`：未分组落
`default/`、多分组只落第一个目录、开发模式改分组挪图、用户模式不动系统图片、
保留名/非法名在写入前被拒且不落地；`TestBucketDiscovery` 更名并改用分组目录名。

## 六、数据修正与数字归一

用户复核预置内容时发现两处：

1. **`溯玉` 用错**：「装备培养」里那条实为 `振玉·溯`（振玉系列的溯版本），`溯玉`
   是货币。两个空间的标签已改为 `振玉·溯`，图片留在装备培养目录（标签改名不改变
   分组），手游货币资产下的 `溯玉` 保持不变。
2. **`level` 两种写法**：手游 13 条是字符串 `'110'`，端游是整数 `110`。直接后果是
   `list_entries(level_filter=110)` 在手游上恒为空（`'110' != 110`），按等级筛选失效。

根因不在数据，而在写入口：新增/编辑参考图时 meta 是从控件收集的**文本**，原样落盘
就会把 `110` 写成 `'110'`。因此除了把 13 条数据改成整数，还在唯一写入口
`ReferenceDatabase._coerce_numeric_meta()` 按 `meta_schema` 里 `type: number` 的字段
统一转数字（空文本视为未填、不写空串；解析不出的文本原样保留），新增与编辑两条路径
都经过它。测试见 `TestNumericMetaCoercion`。

## 七、空间 key 化：`端游/手游` → `desktop/android`

图库空间此前是唯一没有「稳定 key + 展示名」分离的模块（layouts、环境、模板命名
空间都已经是 `android` / `desktop`），于是把空间也统一过来：

- `references/端游.yaml` → `desktop.yaml`、`references/手游.yaml` → `android.yaml`，
  同名图片目录一起改名；空间 yaml 顶层新增本地化展示名
  `name: {zh_CN: 手游, en_US: Mobile}`；
- `DEFAULT_SPACE` 改为 `android`；`ReferenceDatabase.space_label()` 负责 key →
  展示名（`resolve_localized`，没有 `name` 就回落 key）；
- 四处空间下拉改成「显示本地化名字 + `itemData` 存 key」，并把三处原本按**显示
  文本**找空间的 `findText(plan.space)` 改成 `findData`——否则下拉一旦显示中文名，
  方案套用就会失配；
- `save()` 原样写回空间 yaml 的 `name`，避免一次普通条目保存抹掉随包双语名
  （测试 `test_save_keeps_localized_space_name`）；
- `config/system/app.yaml` 四个预置方案的 `space:` 同步改成 key。

**不做迁移**（用户确认：实际使用中几乎都是这四种预置方案，没人自定义）：旧机器上
`interface.json` 的 `actives.space` 与 session 里的自定义方案若仍写中文名，会回退到
`DEFAULT_SPACE` / 被判「空间不存在」，重选一次即可。当前数据侧确认 session 里没有
自定义方案。

## 八、测试补强

`test_reference_db_overlay.py` 的空间用例改为断言 key + 双语名可解析；新增
`test_space_label_resolves_localized_name`（dict / 纯字符串 / 缺失三种形态）与
`test_save_keeps_localized_space_name`。`test_target_plan_selection` 与
`test_plan_admin_setting` 跟着改成「下拉存 key、方案存 key」。

## 九、空间的编辑与刷新

空间栏按钮改为 **编辑 / 刷新 / 新建 / 删除**，去掉「激活空间」——运行时用哪个
图库由主页面下拉或连接方案决定，图库里再给一个激活入口等于维护两份状态。

- **编辑**：`SpaceNameDialog` 按随包语言表逐语种填写展示名（key 只读并给出原因），
  只写空间 yaml 的 `name` 一个键；全部留空表示回落到 key。用户模式改系统空间时
  写到 local 覆盖层，磁盘上未知的语言标签原样带回；
- **刷新**：`load()` 重扫空间并重载，外部改过 yaml / 图片目录后用它对齐；
- 新建、删除沿用原有闸门（系统空间与最后一个空间不可删）。

**管理界面只读切换**：图库、场景这些编辑器的下拉只是切「正在看的那份数据」，
不写 `actives.space` / `actives.layout`——运行时用哪个空间、环境、布局只由主页面
下拉与连接方案决定。为此 `ReferenceDatabase.load()` 拆出 `_load_active_space()`，
新增 `open_space()`（只影响本实例，关窗后主页面刷新即回到方案空间）与
`runtime_space()`（持久化值，失效时回落默认）；`delete()` 改为按**持久化**的
激活空间判断要不要改 `actives`，删的只是正在看的那个时不动它。测试：
`TestPreviewWithoutActivating`（3 项）与 `tests/ui/reference/test_space_switch_is_readonly.py`。

核心落点是 `ReferenceSpaces.name_target()/write_name()`（决定写哪一层、只合并
`name`）与 `ReferenceDatabase.set_space_name()`（激活空间被编辑时重载）。
测试：`TestSpaceDisplayName`（dev 写 system、用户模式只写覆盖层且条目合并恒等、
`None` 清空回落 key、未知空间拒绝）与 `tests/ui/reference/test_space_name_dialog.py`。

---

[← 返回开发日志总览](../README.md)
