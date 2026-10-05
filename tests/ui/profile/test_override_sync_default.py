"""覆写对话框：「同步变更依赖方」默认不勾选、初值按定点小数回填。

覆写的语义是只改本 key。默认勾选会在用户没有察觉的情况下连带触发
``sync_targets``，把其他依赖项一起改掉；默认不勾选时用户仍可手动勾上。
这里把两层都钉住：对话框本身的默认状态，以及覆写入口传下去的参数。

初值另有一条口径：实时恢复类词条的当前值带十几位小数，直接用 ``str()``
回填会超出输入框允许的小数位数、并在失焦时被 Qt 归一成科学计数法。
"""
from types import SimpleNamespace

from PyQt6.QtWidgets import QCheckBox, QDialog, QLineEdit

from lvjiang.ui.profile import cell_editing
from lvjiang.ui.profile.cell_editing import ProfileCellEditingMixin
from lvjiang.ui.profile.dialogs import ask_value_dialog


def test_dialog_defaults_to_unchecked(qtbot, monkeypatch):
    """未显式指定时，复选框的默认状态必须是不勾选。"""
    captured: list[bool] = []

    def fake_exec(dialog: QDialog) -> int:
        # 对话框已构造完成、尚未进入用户交互，此时读到的就是默认状态
        captured.extend(
            box.isChecked() for box in dialog.findChildren(QCheckBox)
        )
        return 0  # 等价于用户点「取消」

    monkeypatch.setattr(QDialog, "exec", fake_exec)

    value, source, sync_checked, ok = ask_value_dialog(
        None,
        title="覆写 - 测试词条",
        hint="当前值: 1",
        prompt="新值:",
        is_float=False,
        min_val=0,
        sources=["来源A", "来源B"],
        sync_checkbox=True,
        source_label="来源/用途",
    )

    assert captured == [False]
    # 取消：不上报修改
    assert (value, source, ok) == (0, "", False)
    assert sync_checked is False


def test_override_entry_passes_unchecked_default(monkeypatch):
    """覆写入口必须显式传「不勾选」，不能依赖别处的默认值。"""
    seen: dict = {}

    def fake_dialog(*args, **kwargs):
        seen.update(kwargs)
        return 0, "", False, False  # 用户取消

    monkeypatch.setattr(cell_editing, "ask_value_dialog", fake_dialog)

    kd = SimpleNamespace(
        decimal=False, label="测试词条", sources=["来源A"], uses=["用途B"])
    ProfileCellEditingMixin._override_value_custom(
        SimpleNamespace(), "用户", "quota", "key", kd, 5,
    )

    assert seen.get("sync_checkbox") is True
    assert seen.get("sync_default") is False, (
        "覆写默认勾选「同步变更依赖方」会连带触发依赖方同步"
    )


def test_initial_value_is_plain_decimal(qtbot, monkeypatch):
    """覆写初值必须是定点小数，不能出现长小数或科学计数法。

    实时恢复词条的当前值形如 ``268.69740824583334``：直接 ``str()`` 回填会被
    ``QDoubleValidator`` 判为无效（超出允许小数位），编辑框失焦时被 Qt 归一成
    ``2.6870E+02``，用户看到的就是一串读不懂的科学计数法。
    """
    seen: list[str] = []

    def fake_exec(dialog: QDialog) -> int:
        # 对话框构造完成、尚未交互，此时读到的就是回填的初值
        seen.extend(line.text() for line in dialog.findChildren(QLineEdit))
        return 0  # 等价于用户点「取消」

    monkeypatch.setattr(QDialog, "exec", fake_exec)

    ask_value_dialog(
        None,
        title="覆写 - 测试词条",
        hint="当前值: 268.6974",
        prompt="新值:",
        is_float=True,
        min_val=0,
        sources=[],
        initial_value=268.69740824583334,
    )

    assert seen[0] == "268.6974", "初值必须按显示精度定点回填"
    assert "E" not in seen[0] and "e" not in seen[0]
