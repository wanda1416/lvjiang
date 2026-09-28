"""升级清理门禁：随包分发的 config/system 必须被替换，而不是叠加

`config/system` 在用户侧是只读出厂数据（用户改动落 config/local），升级时必须做到
「与包内容完全一致」。只覆盖同名文件的话，上游删除或移动过的文件会永远留在用户机器
上——`.wf` 换个目录就变成两份同 id 脚本（一份生效一份幽灵），被删掉的场景、布局、
参照图继续以旧内容加载。这个缺陷已经在用户侧发生过一次，所以拿真实的打包脚本比对
真实约定，而不是靠注释里的假设。

反向约束同样重要：清理范围绝不能扩到 `config/local`（用户自己的覆盖）和
`config/session`（账号、装备库、历史记录），删掉就是毁用户数据。
"""

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALLER = _REPO_ROOT / "packaging" / "installer.iss"
_ANDROID_APP = (
    _REPO_ROOT / "android" / "app" / "src" / "main" / "java"
    / "com" / "lvjiang" / "app" / "App.kt"
)


def _installer_delete_targets() -> list[str]:
    """[InstallDelete] 段里声明的 Name，按原样返回。"""
    lines = _INSTALLER.read_text(encoding="utf-8").splitlines()
    targets: list[str] = []
    in_section = False
    for raw in lines:
        line = raw.strip()
        if line.startswith("["):
            in_section = line.lower() == "[installdelete]"
            continue
        if not in_section or not line or line.startswith(";"):
            continue
        for field in line.split(";"):
            key, _, value = field.partition(":")
            if key.strip().lower() == "name":
                targets.append(value.strip().strip('"'))
    return targets


@pytest.mark.parametrize("target", [r"{app}\config\system", r"{app}\_internal"])
def test_installer_purges_shipped_dirs(target):
    """覆盖安装前必须清空随包分发的目录，Inno 自己不会删消失的文件。"""
    targets = _installer_delete_targets()
    assert target in targets, (
        f"installer.iss 的 [InstallDelete] 缺少 {target}；"
        f"当前声明：{targets}。缺了它，上一版残留的文件会一直留在用户机器上")


@pytest.mark.parametrize("protected", ["config\\local", "config\\session"])
def test_installer_never_touches_user_data(protected):
    """清理范围不得扩到用户数据目录——它们不随包分发，删掉无法恢复。"""
    for target in _installer_delete_targets():
        assert protected not in target, (
            f"installer.iss 声明删除 {target}，其中包含用户数据目录 {protected}；"
            f"随包分发的只有 config\\system，其余目录必须原样保留")


def test_android_wipes_system_config_before_extract():
    """APK 升级解压前先删整个 config/system，否则残留同上。"""
    source = _ANDROID_APP.read_text(encoding="utf-8")
    body = source.partition("private fun syncSystemConfig()")[2]
    assert body, "App.kt 里找不到 syncSystemConfig，测试需要跟着改"
    delete_at = body.find("deleteRecursively")
    copy_at = body.find("copyAssetDir")
    assert delete_at != -1, (
        "syncSystemConfig 没有删除旧的 config/system，升级后会残留上一版文件")
    assert copy_at != -1 and delete_at < copy_at, (
        "删除必须发生在解压之前，否则等于把刚写进去的新配置删掉")
