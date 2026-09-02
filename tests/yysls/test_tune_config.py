"""装备调律配置 UI 冒烟测试

对话框实例化 + 各页面 收集→校验→写盘 往返（在 tmp 目录副本上
执行，不触碰真实规则文件），保证 UI 管线不破坏规则语义。
"""

import shutil
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QComboBox, QHeaderView, QPushButton

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.evaluator import get_tuning_rules
from lvjiang.apps.yysls.core.tuning_rules import (
    TuneConfigManager,
    TuningGroupManager,
    TuningRuleManager,
    get_tuning_rule_manager,
)
from lvjiang.apps.yysls.ui.layout_helpers import navigation_width_for_chars
from lvjiang.apps.yysls.ui.tune_settings import TuningRulesDialog
from lvjiang.apps.yysls.ui.tune_settings.behavior_pages import (
    ScanBehaviorPage,
    TuneBehaviorPage,
)
from lvjiang.apps.yysls.ui.tune_settings.material_config_page import (
    MaterialConfigPage,
)
from lvjiang.apps.yysls.ui.tune_settings.rule_panel import RulePanel

PROJECT_ROOT = Path(__file__).parents[2]
RULES_DIR = PROJECT_ROOT / "config" / "system" / "yysls" / "tuning_rules"
GROUPS_DIR = PROJECT_ROOT / "config" / "system" / "yysls" / "base_groups"
BASE_FILE = PROJECT_ROOT / "config" / "system" / "yysls" / "tune_config.yaml"

ALL_KEYS = ["huiyi_general", "huixin_small", "huixin_big",
            "heal_pure", "heal_fire"]


def _assert_combo_text_fits(combo: QComboBox, minimum: int = 0):
    longest = max(
        combo.fontMetrics().horizontalAdvance(combo.itemText(i))
        for i in range(combo.count())
    )
    assert combo.minimumWidth() >= minimum
    assert combo.minimumWidth() > longest
    assert combo.view().minimumWidth() >= combo.minimumWidth()


@pytest.fixture
def tmp_manager(tmp_path):
    """真实规则的 tmp 副本管理器（写盘不影响真实配置）"""
    for f in RULES_DIR.glob("*.yaml"):
        shutil.copy(f, tmp_path)
    return TuningRuleManager(rules_dir=tmp_path)


@pytest.fixture
def tmp_group_manager(tmp_path):
    """真实规则组的 tmp 副本管理器（写盘不影响真实配置）"""
    for f in GROUPS_DIR.glob("*.yaml"):
        shutil.copy(f, tmp_path)
    # 同时复制 tune_config.yaml（base_rules 声明来源）
    yysls_dir = tmp_path / "yysls"
    yysls_dir.mkdir(exist_ok=True)
    shutil.copy(BASE_FILE, yysls_dir / "tune_config.yaml")
    return TuningGroupManager(groups_dir=tmp_path)


@pytest.fixture
def tmp_config_manager(tmp_path):
    """真实全局配置的 tmp 副本管理器（写盘不影响真实配置）"""
    dst = tmp_path / "tune_config.yaml"
    shutil.copy(BASE_FILE, dst)
    return TuneConfigManager(path=dst)


class TestDialog:
    def test_dialog_nav(self, qtbot):
        dialog = TuningRulesDialog()
        qtbot.addWidget(dialog)
        # 左侧导航：基础规则 + 扫描处理 + 材料处理 + 结束处理 + 分割线 + 流派规则 + 各规则
        # StackedWidget 不含分割线（行 0-3 = 栈页 0-3，行 ≥5 = 栈页 - 1）
        # 对话框加载全部规则（含禁用），与 get_tuning_rules()（仅启用）不同
        n_rules = len(get_tuning_rule_manager().get_all_rule_keys_and_names())
        assert dialog._nav.count() == n_rules + 6
        assert dialog._stack.count() == n_rules + 5
        assert dialog._nav.item(0).text() == "基础规则"
        assert dialog._nav.item(1).text() == "扫描处理"
        assert dialog._nav.item(2).text() == "材料处理"
        assert dialog._nav.item(3).text() == "结束处理"
        # 分割线项不可选中
        assert not dialog._nav.item(4).flags()
        assert dialog._nav.item(5).text() == "流派规则"
        assert dialog._nav.property("navigation") is True
        # 规则页初始只占位，首次进入才构造 RulePanel。
        assert not isinstance(dialog._stack.widget(5), RulePanel)
        assert not dialog.findChildren(RulePanel)
        # 规则项名称随真实规则文件 name 字段（可被用户改名）
        # 规则顺序由 tune_config.yaml 的 tuning_rules 段控制
        first_rule = next(iter(get_tuning_rules().values()))
        assert dialog._nav.item(6).text() == first_rule.name
        # 导航切换驱动右侧内容区（跳过分割线偏移）
        dialog._nav.setCurrentRow(3)
        assert dialog._stack.currentIndex() == 3
        dialog._nav.setCurrentRow(6)
        assert dialog._stack.currentIndex() == 5
        rule_panel = dialog._stack.widget(5)
        assert isinstance(rule_panel, RulePanel)
        assert len(dialog.findChildren(RulePanel)) == 1
        assert rule_panel._nav.property("navigation") is True
        dialog.show()
        QApplication.processEvents()
        first_width = navigation_width_for_chars(dialog._nav, 8)
        second_width = navigation_width_for_chars(rule_panel._nav, 6)
        assert dialog._main_splitter.orientation() == Qt.Orientation.Horizontal
        assert rule_panel._nav_splitter.orientation() == Qt.Orientation.Horizontal
        assert abs(dialog._main_splitter.sizes()[0] - first_width) <= 2
        assert abs(rule_panel._nav_splitter.sizes()[0] - second_width) <= 2
        dialog._main_splitter.setSizes([first_width + 40, 1000])
        rule_panel._nav_splitter.setSizes([second_width + 40, 600])
        assert dialog._main_splitter.sizes()[0] > first_width
        assert rule_panel._nav_splitter.sizes()[0] > second_width
        buttons = dialog.findChildren(QPushButton)
        assert buttons
        assert all(button.styleSheet() for button in buttons)

        # 基础规则 + 三大处理页的规则组选择必须完整显示中文名称。
        for combo in [dialog._base_page._combo, *dialog._group_dropdowns]:
            _assert_combo_text_fits(combo, 200)

    def test_lazy_placeholder_tracks_add_rename_delete(
            self, qtbot, monkeypatch):
        dialog = TuningRulesDialog()
        qtbot.addWidget(dialog)
        nav_count = dialog._nav.count()
        stack_count = dialog._stack.count()

        placeholder = dialog._add_rule_page("lazy_test", "占位规则")
        assert dialog._stack.widget(stack_count) is placeholder
        assert dialog._nav.item(nav_count).text() == "占位规则"

        # RulePanel 在调用对话框回调前会先更新自身 key；占位页
        # 保持同一协议，导航文本应在原位更新。
        placeholder.rule_key = "lazy_renamed"
        dialog._rename_rule("lazy_test", "lazy_renamed", "已重命名")
        assert dialog._nav.item(nav_count).text() == "已重命名"

        monkeypatch.setattr(dialog._manager, "delete_rule", lambda _key: None)
        dialog._delete_rule("lazy_renamed")
        assert dialog._nav.count() == nav_count
        assert dialog._stack.count() == stack_count


class TestBehaviorPages:
    """行为处理页 smoke：真实配置回填 + 变更即校验即保存"""

    def test_all_dropdowns_keep_readable_width(self, qtbot,
                                               tmp_group_manager):
        scan = ScanBehaviorPage(
            tmp_group_manager, "default", lambda _t, _e: None)
        tune = TuneBehaviorPage(
            tmp_group_manager, "default", lambda _t, _e: None)
        material = MaterialConfigPage(
            tmp_group_manager, "default", lambda _t, _e: None)
        for page in (scan, tune, material):
            qtbot.addWidget(page)

        for combo in (scan._min_level_combo, scan._entry_combo,
                      tune._exhausted_combo, material._stone_action):
            _assert_combo_text_fits(combo)

        for page in (scan, tune):
            row = 0
            for key in ("quality", "judge", "action"):
                combo = page._table.cellWidget(row, page._ci[key])
                _assert_combo_text_fits(combo)
                assert (page._table.horizontalHeader().sectionResizeMode(
                    page._ci[key])
                    == QHeaderView.ResizeMode.ResizeToContents)

        for col in (2, 3, 4, 5):
            combo = material._table.cellWidget(0, col)
            _assert_combo_text_fits(combo)
            assert (material._table.horizontalHeader().sectionResizeMode(col)
                    == QHeaderView.ResizeMode.ResizeToContents)

    def test_scan_page_entry_rating_roundtrip(self, qtbot,
                                               tmp_group_manager):
        """调律门槛（扫描处理页）：回填 + 变更写回 scan，
        不碰处置表其他字段"""
        statuses: list[tuple[str, bool]] = []
        page = ScanBehaviorPage(tmp_group_manager, "default",
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(page)
        group = tmp_group_manager.get_group("default")
        original_entry = group.scan.entry_min_rating
        assert page._entry_combo.currentData() == original_entry

        # 变更到不同于当前值的选项
        new_entry = "excellent" if original_entry != "excellent" else "top"
        page._entry_combo.setCurrentIndex(page._entry_combo.findData(new_entry))
        assert statuses and not statuses[-1][1], statuses[-1][0]
        after = tmp_group_manager.get_group("default")
        assert after.scan.entry_min_rating == new_entry
        # 处置表其他字段不受影响
        assert after.scan.enabled == group.scan.enabled
        assert after.scan.rules == group.scan.rules

    def test_scan_page_roundtrip(self, qtbot, tmp_group_manager):
        statuses: list[tuple[str, bool]] = []
        page = ScanBehaviorPage(tmp_group_manager, "default",
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(page)
        scan = tmp_group_manager.get_group("default").scan
        # 回填与真实配置一致
        assert page._enabled_cb.isChecked() == scan.enabled
        assert page._table.rowCount() == len(scan.rules)
        # 判定语义与仅首词条已下沉为表格列（列索引经 _ci 取），
        # 逐行回填
        for i, rule in enumerate(scan.rules):
            judge = page._table.cellWidget(i, page._ci["judge"])
            assert judge.scope() == rule.judge_scope
            assert judge.rules() == rule.judge_rules
            fao = page._table.cellWidget(i, page._ci["first_affix"])
            assert fao.isChecked() == rule.first_affix_only

        # 变更首行「仅首词条」→ 校验通过自动保存并生效，且门槛不丢
        first = scan.rules[0].first_affix_only
        page._table.cellWidget(0, page._ci["first_affix"]).setChecked(
            not first)
        assert statuses and not statuses[-1][1], statuses[-1][0]
        after = tmp_group_manager.get_group("default").scan
        assert after.rules[0].first_affix_only == (not first)
        assert after.entry_min_rating == scan.entry_min_rating

    def test_scan_page_add_delete_rule(self, qtbot, tmp_group_manager):
        statuses: list[tuple[str, bool]] = []
        page = ScanBehaviorPage(tmp_group_manager, "default",
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(page)
        before = len(tmp_group_manager.get_group("default").scan.rules)
        page._on_add_rule()
        assert statuses and not statuses[-1][1], statuses[-1][0]
        assert len(tmp_group_manager.get_group("default").scan.rules) == before + 1
        page._table.setCurrentCell(before, 0)
        page._on_del_rule()
        assert len(tmp_group_manager.get_group("default").scan.rules) == before

    def test_tune_page_roundtrip(self, qtbot, tmp_group_manager):
        statuses: list[tuple[str, bool]] = []
        page = TuneBehaviorPage(tmp_group_manager, "default",
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(page)
        tune = tmp_group_manager.get_group("default").tune
        assert page._enabled_cb.isChecked() == tune.enabled
        assert page._resets_spin.value() == tune.max_resets
        assert (page._exhausted_combo.currentData()
                == tune.reset_exhausted_action)

        # 启用 + 上限调整 → 保存生效，且不覆盖 scan 子段
        scan_before = tmp_group_manager.get_raw("default")["scan"]
        page._enabled_cb.setChecked(True)
        page._resets_spin.setValue(1)
        assert statuses and not statuses[-1][1], statuses[-1][0]
        saved = tmp_group_manager.get_group("default").tune
        assert saved.enabled is True
        assert saved.max_resets == 1
        assert tmp_group_manager.get_raw("default")["scan"] == scan_before

    def test_scan_scope_switch_resets_ratings_domain(self, qtbot,
                                                      tmp_group_manager):
        """判定语义切换时判定结果候选域联动重置：affix → 词条
        候选（选中首个）；切离 → 评级四档（全选 = 不限）"""
        from lvjiang.apps.yysls.core.tuning_rules import (
            rule_affix_candidates,
        )
        statuses: list[tuple[str, bool]] = []
        page = ScanBehaviorPage(tmp_group_manager, "default",
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(page)
        page._on_add_rule()
        row = page._table.rowCount() - 1
        judge = page._table.cellWidget(row, page._ci["judge"])
        ratings = page._table.cellWidget(row, page._ci["ratings"])
        vocab = rule_affix_candidates()

        # 切到自选词条：候选换成词条全集，选中集重置为首个词条，
        # 校验通过自动保存
        judge.setCurrentIndex(judge.findData("affix"))
        judge._on_activated(judge.currentIndex())
        assert [a.text() for a in ratings._actions.values()] == vocab
        assert ratings.selected() == vocab[:1]
        assert statuses and not statuses[-1][1], statuses[-1][0]
        saved = tmp_group_manager.get_group("default").scan.rules[-1]
        assert saved.judge_scope == "affix"
        assert saved.ratings == vocab[:1]

        # 切离 affix：候选换回评级四档，选中集重置为全选（= 不限，
        # 收集归一为空）
        judge.setCurrentIndex(judge.findData("incoming"))
        judge._on_activated(judge.currentIndex())
        assert len(ratings._actions) == 4
        assert len(ratings.selected()) == 4
        assert statuses and not statuses[-1][1], statuses[-1][0]
        saved = tmp_group_manager.get_group("default").scan.rules[-1]
        assert saved.judge_scope == "incoming"
        assert saved.ratings == []


class TestPanelRoundtrip:
    @pytest.mark.parametrize("key", ALL_KEYS)
    def test_all_pages_apply_saves_ok(self, qtbot, tmp_manager, key):
        # 未改动任何控件时逐页触发收集：校验须通过且保存成功
        statuses: list[tuple[str, bool]] = []
        panel = RulePanel(key, tmp_manager,
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(panel)
        original = tmp_manager.get_rule(key)

        appliers = [
            panel._settings_page._apply_playstyles,
            panel._pool_page._apply,
            panel._common_page._apply,
        ] + [page._apply for page in panel._part_pages]
        for apply in appliers:
            apply()
            assert statuses and not statuses[-1][1], \
                f"{key}: {statuses[-1][0]}"

        # 往返后规则语义不变（玩法设定/池集合/转律词条库/四档条件）
        saved = tmp_manager.get_rule(key)
        assert saved.name == original.name
        assert saved.playstyle_options == original.playstyle_options
        assert saved.pool_set == original.pool_set
        assert saved.transmute_priority == original.transmute_priority
        for tier in ("junk_conditions", "normal_conditions",
                     "excellent_conditions", "top_conditions"):
            # 通用判定往返不变
            assert len(getattr(saved.common, tier)) == \
                len(getattr(original.common, tier))
        assert set(saved.patterns) == set(original.patterns)
        for part, pattern in original.patterns.items():
            saved_p = saved.patterns[part]
            assert set(saved_p.first) == set(pattern.first)
            assert saved_p.default_rating == pattern.default_rating
            for tier in ("junk_conditions", "normal_conditions",
                         "excellent_conditions", "top_conditions"):
                saved_groups = getattr(saved_p, tier)
                orig_groups = getattr(pattern, tier)
                assert len(saved_groups) == len(orig_groups)
                assert [len(g.conditions) for g in saved_groups] == \
                    [len(g.conditions) for g in orig_groups]
                assert [g.when for g in saved_groups] == \
                    [g.when for g in orig_groups]

    def test_clear_first_removes_pattern(self, qtbot, tmp_manager):
        # 首词条清空 = 该部位不定义模式：保存成功且模式移除
        statuses: list[tuple[str, bool]] = []
        panel = RulePanel("huiyi_general", tmp_manager,
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(panel)
        page = panel._part_pages[0]  # 主武器
        page._first = []
        page._apply()
        assert statuses and not statuses[-1][1], statuses[-1][0]
        rule = tmp_manager.get_rule("huiyi_general")
        assert "主武器" not in rule.patterns

    def test_common_page_nav_and_layout(self, qtbot, tmp_manager):
        # 通用判定页：导航在分割线下、主武器之上，无首词条/默认判定，
        # 直接展示判定条件 Tab（全部 + 四档）
        panel = RulePanel("huiyi_general", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        assert panel._nav.item(3).text() == "通用判定"
        assert panel._nav.item(4).text() == "主武器"
        panel._nav.setCurrentRow(3)
        assert panel._stack.currentIndex() == 2
        page = panel._common_page
        assert not hasattr(page, "_first_btn")
        assert not hasattr(page, "_rating_combo")
        bar = page._tier_tabs._bar
        assert bar.count() == 5
        assert bar.tabText(0) == "全部"
        assert len(page._tier_editors) == 4

    def test_tier_tabs_all_view_visibility(self, qtbot, tmp_manager):
        # 「全部」页四档均可见；单档 Tab 只显示对应档
        panel = RulePanel("huiyi_general", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        tabs = panel._common_page._tier_tabs
        assert tabs._bar.currentIndex() == 0
        assert all(not e.isHidden() for e in tabs.editors.values())
        tabs._bar.setCurrentIndex(1)  # 垃圾
        hidden = [k for k, e in tabs.editors.items() if e.isHidden()]
        assert not tabs.editors["junk_conditions"].isHidden()
        assert set(hidden) == {"normal_conditions", "excellent_conditions",
                               "top_conditions"}

    def test_common_page_apply_roundtrip(self, qtbot, tmp_manager):
        # 通用判定四档全空 = 不写 common_conditions；有组时写回并可解析
        statuses: list[tuple[str, bool]] = []
        panel = RulePanel("huiyi_general", tmp_manager,
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(panel)
        page = panel._common_page
        for editor in page._tier_editors.values():
            editor.set_groups([])
        page._apply()
        assert statuses and not statuses[-1][1], statuses[-1][0]
        assert "common_conditions" not in tmp_manager.get_raw("huiyi_general")

        editor = page._tier_editors["junk_conditions"]
        editor.set_groups([{"contains_all": ["劲"]}])
        page._apply()
        assert statuses and not statuses[-1][1], statuses[-1][0]
        rule = tmp_manager.get_rule("huiyi_general")
        assert len(rule.common.junk_conditions) == 1


class TestPanelCreateDelete:
    def test_delete_callback_forwards_key(self, qtbot, tmp_manager):
        deleted: list[str] = []
        panel = RulePanel("heal_pure", tmp_manager,
                                lambda t, e: None, on_delete=deleted.append)
        qtbot.addWidget(panel)
        panel._request_delete()
        assert deleted == ["heal_pure"]


class TestSettingsPageBasics:
    """key/名称只读展示；key 重命名走按钮弹窗，名称重命名走导航双击"""

    def test_key_and_name_readonly_labels(self, qtbot, tmp_manager):
        panel = RulePanel("heal_pure", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        page = panel._settings_page
        rule = tmp_manager.get_rule("heal_pure")
        # QLabel 只读展示（非输入框）
        assert page._key_label.text() == "heal_pure"
        assert page._name_label.text() == rule.name
        assert not hasattr(page, "_key_edit")
        assert not hasattr(page, "_name_edit")

    def test_playstyle_tips_button(self, qtbot, tmp_manager):
        # 玩法设定说明收入「?」按钮（点击展示，不用悬停 tooltip）
        panel = RulePanel("heal_pure", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        btn = panel._settings_page._playstyle_tips_btn
        assert btn.text() == "?"
        assert btn.toolTip() == ""

    def test_set_rule_name_saves(self, qtbot, tmp_manager):
        # 导航双击重命名入口：写入工作副本、同步展示、保存生效
        panel = RulePanel("heal_pure", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        panel.set_rule_name("新名字")
        assert panel.rule_name == "新名字"
        assert panel._settings_page._name_label.text() == "新名字"
        assert tmp_manager.get_rule("heal_pure").name == "新名字"

    def test_rename_key_via_button(self, qtbot, tmp_manager, monkeypatch):
        # 「重命名」按钮弹窗确认后：文件改名、新 key 生效、展示同步
        from lvjiang.apps.yysls.ui.tune_settings import rule_settings_page
        monkeypatch.setattr(
            rule_settings_page.QInputDialog, "getText",
            staticmethod(lambda *a, **k: ("heal_pure2", True)))
        panel = RulePanel("heal_pure", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        page = panel._settings_page
        page._rename_key()
        assert panel.rule_key == "heal_pure2"
        assert page._key_label.text() == "heal_pure2"
        assert tmp_manager.get_rule("heal_pure2") is not None
        assert tmp_manager.get_rule("heal_pure") is None

    def test_rename_key_cancel_noop(self, qtbot, tmp_manager, monkeypatch):
        from lvjiang.apps.yysls.ui.tune_settings import rule_settings_page
        monkeypatch.setattr(
            rule_settings_page.QInputDialog, "getText",
            staticmethod(lambda *a, **k: ("whatever", False)))
        panel = RulePanel("heal_pure", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        panel._settings_page._rename_key()
        assert panel.rule_key == "heal_pure"
        assert tmp_manager.get_rule("heal_pure") is not None

