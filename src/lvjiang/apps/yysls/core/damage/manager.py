"""伤害模型的加载、缓存与写回

一个流派方案一个文件（``config/system/yysls/damage_model/<流派>.yaml``），
与 ``config/system/yysls/graduation/<等级>级/<流派>_<方案>_v<版本>.json`` 配套：后者存
编译出来的求值程序与环境参数，前者存那份程序里读不出来的系数表。

两边同源于一份 Excel。``source.sha256`` 对不上就说明有一边换过表而
另一边没跟上，:meth:`mismatched` 会报出来——不比对的话，页面上显示
的系数和毕业率实际用的系数可以差一个赛季，而且看不出来。

方案按等级分文件，系数表没有等级维度（一个流派一份）。所以判定口径是
「**存在**同源的配套方案即视为一致」，而不是「必须与最高等级那份同源」：
某个流派新增更高等级的方案时，旧等级的系数表并没有过期。
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from loguru import logger

from .....core.config.resolver import ConfigResolver, get_resolver
from .....i18n import tr
from .models import MODIFIER_FIELDS, RATIO_FIELDS, DamageModel, DamageModelError
from .parsing import parse_model

_MODELS_REL_DIR = "yysls/damage_model"
_GRADUATION_REL_DIR = "yysls/graduation"


class DamageModelManager:
    """伤害模型管理器"""

    def __init__(self, models_dir: str | Path | None = None):
        if models_dir is None:
            self._resolver = get_resolver()
            self._rel_dir = _MODELS_REL_DIR
        else:
            self._resolver = ConfigResolver(
                system_dir=models_dir, local_dir=models_dir, dev_mode=True)
            self._rel_dir = ""
        self._models: dict[str, DamageModel] = {}
        self._files: dict[str, str] = {}
        self._docs: dict[str, dict] = {}
        self._errors: dict[str, str] = {}
        self.reload()

    def _rel(self, filename: str) -> str:
        return f"{self._rel_dir}/{filename}" if self._rel_dir else filename

    def _scheme_rel(self, filename: str) -> str:
        """配套方案的相对路径。孤立目录是单层的，方案就摆在模型旁边。"""
        return f"{_GRADUATION_REL_DIR}/{filename}" if self._rel_dir else filename

    def reload(self) -> None:
        self._models.clear()
        self._files.clear()
        self._docs.clear()
        self._errors.clear()
        for name in self._resolver.enumerate_entities(self._rel_dir, "*.yaml"):
            path = self._resolver.resolve_read(self._rel(name))
            if path is None:
                continue
            try:
                data = self._resolver._load_yaml(path)
                model = parse_model(data, filename=name)
            except Exception as e:
                logger.error(f"伤害模型 {name} 加载失败，已跳过: {e}")
                self._errors[Path(name).stem] = str(e)
                continue
            self._models[model.school] = model
            self._files[model.school] = name
            self._docs[name] = data

    # ── 查询 ────────────────────────────────────────────

    def schools(self) -> list[str]:
        return list(self._models)

    def model(self, school: str) -> DamageModel | None:
        return self._models.get(school)

    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def mismatched(self, school: str) -> str:
        """没有任何配套方案与系数表同源时返回说明，否则返回空串

        比的是 sha256 而不是文件名或版本号：改过表内容却没改文件名的
        情况最常见，而那正是两边悄悄分家的时刻。

        只要有**一份**配套方案与系数表同源就算一致。方案按等级分文件，系数表
        却是一个流派一份，因此新增更高等级的方案不代表旧等级的系数表过期——
        否则 115 级方案一进来就会让还在用 110 阶表的流派永远挂着「已过期」，
        而按提示去导入 115 的 Excel 并不是想要的结果。
        """
        model = self._models.get(school)
        if model is None or not model.source.get("sha256"):
            return ""
        candidates: list[tuple[int, int, str, dict]] = []
        # 递归枚举：方案按等级分在子目录里（graduation/<等级>级/…）。
        for filename in self._resolver.enumerate_entity_tree(
            self._scheme_rel("").rstrip("/"), "*.json",
        ):
            rel = self._scheme_rel(filename)
            path = self._resolver.resolve_read(rel)
            if path is None:
                continue
            try:
                scheme = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                return tr("配套方案读取失败: {msg}").format(msg=str(e))
            if (scheme.get("schema_version") == 3
                    and scheme.get("school") == school
                    and scheme.get("scheme") == model.scheme):
                candidates.append((
                    int(scheme.get("model_level") or 0),
                    int(scheme.get("model_version") or 0),
                    filename,
                    scheme,
                ))
        if not candidates:
            return tr("找不到配套方案 {name}").format(
                name=f"<等级>级/{school}_{model.scheme}_v<版本>.json")
        recorded = [
            str((scheme.get("source") or {}).get("sha256") or "")
            for *_ignored, scheme in candidates
        ]
        if any(sha == model.source["sha256"] for sha in recorded):
            return ""
        if not any(recorded):
            # 配套方案都没记来源，无从比较，不制造噪声。
            return ""
        return tr("与配套方案不同源，系数表可能已过期（重新导入 Excel 可修复）")

    # ── 写回 ────────────────────────────────────────────

    def save_skill(self, school: str, name: str, payload: dict) -> None:
        """校验并写回单个技能

        整份文件先过一遍解析再落盘：单条看着合法、放进文件却解析不了
        的话，界面会提示保存成功，然后整个流派的系数表打不开。
        """
        filename = self._files.get(school)
        if filename is None:
            raise DamageModelError(
                tr("未知的流派伤害模型: {name}").format(name=school))
        doc = dict(self._docs.get(filename) or {})
        skills = dict(doc.get("skills") or {})
        if name not in skills:
            raise DamageModelError(tr("未知技能: {name}").format(name=name))
        skills[name] = payload
        doc["skills"] = skills
        parse_model(doc, filename=filename)
        self._resolver.write_entity(
            self._rel(filename),
            yaml.dump(doc, allow_unicode=True, sort_keys=False, width=1000),
        )
        self.reload()


_manager: DamageModelManager | None = None


def get_damage_model_manager() -> DamageModelManager:
    global _manager
    if _manager is None:
        _manager = DamageModelManager()
    return _manager


def invalidate_damage_model_cache() -> None:
    global _manager
    _manager = None


__all__ = [
    "MODIFIER_FIELDS",
    "RATIO_FIELDS",
    "DamageModelManager",
    "get_damage_model_manager",
    "invalidate_damage_model_cache",
]
