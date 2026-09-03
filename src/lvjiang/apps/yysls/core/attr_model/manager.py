"""属性来源配置的加载、缓存与求值入口

来源全部外置为 YAML（config/system/yysls/attr_model/ 下一类来源一个
文件），本模块负责加载、校验、缓存，并把词条满值查询接到
game_config 的 affix_caps 上——于是 ``full_affix`` 声明的条目换赛季时
只改 affix_caps 一处，几十个心法自动跟着走。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from .....core.config.resolver import ConfigResolver, get_resolver
from .....i18n import tr
from ...config import get_game_config
from .models import SOURCE_KINDS, AttrModelError, StatEffect
from .parsing import parse_source_file
from .resolver import CapsLookup, ResolveResult, resolve, solve_residual

_SOURCES_REL_DIR = "yysls/attr_model"


def game_config_caps_lookup() -> CapsLookup:
    """把 affix_caps 包装成求值引擎要的满值查询"""

    def lookup(level: int, category: str) -> float | None:
        entry = get_game_config().get_affix_caps(level, category)
        return None if entry is None else float(entry["cap"])

    return lookup


class AttrModelManager:
    """属性来源管理器

    加载目录下全部 YAML，解析失败的文件记录错误并跳过（错误可经
    :meth:`errors` 取出在 UI 里展示），不静默丢弃。
    """

    def __init__(self, sources_dir: str | Path | None = None):
        if sources_dir is None:
            self._resolver = get_resolver()
            self._rel_dir = _SOURCES_REL_DIR
        else:
            # 测试/孤立目录：单层语义（system=local=sources_dir）
            self._resolver = ConfigResolver(
                system_dir=sources_dir, local_dir=sources_dir, dev_mode=True)
            self._rel_dir = ""
        self._effects: list[StatEffect] = []
        self._files: dict[str, str] = {}   # source_id -> 文件名
        self._errors: dict[str, str] = {}
        self.reload()

    def _rel(self, filename: str) -> str:
        return f"{self._rel_dir}/{filename}" if self._rel_dir else filename

    def reload(self) -> None:
        """重新加载全部来源文件"""
        self._effects.clear()
        self._files.clear()
        self._errors.clear()
        seen: dict[str, str] = {}
        for name in self._resolver.enumerate_entities(self._rel_dir, "*.yaml"):
            path = self._resolver.resolve_read(self._rel(name))
            if path is None:
                continue
            try:
                data = self._resolver._load_yaml(path)
                effects = parse_source_file(data, filename=name)
            except Exception as e:
                logger.error(f"属性来源 {name} 加载失败，已跳过: {e}")
                self._errors[Path(name).stem] = str(e)
                continue
            for effect in effects:
                if effect.source_id in seen:
                    logger.error(
                        f"属性来源条目重复: {effect.source_id}"
                        f"（{seen[effect.source_id]} 与 {name}）"
                    )
                    continue
                seen[effect.source_id] = name
                self._files[effect.source_id] = name
                self._effects.append(effect)

    # ── 查询 ────────────────────────────────────────────

    def effects(self, kinds: tuple[str, ...] | None = None) -> list[StatEffect]:
        """全部来源条目，可按类别筛选。顺序稳定：类别声明序 + 文件内序。"""
        wanted = SOURCE_KINDS if kinds is None else kinds
        order = {kind: index for index, kind in enumerate(SOURCE_KINDS)}
        selected = [e for e in self._effects if e.kind in wanted]
        return sorted(selected, key=lambda e: order.get(e.kind, len(order)))

    def unmodeled_count(self) -> int:
        """尚未填数值的条目数，供 UI 显示建模进度"""
        return sum(1 for effect in self._effects if not effect.modeled)

    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def source_file(self, source_id: str) -> str | None:
        return self._files.get(source_id)

    # ── 求值 ────────────────────────────────────────────

    def resolve(
        self,
        *,
        level: int,
        school_attr: str,
        selected: tuple[str, ...] | None = None,
        residual: dict[str, float] | None = None,
    ) -> ResolveResult:
        """求值当前配置

        Args:
            level: 当前赛季装备等级
            school_attr: 流派属性（通用/鸣金/牵丝/裂石/破竹）
            selected: 只参与求值的条目 id；None 表示全部。
                心法只能上若干门、套装只能选一套，由调用方按用户选择传入。
            residual: 手填补足，见 :func:`solve_residual`
        """
        effects = self._select(selected)
        return resolve(
            effects,
            level=level,
            school_attr=school_attr,
            caps_lookup=game_config_caps_lookup(),
            residual=residual,
        )

    def solve_residual(
        self,
        targets: dict[str, float],
        *,
        level: int,
        school_attr: str,
        selected: tuple[str, ...] | None = None,
    ) -> dict[str, float]:
        """反解手填补足，使面板属性等于 targets"""
        effects = self._select(selected)
        return solve_residual(
            effects,
            targets,
            level=level,
            school_attr=school_attr,
            caps_lookup=game_config_caps_lookup(),
        )

    def _select(self, selected: tuple[str, ...] | None) -> list[StatEffect]:
        if selected is None:
            return list(self._effects)
        wanted = set(selected)
        unknown = wanted - {effect.source_id for effect in self._effects}
        if unknown:
            raise AttrModelError(
                tr("未知的属性来源条目: {ids}").format(ids="、".join(sorted(unknown)))
            )
        return [effect for effect in self._effects if effect.source_id in wanted]


_instance: AttrModelManager | None = None


def get_attr_model_manager() -> AttrModelManager:
    """获取全局 AttrModelManager 单例"""
    global _instance
    if _instance is None:
        _instance = AttrModelManager()
    return _instance


def invalidate_attr_model_cache() -> None:
    """丢弃单例，下次访问时重新加载（配置改动后调用）"""
    global _instance
    _instance = None
