"""自动调律应用层所需的外部能力接口。

Protocol 只描述组件实际使用的最小能力，避免把 AutoTuningWorkflow 当作
无边界的服务定位器。现有 Workflow 本身满足这些结构化接口，无需适配继承。
"""

from __future__ import annotations

from typing import Any, Protocol


class SubcallEnginePort(Protocol):
    """导航 DSL 桥所需的最小 Engine 能力。"""

    run_env: str

    def load_subcalls(self, wf_path: Any) -> None: ...

    def call_subcall(self, name: str, args: list | None = None) -> Any: ...


class RouteHostPort(Protocol):
    """平台路径适配器可使用的 Workflow 原语。"""

    TUNE_SCENE: str
    EQUIP_DETAIL: str
    @property
    def engine(self) -> SubcallEnginePort | None: ...

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

    def press(self, key: str, wait: str | None = "step_interval") -> Any: ...


class RecycleHostPort(Protocol):
    """装备回收用例所需的最小 Workflow 能力。"""

    EQUIP_DETAIL: str
    output: dict

    def ocr_scene(
        self,
        scene_key: str,
        field_keys: list[str] | None = None,
        min_confidence: float | None = None,
    ) -> dict[str, str]: ...

    def click_region(self, scene_key: str, field_key: str, **kwargs) -> Any: ...

    def wait_stable(self, timeout: float | str) -> Any: ...


class ResetHostPort(Protocol):
    """调律重置用例所需的最小 Workflow 能力。"""

    TUNE_SCENE: str

    def ocr_scene(
        self,
        scene_key: str,
        field_keys: list[str] | None = None,
        min_confidence: float | None = None,
    ) -> dict[str, str]: ...

    def click_region(self, scene_key: str, field_key: str, **kwargs) -> Any: ...

    def wait_stable(self, timeout: float | str) -> Any: ...

    def wait_delay(self, delay_name: str) -> Any: ...

    def _emit_operation(self, phase: str, message: str, **details) -> None: ...


class TuningRoundHostPort(Protocol):
    """材料读取与单轮调律执行所需的宿主能力。"""

    TUNE_SCENE: str
    RESULT_SCENE: str
    MATERIAL_PANEL: str
    MATERIAL_GROUP: str
    @property
    def base_group(self) -> Any: ...

    @property
    def engine(self) -> Any: ...

    output: dict

    def recognize_references_info_panel(
        self,
        scene_key: str,
        panel_key: str,
        group: str | list[str] | None = None,
    ) -> dict[tuple[int, int], object]: ...

    def ocr_scene(
        self,
        scene_key: str,
        field_keys: list[str] | None = None,
        min_confidence: float | None = None,
    ) -> dict[str, str]: ...

    def click_region(self, scene_key: str, field_key: str, **kwargs) -> Any: ...

    def click_panel(
        self, scene_key: str, panel_key: str, row: int, col: int
    ) -> bool: ...

    def wait_stable(self, timeout: float | str) -> Any: ...

    def wait_delay(self, delay_name: str) -> Any: ...

    def _emit_operation(self, phase: str, message: str, **details) -> None: ...
