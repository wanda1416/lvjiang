"""移动设备工具：入口、扫码、体检与装包路径。

这个工具存在的理由就是"能力都在、没有入口"：APK 只出现在 GitHub 的 Release 页，
手势探针只有命令行，设备端 status 的十几个字段一个字都没显示过。所以用例盯的是
"用户能不能从界面拿到这些东西"，而不是控件长什么样。
"""
from pathlib import Path

import pytest

from lvjiang.core.android.apk_release import (
    ApkFile,
    apk_asset_name,
    apk_download_url,
    checksums_url,
)
from lvjiang.ui.mobile.diagnostics import (
    build_checks,
    build_report,
    capability_notes,
)


def _status(**overrides) -> dict:
    base = {
        "app": "0.13.9", "protocol": 3, "sdk": 34, "a11y": True,
        "shizuku": False, "shizuku_granted": False, "max_strokes": 10,
        "screen": {"w": 1080, "h": 2400, "rotation": 0},
        "calib_identity": True, "pc_connections": 1,
        "last_op": "gesture", "last_op_ok": True,
    }
    base.update(overrides)
    return base


# ─── 资产定位 ──────────────────────────────────────────────

def test_apk_url_follows_the_pc_version_without_network():
    """二维码要能离线生成，而且指向**本机版本**对应的包。

    "最新版"是错的：PC 0.13.9 配手机 0.13.5 时协议不匹配，用户只会看到
    "连不上设备"，根本联想不到是手机上的 app 太旧。
    """
    assert apk_asset_name("0.13.9") == "lvjiang-v0.13.9.apk"
    url = apk_download_url("0.13.9")
    assert url.endswith("/releases/download/0.13.9/lvjiang-v0.13.9.apk")
    assert checksums_url("0.13.9").endswith("/0.13.9/SHA256SUMS.txt")


def test_download_verifies_checksum_and_leaves_no_half_file(tmp_path,
                                                            monkeypatch):
    """校验和不符时不留下可安装的文件——半截或被篡改的包比没有更危险。"""
    from lvjiang.core.android import apk_release

    payload = b"not-a-real-apk"

    class _Response:
        headers = {"Content-Length": str(len(payload))}

        def __init__(self):
            self._done = False

        def read(self, _n):
            if self._done:
                return b""
            self._done = True
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(apk_release, "urlopen", lambda *a, **kw: _Response())
    monkeypatch.setattr(
        apk_release, "fetch_expected_sha256", lambda *a, **kw: "deadbeef")

    with pytest.raises(apk_release.ApkDownloadError, match="校验和"):
        apk_release.download_apk("0.13.9", tmp_path)

    assert list(tmp_path.iterdir()) == [], "失败后不能留下 .part 或成品"


def test_download_accepts_when_checksum_is_unavailable(tmp_path, monkeypatch):
    """取不到 SHA256SUMS 不该拦住用户——离线、代理都可能。"""
    from lvjiang.core.android import apk_release

    class _Response:
        headers: dict = {}

        def __init__(self):
            self._done = False

        def read(self, _n):
            if self._done:
                return b""
            self._done = True
            return b"apk-bytes"

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(apk_release, "urlopen", lambda *a, **kw: _Response())
    monkeypatch.setattr(
        apk_release, "fetch_expected_sha256", lambda *a, **kw: None)

    apk = apk_release.download_apk("0.13.9", tmp_path)

    assert apk.path.exists()
    assert apk.verified is None, "未比对要如实标注，不能伪装成通过"
    assert not apk.path.with_suffix(".apk.part").exists()


# ─── 体检结论 ──────────────────────────────────────────────

def test_not_connected_says_what_to_do():
    items = build_checks({}, "0.13.9", 3)

    assert len(items) == 1
    assert items[0].level == "bad"
    assert "无障碍" in items[0].hint


def test_protocol_mismatch_points_at_the_side_to_upgrade():
    """协议不匹配现在只在日志里打一行 error，用户看到的是"连不上设备"。"""
    older_phone = build_checks(_status(protocol=2), "0.13.9", 3)
    entry = next(i for i in older_phone if i.name == "协议版本")
    assert entry.level == "bad"
    assert "电脑" in entry.hint, "手机端旧 → 该升手机那边，提示要说清是哪边新"

    newer_phone = build_checks(_status(protocol=4), "0.13.9", 3)
    entry = next(i for i in newer_phone if i.name == "协议版本")
    assert "手机" in entry.hint


def test_accessibility_off_is_fatal_and_explains_restricted_settings():
    items = build_checks(_status(a11y=False), "0.13.9", 3)
    entry = next(i for i in items if i.name == "无障碍服务")

    assert entry.level == "bad"
    assert "受限设置" in entry.hint


def test_missing_shizuku_is_not_an_error():
    """Shizuku 是可选能力，不装也能用无障碍通道——不能把它报成问题。"""
    items = build_checks(_status(), "0.13.9", 3)
    entry = next(i for i in items if i.name == "Shizuku")

    assert entry.ok


def test_stroke_limit_comes_from_the_device_not_a_guess():
    """并发上限只有设备知道（AOSP 10，厂商 ROM 不一定）。"""
    items = build_checks(_status(max_strokes=4), "0.13.9", 3)
    entry = next(i for i in items if i.name == "并发手势上限")
    assert entry.value == "4 路" and entry.ok

    single = build_checks(_status(max_strokes=1), "0.13.9", 3)
    entry = next(i for i in single if i.name == "并发手势上限")
    assert entry.level == "bad", "只能注入一路时输入时间线根本落不了地"

    legacy = build_checks(_status(max_strokes=None), "0.13.9", 3)
    entry = next(i for i in legacy if i.name == "并发手势上限")
    assert entry.level == "warn" and "升级" in entry.hint


def test_multiple_pcs_on_one_phone_is_called_out():
    items = build_checks(_status(pc_connections=2), "0.13.9", 3)
    entry = next(i for i in items if i.name == "PC 连接数")

    assert entry.level == "warn"
    assert "互相打断" in entry.hint


def test_capability_notes_explain_the_load_time_gate():
    """requires: [device_gesture] 的脚本在**加载期**被拒，不是跑到一半才失败。"""
    on = capability_notes(_status())
    assert any("device_gesture" in note and "可以运行" in note for note in on)

    off = capability_notes(_status(a11y=False))
    assert any("加载时被拒绝" in note for note in off)


def test_report_covers_the_feedback_checklist():
    """报告字段对齐快速开始 1.6 的「一次性提供」要求，省掉逐条追问。"""
    report = build_report(
        _status(), "0.13.9", 3, "ABC123",
        {"本地安装包": "lvjiang-v0.13.9.apk"})

    for needle in ("0.13.9", "ABC123", "协议版本", "无障碍服务", "屏幕",
                   "并发手势上限", "本地安装包"):
        assert needle in report, needle


# ─── 对话框：单二维码 + 来源状态机 ────────────────────────

class _StubServer:
    """替身服务：只提供 url_for / stop，不真的占端口。"""

    def __init__(self, *_args, **_kwargs):
        self.stopped = False

    def start(self):
        return self

    @property
    def port(self) -> int:
        return 54321

    @property
    def route(self) -> str:
        return "/lvjiang-v0.13.9.apk"

    def url_for(self, host: str) -> str:
        return f"http://{host}:{self.port}{self.route}"

    def stop(self) -> None:
        self.stopped = True


def _dialog(qtbot, monkeypatch, *, devices=(), addresses=("192.168.1.5",)):
    from lvjiang.ui.mobile.dialog import MobileDeviceDialog

    monkeypatch.setattr(
        "lvjiang.ui.mobile.dialog.list_adb_devices",
        lambda *a, **kw: list(devices))
    monkeypatch.setattr(
        "lvjiang.ui.mobile.dialog.lan_addresses", lambda: list(addresses))
    monkeypatch.setattr("lvjiang.ui.mobile.dialog.ApkLanServer", _StubServer)
    dialog = MobileDeviceDialog()
    qtbot.addWidget(dialog)
    return dialog


def _give_apk(dialog, tmp_path) -> Path:
    apk = tmp_path / "lvjiang-v0.13.9.apk"
    apk.write_bytes(b"x" * 1024)
    dialog._set_apk(ApkFile(path=Path(apk), version="0.13.9",
                            sha256="ab" * 32, verified=True))
    return apk


def test_only_one_qr_and_it_defaults_to_the_online_source(qtbot, monkeypatch):
    """同一时刻只有一个码该被扫，默认是在线下载。

    两个并列的二维码区是没想清楚流程的产物：用户没法判断该扫哪个。
    """
    dialog = _dialog(qtbot, monkeypatch)

    assert dialog._source_online.isChecked()
    assert dialog._qr.content.endswith(".apk")
    assert "github" in dialog._qr.content.lower()
    assert dialog._qr_url.text() == dialog._qr.content
    assert "GitHub" in dialog._scan_hint.text()


def test_lan_source_is_disabled_until_an_apk_exists(qtbot, monkeypatch,
                                                    tmp_path):
    """本机共享缺什么就说什么——禁用而不是隐藏，别让人对着灰选项猜。"""
    dialog = _dialog(qtbot, monkeypatch)

    assert dialog._source_lan.isEnabled() is False
    assert "下载 APK" in dialog._source_lan.toolTip()

    _give_apk(dialog, tmp_path)

    assert dialog._source_lan.isEnabled() is True


def test_lan_source_without_any_address_explains_why(qtbot, monkeypatch,
                                                     tmp_path):
    dialog = _dialog(qtbot, monkeypatch, addresses=())
    _give_apk(dialog, tmp_path)

    assert dialog._source_lan.isEnabled() is False
    assert "局域网地址" in dialog._source_lan.toolTip()


def test_choosing_lan_starts_sharing_and_swaps_the_qr(qtbot, monkeypatch,
                                                      tmp_path):
    """选项本身就是开关：切到本机共享即开始共享，二维码跟着换成本机地址。"""
    dialog = _dialog(qtbot, monkeypatch)
    _give_apk(dialog, tmp_path)
    online_url = dialog._qr.content

    dialog._source_lan.setChecked(True)

    assert dialog._server is not None
    assert dialog._qr.content.startswith("http://192.168.1.5:")
    assert dialog._qr.content != online_url
    assert dialog._qr_url.text() == dialog._qr.content
    assert "同一局域网" in dialog._scan_hint.text()


def test_switching_back_online_stops_sharing_and_restores_the_qr(
    qtbot, monkeypatch, tmp_path,
):
    """回到在线就该停掉共享：留着一个没人用的开放端口没有理由。"""
    dialog = _dialog(qtbot, monkeypatch)
    _give_apk(dialog, tmp_path)
    dialog._source_lan.setChecked(True)
    server = dialog._server

    dialog._source_online.setChecked(True)

    assert dialog._server is None
    assert server.stopped is True
    assert "github" in dialog._qr.content.lower()


def test_switching_lan_address_updates_the_qr(qtbot, monkeypatch, tmp_path):
    """换本机地址要立刻重算二维码，而且不中断共享。

    多网卡机器第一次往往选错网段；二维码不跟着变的话，用户只会看到"扫了没反应"。
    服务绑在 0.0.0.0，本机任何地址都可达，所以换地址不需要重启。
    """
    dialog = _dialog(qtbot, monkeypatch,
                     addresses=("192.168.1.5", "10.0.0.8"))
    _give_apk(dialog, tmp_path)
    dialog._source_lan.setChecked(True)
    first = dialog._qr.content
    server = dialog._server

    dialog._lan_combo.setCurrentIndex(dialog._lan_combo.findData("10.0.0.8"))

    assert "10.0.0.8" in dialog._qr.content
    assert dialog._qr.content != first
    assert dialog._server is server, "换地址不该把服务停掉重开"


def test_refreshing_keeps_the_chosen_lan_address(qtbot, monkeypatch, tmp_path):
    """装包、重新扫描之后不能把用户刚选对的地址冲回第一个网卡。"""
    dialog = _dialog(qtbot, monkeypatch,
                     addresses=("192.168.1.5", "10.0.0.8"))
    _give_apk(dialog, tmp_path)
    dialog._lan_combo.setCurrentIndex(dialog._lan_combo.findData("10.0.0.8"))

    dialog._refresh_install_state()

    assert dialog._lan_combo.currentData() == "10.0.0.8"


def test_address_row_only_shows_for_the_lan_source(qtbot, monkeypatch,
                                                   tmp_path):
    """在线下载时本机地址没有意义，不该占着版面。"""
    dialog = _dialog(qtbot, monkeypatch)
    _give_apk(dialog, tmp_path)

    assert dialog._lan_form.isRowVisible(0) is False

    dialog._source_lan.setChecked(True)

    assert dialog._lan_form.isRowVisible(0) is True


def test_adb_install_needs_both_an_apk_and_a_device(qtbot, monkeypatch,
                                                    tmp_path):
    no_device = _dialog(qtbot, monkeypatch)
    _give_apk(no_device, tmp_path)
    assert no_device._btn_adb_install.isEnabled() is False
    assert "ADB 设备" in no_device._btn_adb_install.toolTip()

    ready = _dialog(qtbot, monkeypatch,
                    devices=[{"serial": "ABC123", "model": "Pixel"}])
    assert ready._btn_adb_install.isEnabled() is False
    assert "安装包" in ready._btn_adb_install.toolTip()
    _give_apk(ready, tmp_path)
    assert ready._btn_adb_install.isEnabled() is True
    assert "校验通过" in ready._apk_info.text()


# ─── 找回上次下载的安装包 ──────────────────────────────────

def test_adopts_the_apk_downloaded_last_time(qtbot, monkeypatch, tmp_path):
    """重启软件后要认出 data/apk 里已经下好的包。

    只记在内存里的话，用户下载完一次、重启就看到"本机尚无安装包"，而文件明明
    还躺在 data/apk——他只会以为下载失败了。
    """
    apk = tmp_path / "lvjiang-v0.13.9.apk"
    apk.write_bytes(b"x" * 4096)
    monkeypatch.setattr(
        "lvjiang.ui.mobile.dialog.find_local_apk",
        lambda version, *a, **kw: apk if version == "0.13.9" else None)
    monkeypatch.setattr("lvjiang.ui.mobile.dialog.get_version", lambda: "0.13.9")

    dialog = _dialog(qtbot, monkeypatch)

    assert dialog._apk is not None
    assert dialog._apk.path == apk
    assert dialog._source_lan.isEnabled() is True, "手上有包就该能开本机共享"
    assert str(apk) in dialog._apk_info.text()


def test_existing_apk_shows_up_before_the_hash_is_computed(qtbot, monkeypatch,
                                                           tmp_path):
    """先显示存在，哈希在后台补——算八十兆的哈希不能卡住打开对话框。"""
    apk = tmp_path / "lvjiang-v0.13.9.apk"
    apk.write_bytes(b"x" * 4096)
    monkeypatch.setattr(
        "lvjiang.ui.mobile.dialog.find_local_apk", lambda *a, **kw: apk)
    monkeypatch.setattr("lvjiang.ui.mobile.dialog.get_version", lambda: "0.13.9")
    started = []
    monkeypatch.setattr(
        "lvjiang.ui.mobile.dialog.MobileDeviceDialog._start_hash_worker",
        lambda self, path: started.append(path))

    dialog = _dialog(qtbot, monkeypatch)

    assert started == [apk], "必须把哈希算在后台线程里"
    assert dialog._apk is not None and dialog._apk.sha256 == ""
    assert "正在校验" in dialog._apk_info.text(), (
        "哈希没算出来时要如实说在校验，不能假装校验通过")


def test_apk_of_another_version_is_not_adopted(tmp_path):
    """版本不符的包不算"手上已有"——两边版本不一致正是这个工具要帮用户发现的。"""
    from lvjiang.core.android.apk_release import find_local_apk

    (tmp_path / "lvjiang-v0.13.8.apk").write_bytes(b"old")

    assert find_local_apk("0.13.9", tmp_path) is None
    assert find_local_apk("0.13.8", tmp_path) is not None


def test_download_and_lookup_share_one_directory():
    """下载与找回必须用同一个目录入口，否则改一边就会"下载完重启又不认"。"""
    from lvjiang.core.android import apk_release

    assert apk_release.default_download_dir().name == "apk"
    assert apk_release.default_download_dir().parent.name == "data"
