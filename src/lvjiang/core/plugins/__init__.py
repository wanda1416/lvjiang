"""插件宿主：注册表与加载器（属于 core，不属于任何插件）。

插件代码位于命名空间包 ``lvjiang.apps``（内置子包 + 外置目录挂载），宿主只按
``lvjiang.apps.<name>`` 这一通用约定导入，不认识任何具体插件。

插件通过命令行参数 ``-reg <spec>`` 注册，``<spec>`` 是 ``<name>`` 或
``<name>@<path>``。引擎在启动时按顺序调用 ``load_app(spec)`` 加载插件模块，
并从模块顶层 ``hooks`` 属性获取 ``AppHooks`` 实例，再通过 ``register_hooks``
把识别器 / 工作流 / 内置函数注入到各注册表中。

插件分三级，宿主主动发现前两级：

1. 内置插件：``src/lvjiang/apps/<name>/``，随本包分发，public 打包自动携带；
   名册由 ``pkgutil.iter_modules`` 列举本包子包得到，宿主不写死任何插件名
   （frozen 下 PyInstaller 的 pkgutil 运行时钩子可列举 PYZ 内的子包）。
2. 外置默认目录：``<PROJECT_ROOT>/plugins/<name>/``，打包按目录是否存在决定
   是否携带（public 仓库没有该目录，因此发布包不含外置插件）；
3. 外置任意目录：``-reg <name>@<path>`` 显式声明，目录名不必等于插件名。

外置插件（第 2、3 级）以「子目录 + ``__init__.py``」识别，不读取任何清单文件；
目录名必须是合法 Python 标识符，否则记 warning 跳过，不影响其它插件与主程序启动。
"""
from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from loguru import logger

from ...constants import PROJECT_ROOT
from .hooks import AppHooks, TelemetryDisclosure

__all__ = [
    "AppHooks",
    "TelemetryDisclosure",
    "app_name",
    "builtin_app_ids",
    "discover_app_ids",
    "get_registered_app_ids",
    "get_registered_hooks",
    "get_registry",
    "load_app",
    "load_config_policies",
    "parse_app_spec",
    "register_config_policies",
    "register_hooks",
    "resolve_app_specs",
]

#: 插件命名空间包名；内置插件是它的子包，外置插件挂载到它下面。
APPS_PACKAGE = "lvjiang.apps"
# 内置插件根：插件命名空间包目录下的子目录。
_BUILTIN_ROOT = Path(__file__).parents[2] / "apps"
# 外置默认插件根：仓库根 / 安装目录旁的 plugins/。
_DEFAULT_PLUGINS_ROOT = PROJECT_ROOT / "plugins"

def _is_valid_app_name(name: str) -> bool:
    """插件名必须是稳定、可作包名尾段的 ASCII 标识符。"""
    return bool(name) and name.isascii() and name.isidentifier() and not name.startswith("_")


def _scan_root(root: Path) -> tuple[str, ...]:
    """扫描一个插件根目录，返回按名排序的插件名。

    只认「子目录 + ``__init__.py``」：``base.py`` 之类的模块文件和
    ``__pycache__`` 都不会被当成插件。非法目录名只记 warning 跳过，
    不让一个坏目录拖垮整次扫描或阻止程序启动。
    """
    try:
        entries = sorted(root.iterdir(), key=lambda entry: entry.name.lower())
    except OSError:
        return ()
    names: list[str] = []
    for entry in entries:
        if not entry.is_dir() or not (entry / "__init__.py").is_file():
            continue
        if not _is_valid_app_name(entry.name):
            logger.warning("[plugin] 忽略非法插件目录名: {}", entry)
            continue
        names.append(entry.name)
    return tuple(names)


def builtin_app_ids() -> tuple[str, ...]:
    """随本包分发的内置插件名册（只列本包子包，不扫外置目录）。

    离线工具（``scripts/add_content_version.py``、配置层单测）靠它选择插件
    声明，因此必须保持「与安装目录内容无关」的语义：只列举 ``_BUILTIN_ROOT``
    下的子包。用 ``pkgutil.iter_modules`` 而不是直接扫磁盘：frozen 环境下内置
    插件代码在 PYZ 归档里，磁盘上没有 apps/ 目录，PyInstaller 的 pkgutil
    运行时钩子会按归档内容列举。
    """
    names = {
        info.name for info in pkgutil.iter_modules([str(_BUILTIN_ROOT)])
        if info.ispkg and _is_valid_app_name(info.name)
    }
    return tuple(sorted(names, key=str.lower))


def discover_app_ids() -> tuple[str, ...]:
    """内置名册 + 外置默认目录的插件名册，内置优先、去重。"""
    ordered: dict[str, None] = {}
    for name in (*builtin_app_ids(), *_scan_root(_DEFAULT_PLUGINS_ROOT)):
        ordered.setdefault(name, None)
    return tuple(ordered)


def parse_app_spec(spec: str) -> tuple[str, Path | None]:
    """解析 ``-reg`` 参数值：``<name>`` 或 ``<name>@<path>``。

    返回 ``(插件名, 插件目录或 None)``。显式路径可相对当前工作目录或绝对，
    目录名不必等于插件名。
    """
    name, sep, raw_path = spec.partition("@")
    name = name.strip()
    if not _is_valid_app_name(name):
        raise KeyError(f"无效的插件名: {name!r}")
    if not sep:
        return name, None
    path = raw_path.strip()
    if not path:
        raise KeyError(f"插件 {name!r} 的 @ 路径为空")
    return name, Path(path)


def app_name(spec: str) -> str:
    """取 ``-reg`` 参数值里的插件名，供 i18n 等按名索引的消费者使用。"""
    return parse_app_spec(spec)[0]


def resolve_app_specs(
    specs: Iterable[str], *, all_plugins: bool = False,
) -> tuple[str, ...]:
    """把 ``-reg`` 与 ``--all-plugins`` 展开成最终按序加载的参数名册。

    显式 ``-reg`` 保持出现顺序，同名参数只保留首次出现；``--all-plugins``
    再追加内置与外置默认目录扫描到、但尚未列出的插件（内置在前）。
    """
    ordered: dict[str, str] = {}
    for spec in specs:
        name, _ = parse_app_spec(spec)
        ordered.setdefault(name, spec)
    if all_plugins:
        for name in discover_app_ids():
            ordered.setdefault(name, name)
    return tuple(ordered.values())


def load_app(spec: str) -> AppHooks:
    """加载指定插件并返回其 ``AppHooks``。

    插件模块必须在顶层导出 ``hooks: AppHooks`` 属性。
    """
    name, path = parse_app_spec(spec)
    module_path = f"{APPS_PACKAGE}.{name}"
    if path is not None:
        _mount_app(name, path)
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        if exc.name == module_path:
            raise KeyError(f"插件 {name!r} 未安装") from exc
        logger.exception("加载插件 {} 失败", module_path)
        raise RuntimeError(f"加载插件 {name!r} 失败: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("加载插件 {} 失败", module_path)
        raise RuntimeError(f"加载插件 {name!r} 失败: {exc}") from exc

    hooks = getattr(module, "hooks", None)
    if not isinstance(hooks, AppHooks):
        raise RuntimeError(
            f"插件 {name!r} 模块 {module_path} 未导出有效的 hooks: AppHooks"
        )
    return hooks


def _mount_app(name: str, directory: Path) -> None:
    """把任意目录挂载成 ``lvjiang.apps.<name>`` 包。

    ``-reg name@path`` 允许目录名与插件名不一致，因此不能靠追加父目录到
    ``__path__``，只能显式构造包规格并注入 ``sys.modules``；加载失败时撤销
    挂载，避免在 ``sys.modules`` 里留下半个包。
    """
    module_path = f"{APPS_PACKAGE}.{name}"
    if module_path in sys.modules:
        return
    importlib.import_module(APPS_PACKAGE)
    target = directory if directory.is_absolute() else Path.cwd() / directory
    init_file = target / "__init__.py"
    if not init_file.is_file():
        raise KeyError(f"插件 {name!r} 的目录缺少 __init__.py: {target}")
    module_spec = importlib.util.spec_from_file_location(
        module_path,
        init_file,
        submodule_search_locations=[str(target)],
    )
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"无法为插件 {name!r} 构造导入规格: {target}")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_path] = module
    parent = sys.modules.get(APPS_PACKAGE)
    if parent is not None and not hasattr(parent, name):
        setattr(parent, name, module)
    try:
        module_spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_path, None)
        if parent is not None and getattr(parent, name, None) is module:
            delattr(parent, name)
        raise


def register_hooks(hooks: AppHooks, registry: dict[str, Any] | None = None) -> None:
    """把插件声明的扩展点注入到各注册表。

    阶段 1 仅做日志登记；阶段 2/3/5 会在此处对接识别器、工作流、内置函数注册表。
    """
    registry = registry if registry is not None else _get_global_registry()

    loaded_ids = set(registry.get("app_ids", ()))
    missing = [app_id for app_id in hooks.requires_app_ids if app_id not in loaded_ids]
    if missing:
        raise RuntimeError(
            f"插件 {hooks.id or hooks.name!r} 依赖尚未加载的插件: "
            f"{', '.join(missing)}；请先加载依赖插件"
        )

    register_config_policies(hooks)

    if hooks.i18n_directory is not None:
        # 插件自带词表：目录由插件声明，宿主不往主仓库 config/i18n/apps 写文件。
        from ...i18n import load_app_i18n
        load_app_i18n(hooks.id or hooks.name, directory=hooks.i18n_directory)

    logger.info("[plugin] 注册插件: {}", hooks.name or "<unnamed>")

    for callback in hooks.startup_callbacks:
        try:
            callback()
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 启动初始化失败: {}", hooks.id or hooks.name)

    if hooks.id:
        app_ids = registry.setdefault("app_ids", [])
        if hooks.id not in app_ids:
            app_ids.append(hooks.id)
        # 已装配插件的完整声明：离线同步、历史回传、配置重载等消费方只读
        # 注册表，不再各自 load_app 取值。
        registry.setdefault("hooks", {})[hooks.id] = hooks

    if hooks.window_title:
        registry["window_title"] = hooks.window_title
        logger.info("[plugin]   window_title = {}", hooks.window_title)

    if hooks.left_tab_builders:
        registry.setdefault("left_tab_builders", []).extend(hooks.left_tab_builders)
        logger.info("[plugin]   left tabs: {}", [label for label, _ in hooks.left_tab_builders])

    if hooks.right_tab_builders:
        registry.setdefault("right_tab_builders", []).extend(hooks.right_tab_builders)
        logger.info("[plugin]   right tabs: {}", [label for label, _ in hooks.right_tab_builders])

    if hooks.menu_builders:
        registry.setdefault("menu_builders", []).extend(hooks.menu_builders)
        logger.info("[plugin]   menu builders: {}", len(hooks.menu_builders))

    if hooks.theme_stylesheet_builders:
        registry.setdefault("theme_stylesheet_builders", []).extend(
            hooks.theme_stylesheet_builders
        )

    if hooks.recognizer_classes:
        registry.setdefault("recognizer_classes", []).extend(hooks.recognizer_classes)
        # 实际注入到识别器注册表
        try:
            from ..recognizers import register_recognizer
            for cls in hooks.recognizer_classes:
                register_recognizer(cls)
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 识别器注册失败")
        logger.info("[plugin]   recognizers: {}", len(hooks.recognizer_classes))

    if hooks.workflow_implementations:
        registry.setdefault("workflow_implementations", {}).update(hooks.workflow_implementations)
        # 实际注入到工作流注册表
        try:
            from ...workflows.implementations import register_workflow
            for name, cls_path in hooks.workflow_implementations.items():
                register_workflow(name, cls_path)
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 工作流注册失败")
        logger.info("[plugin]   workflows: {}", list(hooks.workflow_implementations.keys()))

    if hooks.builtin_modules:
        registry.setdefault("builtin_modules", []).extend(hooks.builtin_modules)
        # 实际导入模块触发 @builtin_func 装饰器注册，并把归属登记进元数据
        from ...workflows.builtins._registry import assign_app, list_functions
        before = set(list_functions())
        try:
            for mod_path in hooks.builtin_modules:
                importlib.import_module(mod_path)
                logger.info("[plugin]   builtin module 已加载: {}", mod_path)
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 内置函数模块加载失败")
        assign_app(hooks.id or hooks.name, hooks.builtin_modules,
                   set(list_functions()) - before)
        logger.info("[plugin]   builtin modules: {}", len(hooks.builtin_modules))

    if hooks.profile_period_modules:
        registry.setdefault("profile_period_modules", []).extend(
            hooks.profile_period_modules
        )
        for mod_path in hooks.profile_period_modules:
            try:
                importlib.import_module(mod_path)
                logger.info("[plugin]   profile period module 已加载: {}", mod_path)
            except Exception:  # noqa: BLE001
                logger.exception("[plugin] Profile 周期模块加载失败: {}", mod_path)
                raise

    if hooks.telemetry_modules:
        registry.setdefault("telemetry_modules", []).extend(hooks.telemetry_modules)
        # 实际导入模块触发 register_schema() 注册；单个插件的 schema
        # 声明出错不该拖垮整个插件加载，仅记日志跳过。
        for mod_path in hooks.telemetry_modules:
            try:
                importlib.import_module(mod_path)
                logger.info("[plugin]   telemetry module 已加载: {}", mod_path)
            except Exception:  # noqa: BLE001
                logger.exception("[plugin] 统计事件 schema 模块加载失败: {}", mod_path)

    if hooks.telemetry_disclosures:
        registry.setdefault("telemetry_disclosures", []).extend(
            hooks.telemetry_disclosures
        )

    if hooks.config_policy_modules:
        registry.setdefault("config_policy_modules", []).extend(hooks.config_policy_modules)

    if hooks.sync_document_modules:
        registry.setdefault("sync_document_modules", []).extend(hooks.sync_document_modules)
        # 实际导入模块触发文档 kind 注册（用户级/账户级）；单个插件的
        # 声明出错不该拖垮整个插件加载，仅记日志跳过。
        for mod_path in hooks.sync_document_modules:
            try:
                importlib.import_module(mod_path)
                logger.info("[plugin]   sync document module 已加载: {}", mod_path)
            except Exception:  # noqa: BLE001
                logger.exception("[plugin] 同步文档声明模块加载失败: {}", mod_path)


def register_config_policies(hooks: AppHooks) -> None:
    """装配已知插件的配置声明；不运行启动回调或 UI 构建器。"""
    for module in hooks.config_policy_modules:
        try:
            importlib.import_module(module)
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 配置策略模块加载失败: {}", module)


def load_config_policies(app_specs: Iterable[str]) -> None:
    """只加载各已登记插件的配置策略声明，不注入 Tab / 工作流 / 识别器。

    离线工具（`scripts/add_content_version.py`、配置层单测）需要完整的
    「哪些路径是登记表 / 哪些目录带 content_version」注册表，但不需要——
    也不该——把插件的 UI 与工作流一并装配起来。走 ``register_hooks`` 会
    连带注册识别器与内置函数，对一个补字段的脚本是多余的副作用。

    参数沿用 ``-reg`` 的写法（``<name>`` 或 ``<name>@<path>``），同名插件
    只处理首次出现；单个插件声明出错只记日志跳过，理由同 register_hooks。
    """
    seen: set[str] = set()
    for spec in app_specs:
        try:
            name, _ = parse_app_spec(spec)
        except KeyError:
            logger.exception("[plugin] 解析插件参数失败: {}", spec)
            continue
        if name in seen:
            continue
        seen.add(name)
        try:
            hooks = load_app(spec)
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 加载配置声明失败: {}", name)
            continue
        register_config_policies(hooks)


# ── 全局注册表（内存中的插件扩展点汇总） ─────────────────────────────
_GLOBAL_REGISTRY: dict[str, Any] = {}


def _get_global_registry() -> dict[str, Any]:
    return _GLOBAL_REGISTRY


def get_registry() -> dict[str, Any]:
    """返回当前全局注册表（供 MainWindow / 引擎读取）。"""
    return _GLOBAL_REGISTRY


def get_registered_hooks() -> tuple[AppHooks, ...]:
    """返回已装配 app 的 ``AppHooks``，保持注册顺序。"""
    return tuple(_GLOBAL_REGISTRY.get("hooks", {}).values())


def get_registered_app_ids() -> tuple[str, ...]:
    """返回已装配 app 的稳定 ID，保持注册顺序。"""
    return tuple(str(item) for item in _GLOBAL_REGISTRY.get("app_ids", ()))
