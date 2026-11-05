"""Plugin boundaries and reversible-transfer decisions; no game operations."""
# ruff: noqa: E402
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget

from lvjiang.apps import load_app
from lvjiang.core.user_config import User, save_user_metadata

PLUGIN = Path(__file__).parents[2] / "plugins/shuijiao_tuning"
load_app("shuijiao_tuning@" + str(PLUGIN))
from lvjiang.apps.shuijiao_tuning.core.build_profiles import (
    ProfileJudge,
    load_profiles,
    validate_settings,
)
from lvjiang.apps.shuijiao_tuning.core.junk_recycle import (
    DEFAULT_JUNK_AFFIXES,
    JunkRecyclePolicy,
)
from lvjiang.apps.shuijiao_tuning.workflows.implementations.tuning.decisions import (
    BehaviorAction,
)
from lvjiang.apps.shuijiao_tuning.workflows.implementations.tuning.judge import (
    TuningJudge,
)
from lvjiang.apps.shuijiao_tuning.workflows.tuning_context import TuningRunContext

from lvjiang.apps.yysls.core.equip_parser import Affix, EquipmentData
from lvjiang.apps.yysls.core.tuning_rules import TuneBehavior


def equip(part, *names, level=115, quality="purple"):
    return EquipmentData(type=part, level=level, quality=quality,
                         affixes=[Affix(n, 1, cap_pct=95) for n in names])

def judge(*keys, protect=False):
    return TuningJudge(TuningRunContext(build_profiles=list(keys), profile_catalog=load_profiles(),
                      junk_recycle=JunkRecyclePolicy(enabled=True, protect_other_builds=protect)))

@pytest.fixture
def profiles():
    return ProfileJudge(load_profiles(), junk=DEFAULT_JUNK_AFFIXES)

def test_small_pendant_two_bad_stats_and_repairable_last_slot(profiles):
    item = equip("佩", "最小外功攻击", "全武学增效", "最大外功攻击", "会心率", "会意率")
    assert not profiles.assess("zun", item).viable
    item.affixes[-1] = Affix("敏", 1)
    assert profiles.assess("zun", item).viable

def test_small_wrist_continues_gold_when_last_slot_can_complete_one_transfer(profiles):
    item = equip("腕甲", "会心率", "劲", "会心率", "对首领单位增伤")
    assert profiles.assess("zun", item).viable
    assert judge("zun").gold_viable(item)
    for name in ("最小外功攻击", "敏"):
        full = deepcopy(item)
        full.affixes.append(Affix(name, 1))
        assert profiles.assess("zun", full).viable
    item.affixes.append(Affix("势", 1))
    assert not profiles.assess("zun", item).viable

def test_large_ring_allows_mingjin_instead_of_jin_and_protects_other_build():
    item = equip("环", "最大外功攻击", "最大外功攻击", "最大鸣金攻击", "势", "全武学增效")
    j = judge("zun", protect=True)
    assert "虹虹" in j.top_protection(item)
    assert j.decide_end(TuneBehavior(), item, None, full=True).action is BehaviorAction.SKIP

def test_middle_accepts_both_attacks_jin_min_and_rejects_two_mingjin_stats(profiles):
    item = equip("胸甲", "精准率", "最大外功攻击", "最小外功攻击", "劲", "全奇术增伤")
    assert profiles.assess("yu", item).viable
    item.affixes[1:3] = [Affix("势", 1), Affix("会意率", 1)]
    assert not profiles.assess("yu", item).viable

def test_custom_requirements_are_used_and_frozen(profiles):
    override = {"zun": {"main_weapon": ["敏", "最小外功攻击", "敏", "", ""]}}
    validate_settings(["zun"], override, profiles.catalog)
    custom = ProfileJudge(profiles.catalog, override, DEFAULT_JUNK_AFFIXES)
    override["zun"]["main_weapon"][0] = "最小外功攻击"
    item = equip("手甲", "敏", "最小外功攻击", "敏", "手甲武学增伤", "精准率")
    assert not profiles.assess("zun", item).viable
    assert custom.assess("zun", item).viable

def test_factory_defaults_are_independent_and_saved_user_parameters_win(monkeypatch):
    from lvjiang.apps.shuijiao_tuning.config import auto_tuning_config as config
    first = config.default_auto_tuning_config()
    expected = first["gold_food"]["low_stock"]
    first["gold_food"]["low_stock"] = expected + 1
    assert config.default_auto_tuning_config()["gold_food"]["low_stock"] == expected
    saved = {"min_level": 110, "gold_food": {"enabled": False, "low_stock": 123}}
    monkeypatch.setattr(config, "get_user_workflow_params", lambda *a: saved)
    loaded = config.load_user_auto_tuning_config("test-user")
    assert loaded["min_level"] == 110 and loaded["gold_food"] == saved["gold_food"]

def test_fire_healer_cross_flow_remains_a_valid_fallback_for_chen_pendant():
    item = equip("佩", "最小外功攻击", "最小外功攻击", "敏", "会心率", "会意率")
    selected = judge("chen", protect=True)
    assert not selected.profile_judge().assess("chen", item).viable
    assert "霖霖·火拳" in selected.top_protection(item)
    assert selected.profile_decision(item, full=True).action.value == "skip"
    without = judge("chen", protect=False)
    assert without.profile_decision(item, full=True).action.value == "recycle"


def test_subplugin_does_not_override_original_registration_or_shell():
    original = load_app("yysls")
    plugin = load_app("shuijiao_tuning@" + str(PLUGIN))
    assert plugin.requires_app_ids == ("yysls",)
    assert set(plugin.workflow_implementations) == {"shuijiao_tuning"}
    assert "auto_tuning" in original.workflow_implementations
    assert plugin.window_title is None
    assert not plugin.startup_callbacks and not plugin.builtin_modules


def test_configuration_is_independent_of_original(tmp_path):
    from lvjiang.apps.shuijiao_tuning.config.auto_tuning_config import (
        load_user_auto_tuning_config,
        save_user_auto_tuning_config,
    )

    from lvjiang.apps.yysls.config.auto_tuning_config import (
        load_user_auto_tuning_config as original,
    )
    from lvjiang.core.user_config import User, save_user_metadata
    save_user_metadata(User("test-user"), tmp_path)
    before = original("test-user", tmp_path)
    save_user_auto_tuning_config("test-user", {"min_level":110}, tmp_path)
    assert load_user_auto_tuning_config("test-user", tmp_path)["min_level"] == 110
    assert original("test-user", tmp_path) == before


def test_saved_defaults_have_all_eight_slots_and_editor_constructs(qtbot):
    from lvjiang.apps.shuijiao_tuning.config.auto_tuning_config import (
        default_auto_tuning_config,
    )
    from lvjiang.apps.shuijiao_tuning.ui.tuning.profile_widget import (
        ProfileRequirementsWidget,
    )
    config = default_auto_tuning_config()
    assert len(config["profile_overrides"]) == 13
    for parts in config["profile_overrides"].values():
        assert set(parts) == {"main_weapon","sub_weapon","ring","pendant","head","chest","leg","wrist"}
    editor = ProfileRequirementsWidget()
    qtbot.addWidget(editor)
    editor.set_config(config)
    assert editor.get_config()["profile_overrides"] == config["profile_overrides"]


def test_selected_rules_reach_private_workflow_context():
    from lvjiang.apps.shuijiao_tuning.config.auto_tuning_config import (
        default_auto_tuning_config,
    )
    from lvjiang.apps.shuijiao_tuning.core.agent.launch import prepare_tuning
    config = default_auto_tuning_config()
    config["build_profiles"] = ["zun"]
    context = prepare_tuning(config)
    assert isinstance(context, TuningRunContext)
    assert context.build_profiles == ["zun"]
    assert context.profile_overrides == config["profile_overrides"]
    context.profile_overrides.clear()
    assert config["profile_overrides"]


def test_plugin_page_keeps_host_tabs_and_starts_private_snapshot(qtbot, tmp_path, monkeypatch):
    from lvjiang.apps.shuijiao_tuning.core.tuning_history.repository import (
        TuningHistoryRepository,
    )
    from lvjiang.apps.shuijiao_tuning.ui.tuning.management_widget import (
        TuningManagementWidget,
    )
    from lvjiang.apps.shuijiao_tuning.ui.tuning.tuning_tab import TuningTab
    class Host(QWidget):
        user_changed = pyqtSignal(str)
        automation_state_changed = pyqtSignal(str)

    host = Host()
    qtbot.addWidget(host)
    host.user_manager = SimpleNamespace(users_dir=tmp_path / "users", list_users=lambda: ["example"],
                                        get_active_user_name=lambda: "example")
    save_user_metadata(User("example"), host.user_manager.users_dir)
    host.plugin_runtime = SimpleNamespace(hotkeys=lambda: SimpleNamespace(start="F9", stop="F9", pause="F10"))
    host.is_running = False
    host._backend_ready = lambda: True
    host._plan_allows_backend = lambda: True
    host.start_denied_label = lambda: ""
    logs = []
    host.append_log = logs.append
    host._left_tabs = QTabWidget(host)
    host._left_tabs.addTab(QWidget(), "other")
    host.tabs = QTabWidget(host)
    host.tabs.addTab(QWidget(), "运行日志")
    host.tabs.addTab(QWidget(), "other")
    management = TuningManagementWidget(history_repository=TuningHistoryRepository(tmp_path / "history.db"))
    host.tabs.addTab(management, "调律管理")
    host._main_splitter = QSplitter(host)
    host._main_splitter.addWidget(host._left_tabs)
    records_panel = QWidget()
    QVBoxLayout(records_panel).addWidget(host.tabs)
    host._main_splitter.addWidget(records_panel)
    for name in ("context_bar", "btn_scan_device", "btn_hide_window", "btn_toggle_target_header"):
        setattr(host, name, QWidget(host))
    host.btn_scan_window = QPushButton("扫描窗口", host)
    host.btn_locate = QPushButton("定位", host)
    tab = TuningTab(host)
    host._left_tabs.addTab(tab, "调律")
    qtbot.wait(1)
    assert host._left_tabs.isTabVisible(0)
    assert host.tabs.isTabVisible(1)
    assert host._main_splitter.count() == 2
    assert tab._config_tabs.indexOf(host.tabs) == -1
    tab._profile_editor._checks["zun"].setChecked(True)
    captured = {}
    def start(key, name, configure, **kwargs):
        captured["key"] = key
        wf = SimpleNamespace()
        configure(wf, SimpleNamespace(task_run_id="test-run"))
        captured["ctx"] = wf.run_ctx
    host.run_workflow_implementation = start
    monkeypatch.setattr(tab, "_missing_tuning_output_fields", lambda: [])
    tab._start_tuning()
    assert captured["key"] == "shuijiao_tuning", logs
    assert captured["ctx"].build_profiles == ["zun"]
    assert isinstance(captured["ctx"], TuningRunContext)
