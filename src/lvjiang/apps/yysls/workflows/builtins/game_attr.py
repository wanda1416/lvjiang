"""内置函数 - 燕云游戏属性查询

工作流经常需要判断「当前画面是不是回到了某个赛季页面」，而赛季名称每季都变。
脚本里硬编码赛季名会在换季当天全部失效，所以统一从游戏配置里读：配置已经是
赛季信息的唯一来源（`config/system/yysls/game_config/seasons.yaml`），这里只做
只读转发，不另存一份。
"""

from loguru import logger

from lvjiang.workflows.builtins._registry import builtin_func


def _season_name() -> str:
    """当前生效赛季的名称；无生效赛季返回空字符串。"""
    from ...config import get_game_config
    season = get_game_config().current_season()
    if season is None:
        logger.warning("game_attr: 当前时间不在任何已配置赛季内，赛季名取空值")
        return ""
    return season.name or ""


# 属性名 → 取值函数。一期只开放赛季名称，后续属性在这里追加即可。
_ATTR_GETTERS = {
    "season_name": _season_name,
}


@builtin_func("game_attr")
def _game_attr(attr_name: str, *args) -> str:
    """查询燕云游戏属性

    当前支持的属性：

    | 属性名 | 返回值 |
    |--------|--------|
    | `season_name` | 当前生效赛季名称，如「南吕相和」；无生效赛季返回 `""` |

    属性名拼错或尚未支持时返回空字符串并记警告，不中断工作流——调用方拿到
    空值时应自行决定是放弃该判据还是报错。

    .wf 用法:
        eval $season = game_attr("season_name")
        scan [zhanling_detail].[season] as $found by contains $season
    """
    getter = _ATTR_GETTERS.get(str(attr_name))
    if getter is None:
        logger.warning(
            f"game_attr: 不支持的属性 {attr_name!r}，"
            f"可用属性: {sorted(_ATTR_GETTERS)}")
        return ""
    return getter()
