"""用户资料编辑不能丢失按方案隔离的插件分析设置。"""
import json

import pytest

from lvjiang.apps.yysls.config.analysis_settings import (
    get_graduation_analysis_settings,
    set_graduation_analysis_settings,
)
from lvjiang.core.user_config import User, load_user_metadata, save_user_metadata


def test_analysis_settings_keep_file_shape_and_unrelated_fields(tmp_path):
    save_user_metadata(User("tester", attributes={"note": "preserved"}), tmp_path)
    first = {"minimum_rating": "优秀", "candidate_rating_rules": [["rule", "style"]]}
    set_graduation_analysis_settings("tester", "a", first, tmp_path)
    set_graduation_analysis_settings("tester", "b", {"minimum_rating": "顶级"}, tmp_path)
    first["candidate_rating_rules"].clear()
    saved = get_graduation_analysis_settings("tester", "a", tmp_path)
    assert saved["candidate_rating_rules"] == [["rule", "style"]]
    saved.clear()
    user = load_user_metadata("tester", tmp_path)
    assert user is not None
    user.attributes["other"] = "new"
    save_user_metadata(user, tmp_path)
    disk = json.loads((tmp_path / "tester.json").read_text(encoding="utf-8"))
    assert disk["graduation_analysis"] == {
        "a": {"minimum_rating": "优秀", "candidate_rating_rules": [["rule", "style"]]},
        "b": {"minimum_rating": "顶级"},
    }
    assert disk["attributes"] == {"note": "preserved", "other": "new"}
    assert get_graduation_analysis_settings("tester", "missing", tmp_path) == {}


def test_unchanged_analysis_settings_do_not_write_metadata(tmp_path, monkeypatch):
    from lvjiang.core import user_config

    save_user_metadata(User("tester"), tmp_path)
    value = {"minimum_rating": "优秀"}
    set_graduation_analysis_settings("tester", "a", value, tmp_path)

    def unexpected_write(*args, **kwargs):
        pytest.fail("保存相同分析选项不应重写用户资料")

    monkeypatch.setattr(user_config, "atomic_write_text", unexpected_write)
    set_graduation_analysis_settings("tester", "a", value, tmp_path)
    assert get_graduation_analysis_settings("tester", "a", tmp_path) == value
