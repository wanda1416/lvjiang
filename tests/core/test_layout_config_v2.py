"""layouts.yaml schema v2 和稳定布局身份约束。"""

from pathlib import Path

import pytest
import yaml

from lvjiang.core.layout_config import parse_layout_entries


def test_layout_v2_separates_stable_key_and_display_name():
    entries = parse_layout_entries({
        "schema_version": 2,
        "layouts": {
            "android": {
                "name": "安卓布局",
                "canvas": {"w_ratio": 1.0, "h_ratio": 1.0},
            },
            "android_cast": {
                "name": "投屏布局",
                "extends": "android",
                "canvas": {"w_ratio": 1.0, "h_ratio": 0.92},
            },
        },
    })

    assert entries["android"].name == "安卓布局"
    assert entries["android_cast"].extends == "android"


@pytest.mark.parametrize("version", [None, 1, "2"])
def test_old_or_malformed_layout_schema_is_rejected(version):
    doc = {"layouts": {"android": {"name": "安卓布局"}}}
    if version is not None:
        doc["schema_version"] = version

    with pytest.raises(ValueError, match="仅支持 schema_version: 2"):
        parse_layout_entries(doc)


def test_duplicate_display_names_are_rejected():
    with pytest.raises(ValueError, match="布局名称重复"):
        parse_layout_entries({
            "schema_version": 2,
            "layouts": {
                "android": {"name": "同名布局"},
                "desktop": {"name": "同名布局"},
            },
        })


@pytest.mark.parametrize("key", ["Desktop", "desktop-cast", " desktop"])
def test_layout_key_must_be_stable_lower_snake_case(key):
    with pytest.raises(ValueError, match="布局 key"):
        parse_layout_entries({
            "schema_version": 2,
            "layouts": {key: {"name": "测试布局"}},
        })


def test_system_layout_keys_have_matching_layout_and_template_directories():
    root = Path(__file__).resolve().parents[2]
    doc = yaml.safe_load(
        (root / "config/system/layouts.yaml").read_text(encoding="utf-8"))
    entries = parse_layout_entries(doc)

    assert list(entries) == [
        "android", "android_cast", "desktop", "desktop_fullscreen"]
    assert [entry.name for entry in entries.values()] == [
        "安卓布局", "投屏布局", "桌面布局", "桌面全屏",
    ]
    # 根布局各自拥有场景定义与模板目录
    for key in ("android", "desktop"):
        assert (root / "config/system/layouts" / key).is_dir()
        assert (root / "config/system/templates" / key).is_dir()
    # 继承布局只有画布，区域实时读根布局——多出目录就说明它被当根布局标注过了
    for key in ("android_cast", "desktop_fullscreen"):
        assert entries[key].extends
        assert not (root / "config/system/layouts" / key).exists()


class TestAspectDeclaration:
    """画布尺寸要求：声明写法、非法值和随包预置值"""

    def test_accepts_ratio_pair_and_decimal(self):
        from lvjiang.core.layout_config import parse_aspect_ratio
        assert parse_aspect_ratio("16:9") == pytest.approx(16 / 9)
        assert parse_aspect_ratio("20:9") == pytest.approx(20 / 9)
        # 写法不同但同一块屏幕必须算出同一个数，否则同一台机器会一个过一个不过
        assert parse_aspect_ratio("2560:1440") == pytest.approx(
            parse_aspect_ratio("16:9"))
        assert parse_aspect_ratio("2.22") == pytest.approx(2.22)
        assert parse_aspect_ratio("20：9") == pytest.approx(20 / 9)

    def test_blank_means_no_requirement(self):
        from lvjiang.core.layout_config import parse_aspect_ratio
        assert parse_aspect_ratio("") is None
        assert parse_aspect_ratio(None) is None

    def test_rejects_sexagesimal_accident(self):
        """YAML 1.1 把不加引号的 20:9 读成 1209，不能让它静默变成永不满足的要求。"""
        from lvjiang.core.layout_config import parse_aspect_ratio
        with pytest.raises(ValueError, match="六十进制"):
            parse_aspect_ratio(1209)

    def test_rejects_garbage(self):
        from lvjiang.core.layout_config import parse_aspect_ratio
        for bad in ("abc", "16:0", "-2", True):
            with pytest.raises(ValueError):
                parse_aspect_ratio(bad)

    def test_manifest_rejects_invalid_aspect_and_tolerance(self):
        from lvjiang.core.layout_config import parse_layout_entries
        base = {"schema_version": 2, "layouts": {"x": {"name": "X"}}}

        doc = {**base, "layouts": {"x": {"name": "X", "aspect": "abc"}}}
        with pytest.raises(ValueError, match="aspect 无效"):
            parse_layout_entries(doc)

        for bad in (0, -0.1, 0.5, "0.01"):
            doc = {**base,
                   "layouts": {"x": {"name": "X", "aspect_tolerance": bad}}}
            with pytest.raises(ValueError, match="aspect_tolerance"):
                parse_layout_entries(doc)

    def test_default_tolerance_is_half_percent(self):
        from lvjiang.core.layout_config import (
            DEFAULT_ASPECT_TOLERANCE,
            parse_layout_entries,
        )
        entries = parse_layout_entries(
            {"schema_version": 2, "layouts": {"x": {"name": "X"}}})
        assert DEFAULT_ASPECT_TOLERANCE == 0.005
        assert entries["x"].aspect_tolerance == 0.005
        assert entries["x"].aspect == ""
        assert entries["x"].aspect_ratio is None

    def test_shipped_layouts_declare_expected_aspects(self):
        """随包布局的尺寸要求必须真的解析成屏幕比例，不是 YAML 六十进制那个数。"""
        root = Path(__file__).resolve().parents[2]
        doc = yaml.safe_load(
            (root / "config/system/layouts.yaml").read_text(encoding="utf-8"))
        entries = parse_layout_entries(doc)
        assert entries["android"].aspect_ratio == pytest.approx(20 / 9)
        assert entries["desktop"].aspect_ratio == pytest.approx(16 / 9)
        assert entries["desktop_fullscreen"].aspect_ratio == pytest.approx(16 / 9)
        # 投屏窗口的边框因模拟器而异，刻意不声明
        assert entries["android_cast"].aspect == ""

    def test_full_canvas_layouts_keep_the_strict_default(self):
        """画布覆盖整张截图的布局没有边框像素参与，任何同比例分辨率都精确命中。"""
        from lvjiang.core.layout_config import DEFAULT_ASPECT_TOLERANCE
        root = Path(__file__).resolve().parents[2]
        doc = yaml.safe_load(
            (root / "config/system/layouts.yaml").read_text(encoding="utf-8"))
        entries = parse_layout_entries(doc)
        for key in ("android", "desktop_fullscreen"):
            entry = entries[key]
            canvas = entry.canvas
            assert (canvas["x_ratio"], canvas["y_ratio"]) == (0.0, 0.0)
            assert (canvas["w_ratio"], canvas["h_ratio"]) == (1.0, 1.0)
            assert entry.aspect_tolerance == DEFAULT_ASPECT_TOLERANCE

    def test_shipped_desktop_tolerance_covers_common_resolutions(self):
        """窗口边框是固定像素、画布是比例，所以只有 1920x1080 正好命中 16:9。

        带边框的布局容差必须放得下 1280x720~3840x2160 这一段漂移，同时拦住真正的
        形态错配（窗口模式配「桌面全屏」约 2.7%）。
        """
        from lvjiang.core.layout_config import canvas_aspect_deviation
        root = Path(__file__).resolve().parents[2]
        doc = yaml.safe_load(
            (root / "config/system/layouts.yaml").read_text(encoding="utf-8"))
        entry = parse_layout_entries(doc)["desktop"]
        canvas = entry.canvas
        expected = entry.aspect_ratio
        assert expected is not None
        for client_w, client_h in ((1280, 720), (1920, 1080),
                                   (2560, 1440), (3840, 2160)):
            image_w, image_h = client_w + 16, client_h + 39
            deviation = canvas_aspect_deviation(
                expected,
                canvas["w_ratio"] * image_w,
                canvas["h_ratio"] * image_h,
            )
            assert deviation is not None
            assert deviation <= entry.aspect_tolerance, (
                f"{client_w}x{client_h} 偏差 {deviation:.4%} 超过容差")
        # 全屏画面配窗口布局：这种错配必须拦住
        mismatch = canvas_aspect_deviation(
            expected, canvas["w_ratio"] * 1920, canvas["h_ratio"] * 1080)
        assert mismatch is not None and mismatch > entry.aspect_tolerance
