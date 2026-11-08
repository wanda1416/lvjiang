"""配置显示文本的多语言读写约定。

覆盖的回归：随包分发的系统方案/环境/等待参数在 ``app.yaml`` 里存中英双语时，
设置页保存一次就会把另一种语言整个抹掉——界面只把当前语言解析成字符串交给
写回逻辑，若直接整体落盘，``en_US`` 那一项就永久消失，用户切回英文只能看到
中文原名。同一条保存路径也不能把用户自建的名字升级成映射形态。
"""

import pytest
import yaml

from lvjiang.core.config import resolver as resolver_mod
from lvjiang.core.config.localized import (
    preserve_edited,
    preserve_mapping_field,
    preserve_sequence,
    resolve_localized,
)
from lvjiang.core.config.models import parse_delay_params
from lvjiang.core.config.plans import Plan
from lvjiang.core.config.resolver import (
    ConfigResolver,
    load_app_config,
    load_available_envs,
    save_app_config,
    save_app_config_node,
)

BILINGUAL = {"zh_CN": "安卓", "en_US": "Android"}
INPUT_SIM = {"before_click_wait": [0.1, 0.3], "click_random_offset": 5}

SHIPPED_APP = {
    "input_simulation": INPUT_SIM,
    "delay_params": {
        "step_interval": {
            "label": {"zh_CN": "操作间隔", "en_US": "Step Interval"},
            "range": [1.0, 1.2],
        },
    },
    "envs": [{"key": "android", "name": {"zh_CN": "安卓", "en_US": "Android"}}],
}


@pytest.fixture(autouse=True)
def _default_language(monkeypatch):
    """固定界面语言，避免其他用例初始化过 i18n 影响默认回退断言。"""
    monkeypatch.setattr("lvjiang.i18n.current_language", lambda: "zh_CN")


class TestResolveLocalized:
    def test_plain_string_passes_through(self):
        """字面值不随语言变化——用户自建内容走的永远是这条。"""
        assert resolve_localized("我的方案", "en_US") == "我的方案"

    def test_exact_language_wins(self):
        assert resolve_localized(BILINGUAL, "en_US") == "Android"
        assert resolve_localized(BILINGUAL, "zh_CN") == "安卓"

    def test_hyphenated_tag_matches_underscore_key(self):
        """配置里写 ``en-US`` 还是 ``en_US`` 都能取到。"""
        assert resolve_localized({"en-US": "Android"}, "en_US") == "Android"

    def test_region_falls_back_to_same_language(self):
        """未随包分发的区域（en_GB）退到同语种的 en_US，而不是直接掉中文。"""
        assert resolve_localized(BILINGUAL, "en_GB") == "Android"

    def test_non_chinese_falls_back_to_english(self):
        """非中文界面优先英文：随包 8 种语言本身就是英文回退，配置名掉回中文
        会让同一界面一半英文按钮、一半中文名字。"""
        assert resolve_localized(BILINGUAL, "ja_JP") == "Android"
        assert resolve_localized(BILINGUAL, "ko_KR") == "Android"

    def test_traditional_chinese_stays_chinese(self):
        """中文界面不插英文兜底：zh_TW 经同语种匹配拿到简体原文。"""
        assert resolve_localized(BILINGUAL, "zh_TW") == "安卓"

    def test_non_chinese_still_chinese_when_no_english(self):
        """只写了中文的映射，非中文界面也只能回退中文，不能空白。"""
        assert resolve_localized({"zh_CN": "安卓"}, "ja_JP") == "安卓"

    def test_empty_values_use_fallback(self):
        assert resolve_localized({}, "en_US", fallback="key") == "key"
        assert resolve_localized(None, "en_US", fallback="key") == "key"
        assert resolve_localized({"en_US": ""}, "en_US", fallback="key") == "key"


class TestPreserveEdited:
    def test_unchanged_keeps_every_language(self):
        original = dict(BILINGUAL)
        assert preserve_edited(original, "Android", "en_US") == original
        assert preserve_edited(original, "安卓", "zh_CN") == original

    def test_changed_overwrites_only_edited_language(self):
        updated = preserve_edited(BILINGUAL, "Android Phone", "en_US")
        assert updated == {"zh_CN": "安卓", "en_US": "Android Phone"}

    def test_plain_original_stays_plain(self):
        """用户自建的字符串不会被升级成映射（否则他的内容会突然跟着语言变）。"""
        assert preserve_edited("我的方案", "改过的名字", "en_US") == "改过的名字"

    def test_blank_edit_keeps_original(self):
        assert preserve_edited(BILINGUAL, "", "en_US") == BILINGUAL
        assert preserve_edited("我的方案", "   ", "en_US") == "我的方案"

    def test_new_entry_without_original_is_plain(self):
        assert preserve_edited(None, "新环境", "en_US") == "新环境"


class TestPreserveCollections:
    def test_sequence_aligns_by_key(self):
        original = [{"key": "android", "name": dict(BILINGUAL)}]
        new = [{"key": "android", "name": "安卓"}]
        assert preserve_sequence(
            original, new, key_field="key", text_field="name") == [
            {"key": "android", "name": dict(BILINGUAL)}]

    def test_mapping_aligns_by_key(self):
        original = {"step_interval": {"label": dict(BILINGUAL), "range": [1.0, 1.2]}}
        new = {"step_interval": {"label": "安卓", "range": [1.0, 2.0]}}
        kept = preserve_mapping_field(original, new, text_field="label")
        assert kept["step_interval"]["label"] == dict(BILINGUAL)
        assert kept["step_interval"]["range"] == [1.0, 2.0]


class TestModelReading:
    def test_delay_param_label_is_resolved_to_string(self):
        parsed = parse_delay_params(
            {"step_interval": {"label": dict(BILINGUAL), "range": [1.0, 1.2]}})
        assert parsed["step_interval"].label == "安卓"

    def test_delay_param_plain_label_untouched(self):
        parsed = parse_delay_params(
            {"mine": {"label": "自定义", "range": [1.0, 2.0]}})
        assert parsed["mine"].label == "自定义"

    def test_plan_name_dict_survives_round_trip(self):
        """系统方案的双语名读进来是字符串，写回去必须还是两种语言。"""
        plan = Plan.from_dict(
            {"id": "p1", "name": dict(BILINGUAL), "env": "android"})
        assert plan is not None
        assert plan.name == "安卓"
        assert plan.to_dict()["name"] == dict(BILINGUAL)

    def test_plan_rename_overwrites_one_language_only(self):
        plan = Plan.from_dict(
            {"id": "p1", "name": dict(BILINGUAL), "env": "android"})
        assert plan is not None
        plan.name = "安卓手机"
        assert plan.to_dict()["name"] == {"zh_CN": "安卓手机", "en_US": "Android"}

    def test_plan_with_empty_name_is_still_rejected(self):
        """脏数据判定不变：名字取不出内容时整条跳过。"""
        assert Plan.from_dict({"id": "p1", "name": {"en_US": ""}}) is None


@pytest.fixture
def app_config(tmp_path, monkeypatch):
    """把 app.yaml 的读-改-写全部指向 tmp_path，初始内容按随包形态给双语。"""
    system = tmp_path / "system"
    local = tmp_path / "local"
    system.mkdir()
    local.mkdir()
    (system / "app.yaml").write_text(
        yaml.safe_dump(SHIPPED_APP, allow_unicode=True), encoding="utf-8")
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=True)
    monkeypatch.setattr(resolver_mod, "get_resolver", lambda: resolver)
    return system


class TestAppConfigRoundTrip:
    def test_save_keeps_bilingual_names(self, app_config):
        """设置页把当前语言的字符串交回写入口，另一种语言不能被抹掉。"""
        save_app_config(
            INPUT_SIM,
            {"step_interval": {"label": "操作间隔", "range": [1.0, 1.2]}},
            [{"key": "android", "name": "安卓"}],
        )
        again = load_app_config()
        assert again["envs"] == [
            {"key": "android", "name": {"zh_CN": "安卓", "en_US": "Android"}}]
        assert again["delay_params"]["step_interval"]["label"] == {
            "zh_CN": "操作间隔", "en_US": "Step Interval"}

    def test_save_updates_only_edited_language(self, app_config):
        save_app_config(
            INPUT_SIM,
            {"step_interval": {"label": "操作间隔", "range": [1.0, 1.2]}},
            [{"key": "android", "name": "安卓手机"}],
        )
        assert load_app_config()["envs"] == [
            {"key": "android", "name": {"zh_CN": "安卓手机", "en_US": "Android"}}]

    def test_user_entry_stays_plain(self, app_config):
        """用户自己新增的环境写的是纯字符串，保存后不会变成映射。"""
        save_app_config(INPUT_SIM, {}, [{"key": "my_env", "name": "我的环境"}])
        assert load_app_config()["envs"] == [
            {"key": "my_env", "name": "我的环境"}]

    def test_load_available_envs_resolves_name(self, app_config):
        assert load_available_envs() == [("android", "安卓")]


class TestShippedAppYaml:
    """随包 config/system/app.yaml 的内容契约。"""

    @pytest.fixture
    def shipped(self, tmp_path, monkeypatch):
        from lvjiang.core.paths import get_config_system_dir

        resolver = ConfigResolver(
            system_dir=get_config_system_dir(),
            local_dir=tmp_path / "local",
            dev_mode=True,
        )
        monkeypatch.setattr(resolver_mod, "get_resolver", lambda: resolver)
        return load_app_config()

    def test_env_and_plan_names_are_bilingual(self, shipped):
        """随包环境名与方案名必须中英双语，否则英文界面仍显示中文。"""
        for item in shipped["envs"]:
            assert set(item["name"]) == {"zh_CN", "en_US"}, item
        for plan in shipped["plans"]:
            assert set(plan["name"]) == {"zh_CN", "en_US"}, plan

    def test_delay_param_labels_are_bilingual(self, shipped):
        for key, item in shipped["delay_params"].items():
            assert set(item["label"]) == {"zh_CN", "en_US"}, key


class TestUserModeOverlay:
    """普通用户模式：改动落 config/local，没改动的不能凭空造出覆盖文件。"""

    @pytest.fixture
    def user_config(self, tmp_path, monkeypatch):
        """system 层用真实随包 app.yaml，local 层留空。"""
        import shutil

        from lvjiang.core.paths import get_config_system_dir

        system = tmp_path / "system"
        local = tmp_path / "local"
        system.mkdir()
        local.mkdir()
        shutil.copy(get_config_system_dir() / "app.yaml", system / "app.yaml")
        resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
        monkeypatch.setattr(resolver_mod, "get_resolver", lambda: resolver)
        return local

    @staticmethod
    def _unchanged_args():
        """把当前合并视图按界面拿到的东西原样交回（等价于用户什么都没改）。"""
        merged = load_app_config()
        envs = [
            {"key": item["key"], "name": resolve_localized(item["name"])}
            for item in merged["envs"]
        ]
        delay = {
            key: {"label": resolve_localized(item["label"]), "range": item["range"]}
            for key, item in merged["delay_params"].items()
        }
        return merged["input_simulation"], delay, envs

    def test_unchanged_save_writes_no_local_file(self, user_config):
        """什么都没改时不能生成 local/app.yaml —— 否则每次保存都冻住一份覆盖，
        系统后续更新就再也进不到合并视图。"""
        input_sim, delay, envs = self._unchanged_args()
        save_app_config(input_sim, delay, envs)
        assert not (user_config / "app.yaml").exists()

    def test_edited_name_stores_only_that_entry(self, user_config):
        """改一个名字只落那一条增量，local 不能把整张 envs 冻住。"""
        input_sim, delay, envs = self._unchanged_args()
        envs[0]["name"] = "安卓手机"
        save_app_config(input_sim, delay, envs)

        overlay = yaml.safe_load(
            (user_config / "app.yaml").read_text(encoding="utf-8"))
        assert set(overlay["envs"]) == {"__added__"}
        assert overlay["envs"]["__added__"] == [
            {"key": "android", "name": {"zh_CN": "安卓手机", "en_US": "Android"}}]
        # 另一种语言保留，另一条环境仍由 system 层提供
        assert load_available_envs() == [
            ("android", "安卓手机"), ("desktop", "桌面")]

    @staticmethod
    def _system_add_env(local_dir, key: str, name: str) -> None:
        """模拟版本更新：系统随包配置新增一个环境。"""
        system_file = local_dir.parent / "system" / "app.yaml"
        doc = yaml.safe_load(system_file.read_text(encoding="utf-8"))
        doc["envs"].append({"key": key, "name": {"zh_CN": name, "en_US": name}})
        system_file.write_text(
            yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    def test_system_added_env_reaches_user_with_local_override(self, user_config):
        """系统后续新增的环境必须能被已经改过名的用户看到。

        此前 envs 走整键替换，用户改过一次名字就把整张表冻进 local，之后随包
        新增的环境在他这里永远不出现。
        """
        input_sim, delay, envs = self._unchanged_args()
        envs[0]["name"] = "安卓手机"
        save_app_config(input_sim, delay, envs)

        self._system_add_env(user_config, "emulator", "模拟器")

        assert load_available_envs() == [
            ("android", "安卓手机"), ("desktop", "桌面"), ("emulator", "模拟器")]

    def test_plan_rename_stores_only_that_plan(self, user_config):
        """方案同理：改一个名字只落那一条；随包新增的方案仍然出现。"""
        items = [
            Plan.from_dict(item, distributed=True).to_dict()
            for item in load_app_config()["plans"]
        ]
        renamed = items[0]["id"]
        items[0] = {**items[0], "name": {"zh_CN": "手游版", "en_US": "Mobile"}}
        save_app_config_node("plans", items)

        overlay = yaml.safe_load(
            (user_config / "app.yaml").read_text(encoding="utf-8"))
        assert set(overlay["plans"]) == {"__added__"}
        assert [item["id"] for item in overlay["plans"]["__added__"]] == [renamed]

        system_file = user_config.parent / "system" / "app.yaml"
        doc = yaml.safe_load(system_file.read_text(encoding="utf-8"))
        doc["plans"].append({
            "id": "brandnew",
            "name": {"zh_CN": "新方案", "en_US": "New Plan"},
            "env": "android",
            "modes": ["adb"],
        })
        system_file.write_text(
            yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

        plans = [Plan.from_dict(item) for item in load_app_config()["plans"]]
        names = [plan.name for plan in plans]
        assert "手游版" in names
        assert "新方案" in names
        assert "端游" in names, "未改动的预置方案不能因为别人改名而消失"

