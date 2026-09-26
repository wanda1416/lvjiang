"""工作流实现可依赖的通用结构化能力接口。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from lvjiang.core.layout_models import Region


class SubcallEnginePort(Protocol):
    """调用已加载 DSL 子流程所需的最小引擎能力。"""

    run_env: str

    def load_subcalls(self, wf_path: Any) -> None: ...

    def call_subcall(self, name: str, args: list | None = None) -> Any: ...


class SceneAutomationPort(Protocol):
    """场景型工作流常用的输入、等待和识别能力。"""

    def click_region(self, scene_key: str, field_key: str, **kwargs) -> Any: ...

    def wait_stable(self, timeout: float | str) -> Any: ...

    def wait_delay(self, delay_name: str) -> Any: ...

    def ocr_scene_by(
        self,
        scene_key: str,
        field_keys: list[str],
        target_value: Any,
        mode: str,
        min_confidence: float | None = None,
    ) -> str: ...

    def ocr_scene(
        self,
        scene_key: str,
        field_keys: list[str] | None = None,
        min_confidence: float | None = None,
        regions_override: list[Region] | None = None,
        cleaning_group: str | None = None,
    ) -> dict[str, str]: ...

    def press(self, key: str, wait: str | None = "step_interval") -> Any: ...
