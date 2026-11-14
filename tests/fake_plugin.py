"""框架测试用的最小假插件。

框架（core / ui / workflows）测试验证的是「插件通过 hook 声明 → 宿主消费」这套
机制，不应该借某个真实插件当载体：那样一旦插件改名、拆出或没随包分发，框架测试
就跟着坏，而且测试代码本身就在让框架「认识」插件。这里提供一个与任何真实插件都
无关的假插件 ``demo_app``：

- :func:`fake_hooks`：按需填写字段的 ``AppHooks``，``id``/``name`` 已预置；
- :func:`use_registered_hooks`：把「已装配插件」替换成给定的 hooks 序列，覆盖
  ``lvjiang.core.plugins.get_registered_hooks`` 以及所有 ``from ...plugins import
  get_registered_hooks`` 绑定了本地名的宿主模块（不用逐个记住模块名）；
- :func:`install_fake_app`：把假插件模块挂进 ``sys.modules``，让 ``load_app`` /
  ``ensure_loaded`` 能按名加载；
- :func:`write_fake_app_package`：在磁盘上生成一个插件包目录，供子进程或
  ``-reg name@path`` 路径加载。

真实插件自己的注册行为（它声明了哪些路径、通道、工作流）在各插件自己的测试目录
里验证，例如 ``tests/yysls``。
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from lvjiang.core import plugins
from lvjiang.core.plugins import AppHooks

#: 假插件的插件 ID；刻意不与任何真实插件重名。
FAKE_APP_ID = "demo_app"
FAKE_APP_NAME = "示例插件"


def fake_hooks(**fields: Any) -> AppHooks:
    """构造假插件声明；未给出的字段保持 ``AppHooks`` 默认值。"""
    fields.setdefault("id", FAKE_APP_ID)
    fields.setdefault("name", FAKE_APP_NAME)
    return AppHooks(**fields)


def use_registered_hooks(monkeypatch: pytest.MonkeyPatch, *hooks: AppHooks) -> None:
    """让宿主把 ``hooks`` 视为当前已装配的全部插件。"""
    original = plugins.get_registered_hooks

    def registered() -> tuple[AppHooks, ...]:
        return tuple(hooks)

    monkeypatch.setattr(plugins, "get_registered_hooks", registered)
    for name, module in list(sys.modules.items()):
        if (name.startswith("lvjiang.") and module is not None
                and getattr(module, "get_registered_hooks", None) is original):
            monkeypatch.setattr(module, "get_registered_hooks", registered)


def install_fake_app(
    monkeypatch: pytest.MonkeyPatch, hooks: AppHooks | None = None,
    name: str = FAKE_APP_ID, **attributes: Any,
) -> ModuleType:
    """把 ``lvjiang.apps.<name>`` 换成只导出 ``hooks`` 的内存模块。"""
    module = ModuleType(f"{plugins.APPS_PACKAGE}.{name}")
    module.hooks = hooks if hooks is not None else fake_hooks(id=name)  # type: ignore[attr-defined]
    for key, value in attributes.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


def write_fake_app_package(root: Path, name: str = FAKE_APP_ID, body: str = "") -> Path:
    """在 ``root/name`` 写一个可被 ``-reg name@path`` 挂载的假插件包。

    ``body`` 是包 ``__init__`` 的额外源码；没有自行定义 ``hooks`` 时补一个只带
    ``id``/``name`` 的默认声明。
    """
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    source = "from lvjiang.core.plugins import AppHooks\n\n" + textwrap.dedent(body).strip() + "\n"
    if "hooks =" not in source:
        source += f"hooks = AppHooks(id={name!r}, name={name!r})\n"
    (directory / "__init__.py").write_text(source, encoding="utf-8")
    return directory


@pytest.fixture
def fake_app(monkeypatch: pytest.MonkeyPatch):
    """注册单个假插件的快捷夹具：``fake_app(**fields)`` 返回已生效的 hooks。"""

    def register(**fields: Any) -> AppHooks:
        hooks = fake_hooks(**fields)
        use_registered_hooks(monkeypatch, hooks)
        return hooks

    return register
