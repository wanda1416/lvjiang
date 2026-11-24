"""没有毕业率模型时停在空状态。

110 阶的「鸣金·霆」只有基础方案表、没有毕业率模型：过去这种情况会一路走到
搜索线程里再抛「未找到对应流派的毕业率方案」，用户看到的是一条报错弹窗。
这里钉住新契约：点击搜索时先判定模型，给出说明现有等级的提示，不启动搜索。
"""

import lvjiang.apps.yysls.ui.loadout.optimal_combo as mod


class _StubCombo:
    def __init__(self, key: str) -> None:
        self._key = key

    def currentData(self):  # noqa: N802 — 对齐 QComboBox 接口
        return self._key


class _StubSlotGroup:
    def get_selected(self):
        return [{"_fp": "x", "name": "装备", "type": "主武器"}]


class _Jobs:
    def __init__(self) -> None:
        self.started = False

    def start(self, *_args, **_kwargs) -> None:
        self.started = True


def _page(monkeypatch) -> mod.OptimalComboPage:
    page = mod.OptimalComboPage.__new__(mod.OptimalComboPage)
    page._school = "鸣金·霆"
    page._scheme = "基础方案"
    page._world_level = 110
    page._slot_groups = {slot_key: _StubSlotGroup() for slot_key, _dn, _ft in mod._SLOT_ORDER}
    page._combo_equipment_set = _StubCombo("output_a")
    page._selected_gongjues = lambda: [""]
    page._jobs = _Jobs()
    return page


def test_missing_model_shows_empty_state_instead_of_raising(monkeypatch, qtbot):
    page = _page(monkeypatch)
    messages: list[str] = []

    class _Message:
        @staticmethod
        def information(_parent, _title, message):
            messages.append(message)

        @staticmethod
        def warning(_parent, _title, message):  # pragma: no cover - 不应走到
            messages.append(message)

    class _Config:
        @staticmethod
        def current_equip_level() -> int:
            return 110

    monkeypatch.setattr(mod, "QMessageBox", _Message)
    monkeypatch.setattr("lvjiang.apps.yysls.config.get_game_config", lambda: _Config())
    monkeypatch.setattr(
        "lvjiang.apps.yysls.core.graduation.get_graduation_calculator",
        lambda *_args, **_kwargs: None)

    page._on_search()

    assert page._jobs.started is False
    assert messages and "110 级" in messages[0] and "鸣金·霆" in messages[0]
