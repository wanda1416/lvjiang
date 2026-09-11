"""布局 Region 的固定 UI 模板绑定。"""

import cv2
import numpy as np
import pytest

from lvjiang.core.layout_models import Region, TemplateBinding
from lvjiang.core.recognizers import template_locator as tl
from lvjiang.core.recognizers.template_locator import (
    normalize_template_name,
    validate_template_image,
)
from lvjiang.workflows.grammar import parse_text
from lvjiang.workflows.static_check import check_refs
from lvjiang.workflows.workflow_references import collect_refs


def test_template_binding_roundtrip_and_optional_omission():
    plain = Region("plain", 0.1, 0.2, 0.3, 0.4)
    assert "template" not in plain.to_dict()

    region = Region(
        "logo", 0.1, 0.2, 0.3, 0.4,
        template=TemplateBinding(
            "默认布局/general_control/logo", 0.87, 2400, 1080),
    )
    data = region.to_dict()

    assert data["template"] == {
        "name": "默认布局/general_control/logo",
        "min_score": 0.87,
        "record_w": 2400,
        "record_h": 1080,
    }
    assert Region.from_dict(data).template == region.template
    assert region.clone().template == region.template


@pytest.mark.parametrize("name", ["", "../secret", "/absolute", "a\\b"])
def test_template_name_rejects_unsafe_paths(name):
    with pytest.raises(ValueError, match="模板名"):
        TemplateBinding(name)
    with pytest.raises(ValueError, match="模板名"):
        normalize_template_name(name)


@pytest.mark.parametrize("score", [-0.01, 1.01, float("nan")])
def test_template_score_must_be_probability(score):
    with pytest.raises(ValueError, match="阈值"):
        TemplateBinding("logo", score)


def test_template_record_size_must_be_complete():
    with pytest.raises(ValueError, match="宽高"):
        TemplateBinding("logo", record_w=1920, record_h=0)


def test_template_image_rejects_tiny_and_flat_crops():
    with pytest.raises(ValueError, match="4×4"):
        validate_template_image(np.zeros((3, 10, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="纯色"):
        validate_template_image(np.zeros((10, 10, 3), dtype=np.uint8))


def test_template_image_accepts_structured_crop():
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    image[2:8, 2:8] = 255
    validate_template_image(image)


def test_store_rejects_flat_template_from_disk(tmp_path):
    """磁盘模板可能来自 system/remote/手工复制，不能依赖 UI 入口校验。"""
    cv2.imwrite(str(tmp_path / "flat.png"), np.zeros((20, 20, 3), dtype=np.uint8))

    assert tl.TemplateStore(tmp_path).get("flat") is None


def test_write_template_creates_png_only(monkeypatch):
    writes = []
    resolver = type("Resolver", (), {
        "write_entity": lambda self, path, data: writes.append((path, data)),
    })()
    from lvjiang.core.config import resolver as resolver_mod
    monkeypatch.setattr(resolver_mod, "get_resolver", lambda: resolver)
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    image[2:8, 2:8] = 255

    tl.write_template("layout/scene/logo", image)

    assert [path for path, _data in writes] == [
        "templates/layout/scene/logo.png",
    ]
    assert isinstance(writes[0][1], bytes)


def test_delete_template_removes_dev_png_only(tmp_path, monkeypatch):
    from lvjiang.core.config import resolver as resolver_mod
    system = tmp_path / "system"
    local = tmp_path / "local"
    target = system / "templates/layout/scene/logo.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"png")
    resolver = resolver_mod.ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=True)
    monkeypatch.setattr(resolver_mod, "get_resolver", lambda: resolver)

    assert tl.delete_template("layout/scene/logo") is True
    assert not target.exists()


def test_delete_template_user_removes_local_shadow_not_system(
    tmp_path, monkeypatch,
):
    from lvjiang.core.config import resolver as resolver_mod
    system = tmp_path / "system"
    local = tmp_path / "local"
    rel = "templates/layout/scene/logo.png"
    system_target = system / rel
    local_target = local / rel
    system_target.parent.mkdir(parents=True)
    local_target.parent.mkdir(parents=True)
    system_target.write_bytes(b"system")
    local_target.write_bytes(b"local")
    resolver = resolver_mod.ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=False)
    monkeypatch.setattr(resolver_mod, "get_resolver", lambda: resolver)

    assert tl.delete_template("layout/scene/logo") is True
    assert system_target.read_bytes() == b"system"
    assert not local_target.exists()


class _Layout:
    def __init__(self, region):
        self.region = region

    def get_scene_regions(self, scene):
        return [self.region]

    def get_scene_points(self, scene):
        return []

    def get_scene_arrows(self, scene):
        return []

    def get_scene_panels(self, scene):
        return []


def _template_refs():
    program = parse_text("scan [scene].[logo] as $hit by image\n")
    return collect_refs(program.body, program.procs)


def _find_template_source_refs():
    program = parse_text(
        "find as $hit by image [scene].[logo]\n")
    return collect_refs(program.body, program.procs)


def test_static_check_requires_template_binding():
    refs = _template_refs()
    assert refs[0].kind == "template_scan"

    problems = check_refs(refs, _Layout(Region("logo", 0, 0, 1, 1)))

    assert [p.reason for p in problems] == ["区域未绑定模板"]


def test_static_check_requires_template_file(tmp_path, monkeypatch):
    region = Region(
        "logo", 0, 0, 1, 1, template=TemplateBinding("scene/logo"))
    store = tl.TemplateStore(tmp_path)
    monkeypatch.setattr(tl, "_STORE", store)
    assert "模板文件不存在" in check_refs(
        _template_refs(), _Layout(region))[0].reason

    path = tmp_path / "scene"
    path.mkdir()
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    image[2:8, 2:8] = 255
    cv2.imwrite(str(path / "logo.png"), image)

    assert check_refs(_template_refs(), _Layout(region)) == []


def test_find_template_source_static_check_allows_disabled_region(
    tmp_path, monkeypatch,
):
    region = Region(
        "logo", 0, 0, 0, 0, disabled=True,
        template=TemplateBinding("scene/logo"),
    )
    store = tl.TemplateStore(tmp_path)
    monkeypatch.setattr(tl, "_STORE", store)
    path = tmp_path / "scene"
    path.mkdir()
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    image[2:8, 2:8] = 255
    cv2.imwrite(str(path / "logo.png"), image)

    refs = _find_template_source_refs()
    assert refs[0].kind == "template_source"
    assert check_refs(refs, _Layout(region)) == []
