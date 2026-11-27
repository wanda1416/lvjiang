"""独立任务包（Standalone Task Package）与场景管理双模式回归测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from lvjiang.core.config.resolver import (
    ConfigResolver,
    get_resolver,
)
from lvjiang.core.layout.models import CanvasConfig, Layout, Region
from lvjiang.core.layout.screenshots import (
    layout_screenshots_dir,
    use_screenshots_dir,
)
from lvjiang.core.scene.config import load_scene_manifest
from lvjiang.core.scene.definition import SceneRegistry
from lvjiang.core.scene.registry import get_registry
from lvjiang.workflows.errors import WorkflowUserError
from lvjiang.workflows.package import (
    create_package_resolver,
    discover_task_packages,
    find_task_package_for_wf,
    package_screenshots_dir,
    task_package_runtime_scope,
)


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_base_config(root_sys: Path) -> None:
    """构造最小化的全局主配置（scenes + layouts: desktop / android / android_cast + templates）。"""
    _write_json(root_sys / "scenes" / "_meta.json", {"groups": [{"key": "general", "scenes": ["game_main_page"]}]})
    _write_yaml(
        root_sys / "scenes" / "general.yaml",
        {"name": "通用", "scenes": ["game_main_page"]},
    )
    _write_yaml(
        root_sys / "scenes" / "general" / "game_main_page.yaml",
        {
            "key": "game_main_page",
            "name": "主界面",
            "regions": [{"key": "enter_btn", "name": "进入按钮"}],
            "points": [],
            "panels": [],
        },
    )

    _write_json(
        root_sys / "layouts" / "_meta.json",
        {"layouts": ["desktop", "android", "android_cast"]},
    )
    _write_yaml(
        root_sys / "layouts" / "desktop.yaml",
        {"name": "桌面端", "canvas": {"x": 0, "y": 0, "w": 1920, "h": 1080}},
    )
    _write_yaml(
        root_sys / "layouts" / "android.yaml",
        {"name": "安卓端", "canvas": {"x": 0, "y": 0, "w": 2400, "h": 1080}},
    )
    _write_yaml(
        root_sys / "layouts" / "android_cast.yaml",
        {
            "name": "安卓投屏",
            "extends": "android",
            "canvas": {"x": 0, "y": 0, "w": 1920, "h": 864},
        },
    )
    _write_json(
        root_sys / "layouts" / "desktop" / "general" / "game_main_page.json",
        {
            "regions": [
                {"key": "enter_btn", "x_ratio": 0.1, "y_ratio": 0.1, "w_ratio": 0.2, "h_ratio": 0.1}
            ]
        },
    )
    _write_json(
        root_sys / "layouts" / "android" / "general" / "game_main_page.json",
        {
            "regions": [
                {"key": "enter_btn", "x_ratio": 0.2, "y_ratio": 0.2, "w_ratio": 0.2, "h_ratio": 0.1}
            ]
        },
    )
    (root_sys / "templates").mkdir(parents=True, exist_ok=True)
    (root_sys / "templates" / "global_ok.png").write_bytes(b"GLOBAL_PNG")


def _seed_task_package(
    pkg_dir: Path,
    *,
    pkg_id: str = "fengshajiusi",
    group_key: str = "fsjs",
    scene_key: str = "fsjs_lobby",
    entry: str = "fengshajiusi.wf",
    include_template: bool = True,
) -> Path:
    """在指定目录构造标准独立任务包（manifest.yaml + .wf + scenes/ + layouts/）。"""
    _write_yaml(
        pkg_dir / "manifest.yaml",
        {
            "schema_version": 1,
            "id": pkg_id,
            "name": "风沙酒肆",
            "entry": entry,
            "version": "1.0.0",
        },
    )
    wf_file = pkg_dir / entry
    wf_file.parent.mkdir(parents=True, exist_ok=True)
    wf_file.write_text(
        "#% name: 风沙酒肆\n#% runnable: true\nset $ok = 1\nreturn $ok\n",
        encoding="utf-8",
    )

    _write_json(pkg_dir / "scenes" / "_meta.json", {"groups": [{"key": group_key, "scenes": [scene_key]}]})
    _write_yaml(
        pkg_dir / "scenes" / f"{group_key}.yaml",
        {"name": "风沙酒肆", "scenes": [scene_key]},
    )
    _write_yaml(
        pkg_dir / "scenes" / group_key / f"{scene_key}.yaml",
        {
            "key": scene_key,
            "name": "酒肆大厅",
            "regions": [{"key": "drink_btn", "name": "喝酒按钮"}],
            "points": [],
            "panels": [],
        },
    )

    _write_json(
        pkg_dir / "layouts" / "_meta.json",
        {"layouts": ["desktop", "android"]},
    )
    _write_yaml(
        pkg_dir / "layouts" / "desktop.yaml",
        {"name": "桌面端", "canvas": {"x": 0, "y": 0, "w": 1920, "h": 1080}},
    )
    _write_yaml(
        pkg_dir / "layouts" / "android.yaml",
        {"name": "安卓端", "canvas": {"x": 0, "y": 0, "w": 2400, "h": 1080}},
    )
    _write_json(
        pkg_dir / "layouts" / "desktop" / group_key / f"{scene_key}.json",
        {
            "regions": [
                {"key": "drink_btn", "x_ratio": 0.5, "y_ratio": 0.5, "w_ratio": 0.1, "h_ratio": 0.1}
            ]
        },
    )
    _write_json(
        pkg_dir / "layouts" / "android" / group_key / f"{scene_key}.json",
        {
            "regions": [
                {"key": "drink_btn", "x_ratio": 0.7, "y_ratio": 0.6, "w_ratio": 0.12, "h_ratio": 0.08}
            ]
        },
    )
    if include_template:
        (pkg_dir / "templates").mkdir(parents=True, exist_ok=True)
        (pkg_dir / "templates" / "fsjs_icon.png").write_bytes(b"PKG_PNG")
    return wf_file


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """隔离的 root ConfigResolver + 基础 SceneRegistry，不触碰真实仓库目录。"""
    sys_dir = tmp_path / "system"
    loc_dir = tmp_path / "local"
    sys_dir.mkdir()
    loc_dir.mkdir()
    _seed_base_config(sys_dir)

    root_resolver = ConfigResolver(
        system_dir=sys_dir,
        local_dir=loc_dir,
        dev_mode=False,
        track_content=False,
    )
    manifest = load_scene_manifest(root_resolver)
    base_registry = SceneRegistry(
        resolver=root_resolver,
        scene_order=manifest.order if manifest else None,
        group_config=manifest.groups if manifest else None,
        group_names=manifest.group_names if manifest else None,
        disabled_scenes=manifest.disabled if manifest else None,
        group_layers=manifest.group_layers if manifest else None,
        scene_layers=manifest.scene_layers if manifest else None,
    )

    monkeypatch.setattr("lvjiang.core.config.resolver._resolver", root_resolver)
    monkeypatch.setattr("lvjiang.core.scene.registry._registry", base_registry)
    return tmp_path, sys_dir, loc_dir, root_resolver, base_registry


def test_ordinary_task_untouched(isolated_env):
    """普通任务（无 manifest.yaml）完全走原路径，不修改布局、解析器或注册表。"""
    _, sys_dir, _, root_resolver, base_registry = isolated_env
    ordinary_wf = sys_dir / "workflows" / "daily" / "ordinary.wf"
    ordinary_wf.parent.mkdir(parents=True, exist_ok=True)
    ordinary_wf.write_text("#% runnable: true\nreturn 1\n", encoding="utf-8")

    assert find_task_package_for_wf(ordinary_wf) is None

    base_layout = Layout(
        key="desktop",
        name="桌面端",
        canvas=CanvasConfig(),
        regions={"game_main_page": [Region(key="enter_btn", x_ratio=0.1, y_ratio=0.1, w_ratio=0.2, h_ratio=0.1)]},
    )
    with task_package_runtime_scope(ordinary_wf, base_layout) as active_layout:
        assert active_layout is base_layout
        assert get_resolver() is root_resolver
        assert get_registry() is base_registry


def test_manifest_entry_path_traversal_rejected(isolated_env):
    """manifest.yaml 中的 entry 禁止路径穿越、绝对路径或非 .wf 后缀。"""
    _, _, loc_dir, _, _ = isolated_env
    pkg_dir = loc_dir / "workflows" / "standalone" / "badpkg"
    pkg_dir.mkdir(parents=True)
    wf = pkg_dir / "badpkg.wf"
    wf.write_text("#% runnable: true\n", encoding="utf-8")

    for bad_entry in ("../outside.wf", "/abs/path.wf", "script.py"):
        _write_yaml(pkg_dir / "manifest.yaml", {"id": "badpkg", "entry": bad_entry})
        with pytest.raises(WorkflowUserError):
            find_task_package_for_wf(wf)


def test_discover_task_packages_and_shadow_layer(isolated_env):
    """发现层识别 system 与 local 独立包，local 影子优先。"""
    _, sys_dir, loc_dir, root_resolver, _ = isolated_env
    _seed_task_package(sys_dir / "workflows" / "standalone" / "fengshajiusi")
    _seed_task_package(loc_dir / "workflows" / "thirdparty" / "my_pkg", pkg_id="my_pkg", group_key="mp", scene_key="mp_home", entry="my_pkg.wf")

    pkgs = discover_task_packages(root_resolver)
    ids = [p.id for p in pkgs]
    assert ids == ["fengshajiusi", "my_pkg"]
    assert pkgs[0].layer == "system"
    assert pkgs[1].layer == "local"

    # system 包在用户模式下写 local 影子
    res_sys = create_package_resolver(pkgs[0], root_resolver)
    assert res_sys.system_dir == sys_dir / "workflows" / "standalone" / "fengshajiusi"
    assert res_sys.local_dir == loc_dir / "workflows" / "standalone" / "fengshajiusi"


def test_duplicate_group_or_scene_raises_on_start(isolated_env):
    """任务包分组或场景与全局重名时，启动即报错中止。"""
    _, _, loc_dir, _, _ = isolated_env
    # 1. 分组重名（general）
    wf_dup_group = _seed_task_package(
        loc_dir / "workflows" / "standalone" / "dup_group",
        pkg_id="dup_group",
        group_key="general",
        scene_key="fsjs_unique",
        entry="dup_group.wf",
    )
    with pytest.raises(WorkflowUserError, match="重复分组: general"):
        with task_package_runtime_scope(wf_dup_group, None):
            pass

    # 2. 场景重名（game_main_page）
    wf_dup_scene = _seed_task_package(
        loc_dir / "workflows" / "standalone" / "dup_scene",
        pkg_id="dup_scene",
        group_key="fsjs_unique_group",
        scene_key="game_main_page",
        entry="dup_scene.wf",
    )
    with pytest.raises(WorkflowUserError, match="重复场景: game_main_page"):
        with task_package_runtime_scope(wf_dup_scene, None):
            pass


def test_runtime_scope_merges_same_named_layouts_and_releases_on_exit(isolated_env):
    """运行期按同名根布局（desktop / android / android_cast 别名）合并，退出后彻底释放。"""
    _, _, loc_dir, root_resolver, base_registry = isolated_env
    wf = _seed_task_package(loc_dir / "workflows" / "standalone" / "fengshajiusi")

    desktop_layout = Layout(
        key="desktop",
        name="桌面端",
        canvas=CanvasConfig(),
        regions={"game_main_page": [Region(key="enter_btn", x_ratio=0.1, y_ratio=0.1, w_ratio=0.2, h_ratio=0.1)]},
    )
    android_cast_layout = Layout(
        key="android_cast",
        name="安卓投屏",
        canvas=CanvasConfig(),
        regions={"game_main_page": [Region(key="enter_btn", x_ratio=0.2, y_ratio=0.2, w_ratio=0.2, h_ratio=0.1)]},
    )

    # 1. 桌面端同名布局合并 + 模板优先与回退
    with task_package_runtime_scope(wf, desktop_layout) as merged:
        assert merged is not None and merged is not desktop_layout
        assert "game_main_page" in merged.regions
        assert "fsjs_lobby" in merged.regions
        assert merged.get_scene_regions("fsjs_lobby")[0].x_ratio == pytest.approx(0.5)
        assert get_registry().get_scene("fsjs_lobby") is not None
        assert get_registry().get_scene("game_main_page") is not None
        # 模板叠加：包内优先 + 全局回退
        assert get_resolver().resolve_read("templates/fsjs_icon.png") is not None
        assert get_resolver().resolve_read("templates/global_ok.png") is not None

    # 退出后彻底释放，不污染全局注册表和原始 Layout
    assert get_registry() is base_registry
    assert get_registry().get_scene("fsjs_lobby") is None
    assert "fsjs_lobby" not in desktop_layout.regions
    assert get_resolver() is root_resolver
    assert get_resolver().resolve_read("templates/fsjs_icon.png") is None

    # 2. 安卓投屏别名布局（extends: android）自动回退合并任务包的 android 布局，且异常也必定释放
    with pytest.raises(RuntimeError, match="boom"):
        with task_package_runtime_scope(wf, android_cast_layout) as merged_android:
            assert merged_android is not None
            assert merged_android.get_scene_regions("fsjs_lobby")[0].x_ratio == pytest.approx(0.7)
            raise RuntimeError("boom")

    assert get_registry() is base_registry
    assert get_registry().get_scene("fsjs_lobby") is None
    assert "fsjs_lobby" not in android_cast_layout.regions


def test_screenshots_isolated_per_task_package(isolated_env, tmp_path: Path):
    """任务包截图目录隔离在 _packages/<id>/ 下，不与全局 desktop 截图目录冲突。"""
    _, _, loc_dir, _, _ = isolated_env
    wf = _seed_task_package(loc_dir / "workflows" / "standalone" / "fengshajiusi")
    pkg = find_task_package_for_wf(wf)
    assert pkg is not None

    fake_shots = tmp_path / "screenshots"
    with use_screenshots_dir(fake_shots):
        main_desktop_dir = layout_screenshots_dir("desktop")
        pkg_root = package_screenshots_dir(pkg)
        with use_screenshots_dir(pkg_root):
            pkg_desktop_dir = layout_screenshots_dir("desktop")
        assert pkg_desktop_dir == fake_shots / "_packages" / "fengshajiusi" / "desktop"
        assert pkg_desktop_dir != main_desktop_dir
        assert layout_screenshots_dir("desktop") == main_desktop_dir


def test_workflow_engine_executes_task_package_with_static_check(isolated_env):
    """WorkflowEngine._execute_dsl 运行独立任务包时通过静态校验并在退出后释放。"""
    from lvjiang.workflows.engine import WorkflowEngine

    _, _, loc_dir, _, base_registry = isolated_env
    wf = _seed_task_package(loc_dir / "workflows" / "standalone" / "fengshajiusi")
    # 写入同时静态引用主场景与任务私有场景的脚本（放在分支中避免真触发点击，但会走 check_refs）
    wf.write_text(
        "#% name: 风沙酒肆\n"
        "#% runnable: true\n"
        "if false\n"
        "    click [game_main_page].[enter_btn]\n"
        "    click [fsjs_lobby].[drink_btn]\n"
        "end\n"
        "return 42\n",
        encoding="utf-8",
    )

    desktop_layout = Layout(
        key="desktop",
        name="桌面端",
        canvas=CanvasConfig(),
        regions={"game_main_page": [Region(key="enter_btn", x_ratio=0.1, y_ratio=0.1, w_ratio=0.2, h_ratio=0.1)]},
    )
    engine = WorkflowEngine(capture=None, input_ctrl=None, layout=desktop_layout, ocr=None)
    engine.execute(wf)
    assert engine.return_value == 42
    # 退出后引擎持有的布局还原，且全局注册表未受污染
    assert engine._layout is desktop_layout
    assert "fsjs_lobby" not in desktop_layout.regions
    assert get_registry() is base_registry
    assert get_registry().get_scene("fsjs_lobby") is None


def test_scene_editor_dual_mode_switch_and_restore(isolated_env, qtbot):
    """场景管理顶栏双模式切换：加载任务场景仅看包内场景，退出还原主场景且不污染全局。"""
    from lvjiang.ui.scene_editor.dialog import SceneEditorDialog
    _, _, loc_dir, root_resolver, base_registry = isolated_env
    wf = _seed_task_package(loc_dir / "workflows" / "standalone" / "fengshajiusi")
    pkg = find_task_package_for_wf(wf)
    assert pkg is not None

    dlg = SceneEditorDialog()
    qtbot.addWidget(dlg)
    try:
        assert dlg.active_task_package is None
        assert dlg._btn_task_package.text() == "加载任务场景"
        assert "game_main_page" in dlg._tabs
        assert "fsjs_lobby" not in dlg._tabs

        # 切入任务场景模式
        assert dlg._enter_task_package_mode(pkg) is True
        assert dlg.active_task_package == pkg
        assert dlg._btn_task_package.text() == "退出任务场景"
        assert "风沙酒肆" in dlg.windowTitle()
        assert "fsjs_lobby" in dlg._tabs
        assert "game_main_page" not in dlg._tabs
        # 全局主注册表完全未被污染
        assert base_registry.get_scene("fsjs_lobby") is None

        # 退出任务场景模式
        assert dlg._exit_task_package_mode(restore_ui=True) is True
        assert dlg.active_task_package is None
        assert dlg._btn_task_package.text() == "加载任务场景"
        assert "game_main_page" in dlg._tabs
        assert "fsjs_lobby" not in dlg._tabs
        assert get_resolver() is root_resolver
        assert get_registry() is base_registry
    finally:
        dlg.close()

def _global_only_layout() -> Layout:
    """只有全局主场景的布局：不含任何任务包私有场景。"""
    return Layout(
        key="desktop",
        name="桌面端",
        canvas=CanvasConfig(),
        regions={"game_main_page": [
            Region(key="enter_btn", x_ratio=0.1, y_ratio=0.1, w_ratio=0.2, h_ratio=0.1)
        ]},
    )


def test_validate_only_merges_package_layout(isolated_env):
    """预检要能吃下包私有场景：全局布局里没有该场景，仍应校验通过。

    回归点：``validate_only`` 不进任务包作用域时，CLI ``--dry-run``、编辑器
    「检查」与 CI 门禁都会把能跑的包判成「场景未绑定任何坐标」。
    """
    from lvjiang.workflows.engine import WorkflowEngine

    _, _, loc_dir, root_resolver, base_registry = isolated_env
    wf = _seed_task_package(loc_dir / "workflows" / "standalone" / "fengshajiusi")
    wf.write_text(
        "#% id: fsjs_demo\n"
        "#% name: 风沙酒肆\n"
        "#% runnable: true\n"
        "if false\n"
        "    click [fsjs_lobby].[drink_btn]\n"
        "    find [fsjs_lobby].[drink_btn] as $hit by contains \"喝酒\"\n"
        "end\n"
        "return 0\n",
        encoding="utf-8",
    )

    layout = _global_only_layout()
    engine = WorkflowEngine(capture=None, ocr=None, input_ctrl=None,
                            layout=layout, run_env="desktop")
    engine.validate_only(wf)

    # 作用域已释放：布局、解析器与全局注册表都没被改动
    assert engine._layout is layout
    assert "fsjs_lobby" not in layout.regions
    assert get_resolver() is root_resolver
    assert get_registry() is base_registry
    assert base_registry.get_scene("fsjs_lobby") is None


def test_validate_only_reports_missing_package_region(isolated_env):
    """包内场景漏标区域仍要报错：合并不能变成「只要有个包就放行」。"""
    from lvjiang.workflows.engine import WorkflowEngine

    _, _, loc_dir, _, _ = isolated_env
    wf = _seed_task_package(loc_dir / "workflows" / "standalone" / "fengshajiusi")
    wf.write_text(
        "#% id: fsjs_demo\n"
        "#% name: 风沙酒肆\n"
        "#% runnable: true\n"
        "find [fsjs_lobby].[drink_btn] as $hit by contains \"喝酒\"\n"
        "return 0\n",
        encoding="utf-8",
    )
    # 把包布局里那个区域删掉
    _write_json(
        loc_dir / "workflows" / "standalone" / "fengshajiusi"
        / "layouts" / "desktop" / "fsjs" / "fsjs_lobby.json",
        {"regions": []},
    )

    engine = WorkflowEngine(capture=None, ocr=None, input_ctrl=None,
                            layout=_global_only_layout(), run_env="desktop")
    with pytest.raises(WorkflowUserError) as excinfo:
        engine.validate_only(wf)
    assert "fsjs_lobby" in str(excinfo.value)


def test_validate_only_ordinary_task_keeps_global_scope(isolated_env):
    """普通脚本（同级无 manifest.yaml）预检不合并任何东西，作用域零变化。"""
    from lvjiang.workflows.engine import WorkflowEngine

    _, sys_dir, _, root_resolver, base_registry = isolated_env
    ordinary = sys_dir / "workflows" / "daily" / "ordinary.wf"
    ordinary.parent.mkdir(parents=True, exist_ok=True)
    ordinary.write_text(
        "#% name: 普通任务\n"
        "#% runnable: true\n"
        "find [game_main_page].[enter_btn] as $hit by contains \"进入\"\n"
        "return 0\n",
        encoding="utf-8",
    )

    layout = _global_only_layout()
    engine = WorkflowEngine(capture=None, ocr=None, input_ctrl=None,
                            layout=layout, run_env="desktop")
    engine.validate_only(ordinary)
    assert engine._layout is layout
    assert get_resolver() is root_resolver
    assert get_registry() is base_registry
def test_find_task_package_supports_nested_subdir_entry(isolated_env):
    """入口 .wf 放在任务包子目录（如 sub/main.wf）时仍能向上识别到 manifest.yaml。"""
    _, _, loc_dir, _, _ = isolated_env
    wf = _seed_task_package(
        loc_dir / "workflows" / "standalone" / "nested_pkg",
        pkg_id="nested_pkg",
        group_key="np",
        scene_key="np_home",
        entry="sub/main.wf",
    )
    pkg = find_task_package_for_wf(wf)
    assert pkg is not None
    assert pkg.id == "nested_pkg"
    assert pkg.entry == "sub/main.wf"


def test_task_package_runtime_scope_allows_same_package_reentry(isolated_env):
    """同一任务包内嵌套进入 task_package_runtime_scope 时复用作用域，不报自身冲突。"""
    _, _, loc_dir, _, base_registry = isolated_env
    wf = _seed_task_package(loc_dir / "workflows" / "standalone" / "fengshajiusi")
    layout = _global_only_layout()
    with task_package_runtime_scope(wf, layout) as outer_layout:
        assert outer_layout is not None and "fsjs_lobby" in outer_layout.regions
        with task_package_runtime_scope(wf, outer_layout) as inner_layout:
            assert inner_layout is outer_layout
            assert get_registry().get_scene("fsjs_lobby") is not None
    assert get_registry() is base_registry
    assert get_registry().get_scene("fsjs_lobby") is None


def test_delete_scene_across_all_layouts_respects_scoped_screenshots_dir(isolated_env, tmp_path: Path):
    """任务场景作用域内删场景时，只删除任务包截图目录中的截图，不触碰全局截图目录。"""
    from lvjiang.core.layout.references import delete_scene_across_all_layouts

    global_shots = tmp_path / "global_shots"
    pkg_shots = tmp_path / "pkg_shots"
    (global_shots / "desktop").mkdir(parents=True)
    (pkg_shots / "desktop").mkdir(parents=True)
    global_img = global_shots / "desktop" / "fsjs_lobby.png"
    pkg_img = pkg_shots / "desktop" / "fsjs_lobby.png"
    global_img.write_bytes(b"GLOBAL")
    pkg_img.write_bytes(b"PKG")

    with use_screenshots_dir(pkg_shots):
        delete_scene_across_all_layouts("fsjs_lobby")

    assert not pkg_img.exists()
    assert global_img.exists()


def test_scene_editor_cancel_close_keeps_mode_and_reject_releases(
        isolated_env, qtbot, monkeypatch):
    """点 X 取消关闭不退出任务场景；按 Esc 确认后彻底退出。

    关键契约：编辑器的作用域只在它自己的操作期间生效——进入任务场景后环境里
    仍是全局解析器/注册表，所以主窗口的脚本发现、工作流解析不会被切走。
    """
    from lvjiang.ui.scene_editor.dialog import SceneEditorDialog

    _, _, loc_dir, root_resolver, base_registry = isolated_env
    wf = _seed_task_package(loc_dir / "workflows" / "standalone" / "fengshajiusi")
    pkg = find_task_package_for_wf(wf)
    assert pkg is not None

    dlg = SceneEditorDialog()
    qtbot.addWidget(dlg)
    try:
        assert dlg._enter_task_package_mode(pkg) is True
        # 编辑器视图切到包内场景，但环境作用域仍指向全局
        assert "fsjs_lobby" in dlg._tabs
        assert get_resolver() is root_resolver
        assert get_registry() is base_registry
        assert root_resolver.resolve_read("scenes/general/game_main_page.yaml") is not None

        # 1. 点 X 后在未保存确认框点「取消」：仍在任务场景模式，作用域依然全局
        monkeypatch.setattr(dlg, "_confirm_discard_changes", lambda _action: False)
        dlg.close()
        assert dlg.active_task_package == pkg
        assert "fsjs_lobby" in dlg._tabs
        assert get_resolver() is root_resolver
        assert get_registry() is base_registry

        # 2. 按 Esc（reject）并确认放弃：彻底退出，主场景与全局作用域照常
        monkeypatch.setattr(dlg, "_confirm_discard_changes", lambda _action: True)
        dlg.reject()
        assert dlg.active_task_package is None
        # 关闭路径走 restore_ui=False，界面还原由 test_scene_editor_dual_mode_switch_and_restore
        # 覆盖；这里只保证作用域已归还全局。
        assert get_resolver() is root_resolver
        assert get_registry() is base_registry
    finally:
        dlg.close()


def test_loader_ignores_task_package_scope(isolated_env):
    """任务包作用域内，脚本发现与工作流解析仍锁死全局 config（local→system）。

    回归点：场景管理进入任务场景模式后曾把线程级 ContextVar 常驻切到包目录，
    主窗口再解析 wf 就成了「工作流文件不存在」，脚本列表也变成 0。
    """
    from lvjiang.core.config.resolver import (
        load_app_config,
        reset_scoped_resolver,
        set_scoped_resolver,
    )
    from lvjiang.workflows.discovery import discover_scripts, resolve_workflow_path

    _, sys_dir, loc_dir, root_resolver, _ = isolated_env
    _write_yaml(
        sys_dir / "app.yaml",
        {"envs": [{"key": "desktop", "name": "桌面"}, {"key": "android", "name": "安卓"}]},
    )
    wf = _seed_task_package(loc_dir / "workflows" / "standalone" / "fengshajiusi")
    pkg = find_task_package_for_wf(wf)
    assert pkg is not None

    global_ids = [cfg["id"] for cfg in discover_scripts()]
    token = set_scoped_resolver(create_package_resolver(pkg))
    try:
        assert [cfg["id"] for cfg in discover_scripts()] == global_ids
        path, used = resolve_workflow_path("standalone/fengshajiusi/fengshajiusi.wf")
        assert path is not None and path.is_file()
        assert used == "standalone/fengshajiusi/fengshajiusi.wf"
        # app.yaml 的环境列表属于全局配置事实，不能被包作用域切走
        assert [item["key"] for item in load_app_config()["envs"]] == ["desktop", "android"]
    finally:
        reset_scoped_resolver(token)
    assert get_resolver() is root_resolver
def test_in_package_scope_truncates_qt_args_for_kwonly_and_kwargs():
    """Qt 信号多传位置参数时，含仅关键字参数或 **kwargs 的槽函数也应正确截断。"""
    from lvjiang.ui.scene_editor.task_package_ops import bind_package_scope

    class Dummy:
        _package_scope_state = None

        def kwonly(self, *, flag: bool = True):
            return flag

        def with_kwargs(self, value: int = 1, **kwargs):
            return value, kwargs

    bind_package_scope(Dummy)
    obj = Dummy()
    assert obj.kwonly(False) is True
    assert obj.with_kwargs(42, "extra_qt_arg") == (42, {})
