"""Windows 升级清理门禁：随包分发的 config/system 必须被替换，而不是叠加

`config/system` 在用户侧是只读出厂数据（用户改动落 config/local），升级时必须做到
「与包内容完全一致」。只覆盖同名文件的话，上游删除或移动过的文件会永远留在用户机器
上——`.wf` 换个目录就变成两份同 id 脚本（一份生效一份幽灵），被删掉的场景、布局、
参照图继续以旧内容加载。这个缺陷已经在用户侧发生过一次，所以拿真实的打包脚本比对
真实约定，而不是靠注释里的假设。

反向约束同样重要：清理范围绝不能扩到 `config/local`（用户自己的覆盖）、
`config/session`（账号、装备库、历史记录）和 `data/capture`（截图、录屏），
删掉就是毁用户数据。
"""

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALLER = _REPO_ROOT / "packaging" / "installer.iss"
_PACKAGE_BAT = _REPO_ROOT / "packaging" / "package.bat"


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


@pytest.mark.parametrize("target", [
    r"{app}\config\system",
    r"{app}\_internal",
    r"{app}\data\scrcpy",
])
def test_installer_purges_shipped_dirs(target):
    """覆盖安装前必须清空随包分发的目录，Inno 自己不会删消失的文件。"""
    targets = _installer_delete_targets()
    assert target in targets, (
        f"installer.iss 的 [InstallDelete] 缺少 {target}；"
        f"当前声明：{targets}。缺了它，上一版残留的文件会一直留在用户机器上")


@pytest.mark.parametrize("protected", [
    "config\\local",
    "config\\session",
    "data\\capture",
])
def test_installer_never_touches_user_data(protected):
    """清理范围不得扩到用户数据目录——它们不随包分发，删掉无法恢复。"""
    for target in _installer_delete_targets():
        assert protected not in target, (
            f"installer.iss 声明删除 {target}，其中包含用户数据目录 {protected}；"
            "用户数据目录必须原样保留")


def test_windows_package_distributes_scrcpy_server_with_adb():
    """JAR 已并入 adb 目录，打包脚本不得继续维护旧的单文件目录。"""
    source = _PACKAGE_BAT.read_text(encoding="utf-8").lower()
    assert r"data\adb" in source
    assert r"data\scrcpy" not in source
