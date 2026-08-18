"""内置函数 - Profile 玩家档案读写

提供 DSL 工作流对 quota/regen/stock/note 四模型的访问能力。
所有函数通过 _engine.run_username 获取当前用户名。
"""

from loguru import logger

from lvjiang.workflows.builtins._registry import builtin_func

from .....i18n import tr


def _get_username(_engine) -> str:
    """从引擎获取当前用户名"""
    return getattr(_engine, "run_username", "") or "default"


@builtin_func("profile_get")
def _profile_get(_engine, key: str, *args) -> float | str | None:
    """读取 profile 属性值（自动识别模型类型）

    走共享读取管线 profile_ops.profile_read()，与 UI 读取路径一致：
    自动识别模型类型 → 读 entry → regen 实时计算。
    note 模型返回文本字符串，其他模型返回数值。
    key 不存在返回 None。

    .wf 用法:
        eval $value = profile_get("niaoniao_of_week")
        if $value != null
            log concat("袅袅剩余: ", $value)
        end
    """
    if not key:
        logger.warning("profile_get: key 为空")
        return None

    from ...core.profile_engine.profile_ops import profile_read
    username = _get_username(_engine)
    return profile_read(username, key)


@builtin_func("profile_set")
def _profile_set(_engine, key: str, value, *args) -> float | str:
    """写入 profile 属性值（自动识别模型类型）

    走共享写入管线 profile_action()，与 UI 增减完全一致：
    数值模型: clamp → delta → detail → db_upsert → sync_targets。
    note 模型: 文本直接写入 value_text 列，不走数值管线。
    source 固定为 "DSL 写入"。

    .wf 用法:
        eval profile_set("niaoniao_of_week", 10)
        eval profile_set("tili", 100)
        eval profile_set("took_xinfa", "已拿")
    """
    if not key:
        logger.warning("profile_set: key 为空")
        return 0

    from ...config.user_profile import get_profile_config
    from ...core.profile_engine.profile_ops import profile_action
    username = _get_username(_engine)
    config = get_profile_config()
    model_type = config.get_model_type(key) or ""

    # note 模型允许字符串值，不强制转 float
    if model_type == "note":
        # bool/0/None 视为清空备注，其余转字符串
        if value is None or value is False or value == 0:
            text = ""
        else:
            text = str(value)
        return profile_action(
            username, key, set_value=text,
            source=tr("DSL 写入"),
        )

    try:
        value_num = float(value)
    except (TypeError, ValueError):
        logger.warning(f"profile_set: value 无法转为数字: {value!r}")
        return 0

    return profile_action(username, key, set_value=value_num, source=tr("DSL 写入"))


@builtin_func("profile_inc")
def _profile_inc(_engine, key: str, delta=1, *args) -> float:
    """增减 profile 属性值（自动识别模型类型）

    走共享写入管线 profile_action()，与 UI 增减完全一致：
    clamp → delta → detail → db_upsert → sync_targets。
    source 固定为 "DSL 写入"。

    .wf 用法:
        # 完成任务，配额 -1
        eval $remaining = profile_inc("niaoniao_of_week", -1)
        log concat("袅袅剩余: ", $remaining)

        # 消耗体力
        eval $tili = profile_inc("tili", -30)
    """
    if not key:
        logger.warning("profile_inc: key 为空")
        return 0

    from ...config.user_profile import get_profile_config
    config = get_profile_config()
    model_type = config.get_model_type(key) or ""
    if model_type == "note":
        logger.warning(f"profile_inc: note 模型 key '{key}' 不支持增减操作")
        return 0

    try:
        delta_num = float(delta)
    except (TypeError, ValueError):
        logger.warning(f"profile_inc: delta 无法转为数字: {delta!r}")
        return 0

    from ...core.profile_engine.profile_ops import profile_action
    username = _get_username(_engine)
    return profile_action(username, key, delta=delta_num, source=tr("DSL 写入"))


@builtin_func("profile_model")
def _profile_model(_engine, key: str, *args) -> str:
    """查询 profile key 所属的模型类型

    返回 "quota"、"regen"、"stock"、"note" 之一；key 未定义返回空字符串。

    .wf 用法:
        eval $model = profile_model("niaoniao_of_week")
        if $model equals "quota"
            log "这是配额类型"
        end
    """
    if not key:
        return ""

    from ...config.user_profile import get_profile_config
    config = get_profile_config()
    return config.get_model_type(key) or ""


@builtin_func("profile_all")
def _profile_all(_engine, *args) -> dict:
    """获取当前用户的全部 profile 数据

    走共享读取管线 profile_ops.profile_read_all()，与 UI 读取路径一致：
    读全部 entry → regen 条目按当前时间计算。

    返回 {model_type: {key: {value, updated_at}}} 结构的字典。

    .wf 用法:
        eval $all = profile_all()
        eval $quota = $all.quota
        collect $all as "profile"
    """
    from ...core.profile_engine.profile_ops import profile_read_all
    username = _get_username(_engine)
    return profile_read_all(username)
