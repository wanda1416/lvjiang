"""插件注册表与加载器。

插件通过命令行参数 ``-reg <spec>`` 注册，``<spec>`` 是 ``<name>`` 或
``<name>@<path>``。引擎在启动时按顺序调用 ``load_app(spec)`` 加载插件模块，
并从模块顶层 ``hooks`` 属性获取 ``AppHooks`` 实例，再通过 ``register_hooks``
把识别器 / 工作流 / 内置函数注入到各注册表中。

插件分三级，宿主主动发现前两级：

1. 内置插件：``src/lvjiang/apps/<name>/``，随本包分发，public 打包自动携带；
2. 外置默认目录：``<PROJECT_ROOT>/plugins/<name>/``，打包按目录是否存在决定
   是否携带（public 仓库没有该目录，因此发布包不含外置插件）；
3. 外置任意目录：``-reg <name>@<path>`` 显式声明，目录名不必等于插件名。

插件以「子目录 + ``__init__.py``」识别，不读取任何清单文件；目录名必须是合法
Python 标识符，否则记 warning 跳过，不影响其它插件与主程序启动。
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..constants import PROJECT_ROOT
from .base import AppHooks

logger = logging.getLogger(__name__)

# 内置插件根：本包目录下的子目录。
_BUILTIN_ROOT = Path(__file__).parent
# 外置默认插件根：仓库根 / 安装目录旁的 plugins/。
_DEFAULT_PLUGINS_ROOT = PROJECT_ROOT / "plugins"

if _DEFAULT_PLUGINS_ROOT.is_dir() and str(_DEFAULT_PLUGINS_ROOT) not in __path__:
    # 目录名等于插件名时，靠包搜索路径即可解析 lvjiang.apps.<name>；
    # 名称与目录名不一致的任意目录走 load_app 里的显式挂载。
    __path__ = [*__path__, str(_DEFAULT_PLUGINS_ROOT)]


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
            logger.warning("[plugin] 忽略非法插件目录名: %s", entry)
            continue
        names.append(entry.name)
    return tuple(names)


def builtin_app_ids() -> tuple[str, ...]:
    """随本包分发的内置插件名册（只扫内置根，不扫外置目录）。

    离线工具（``scripts/add_content_version.py``、配置层单测）靠它选择插件
    声明，因此必须保持「与安装目录内容无关」的语义。
    """
    return _scan_root(_BUILTIN_ROOT)


def discover_app_ids() -> tuple[str, ...]:
    """内置根与外置默认目录的插件名册，内置优先且各按名排序、去重。"""
    ordered: dict[str, None] = {}
    for name in (*_scan_root(_BUILTIN_ROOT), *_scan_root(_DEFAULT_PLUGINS_ROOT)):
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
    module_path = f"lvjiang.apps.{name}"
    if path is not None:
        _mount_app(name, path)
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        if exc.name == module_path:
            raise KeyError(f"插件 {name!r} 未安装") from exc
        logger.exception("加载插件 %s 失败", module_path)
        raise RuntimeError(f"加载插件 {name!r} 失败: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("加载插件 %s 失败", module_path)
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
    module_path = f"lvjiang.apps.{name}"
    if module_path in sys.modules:
        return
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
    parent = sys.modules.get("lvjiang.apps")
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

    logger.info("[plugin] 注册插件: %s", hooks.name or "<unnamed>")

    for callback in hooks.startup_callbacks:
        try:
            callback()
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 启动初始化失败: %s", hooks.id or hooks.name)

    if hooks.id:
        app_ids = registry.setdefault("app_ids", [])
        if hooks.id not in app_ids:
            app_ids.append(hooks.id)

    if hooks.window_title:
        registry["window_title"] = hooks.window_title
        logger.info("[plugin]   window_title = %s", hooks.window_title)

    if hooks.left_tab_builders:
        registry.setdefault("left_tab_builders", []).extend(hooks.left_tab_builders)
        logger.info("[plugin]   left tabs: %s", [label for label, _ in hooks.left_tab_builders])

    if hooks.right_tab_builders:
        registry.setdefault("right_tab_builders", []).extend(hooks.right_tab_builders)
        logger.info("[plugin]   right tabs: %s", [label for label, _ in hooks.right_tab_builders])

    if hooks.menu_builders:
        registry.setdefault("menu_builders", []).extend(hooks.menu_builders)
        logger.info("[plugin]   menu builders: %d", len(hooks.menu_builders))

    if hooks.theme_stylesheet_builders:
        registry.setdefault("theme_stylesheet_builders", []).extend(
            hooks.theme_stylesheet_builders
        )

    if hooks.recognizer_classes:
        registry.setdefault("recognizer_classes", []).extend(hooks.recognizer_classes)
        # 实际注入到识别器注册表
        try:
            from ..core.recognizers import register_recognizer
            for cls in hooks.recognizer_classes:
                register_recognizer(cls)
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 识别器注册失败")
        logger.info("[plugin]   recognizers: %d", len(hooks.recognizer_classes))

    if hooks.workflow_implementations:
        registry.setdefault("workflow_implementations", {}).update(hooks.workflow_implementations)
        # 实际注入到工作流注册表
        try:
            from ..workflows.implementations import register_workflow
            for name, cls_path in hooks.workflow_implementations.items():
                register_workflow(name, cls_path)
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 工作流注册失败")
        logger.info("[plugin]   workflows: %s", list(hooks.workflow_implementations.keys()))

    if hooks.builtin_modules:
        registry.setdefault("builtin_modules", []).extend(hooks.builtin_modules)
        # 实际导入模块触发 @builtin_func 装饰器注册
        try:
            for mod_path in hooks.builtin_modules:
                importlib.import_module(mod_path)
                logger.info("[plugin]   builtin module 已加载: %s", mod_path)
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 内置函数模块加载失败")
        logger.info("[plugin]   builtin modules: %d", len(hooks.builtin_modules))

    if hooks.profile_period_modules:
        registry.setdefault("profile_period_modules", []).extend(
            hooks.profile_period_modules
        )
        for mod_path in hooks.profile_period_modules:
            try:
                importlib.import_module(mod_path)
                logger.info("[plugin]   profile period module 已加载: %s", mod_path)
            except Exception:  # noqa: BLE001
                logger.exception("[plugin] Profile 周期模块加载失败: %s", mod_path)
                raise

    if hooks.telemetry_modules:
        registry.setdefault("telemetry_modules", []).extend(hooks.telemetry_modules)
        # 实际导入模块触发 register_schema() 注册；单个插件的 schema
        # 声明出错不该拖垮整个插件加载，仅记日志跳过。
        for mod_path in hooks.telemetry_modules:
            try:
                importlib.import_module(mod_path)
                logger.info("[plugin]   telemetry module 已加载: %s", mod_path)
            except Exception:  # noqa: BLE001
                logger.exception("[plugin] 统计事件 schema 模块加载失败: %s", mod_path)

    if hooks.telemetry_disclosures:
        registry.setdefault("telemetry_disclosures", []).extend(
            hooks.telemetry_disclosures
        )

    if hooks.config_policy_modules:
        registry.setdefault("config_policy_modules", []).extend(hooks.config_policy_modules)


def register_config_policies(hooks: AppHooks) -> None:
    """装配已知插件的配置声明；不运行启动回调或 UI 构建器。"""
    for module in hooks.config_policy_modules:
        try:
            importlib.import_module(module)
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 配置策略模块加载失败: %s", module)


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
            logger.exception("[plugin] 解析插件参数失败: %s", spec)
            continue
        if name in seen:
            continue
        seen.add(name)
        try:
            hooks = load_app(spec)
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 加载配置声明失败: %s", name)
            continue
        register_config_policies(hooks)


# ── 全局注册表（内存中的插件扩展点汇总） ─────────────────────────────
_GLOBAL_REGISTRY: dict[str, Any] = {}


def _get_global_registry() -> dict[str, Any]:
    return _GLOBAL_REGISTRY


def get_registry() -> dict[str, Any]:
    """返回当前全局注册表（供 MainWindow / 引擎读取）。"""
    return _GLOBAL_REGISTRY


def get_registered_app_ids() -> tuple[str, ...]:
    """返回已装配 app 的稳定 ID，保持注册顺序。"""
    return tuple(str(item) for item in _GLOBAL_REGISTRY.get("app_ids", ()))
