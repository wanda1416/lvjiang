"""Versioned, strictly matched state graphs; screenshots never advance state."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any, NoReturn, TextIO

import cv2
import numpy as np


class SimulationError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}")


def confined(root: Path, name: str) -> Path:
    if not isinstance(name, str) or Path(name).is_absolute():
        raise SimulationError("invalid_fixture_path", str(name))
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise SimulationError("invalid_fixture_path", name)
    return path


def number(value: Any, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise SimulationError("invalid_number", str(value))
    if not math.isfinite(value) or value < minimum:
        raise SimulationError("invalid_number", str(value))
    return float(value)


def png_size(path: Path, sha: str) -> tuple[int, int]:
    if not path.is_file() or path.stat().st_size > 32_000_000:
        raise SimulationError("invalid_frame", path.name)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        raise SimulationError("frame_hash_mismatch", path.name)
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise SimulationError("invalid_png", path.name)
    w, h = struct.unpack(">II", data[16:24])
    if w < 1 or h < 1 or w * h > 8_294_400:
        raise SimulationError("frame_pixel_budget", path.name)
    return w, h


class Scenario:
    """Only explicit fixture state/edges are supported, not inferred branches."""

    @classmethod
    def load(cls, directory: Path) -> Scenario:
        root = directory.resolve()
        path = confined(root, "scenario.json")
        if path.stat().st_size > 2_000_000:
            raise SimulationError("fixture_metadata_budget")
        spec = json.loads(path.read_text(encoding="utf-8"))
        if type(spec.get("format_version")) is not int or spec["format_version"] != 1:
            raise SimulationError("unsupported_version")
        allowed = {
            "format_version",
            "provenance",
            "run_env",
            "workflow_bundle",
            "environment",
            "workflow",
            "workflow_sha256",
            "start",
            "states",
            "terminal",
            "edges",
            "layout",
            "params",
            "limits",
            "expected_return",
            "evidence",
        }
        if set(spec) - allowed:
            raise SimulationError(
                "unsupported_fixture_fields", str(set(spec) - allowed)
            )
        if spec.get("provenance") not in {"synthetic", "recorded"}:
            raise SimulationError("missing_provenance")
        if spec.get("run_env", "") not in {"", "desktop", "android"}:
            raise SimulationError("unsupported_runtime_environment")
        workflow = confined(root, spec["workflow"])
        if workflow.suffix != ".wf" or workflow.stat().st_size > 1_000_000:
            raise SimulationError("unsupported_workflow")
        if hashlib.sha256(workflow.read_bytes()).hexdigest() != spec["workflow_sha256"]:
            raise SimulationError("workflow_hash_mismatch")
        if "workflow_bundle" in spec:
            from .bundle import verify_bundle

            bundle_dir = confined(root, spec["workflow_bundle"])
            bundle = verify_bundle(bundle_dir)
            if bundle["files"][bundle["entry"]] != spec["workflow_sha256"]:
                raise SimulationError("bundle_entry_mismatch")
        environment = spec.get("environment", {})
        if not isinstance(environment, dict) or set(environment) - {
            "delay_params",
            "session",
            "attributes",
            "unattended",
        }:
            raise SimulationError("unsupported_environment")
        if any(
            not isinstance(environment.get(key, {}), dict)
            for key in ("delay_params", "session", "attributes")
        ) or not isinstance(environment.get("unattended", True), bool):
            raise SimulationError("invalid_environment_type")
        if any(
            not isinstance(value, dict)
            for value in environment.get("attributes", {}).values()
        ):
            raise SimulationError("invalid_attribute_snapshot")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in environment.get("delay_params", {}).values()
        ):
            raise SimulationError("invalid_delay_parameters")
        states, edges = spec["states"], spec["edges"]
        if not isinstance(states, dict) or not states or len(states) > 10_000:
            raise SimulationError("invalid_states")
        if not isinstance(edges, list) or len(edges) > 20_000:
            raise SimulationError("invalid_edges")
        if (
            not isinstance(spec.get("terminal"), list)
            or not spec["terminal"]
            or spec["start"] not in states
            or set(spec["terminal"]) - states.keys()
        ):
            raise SimulationError("invalid_state_reference")
        sizes = {}
        total_frame_bytes = 0
        timers: dict[str, int] = {}
        for edge in edges:
            if edge.get("action") == "timer":
                timers[edge["source"]] = timers.get(edge["source"], 0) + 1
        for key, frame in states.items():
            if not isinstance(key, str) or not 0 < len(key) <= 80:
                raise SimulationError("invalid_state_id")
            frame_path = confined(root, frame["frame"])
            total_frame_bytes += frame_path.stat().st_size
            if total_frame_bytes > 512_000_000:
                raise SimulationError("fixture_frame_budget")
            sizes[key] = png_size(frame_path, frame["sha256"])
        for edge in edges:
            if edge["source"] not in states or edge["target"] not in states:
                raise SimulationError("invalid_edge_reference")
            if edge["action"] not in {
                "timer",
                "click",
                "drag",
                "move",
                "scroll",
                "key_down",
                "key_up",
                "mouse_button",
            }:
                raise SimulationError("unsupported_action", edge["action"])
            permitted = (
                {"source", "target", "action", "after"}
                if edge["action"] == "timer"
                else {
                    "source",
                    "target",
                    "action",
                    "match",
                    "min_elapsed",
                    "max_elapsed",
                }
            )
            if set(edge) - permitted:
                raise SimulationError("unsupported_edge_fields")
            if edge["action"] == "timer":
                if number(edge["after"]) <= 0:
                    raise SimulationError("zero_timer")
                if timers[edge["source"]] != 1:
                    raise SimulationError("ambiguous_timer")
                continue
            low = number(edge.get("min_elapsed", 0))
            high = number(edge.get("max_elapsed", 1e12))
            if low > high:
                raise SimulationError("invalid_edge_window")
            match = edge.get("match", {})
            keys = {
                "click": {"box", "button", "hold"},
                "drag": {"from_box", "to_box", "duration", "hold"},
                "move": {"box", "duration"},
                "scroll": {"box", "direction", "amount", "interval"},
                "key_down": {"key"},
                "key_up": {"key"},
                "mouse_button": {"button", "pressed"},
            }[edge["action"]]
            if not isinstance(match, dict) or set(match) - keys:
                raise SimulationError("unsupported_match_fields")
            required = {
                "click": {"box", "button"},
                "drag": {"from_box", "to_box"},
                "move": {"box"},
                "scroll": {"box", "direction", "amount"},
                "key_down": {"key"},
                "key_up": {"key"},
                "mouse_button": {"button", "pressed"},
            }[edge["action"]]
            if not required <= match.keys():
                raise SimulationError("missing_match_fields")
            for field in ("duration", "hold", "interval"):
                value = match.get(field)
                if value is None:
                    continue
                if isinstance(value, list):
                    if len(value) != 2 or number(value[0]) > number(value[1]):
                        raise SimulationError("invalid_duration_range")
                else:
                    number(value)
            if "button" in match and match["button"] not in {
                "left",
                "right",
                "middle",
                "x1",
                "x2",
            }:
                raise SimulationError("invalid_button")
            if "pressed" in match and type(match["pressed"]) is not bool:
                raise SimulationError("invalid_pressed")
            if "key" in match and (
                not isinstance(match["key"], str) or not 0 < len(match["key"]) <= 32
            ):
                raise SimulationError("invalid_key")
            if "direction" in match and match["direction"] not in {"up", "down"}:
                raise SimulationError("invalid_scroll_direction")
            if "amount" in match and (
                type(match["amount"]) is not int or match["amount"] < 1
            ):
                raise SimulationError("invalid_scroll_amount")
            for field in ("box", "from_box", "to_box"):
                if field in match:
                    box = match[field]
                    if not isinstance(box, list) or len(box) != 4:
                        raise SimulationError("invalid_action_box")
                    a, b, c, d = (number(n) for n in box)
                    if (
                        a > c
                        or b > d
                        or c >= sizes[edge["source"]][0]
                        or d >= sizes[edge["source"]][1]
                    ):
                        raise SimulationError("invalid_action_box")
        limits = spec.get("limits", {})
        number(limits.get("virtual_seconds", 300), minimum=0.001)
        n = limits.get("events", 10_000)
        if type(n) is not int or not 1 <= n <= 1_000_000:
            raise SimulationError("invalid_event_budget")
        obj = cls()
        obj.root, obj.spec, obj.workflow, obj.sizes = root, spec, workflow, sizes
        obj.edge_indices = {id(edge): n for n, edge in enumerate(edges)}
        return obj


class SimulatedDevice:
    """Duck-typed capture/input backend, no OS device objects or global clock patch."""

    min_capture_interval = 0.0

    def __init__(self, scenario: Scenario, trace: TextIO | None = None):
        self.scenario = scenario
        self.state = scenario.spec["start"]
        self.time = self.entered = 0.0
        self.events = 0
        self.visited_states = {self.state}
        self.visited_edges: set[int] = set()
        self.failure: SimulationError | None = None
        self.stopped = False
        self.trace = trace
        self.stop_check = lambda: False

    def fail(self, code: str, detail: str = "") -> NoReturn:
        self.failure = self.failure or SimulationError(code, detail)
        raise self.failure

    def event(self, kind: str, **fields: Any) -> None:
        if self.failure:
            raise self.failure
        if self.stopped or self.stop_check():
            self.fail("stopped")
        self.events += 1
        if self.events > self.scenario.spec.get("limits", {}).get("events", 10_000):
            self.fail("event_budget")
        if self.trace is not None:
            self.trace.write(
                json.dumps(
                    {
                        "event": self.events,
                        "time": self.time,
                        "state": self.state,
                        "kind": kind,
                        **fields,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    def capture(self) -> np.ndarray:
        self.event("capture")
        entry = self.scenario.spec["states"][self.state]
        path = confined(self.scenario.root, entry["frame"])
        try:
            png_size(path, entry["sha256"])
            image = cv2.imdecode(
                np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR
            )
        except (SimulationError, OSError) as exc:
            self.fail("frame_changed_or_missing", type(exc).__name__)
        if image is None:
            self.fail("invalid_frame", self.state)
        return image

    def capture_lossless(self, *, timeout: float = 5) -> np.ndarray:
        return self.capture()

    def get_capture_size(self) -> tuple[int, int]:
        return self.scenario.sizes[self.state]

    def monotonic(self) -> float:
        return self.time

    def sleep(self, seconds: float) -> None:
        seconds = number(seconds)
        self.event("wait", seconds=seconds)
        end = self.time + seconds
        if end > self.scenario.spec.get("limits", {}).get("virtual_seconds", 300):
            self.fail("virtual_timeout")
        while True:
            timers = [
                e
                for e in self.scenario.spec["edges"]
                if e["source"] == self.state and e["action"] == "timer"
            ]
            if not timers or self.entered + timers[0]["after"] > end + 1e-12:
                break
            edge = timers[0]
            self.time = self.entered + edge["after"]
            self._enter(edge)
        self.time = end

    def _enter(self, edge: dict) -> None:
        index = self.scenario.edge_indices[id(edge)]
        self.event(
            "transition", target=edge["target"], action=edge["action"], edge=index
        )
        self.visited_edges.add(index)
        self.state, self.entered = edge["target"], self.time
        self.visited_states.add(self.state)

    @staticmethod
    def _matches(match: dict, args: dict) -> bool:
        for key, value in match.items():
            if key in {"box", "from_box", "to_box"}:
                xy = args.get(
                    {"box": "xy", "from_box": "from_xy", "to_box": "to_xy"}[key]
                )
                if xy is None or not (
                    value[0] <= xy[0] <= value[2] and value[1] <= xy[1] <= value[3]
                ):
                    return False
            elif key in {"duration", "hold", "interval"} and isinstance(value, list):
                if args.get(key) is None or not value[0] <= args[key] <= value[1]:
                    return False
            elif args.get(key) != value:
                return False
        return True

    def act(self, action: str, **args: Any) -> None:
        self.event("input", action=action, args=args)
        duration_keys = {
            "click": ("hold",),
            "drag": ("duration", "hold"),
            "move": ("duration",),
            "scroll": ("interval",),
        }.get(action, ())
        edges = [
            e
            for e in self.scenario.spec["edges"]
            if e["source"] == self.state
            and e["action"] == action
            and e.get("min_elapsed", 0)
            <= self.time - self.entered
            <= e.get("max_elapsed", 1e12)
            and self._matches(
                {**dict.fromkeys(duration_keys), **e.get("match", {})}, args
            )
        ]
        if len(edges) != 1:
            self.fail(
                "ambiguous_transition" if edges else "unsupported_transition", action
            )
        self._enter(edges[0])

    def click_screen(
        self,
        x: int,
        y: int,
        poi_name: str = "",
        *,
        button: str = "left",
        hold: float | None = None,
        **kwargs: Any,
    ) -> None:
        if set(kwargs) - {"pre_delay", "post_delay", "random_offset"}:
            self.fail("unsupported_input_options")
        self._input_delay(kwargs.get("pre_delay"))
        self.act("click", xy=(x, y), button=button, hold=hold)
        if hold:
            self.sleep(hold)
        self._input_delay(kwargs.get("post_delay"))

    def drag_screen(
        self,
        x: int,
        y: int,
        tx: int,
        ty: int,
        poi_name: str = "",
        *,
        duration: float | None = None,
        hold: float | None = None,
        **kwargs: Any,
    ) -> None:
        if set(kwargs) - {"pre_delay", "post_delay"}:
            self.fail("unsupported_input_options")
        self._input_delay(kwargs.get("pre_delay"))
        self.act("drag", from_xy=(x, y), to_xy=(tx, ty), duration=duration, hold=hold)
        if hold:
            self.sleep(hold)
        if duration:
            self.sleep(duration)
        self._input_delay(kwargs.get("post_delay"))

    def move_screen(
        self, x: int, y: int, poi_name: str = "", duration: float | None = None
    ) -> None:
        self.act("move", xy=(x, y), duration=duration)
        if duration:
            self.sleep(duration)

    def scroll_screen(
        self,
        x: int,
        y: int,
        direction: str = "down",
        amount: int = 1,
        poi_name: str = "",
        *,
        interval: float | None = None,
    ) -> None:
        self.act(
            "scroll", xy=(x, y), direction=direction, amount=amount, interval=interval
        )
        if interval is not None:
            self.sleep(max(0, amount - 1) * interval)

    def _input_delay(self, value: Any) -> None:
        if value is None:
            return  # Simulator declares zero implicit backend delay.
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            self.fail("unsupported_input_delay")
        lo, hi = number(value[0]), number(value[1])
        if lo > hi:
            self.fail("invalid_input_delay")
        self.sleep((lo + hi) / 2)

    def move_relative(self, *args: Any, **kwargs: Any) -> None:
        self.fail("unsupported_capability", "relative pointer motion")

    def key_down(self, key: str) -> None:
        self.act("key_down", key=key)

    def key_up(self, key: str) -> None:
        self.act("key_up", key=key)

    def mouse_button(self, button: str, pressed: bool) -> None:
        self.act("mouse_button", button=button, pressed=pressed)

    def assert_finished(self) -> None:
        if self.failure:
            raise self.failure
        if self.stopped:
            self.fail("stopped")
        if self.state not in self.scenario.spec["terminal"]:
            self.fail("nonterminal_state", self.state)
