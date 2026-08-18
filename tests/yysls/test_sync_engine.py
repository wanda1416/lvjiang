"""触发器同步引擎测试

覆盖 sync_engine.py 的 fire_sync_targets + _apply_ratio。
"""

from __future__ import annotations

from lvjiang.apps.yysls.config.profile_models import (
    DIR_BOTH,
    DIR_NEG,
    DIR_POS,
    MODEL_QUOTA,
    MODEL_REGEN,
    MODEL_STOCK,
    KeyDef,
    QuotaKeyDef,
    RegenKeyDef,
    StockKeyDef,
    SyncTargetDef,
)
from lvjiang.apps.yysls.config.user_profile import ProfileSchema
from lvjiang.apps.yysls.core.profile_engine.sync_engine import (
    _apply_ratio,
    fire_sync_targets,
)

# ─── 辅助工具 ─────────────────────────────────────────────


def _make_schema(*key_defs: tuple[str, KeyDef]) -> ProfileSchema:
    """快速构建 ProfileSchema，按 model_type 分组"""
    keys_by_model: dict[str, list[KeyDef]] = {
        MODEL_QUOTA: [],
        MODEL_REGEN: [],
        MODEL_STOCK: [],
    }
    for model_type, kd in key_defs:
        keys_by_model[model_type].append(kd)
    return ProfileSchema(keys_by_model=keys_by_model)


def _mock_write_fn(applied_by_key: dict[str, int | float] | None = None, fail_keys: set[str] | None = None):
    """创建一个记录调用的 write_fn mock

    applied_by_key: 指定目标的实际生效量（模拟 clamp 截断），默认等于请求 delta。
    fail_keys: 写入失败的目标 key 集合，返回 None。
    """
    def write_fn(user_name, model_type, key, *, delta, change_type, detail, source):
        write_fn.calls.append({
            "user_name": user_name,
            "model_type": model_type,
            "key": key,
            "delta": delta,
            "change_type": change_type,
            "detail": detail,
            "source": source,
        })
        if fail_keys and key in fail_keys:
            return None
        applied = applied_by_key.get(key, delta) if applied_by_key else delta
        # 返回 (new_value, applied_delta)，new_value 用假值即可
        return applied, applied
    write_fn.calls: list[dict] = []
    return write_fn


# ─── _apply_ratio ─────────────────────────────────────────


class TestApplyRatio:
    def test_int_model_rounds(self):
        assert _apply_ratio(100, 1.5, MODEL_STOCK) == 150
        assert _apply_ratio(100, 0.33, MODEL_QUOTA) == 33
        assert _apply_ratio(100, -1.0, MODEL_STOCK) == -100

    def test_regen_model_rounds_without_decimal(self):
        result = _apply_ratio(100, 0.5, MODEL_REGEN)
        assert result == 50
        assert isinstance(result, int)

    def test_decimal_regen_keeps_float(self):
        result = _apply_ratio(
            100,
            0.25,
            MODEL_REGEN,
            RegenKeyDef(
                key="xinli",
                label="心力",
                decimal=True,
                regen_type="realtime",
                regen_rate_value=0.125,
                regen_rate_unit="minute",
            ),
        )
        assert result == 25.0
        assert isinstance(result, float)

    def test_decimal_stock_keeps_float(self):
        result = _apply_ratio(
            10,
            0.25,
            MODEL_STOCK,
            StockKeyDef(key="tongbao", label="通宝", decimal=True),
        )
        assert result == 2.5
        assert isinstance(result, float)

    def test_ratio_zero(self):
        assert _apply_ratio(100, 0.0, MODEL_STOCK) == 0
        assert _apply_ratio(100, 0.0, MODEL_REGEN) == 0


# ─── fire_sync_targets ───────────────────────────────────


class TestFireSyncTargets:
    def test_single_target_1_to_1(self, monkeypatch):
        """单目标 1:1 同步（等价于旧行为）"""
        source_kd = QuotaKeyDef(
            key="dihua", label="地花",
            sync_targets=[SyncTargetDef(key="stock:bugan", ratio=1.0)],
        )
        target_kd = StockKeyDef(key="bugan", label="不肝")
        schema = _make_schema(
            (MODEL_QUOTA, source_kd),
            (MODEL_STOCK, target_kd),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn()
        fire_sync_targets(write_fn, "player1", source_kd, delta=100, source="打本")

        assert len(write_fn.calls) == 1
        call = write_fn.calls[0]
        assert call["model_type"] == MODEL_STOCK
        assert call["key"] == "bugan"
        assert call["delta"] == 100
        assert call["detail"] == "sync_from:dihua"
        assert call["source"] == "打本"

    def test_multi_target(self, monkeypatch):
        """多目标同步：一次变更写入多个目标"""
        source_kd = QuotaKeyDef(
            key="dihua", label="地花",
            sync_targets=[
                SyncTargetDef(key="stock:bugan", ratio=1.0),
                SyncTargetDef(key="stock:yuanbao", ratio=-1.0),
            ],
        )
        schema = _make_schema(
            (MODEL_QUOTA, source_kd),
            (MODEL_STOCK, StockKeyDef(key="bugan", label="不肝")),
            (MODEL_STOCK, StockKeyDef(key="yuanbao", label="元宝")),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn()
        fire_sync_targets(write_fn, "player1", source_kd, delta=100, source="打本")

        assert len(write_fn.calls) == 2
        assert write_fn.calls[0]["key"] == "bugan"
        assert write_fn.calls[0]["delta"] == 100
        assert write_fn.calls[1]["key"] == "yuanbao"
        assert write_fn.calls[1]["delta"] == -100

    def test_ratio_variants(self, monkeypatch):
        """倍率：ratio=2 / ratio=0.5 / ratio=0（禁用）"""
        source_kd = QuotaKeyDef(
            key="k", label="l",
            sync_targets=[
                SyncTargetDef(key="stock:a", ratio=2.0),
                SyncTargetDef(key="stock:b", ratio=0.5),
                SyncTargetDef(key="stock:c", ratio=0.0),  # 禁用
            ],
        )
        schema = _make_schema(
            (MODEL_QUOTA, source_kd),
            (MODEL_STOCK, StockKeyDef(key="a")),
            (MODEL_STOCK, StockKeyDef(key="b")),
            (MODEL_STOCK, StockKeyDef(key="c")),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn()
        fire_sync_targets(write_fn, "p", source_kd, delta=100, source="s")

        # ratio=2 → 200
        assert write_fn.calls[0]["delta"] == 200
        # ratio=0.5 → 50
        assert write_fn.calls[1]["delta"] == 50
        # ratio=0 → 显式禁用，不产生写入
        assert len(write_fn.calls) == 2

    def test_cross_model_quota_to_stock(self, monkeypatch):
        """跨模型：Quota→Stock"""
        source_kd = QuotaKeyDef(
            key="dihua", label="地花",
            sync_targets=[SyncTargetDef(key="stock:bugan")],
        )
        schema = _make_schema(
            (MODEL_QUOTA, source_kd),
            (MODEL_STOCK, StockKeyDef(key="bugan")),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn()
        fire_sync_targets(write_fn, "p", source_kd, delta=50, source="s")

        assert write_fn.calls[0]["model_type"] == MODEL_STOCK

    def test_cross_model_stock_to_quota(self, monkeypatch):
        """跨模型：Stock→Quota"""
        source_kd = StockKeyDef(
            key="yuanbao", label="元宝",
            sync_targets=[SyncTargetDef(key="quota:dihua", ratio=-1.0)],
        )
        schema = _make_schema(
            (MODEL_QUOTA, QuotaKeyDef(key="dihua")),
            (MODEL_STOCK, source_kd),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn()
        fire_sync_targets(write_fn, "p", source_kd, delta=100, source="s")

        assert write_fn.calls[0]["model_type"] == MODEL_QUOTA
        assert write_fn.calls[0]["delta"] == -100

    def test_cross_model_regen_to_stock(self, monkeypatch):
        """跨模型：Regen→Stock（目标为 int 模型，round 取整）"""
        source_kd = RegenKeyDef(
            key="tili", label="体力",
            sync_targets=[SyncTargetDef(key="stock:bugan", ratio=0.5)],
        )
        schema = _make_schema(
            (MODEL_REGEN, source_kd),
            (MODEL_STOCK, StockKeyDef(key="bugan")),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn()
        fire_sync_targets(write_fn, "p", source_kd, delta=10.0, source="s")

        # 目标是 Stock（int 模型），round(10.0 * 0.5) = 5
        assert write_fn.calls[0]["delta"] == 5
        assert isinstance(write_fn.calls[0]["delta"], int)

    def test_cycle_prevention(self, monkeypatch):
        """防环：A→B→A 不无限递归"""
        kd_a = StockKeyDef(
            key="a", label="A",
            sync_targets=[SyncTargetDef(key="stock:b")],
        )
        kd_b = StockKeyDef(
            key="b", label="B",
            sync_targets=[SyncTargetDef(key="stock:a")],
        )
        schema = _make_schema(
            (MODEL_STOCK, kd_a),
            (MODEL_STOCK, kd_b),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn()
        fire_sync_targets(write_fn, "p", kd_a, delta=100, source="s")

        # A→B 写入一次，B→A 被防环阻断
        assert len(write_fn.calls) == 1
        assert write_fn.calls[0]["key"] == "b"

    def test_chain_sync(self, monkeypatch):
        """链式：A→B→C 顺序触发"""
        kd_a = StockKeyDef(
            key="a", label="A",
            sync_targets=[SyncTargetDef(key="stock:b")],
        )
        kd_b = StockKeyDef(
            key="b", label="B",
            sync_targets=[SyncTargetDef(key="stock:c", ratio=0.5)],
        )
        kd_c = StockKeyDef(key="c", label="C")
        schema = _make_schema(
            (MODEL_STOCK, kd_a),
            (MODEL_STOCK, kd_b),
            (MODEL_STOCK, kd_c),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn()
        fire_sync_targets(write_fn, "p", kd_a, delta=100, source="s")

        assert len(write_fn.calls) == 2
        assert write_fn.calls[0]["key"] == "b"
        assert write_fn.calls[0]["delta"] == 100
        assert write_fn.calls[1]["key"] == "c"
        assert write_fn.calls[1]["delta"] == 50  # 100 * 0.5

    def test_target_not_found_skipped(self, monkeypatch):
        """目标 key 不存在时跳过"""
        source_kd = StockKeyDef(
            key="a", label="A",
            sync_targets=[SyncTargetDef(key="stock:nonexistent")],
        )
        schema = _make_schema((MODEL_STOCK, source_kd))
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn()
        fire_sync_targets(write_fn, "p", source_kd, delta=100, source="s")

        assert len(write_fn.calls) == 0

    def test_source_override(self, monkeypatch):
        """SyncTargetDef.source 覆盖默认 source"""
        source_kd = QuotaKeyDef(
            key="k", label="l",
            sync_targets=[SyncTargetDef(key="stock:a", source="自定义来源")],
        )
        schema = _make_schema(
            (MODEL_QUOTA, source_kd),
            (MODEL_STOCK, StockKeyDef(key="a")),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn()
        fire_sync_targets(write_fn, "p", source_kd, delta=100, source="默认来源")

        assert write_fn.calls[0]["source"] == "自定义来源"

    def test_no_sync_targets_no_calls(self, monkeypatch):
        """无 sync_targets 时不触发任何写入"""
        source_kd = StockKeyDef(key="a", label="A")
        schema = _make_schema((MODEL_STOCK, source_kd))
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn()
        fire_sync_targets(write_fn, "p", source_kd, delta=100, source="s")

        assert len(write_fn.calls) == 0

    def test_int_model_rounds_delta(self, monkeypatch):
        """int 模型 round() 取整"""
        source_kd = QuotaKeyDef(
            key="k", label="l",
            sync_targets=[SyncTargetDef(key="stock:a", ratio=0.33)],
        )
        schema = _make_schema(
            (MODEL_QUOTA, source_kd),
            (MODEL_STOCK, StockKeyDef(key="a")),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn()
        fire_sync_targets(write_fn, "p", source_kd, delta=100, source="s")

        # round(100 * 0.33) = 33
        assert write_fn.calls[0]["delta"] == 33
        assert isinstance(write_fn.calls[0]["delta"], int)

    def test_chain_uses_applied_delta_after_clamp(self, monkeypatch):
        """链式同步用目标实际生效量（clamp 后）驱动下游，而非请求量"""
        kd_a = StockKeyDef(
            key="a", label="A",
            sync_targets=[SyncTargetDef(key="stock:b")],
        )
        kd_b = StockKeyDef(
            key="b", label="B",
            sync_targets=[SyncTargetDef(key="stock:c")],
        )
        kd_c = StockKeyDef(key="c", label="C")
        schema = _make_schema(
            (MODEL_STOCK, kd_a),
            (MODEL_STOCK, kd_b),
            (MODEL_STOCK, kd_c),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        # B 被 clamp：请求 +100 实际只生效 +5
        write_fn = _mock_write_fn(applied_by_key={"b": 5})
        fire_sync_targets(write_fn, "p", kd_a, delta=100, source="s")

        assert write_fn.calls[0]["key"] == "b"
        assert write_fn.calls[0]["delta"] == 100
        # C 收到的是 B 的实际生效量 5，而非 100
        assert write_fn.calls[1]["key"] == "c"
        assert write_fn.calls[1]["delta"] == 5

    def test_chain_stops_when_applied_zero(self, monkeypatch):
        """目标 clamp 后生效量为 0 时，不触发下游递归"""
        kd_a = StockKeyDef(
            key="a", label="A",
            sync_targets=[SyncTargetDef(key="stock:b")],
        )
        kd_b = StockKeyDef(
            key="b", label="B",
            sync_targets=[SyncTargetDef(key="stock:c")],
        )
        schema = _make_schema(
            (MODEL_STOCK, kd_a),
            (MODEL_STOCK, kd_b),
            (MODEL_STOCK, StockKeyDef(key="c")),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn(applied_by_key={"b": 0})
        fire_sync_targets(write_fn, "p", kd_a, delta=100, source="s")

        assert len(write_fn.calls) == 1
        assert write_fn.calls[0]["key"] == "b"

    def test_write_failure_aborts_downstream(self, monkeypatch):
        """目标写入失败时中止其下游递归，不影响其他目标"""
        kd_a = StockKeyDef(
            key="a", label="A",
            sync_targets=[
                SyncTargetDef(key="stock:b"),
                SyncTargetDef(key="stock:d"),
            ],
        )
        kd_b = StockKeyDef(
            key="b", label="B",
            sync_targets=[SyncTargetDef(key="stock:c")],
        )
        schema = _make_schema(
            (MODEL_STOCK, kd_a),
            (MODEL_STOCK, kd_b),
            (MODEL_STOCK, StockKeyDef(key="c")),
            (MODEL_STOCK, StockKeyDef(key="d")),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn(fail_keys={"b"})
        fire_sync_targets(write_fn, "p", kd_a, delta=100, source="s")

        # B 写入失败（仍记录调用），C 不触发，D 正常写入
        keys = [c["key"] for c in write_fn.calls]
        assert keys == ["b", "d"]

    def test_direction_pos_fires_only_on_increase(self, monkeypatch):
        """正向限定：仅源数值增加时触发"""
        source_kd = StockKeyDef(
            key="bayinqiao", label="八音窍",
            sync_targets=[SyncTargetDef(key="quota:jindu", direction=DIR_POS)],
        )
        schema = _make_schema(
            (MODEL_STOCK, source_kd),
            (MODEL_QUOTA, QuotaKeyDef(key="jindu")),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn()
        fire_sync_targets(write_fn, "p", source_kd, delta=1, source="s")
        assert len(write_fn.calls) == 1

        write_fn2 = _mock_write_fn()
        fire_sync_targets(write_fn2, "p", source_kd, delta=-1, source="s")
        assert len(write_fn2.calls) == 0

    def test_direction_neg_fires_only_on_decrease(self, monkeypatch):
        """负向限定：仅源数值减少时触发（用户场景：减少八音窍才同步进度）"""
        source_kd = StockKeyDef(
            key="bayinqiao", label="八音窍",
            sync_targets=[SyncTargetDef(key="quota:jindu", direction=DIR_NEG)],
        )
        schema = _make_schema(
            (MODEL_STOCK, source_kd),
            (MODEL_QUOTA, QuotaKeyDef(key="jindu")),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        write_fn = _mock_write_fn()
        fire_sync_targets(write_fn, "p", source_kd, delta=-1, source="s")
        assert len(write_fn.calls) == 1

        write_fn2 = _mock_write_fn()
        fire_sync_targets(write_fn2, "p", source_kd, delta=1, source="s")
        assert len(write_fn2.calls) == 0

    def test_direction_both_fires_on_any_change(self, monkeypatch):
        """全向（默认）：增减均触发"""
        source_kd = StockKeyDef(
            key="a", label="A",
            sync_targets=[SyncTargetDef(key="stock:b", direction=DIR_BOTH)],
        )
        schema = _make_schema(
            (MODEL_STOCK, source_kd),
            (MODEL_STOCK, StockKeyDef(key="b")),
        )
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.profile_engine.sync_engine.get_profile_config",
            lambda: schema,
        )

        for d in (10, -10):
            write_fn = _mock_write_fn()
            fire_sync_targets(write_fn, "p", source_kd, delta=d, source="s")
            assert len(write_fn.calls) == 1
