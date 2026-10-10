"""Bounded, authorized YYSLS operations for external Agents."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml
from fasteners import InterProcessLock

from ....._version import __version__
from .....core.config.document_store import DocumentDirectoryStore
from .....core.config.resolver import ConfigResolver
from ..loadout import LoadoutRepository
from ..tuning_rules import parse_tuning_group, parse_tuning_rule


def revision(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False,
    ).encode("utf-8")).hexdigest()


class AgentService:
    def __init__(self, root: Path, *, dispatch: Callable, game_config=None):
        self.root = root
        self.resolver = ConfigResolver(
            root / "config/system", root / "config/local", dev_mode=False,
            remote_dir=root / "config/remote",
        )
        self.users_dir = root / "config/session/users"
        self.store = DocumentDirectoryStore(
            root / "config/session/agent", {"results": "results.json", "tasks": "tasks.json"}, label="Agent 生成结果",
        )
        self.dispatch = dispatch
        self._game_config = game_config
        self._lock = threading.RLock()
        self.enabled = False
        self._jobs: dict[str, dict] = {}

    @property
    def game_config(self):
        if self._game_config is None:
            from ...config import get_game_config
            return get_game_config()
        return self._game_config

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise PermissionError("MCP 服务已关闭，请在智能调律页启动 MCP 服务")

    def list_users(self) -> dict:
        """读取当前用户和全部用户；切换用户不影响连接，无需重新导出配置。"""
        self._ensure_enabled()
        return self.dispatch("context", {})

    def _entity(self, path: str) -> dict:
        source = self.resolver.resolve_read(path)
        if source is None:
            raise ValueError("配置不存在")
        value = yaml.safe_load(source.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("配置格式无效")
        return value

    def _check(self, user: str) -> None:
        from .....core.user_config import is_valid_username
        self._ensure_enabled()
        if not is_valid_username(user) or user not in self.list_users()["users"]:
            raise ValueError("用户不存在，请调用 list_users 获取当前用户与全部用户")

    def get_capabilities(self) -> dict:
        """先读取动态用户/目标和功能状态；开启服务即开放现有 MCP 能力。"""
        context = self.list_users()
        return {"version": __version__, "api_version": 2, **context,
                **self.list_targets(),
                "scan_coverage": "visible_game_plan_grid",
                "configuration_policy": "create_new_local_only",
                "cultivation_policy": "analysis_only",
                "instructions": "先读 entry 文档；当前用户随主界面变化，所有用户均可指定。毕业率分析先调用 list_plans 取得 plan_id，只读分析不要求设备空闲或调律配置结果 ID。设备检查和结果 ID 属于扫描/调律执行流程。功能不可用时按接口原因告知用户。"}

    def list_targets(self) -> dict:
        """列出本实例全部窗口/设备、当前选中目标及可执行状态。"""
        self._ensure_enabled()
        return self.dispatch("targets", {})

    def _resolve_target(self, target_id: str) -> str:
        return str(self.dispatch("resolve_target", {"target_id": target_id})["target_id"])

    def list_plans(self, user: str) -> dict:
        """查询指定用户备战方案及 plan_id，供详情和分析接口使用；不要求设备空闲。"""
        self._check(user)
        state = LoadoutRepository(user, self.users_dir).load()
        return {"revision": revision(state.to_dict()),
                "plans": [{"plan_id": p.id, **p.to_dict()} for p in state.plans.values()]}

    def query_equipment(self, user: str, offset: int = 0, limit: int = 30) -> dict:
        """分页查询装备；is_mock 表示模拟装备，不得当作游戏真实产出。"""
        self._check(user)
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("分页范围无效，limit 为 1～100")
        state = LoadoutRepository(user, self.users_dir).load()
        items = list(state.equipment_items.items())
        return {"total": len(items), "revision": revision(state.to_dict()),
                "items": [{"fingerprint": key, **value} for key, value in items[offset:offset + limit]]}

    def get_plan_context(self, user: str, plan_id: str) -> dict:
        """读取方案装备、完整性及对应公共文档引用；不会应用或改写方案。"""
        self._check(user)
        state = LoadoutRepository(user, self.users_dir).load()
        plan = state.plans.get(plan_id)
        if plan is None:
            raise ValueError("备战方案不存在")
        from ...config.play_styles import get_play_styles
        from ..loadout.models import resolve_school
        school = resolve_school(plan.main_martial_art, plan.sub_martial_art, self.game_config.get_schools())
        return {"plan": {"plan_id": plan.id, **plan.to_dict()}, "equipment": state.resolved_equipment(plan_id),
                "base_attributes": get_play_styles(school).get(plan.base_attribute, {}) if school else {},
                "revision": revision(state.to_dict()),
                "documents": ["equipment", "schools", "damage", "analysis", "generation"]}

    def get_game_config(self, section: str = "playstyles") -> dict:
        """按主题读实际生效的游戏配置；不开放 app、场景、布局或任意路径。"""
        from ...config.game_config_files import GAME_CONFIG_SECTION_FILES
        self._ensure_enabled()
        path = GAME_CONFIG_SECTION_FILES.get(section)
        if path is None:
            raise ValueError("不支持该公共配置主题")
        data = self.resolver.load_merged(path)
        return {"section": section, "value": data.get(section), "revision": revision(data)}

    def list_graduation_schemes(self) -> dict:
        """列出实际可用毕业率模型；没有适用模型时不得编造毕业率。"""
        from ..graduation.model_registry import list_graduation_models
        self._ensure_enabled()
        return {"models": [{k: v for k, v in asdict(model).items() if k != "rel_path"}
                           for model in list_graduation_models()]}

    def get_tuning_config(self, user: str) -> dict:
        """读用户选择和已有规则/基础组，仅作为生成新定义的参考。"""
        from ...config.auto_tuning_config import load_user_auto_tuning_config
        self._check(user)
        definitions = {}
        for directory in ("base_groups", "tuning_rules"):
            definitions[directory] = [
                self._entity(f"yysls/{directory}/{name}")
                for name in self.resolver.enumerate_entities(f"yysls/{directory}", "*.yaml")
            ]
        config = load_user_auto_tuning_config(user, self.users_dir)
        return {"user_config": config, "revision": revision(config),
                **definitions,
                "tune_config": self.resolver.load_merged("yysls/tune_config.yaml")}

    def update_tuning_config(self, user: str, expected_revision: str, patch: dict) -> dict:
        """显式保存用户调律选择；字段级合并，不改共享定义或其他工作流，陈旧修订拒绝。"""
        from .....core.user_config import mutate_user_metadata
        from ...config.auto_tuning_config import default_auto_tuning_config
        from .launch import prepare_tuning
        self._check(user)
        allowed = set(default_auto_tuning_config()) - {"skip_tuning"}
        if set(patch) - allowed:
            raise ValueError("设置补丁包含不开放的字段")

        def mutate(metadata):
            current = default_auto_tuning_config()
            current.update(metadata.workflow_params.get("auto_tuning", {}))
            if revision(current) != expected_revision:
                raise ValueError("用户调律设置已改变，请重新读取后修改")
            for key, value in patch.items():
                if key in {"rules", "switches", "smart_tuning_enabled"}:
                    if not isinstance(value, dict):
                        raise ValueError("选择字段必须是对象")
                    for item, selection in value.items():
                        if key == "rules":
                            if not isinstance(selection, dict) or set(selection) - {"enabled", "playstyles"}:
                                raise ValueError("规则选择字段无效")
                            current[key][item] = {**current[key].get(item, {}), **selection}
                        else:
                            current[key][item] = selection
                else:
                    current[key] = value
            prepare_tuning(current)
            self._check(user)
            metadata.workflow_params["auto_tuning"] = current
        saved = mutate_user_metadata(user, mutate, self.users_dir).workflow_params["auto_tuning"]
        return {"config": saved, "revision": revision(saved)}

    def _plan(self, user: str, plan_id: str, overrides: dict):
        from ..graduation.context import PlanScoringContext
        self._check(user)
        state = LoadoutRepository(user, self.users_dir).load()
        if plan_id not in state.plans:
            raise ValueError("备战方案不存在")
        plan = copy.deepcopy(state.plans[plan_id])
        if set(overrides) - {"playstyle", "graduation_scheme", "base_attribute"}:
            raise ValueError("分析覆盖字段无效")
        if "base_attribute" in overrides and overrides["base_attribute"] not in {
                p.base_attribute for p in state.plans.values()}:
            raise PermissionError("基础属性不属于该用户方案引用范围")
        for key, value in overrides.items():
            if not isinstance(value, str):
                raise ValueError("分析选择必须是有效名称")
            setattr(plan, key, value)
        if plan.playstyle:
            if plan.playstyle not in self.game_config.get_playstyles_for_arts(
                    [plan.main_martial_art, plan.sub_martial_art]):
                raise ValueError("目标玩法与两门武学不匹配")
        context = PlanScoringContext.from_plan(plan, game_config=self.game_config,
                                              world_level=state.world_level or None)
        return state, plan, context

    def _job(self, user: str, function: Callable) -> dict:
        job_id = uuid.uuid4().hex
        cancelled = threading.Event()
        record = {"user": user, "state": "running", "cancelled": cancelled}
        with self._lock:
            if len(self._jobs) >= 100:
                finished = [key for key, value in self._jobs.items() if value["state"] != "running"]
                if not finished:
                    raise ValueError("计算任务过多，请先停止或等待已有任务")
                self._jobs.pop(finished[0])
            self._jobs[job_id] = record

        def run():
            try:
                result = function(cancelled.is_set)
                result = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False,
                                               default=lambda value: list(value) if isinstance(value, (set, frozenset)) else str(value)))
                self._check(user)
                record.update(state="cancelled" if cancelled.is_set() else "completed", result=result)
            except Exception as exc:  # noqa: BLE001 - per-job failure isolation
                record.update(state="failed", error=str(exc))
        threading.Thread(target=run, daemon=True, name="agent-analysis").start()
        return {"job_id": job_id, "state": "running"}

    def analyze_cultivation(self, user: str, plan_id: str, overrides: dict | None = None) -> dict:
        """用 list_plans 返回的 plan_id 异步分析毕业率和培养潜力；不写回、不操作游戏，无需设备空闲或调律配置结果 ID。"""
        from ..graduation.affix_impact import analyze_affix_impacts
        from ..graduation.assumptions import Assumptions
        from ..graduation.transmute_optimizer import (
            TransmuteSearchRequest,
            optimize_transmutes,
        )
        state, plan, context = self._plan(user, plan_id, overrides or {})
        equipped = state.resolved_equipment(plan_id)
        if len(equipped) != 8:
            raise ValueError("请先扫描完整的八件备战装备")
        gc = self.game_config

        def calculate(cancel):
            scorer = context.scorer(game_config=gc, stop_check=cancel)
            assumptions = Assumptions(full_level=context.world_level, full_chengyin=True,
                                      full_dingyin=True, playstyle=plan.playstyle)
            return {"plan_id": plan_id, "revision": revision(state.to_dict()),
                    "current_rate": scorer.rate(equipped),
                    "potential_rate": scorer.rate(assumptions.project(equipped, gc)),
                    "assumptions": asdict(assumptions),
                    "affix_impacts": asdict(analyze_affix_impacts(
                        equipped, context.calculator, context.base_attrs, context.school,
                        game_config=gc, graduation_context=context.attr_context)),
                    "transmute": asdict(optimize_transmutes(TransmuteSearchRequest(
                        equipped, context.calculator, context.base_attrs, context.school, gc,
                        graduation_context=context.attr_context, playstyle=plan.playstyle,
                        stop_check=cancel))),
                    "policy": "建议不自动执行；潜力依赖所列培养假设，不代表概率或即时收益。"}
        return self._job(user, calculate)

    def search_best_combo(self, user: str, plan_id: str, overrides: dict | None = None) -> dict:
        """异步搜索指定用户装备库的真实组合；排除模拟装备，不应用结果。"""
        from ..combat.equipment import EquipmentInventory
        from ..graduation.candidate_pool import CandidateFilter, collect_candidates
        from ..graduation.optimal_combo import search_optimal_combo
        state, plan, context = self._plan(user, plan_id, overrides or {})
        # Candidate selection is delegated to the same pool used by the UI.
        inventory = EquipmentInventory(user, users_dir=self.users_dir, state=state)
        candidates = collect_candidates(
            inventory.bag_items, None,
            main_weapon_type=self.game_config.get_martial_art_weapon(plan.main_martial_art),
            sub_weapon_type=self.game_config.get_martial_art_weapon(plan.sub_martial_art),
            filters=CandidateFilter(),
        )
        def calculate(cancel):
            import time
            deadline = time.monotonic() + 10
            def stopped():
                return cancel() or time.monotonic() >= deadline
            results = search_optimal_combo(
                candidates, context.calculator, context.base_attrs,
                graduation_context=context.attr_context,
                cancel_flag=stopped, playstyle=plan.playstyle, max_per_slot=30,
            )
            return {"plan_id": plan_id, "revision": revision(state.to_dict()),
                    "results": results, "search_complete": not stopped(),
                    "candidate_limit_per_slot": 30}
        return self._job(user, calculate)

    def get_analysis_job(self, user: str, job_id: str, cancel: bool = False) -> dict:
        """查询或取消本连接所属计算作业；取消不影响游戏任务。"""
        self._check(user)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["user"] != user:
                raise ValueError("计算任务不存在")
            if cancel:
                job["cancelled"].set()
            return {key: value for key, value in job.items() if key not in {"cancelled", "user"}}

    def _generated(self, user: str, payload: dict, result_id: str) -> tuple[dict, list[dict], dict]:
        self._check(user)
        from .....core.access import is_readonly
        if is_readonly():
            raise PermissionError("当前律匠实例为共享配置只读模式，不能生成规则")
        if set(payload) - {"name", "goal", "plan_ids", "plan_targets", "suggestions", "base_group", "rules", "run_config"}:
            raise ValueError("生成配置包含未知字段")
        if not isinstance(payload.get("goal", ""), str):
            raise ValueError("goal 必须是目标说明文本")
        suggestions = payload.get("suggestions", [])
        if not isinstance(suggestions, list) or len(suggestions) > 100 or any(
                not isinstance(value, str) or len(value) > 2000 for value in suggestions):
            raise ValueError("suggestions 必须是最多 100 条培养建议文本")
        group = copy.deepcopy(payload.get("base_group", {}))
        rules = copy.deepcopy(payload.get("rules", []))
        if not isinstance(group, dict) or not isinstance(rules, list) or not 1 <= len(rules) <= 10:
            raise ValueError("请提供一个完整基础组和 1～10 条调律规则")
        if set(group) - {"key", "name", "description", "order", "content_version", "materials", "scan", "tune", "smart_tuning"}:
            raise ValueError("基础组包含未知字段")
        from ...config.tune_slots import DEFAULT_SLOTS
        plan_ids = payload.get("plan_ids", [])
        targets = payload.get("plan_targets", {})
        state = LoadoutRepository(user, self.users_dir).load()
        if not isinstance(plan_ids, list) or any(p not in state.plans for p in plan_ids):
            raise ValueError("目标方案不属于该用户")
        if not isinstance(targets, dict) or set(targets) - set(plan_ids):
            raise ValueError("玩法覆盖必须属于本次目标方案")
        for plan_id, overrides in targets.items():
            if not isinstance(overrides, dict) or set(overrides) - {"playstyle", "base_attribute", "graduation_scheme"}:
                raise ValueError("方案覆盖字段无效")
            original = state.plans[plan_id]
            if "playstyle" in overrides and overrides["playstyle"] not in self.game_config.get_playstyles_for_arts(
                    [original.main_martial_art, original.sub_martial_art]):
                raise ValueError("目标玩法与方案武学不符")
            if "base_attribute" in overrides and overrides["base_attribute"] not in {
                    p.base_attribute for p in state.plans.values()}:
                raise PermissionError("基础属性不属于该用户方案引用范围")
        key = f"agent_{result_id}"
        group.update(key=key, name=str(payload.get("name") or "智能调律基础规则"))
        group.pop("content_version", None)
        parse_tuning_group(group)
        switches = set(self.resolver.load_merged("yysls/tune_config.yaml").get("switches", {}))
        selections = {}
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise ValueError("调律规则必须为对象")
            if rule.get("disabled"):
                raise ValueError("不能将已禁用的规则自动转为启用规则")
            if set(rule) - {"key", "name", "order", "disabled", "content_version", "playstyles", "playstyle_switches",
                           "transmute_priority", "affix_pool", "patterns", "common_conditions", "default_rating", "quality_thresholds"}:
                raise ValueError("调律规则包含未知字段")
            rule.update(key=f"{key}_{index + 1}", disabled=False)
            rule["name"] = f"{group['name']} / {rule.get('name') or '调律规则'}"
            rule.pop("content_version", None)
            parsed = parse_tuning_rule(rule, switches)
            if not parsed.playstyles or not parsed.affix_pool or not parsed.patterns:
                raise ValueError("新调律规则必须包含玩法、词条池和部位条件，不能发布空骨架")
            selections[parsed.key] = {"enabled": True, "playstyles": list(parsed.playstyles)}
        from ...config.auto_tuning_config import default_auto_tuning_config
        run = default_auto_tuning_config()
        supplied = payload.get("run_config", {})
        if not isinstance(supplied, dict) or set(supplied) - set(run):
            raise ValueError("运行参数包含未知字段")
        if any(key in supplied for key in ("rules", "base_group", "skip_tuning", "smart_tuning_enabled")):
            raise ValueError("规则绑定由生成接口创建；不能设置测试开关")
        run.update(supplied)
        from .launch import validate_run_fields
        validate_run_fields(run)
        if not isinstance(run["selected_slots"], list) or not run["selected_slots"] or any(
                slot not in DEFAULT_SLOTS for slot in run["selected_slots"]):
            raise ValueError("请选择合法调律部位")
        run.update(base_group=key, rules=selections,
                   smart_tuning_enabled={key: True}, skip_tuning=False)
        return group, rules, run

    def preview_generated_tuning(self, user: str, payload: dict) -> dict:
        """校验全新的基础组、规则与运行参数；不保存，不修改任何已有配置。"""
        group, rules, run = self._generated(user, payload, "preview")
        return {"base_group": group, "rules": rules, "run_config": run,
                "policy": "将使用新 key 保存到 local；不会设为用户默认配置。"}

    def create_generated_tuning(self, user: str, request_id: str, payload: dict) -> dict:
        """成套创建新 local 配置和私人结果记录；重试幂等，已有定义永不覆盖。"""
        self._check(user)
        if not request_id or len(request_id) > 128:
            raise ValueError("必须提供不超过 128 字符的幂等请求 ID")
        result_id = revision([user, request_id])[:24]
        group, rules, run = self._generated(user, payload, result_id)
        digest = revision(payload)
        self.store.ensure_initialized()
        with self._lock, InterProcessLock(str(self.store.root / "generation.lock")):
            self._check(user)
            records = self.store.load("results")
            existing = records.get(result_id)
            if existing:
                if existing["payload_revision"] != digest:
                    raise ValueError("该请求 ID 已用于不同内容，请为新方案使用新请求 ID")
                return existing
            created = []
            try:
                from ..tuning_rules.manager import create_generated_entities
                created = create_generated_entities(self.resolver, group, rules)
                record = {"id": result_id, "user": user, "name": str(payload.get("name") or "智能调律方案"),
                          "goal": payload.get("goal", ""), "suggestions": payload.get("suggestions", []),
                          "plan_ids": payload.get("plan_ids", []), "run_config": run,
                          "plan_targets": payload.get("plan_targets", {}),
                          "definitions_revision": revision([group, rules]), "payload_revision": digest,
                          "created_at": datetime.now(timezone.utc).isoformat(), "status": "ready"}
                self._check(user)
                self.store.mutate("results", lambda data: {**data, result_id: record})
            except Exception:
                for path in created:
                    path.unlink(missing_ok=True)
                raise
        self.dispatch("reload_rules", {})
        return record

    def list_generated_tuning(self, user: str) -> dict:
        """查询指定用户的生成结果，供 Agent 展示及调用启动。"""
        self._check(user)
        return {"results": [r for r in self.store.load("results").values() if r["user"] == user]}

    def validate_auto_tuning(self, user: str, result_id: str, target_id: str = "") -> dict:
        """复核新配置修订及指定设备的运行前提；回收/重置等行为由配置定义。"""
        self._check(user)
        record = self.store.load("results").get(result_id)
        if record is None or record["user"] != user or record["status"] != "ready":
            raise ValueError("没有该用户可启动的生成结果")
        config = record["run_config"]
        group = self._entity(f"yysls/base_groups/{config['base_group']}.yaml")
        rules = [self._entity(f"yysls/tuning_rules/{key}.yaml") for key in config["rules"]]
        if revision([group, rules]) != record["definitions_revision"]:
            raise ValueError("生成配置已被修改，请重新生成或重新核对后启动")
        parse_tuning_group(group)
        target_id = self._resolve_target(target_id)
        state = LoadoutRepository(user, self.users_dir).load()
        if record["plan_ids"]:
            selected = {key: state.plans[key] for key in record["plan_ids"] if key in state.plans}
            if len(selected) != len(record["plan_ids"]):
                raise ValueError("目标方案已被删除，请重新生成")
            state.plans = selected
            state.active_plan_id = next(iter(selected))
        for key, overrides in record.get("plan_targets", {}).items():
            for field, value in overrides.items():
                setattr(state.plans[key], field, value)
        self.dispatch("validate", {"user": user, "target_id": target_id, "config": config})
        return {"ready": True, "result_id": result_id, "config": config,
                "smart_state": state.to_dict(),
                "target_id": target_id}

    def start_auto_tuning(self, user: str, result_id: str, request_id: str, target_id: str = "") -> dict:
        """按生成结果启动现有调律；本次参数不自动设为默认。"""
        validated = self.validate_auto_tuning(user, result_id, target_id)
        return self.dispatch("start_tuning", {**validated, "user": user, "request_id": request_id})

    def start_scan_all_loadouts(self, user: str, request_id: str, skip_existing: bool = False,
                                target_id: str = "") -> dict:
        """采集游戏可见网格内方案，会切换游戏方案并写入方案/装备/基础属性；不会改应用活动方案。"""
        self._check(user)
        target_id = self._resolve_target(target_id)
        return self.dispatch("scan", {"user": user, "target_id": target_id,
                                     "request_id": request_id, "skip_existing": skip_existing})

    def get_task_status(self, user: str, task_id: str, action: str = "status") -> dict:
        """查询本连接启动的游戏任务，或 pause/resume/stop；终态和停止请求分别返回。"""
        self._check(user)
        if action not in {"status", "pause", "resume", "stop"}:
            raise ValueError("任务操作无效")
        return self.dispatch("task", {"user": user, "task_id": task_id, "action": action})

    def query_tuning_history(self, user: str, limit: int = 20) -> dict:
        """查询指定用户的实际调律历史，不返回私有日志路径。"""
        from ..tuning_history.repository import TuningHistoryRepository
        self._check(user)
        if not 1 <= limit <= 100:
            raise ValueError("limit 为 1～100")
        repo = TuningHistoryRepository(self.root / "config/session/tuning_history.db")
        return {"runs": [{k: v for k, v in asdict(r).items() if k not in {"markdown_path", "config_snapshot"}}
                         for r in repo.list_runs(1000) if r.username == user][:limit]}

    def get_tuning_results(self, user: str, run_id: str, offset: int = 0, limit: int = 30) -> dict:
        """分页读取指定用户某次实际调律的逐件结果。"""
        from ..tuning_history.repository import TuningHistoryRepository
        self._check(user)
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("分页范围无效")
        repo = TuningHistoryRepository(self.root / "config/session/tuning_history.db")
        run = repo.get_run(run_id)
        if run is None or run.username != user:
            raise ValueError("调律记录不存在")
        items = repo.get_results(run_id)
        return {"total": len(items), "results": [asdict(r) for r in items[offset:offset + limit]]}
