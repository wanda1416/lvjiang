"""核心看不懂的 Profile 定义：忽略它，但一个字都不能改。

周期和模型类型都可以由插件注册（yysls 的 `season` 就是其一）。不加载该插件
时核心解释不了这些定义，但 profile.yaml 是**用户数据**——下次带着插件启动还
要照原样用，所以这里的契约是"忽略，不改写、不删除"。
"""
import pytest

from lvjiang.core.profile.models import MODEL_QUOTA
from lvjiang.core.profile.schema import (
    ProfileSchema,
    get_profile_config,
    reload_profile_config,
    save_profile_config,
)


@pytest.fixture
def profile_path(tmp_path, monkeypatch):
    """用 conftest 的全局隔离给出的 profile.yaml 路径。

    不自己再指一次：这样这些用例顺带验证了那道全局门禁确实生效——用例读写的
    是私有副本，碰不到真实用户数据，也不可能感知插件注册的周期。
    """
    from lvjiang.core.profile import schema as schema_module

    path = schema_module._PROFILE_PATH
    assert tmp_path in path.parents, (
        f"profile.yaml 没有被隔离到用例目录: {path}")
    monkeypatch.setattr(schema_module, "_config", None)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 显式扮演"没加载 yysls 插件"：season 由插件注册，而插件是否被某个别的
    # 用例导入过不该决定这些用例的结论——那正是这里要守的性质。
    original = schema_module.get_profile_period
    monkeypatch.setattr(
        schema_module, "get_profile_period",
        lambda name: None if name == "season" else original(name))
    return path


def _write(path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_unregistered_period_is_ignored_not_fatal(profile_path):
    """一条定义用了未注册周期，不该让整个 profile.yaml 失效。

    旧行为是加载阶段直接抛异常，于是所有 Profile 功能一起瘫掉——而问题只在
    一条定义上。
    """
    _write(profile_path, """
quota:
  - key: weekly_ok
    group: 周常
    period: week
  - key: zhige_of_season
    group: 赛季
    period: season
""")

    schema = reload_profile_config()

    assert [kd.key for kd in schema.get_all_keys()] == ["weekly_ok"]
    assert schema.get_key("zhige_of_season") is None, (
        "核心算不出它的周期边界，不能进索引，否则后台 tick 会持续失败")
    assert schema.get_model_type("zhige_of_season") is None


def test_ignored_definition_survives_a_save_untouched(profile_path):
    """写回时必须原样还原：不得删除，也不得擅自改周期。"""
    _write(profile_path, """
quota:
  - key: weekly_ok
    group: 周常
    period: week
  - key: zhige_of_season
    group: 赛季
    period: season
    reset_day: 3
    label: 执戈
""")
    schema = reload_profile_config()

    save_profile_config(schema)

    data = profile_path.read_text(encoding="utf-8")
    assert "zhige_of_season" in data, "保存不得删除看不懂的定义"
    assert "season" in data, "更不得把它的周期改成核心认识的值"
    restored = reload_profile_config()
    raw = restored.ignored_by_model[MODEL_QUOTA]
    assert [item[1]["key"] for item in raw] == ["zhige_of_season"]
    assert raw[0][1]["period"] == "season"
    assert raw[0][1]["reset_day"] == 3
    assert raw[0][1]["label"] == "执戈"


def test_ignored_definition_keeps_its_position(profile_path):
    """按原始下标插回原位，避免每次保存都把文件顺序搅一遍。"""
    _write(profile_path, """
quota:
  - key: a
    period: week
  - key: zhige_of_season
    period: season
  - key: b
    period: week
""")
    schema = reload_profile_config()

    save_profile_config(schema)

    keys = [item["key"] for item in
            __import__("yaml").safe_load(
                profile_path.read_text(encoding="utf-8"))["quota"]]
    assert keys == ["a", "zhige_of_season", "b"]


def test_unknown_top_level_section_survives(profile_path):
    """整段不认识的小节（插件自带的模型类型）原样保留。"""
    _write(profile_path, """
quota:
  - key: weekly_ok
    period: week
yysls_custom:
  - key: plugin_only
    whatever: 1
""")
    schema = reload_profile_config()
    assert schema.unknown_sections["yysls_custom"] == [
        {"key": "plugin_only", "whatever": 1}]

    save_profile_config(schema)

    data = __import__("yaml").safe_load(
        profile_path.read_text(encoding="utf-8"))
    assert data["yysls_custom"] == [{"key": "plugin_only", "whatever": 1}]


def test_unknown_fields_on_a_known_key_survive(profile_path):
    """已知定义上的陌生字段同样不能被抹掉。

    `from_dict` 只认已知字段，不留底的话插件自带的字段会在下一次保存时静默
    消失——那也是改用户数据。
    """
    _write(profile_path, """
quota:
  - key: weekly_ok
    group: 周常
    period: week
    plugin_hint: 保留我
""")
    schema = reload_profile_config()

    save_profile_config(schema)

    data = __import__("yaml").safe_load(
        profile_path.read_text(encoding="utf-8"))
    assert data["quota"][0]["plugin_hint"] == "保留我"


def test_clearing_a_known_field_is_not_resurrected(profile_path):
    """已知字段以当前值为准：清回默认值就该从文件里消失。

    留底只补"不认识的字段"，否则编辑器改不动任何东西。
    """
    _write(profile_path, """
quota:
  - key: weekly_ok
    period: week
    cap: 600
""")
    schema = reload_profile_config()
    key_def = schema.get_key("weekly_ok")
    assert key_def is not None
    key_def.cap = None

    save_profile_config(schema)

    data = __import__("yaml").safe_load(
        profile_path.read_text(encoding="utf-8"))
    assert "cap" not in data["quota"][0]


def test_saving_a_newly_made_unknown_period_is_still_rejected(profile_path):
    """编辑器里造出核心不认识的周期仍然要被拒绝。

    留底针对的是"文件里本来就有、我们看不懂"的数据；新写出去的定义不在此列，
    否则就等于默许写入永远算不出边界的配置。
    """
    from lvjiang.core.profile.models import QuotaKeyDef

    schema = ProfileSchema(keys_by_model={
        MODEL_QUOTA: [QuotaKeyDef(key="new_key", period="season")],
    })

    with pytest.raises(ValueError, match="season"):
        save_profile_config(schema)


def test_tests_never_touch_the_real_profile(profile_path, tmp_path):
    """用例读的是私有副本：不该也不可能感知真实用户数据里的插件周期。"""
    assert tmp_path in profile_path.parents
    assert not profile_path.exists()

    assert get_profile_config().get_all_keys() == []


def test_editor_save_from_scratch_still_keeps_ignored_definitions(
    profile_path,
):
    """定义编辑器从自己的草稿新建 schema 保存，也不能删掉留底数据。

    编辑器看不见这些定义（那正是"忽略"的本意），所以它构造的 schema 里没有
    它们。保留这件事必须落在保存入口，不能依赖每个调用方记得。
    """
    from lvjiang.core.profile.models import QuotaKeyDef

    _write(profile_path, """
quota:
  - key: weekly_ok
    period: week
  - key: zhige_of_season
    period: season
yysls_custom:
  - key: plugin_only
""")
    reload_profile_config()

    # 编辑器的保存路径：只带着自己草稿里的那几条
    save_profile_config(ProfileSchema(keys_by_model={
        MODEL_QUOTA: [QuotaKeyDef(key="weekly_ok", period="week")],
    }))

    data = __import__("yaml").safe_load(
        profile_path.read_text(encoding="utf-8"))
    assert [item["key"] for item in data["quota"]] == [
        "weekly_ok", "zhige_of_season"]
    assert data["quota"][1]["period"] == "season"
    assert data["yysls_custom"] == [{"key": "plugin_only"}]


def test_deleting_a_recognized_key_still_works(profile_path):
    """能看懂的定义照旧可以删——保留只针对看不懂的那些。"""
    from lvjiang.core.profile.models import QuotaKeyDef

    _write(profile_path, """
quota:
  - key: a
    period: week
  - key: b
    period: week
""")
    reload_profile_config()

    save_profile_config(ProfileSchema(keys_by_model={
        MODEL_QUOTA: [QuotaKeyDef(key="a", period="week")],
    }))

    data = __import__("yaml").safe_load(
        profile_path.read_text(encoding="utf-8"))
    assert [item["key"] for item in data["quota"]] == ["a"]
