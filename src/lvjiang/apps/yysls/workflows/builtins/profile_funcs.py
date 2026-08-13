"""内置函数 - Profile 玩家档案读写

提供 DSL 工作流对 quota/regen/stock 三模型的访问能力。
所有函数通过 _engine.run_username 获取当前用户名。
"""

from loguru import logger

from lvjiang.workflows.builtins._registry import builtin_func


def _get_username(_engine) -> str:
    """从引擎获取当前用户名"""
    return getattr(_engine, "run_username", "") or "default"


@builtin_func("profile_get")
def _profile_get(_engine, key: str, *args) -> float | None:
    """读取 profile 属性值（自动识别模型类型）

    根据 key 在 profile.yaml 中的定义，自动确定所属模型（quota/regen/stock），
    从 ProfileDB 读取当前值。key 不存在返回 None。

    .wf 用法:
        eval $value = profile_get("niaoniao_of_week")
        if $value != null
            log concat("袅袅剩余: ", $value)
        end
    """
    if not key:
        logger.warning("profile_get: key 为空")
        return None

    from ...config.user_profile import get_profile_config
    from ...profile.profile_db import db_read_entry

    username = _get_username(_engine)
    config = get_profile_config()

    # 自动识别模型类型
    model_type = config.get_model_type(key)
    if model_type is None:
        logger.warning(f"profile_get: key '{key}' 未在 profile.yaml 中定义")
        return None

    entry = db_read_entry(username, model_type, key)
    if not entry:
        logger.debug(f"profile_get: {key} 无数据")
        return None

    return entry.get("value")


@builtin_func("profile_set")
def _profile_set(_engine, key: str, value, *args) -> float:
    """写入 profile 属性值（自动识别模型类型）

    将指定 key 的值设为 value，自动记录变更历史（change_type="action"）。
    key 不存在时记录警告并返回 0。

    .wf 用法:
        eval profile_set("niaoniao_of_week", 10)
        eval profile_set("tili", 100)
    """
    if not key:
        logger.warning("profile_set: key 为空")
        return 0

    from ...config.user_profile import get_profile_config
    from ...profile.profile_db import db_upsert

    username = _get_username(_engine)
    config = get_profile_config()

    model_type = config.get_model_type(key)
    if model_type is None:
        logger.warning(f"profile_set: key '{key}' 未在 profile.yaml 中定义")
        return 0

    try:
        value_num = float(value)
    except (TypeError, ValueError):
        logger.warning(f"profile_set: value 无法转为数字: {value!r}")
        return 0

    db_upsert(username, model_type, key, value_num,
              change_type="action", source="dsl")
    logger.debug(f"profile_set: {key} = {value_num} ({model_type})")
    return value_num


@builtin_func("profile_inc")
def _profile_inc(_engine, key: str, delta=1, *args) -> float:
    """增减 profile 属性值（自动识别模型类型）

    在当前值基础上增加 delta（负数表示减少）。
    key 不存在或无当前值时，视为从 0 开始增减。
    返回增减后的新值。

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
    from ...profile.profile_db import db_read_entry, db_upsert

    username = _get_username(_engine)
    config = get_profile_config()

    model_type = config.get_model_type(key)
    if model_type is None:
        logger.warning(f"profile_inc: key '{key}' 未在 profile.yaml 中定义")
        return 0

    try:
        delta_num = float(delta)
    except (TypeError, ValueError):
        logger.warning(f"profile_inc: delta 无法转为数字: {delta!r}")
        return 0

    # 读取当前值
    entry = db_read_entry(username, model_type, key)
    current = entry.get("value", 0) if entry else 0

    new_value = current + delta_num
    db_upsert(username, model_type, key, new_value,
              change_type="action", source="dsl",
              detail=f"inc {delta_num}")
    logger.debug(f"profile_inc: {key} {current} + {delta_num} = {new_value} ({model_type})")
    return new_value


@builtin_func("profile_model")
def _profile_model(_engine, key: str, *args) -> str:
    """查询 profile key 所属的模型类型

    返回 "quota"、"regen"、"stock" 之一；key 未定义返回空字符串。

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

    返回 {model_type: {key: {value, updated_at}}} 结构的字典。

    .wf 用法:
        eval $all = profile_all()
        eval $quota = $all.quota
        collect $all as "profile"
    """
    from ...profile.profile_db import db_read_all

    username = _get_username(_engine)
    return db_read_all(username)
