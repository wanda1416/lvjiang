# 开发日志 2026-08-13（六）

> 接续本日《批处理框架从 0 到 1》。
> 本轮主题：**配置三层架构落地 + 打包体系从 0 到 1**。

---

## 一、配置三层架构落地

- 双模式三层配置落地——`config_resolver` 统一解析，默认布局与参考图入库为系统资产（`d44f982`）；
- 配置读写全面收拢——`SessionStore`（session.json 唯一读写，内存缓存+RLock+原子落盘）+ `ConfigResolver`（system/local 双层合并）作为统一咽喉，封堵旁路读写（`1092a84`）；
- 顶层 `config.py` 收拢至 `core/config` 包——`config.py → core/config/models.py`（`UserConfig`/`InputSimConfig`/`DelayParam`/`MaterialGridConfig` dataclass），`session.py` 新增 `load_settings`/`save_settings`/`load_material_grid`/`save_material_grid`（`cb8313c`）；
- 层根常量收拢至 resolver，封堵 constants 旁路——`SYSTEM_CONFIG_DIR`/`LOCAL_CONFIG_DIR` 从 `constants.py` 移至 `resolver.py` 模块级（`cca7fe4`）；
- 移除 `SessionStore` 与 `ReferenceDB` 的懒加载，构造时直接读盘/加载，删除 `_ensure_loaded()` 及相关 assert（`96a1526`）；
- 单文件布局拆分为 `layouts.yaml` + `layouts/{name}/{scene}.json` 目录结构（`aa0284c`）；
- core 模块拆分——数据类独立建文件 + 模块重命名：删除零导入死代码 `material_db.py`，新建 `layout_models.py` 从 `scene_registry.py` 抽出运行时布局数据类（`CanvasConfig`/`FoundRegion`/`Region`/`Point`/`Arrow`/`Panel`/`Layout`），`scene_registry.py` 仅保留注册表全局函数（`b04740b`）；
- 日常配置持久化 + 插件 session 合并 + 开关注册表动态刷新——日常页脚本选择与参数持久化到 `session.json` 的 daily 节点，`PluginSession` 从独立文件改为读写主 `session.json` 的 yysls 顶层节点（`bab1d06`）；
- yysls 模块命名与结构优化——文件重命名消除歧义 + 统一静态/动态命名原则，如 `plugin_session.py → session.py`（`e7ad75d`）；
- 输入模拟配置重构 `DelayConfig`→`InputSimConfig`：`DelayConfig` 重命名为 `InputSimConfig`（只含点击/移动/抖动参数），`CustomDelay` 重命名为 `DelayParam` 并独立为顶层类，命名等待参数（`delay_params`）从 `session.json` 迁移到 `app.yaml` 随版本分发，`UserConfig.input_delay` 重命名为 `input_sim` + `delay_params`，各输入后端（ADB/SendInput/PostMessage/OnDevice）统一使用 `_inject_input_sim`，新增 `config/system/app.yaml` 承载系统默认（`dc83a52`）；
- 修复 `active_layout` 未指定时自动回退到第一个可用布局——`session.json` 不存在时 `active_layout` 为空字符串导致加载路径报错，`get_active_layout_name()` 为空时自动枚举布局文件并选择第一个（`c88fd06`）；
- 布局下拉列表保持 YAML 定义顺序而非字典序（`9e73145`）；
- `ConfigResolver` `dev_mode` 构造时缓存（`cf2d586`）；
- 统一工作流配置存储到 `wf_configs` 节点（`f6d0b28`）；
- 清理 `tuning_base` 残留引用 + 日志级别过滤器（`fda31dc`）。

## 二、布局别名与图库空间管理

- 图库空间配置拆分——多空间独立配置集 + 调律启动预检（`f277668`）；
- 新建空间移除预填字段 + 空间栏新增激活按钮与状态显示（`1cd4050`）；
- 场景编辑器 UI 持久化修复 + 调律材料不足三选项对话框 + 图库管理 output 字段隔离 + 布局别名功能（`5d9b10d`）；
- 别名布局截图独立管理，不重定向到父布局（`a9a0643`）。

## 三、配置/场景数据批量更新（chore）

多轮场景布局与工作流配置的批量调整，不逐条展开：布局配置、场景定义与江湖活动工作流更新（`db4f164`）；场景配置和文档更新（`7b09873`）；场景配置更新（`da34273`）；背包场景布局更新 + 新增 `role_detail`（`899828f`）；场景配置更新（`ccace93`）；新增活动场景配置与修复 `script_ops` 导入路径（`5aa95e6`）；layout JSON 更新排序后的 region 顺序（`4ebc63e`）；新增货币资产与装备预览图参考图库（`c2f5ad9`）；新增增益道具参考图（`0af441e`）；布局/场景/工作流配置更新与静态检查文档（`968444b`）。

## 四、打包体系从 0 到 1

- PyInstaller onedir 一键打包（launcher + spec + package.bat）（`fb5fcc6`）；
- adb 子进程统一加 `CREATE_NO_WINDOW`，修复 windowed 打包下控制台闪窗（`790133d`）；
- 内置 adb 随包分发，用户免装 platform-tools（`17c36c4`）；
- 新增 Inno Setup 安装包构建——新增 `installer.iss` 生成 `lvjiang-win64-setup.exe`，`package.bat` 自动检测 Inno Setup 并调用，更新 `readme.md` 说明安装包产物（`3ebb684`）；
- 打包产物文件名采用标准命名格式「名称-版本-平台」——zip: `lvjiang-v0.2.0-win64.zip`，setup: `lvjiang-v0.2.0-win64-setup.exe`（`c352d9d`）。

---

## 结果

- 配置读写路径统一收拢至 `config_resolver`，默认布局/参考图正式作为系统资产入库；
- 打包体系从零搭建完成：PyInstaller onedir + Inno Setup 安装包 + 内置 adb，产物命名规范化；
- 本篇 commit 约 20 个。

---

## 关键设计决策（用户确认）

1. **默认布局与参考图作为系统资产入库**：不再依赖运行时生成，随版本分发，保证开箱可用。
2. **`InputSimConfig` 语义收窄**：仅保留点击/移动/抖动参数，命名等待参数改随 `app.yaml` 版本分发而非用户 `session.json`。
3. **打包产物命名统一为「名称-版本-平台」**：便于用户与自动化脚本识别版本与目标平台。
4. **内置 adb 随包分发**：免去用户单独安装 platform-tools 的门槛。
