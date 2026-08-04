"""配置解析器 —— system/local 双层配置的唯一读写咽喉

三层目录职责：
- config/system  出厂默认（进 git，用户模式下只读）
- config/local   用户覆盖层：影子文件 + 键级 diff + 墓碑（目录结构镜像 system）
- config/session 纯运行态（不经本模块，见 core.config.session.SessionStore）

读语义（两模式一致）：恒为 local 覆盖 system 的合并视图。
写语义（按模式路由）：开发模式写 system，用户模式写 local。

模式判定：LVJIANG_DEV_MODE 环境变量（1/0）强制覆盖 > PROJECT_ROOT/.git 探测。

两档合并语义：
- 实体文件（一物一文件：scenes/*.yaml、workflows/*.wf、
  layouts/{name}/{scene}.json、yysls/tuning_rules/*.yaml、
  references/**/*.png）→ 整文件影子 + 墓碑
  （local/<rel>.deleted 空标记文件）
- 聚合键值文件（scenes.yaml、workflows.yaml、layouts.yaml、
  yysls/attributes.yaml、yysls/tuning_base.yaml）→ 键级 diff 深合并；
  dict 递归、列表与标量整键替换；
  每层 dict 支持 "__deleted__": [key, ...] 删除键
"""
from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Callable

import yaml
from loguru import logger

# 聚合 diff 中的删除键标记
DELETED_KEY = "__deleted__"
# 墓碑文件后缀
TOMBSTONE_SUFFIX = ".deleted"


# ─── 纯函数：聚合 diff 的合并与求解 ─────────────────────────

def merge_doc(base: dict, overlay: dict) -> dict:
    """system 文档 ← local diff 深合并（不修改入参）

    dict 递归合并；列表与标量整键替换；overlay 每层的 __deleted__
    列表指定要从结果中删除的键。
    """
    result = deepcopy(base) if isinstance(base, dict) else {}
    if not isinstance(overlay, dict):
        return result
    for key in overlay.get(DELETED_KEY, []) or []:
        result.pop(key, None)
    for key, value in overlay.items():
        if key == DELETED_KEY:
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_doc(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def compute_diff(base: dict, desired: dict) -> dict:
    """逆向求最小 diff，满足 merge_doc(base, diff) == desired

    新增/改动键入 diff；base 有而 desired 无的键入 __deleted__；
    嵌套 dict 递归求解，空节点不写入。
    """
    base = base if isinstance(base, dict) else {}
    diff: dict = {}
    deleted = [k for k in base if k not in desired]
    if deleted:
        diff[DELETED_KEY] = deleted
    for key, value in desired.items():
        base_value = base.get(key)
        if isinstance(value, dict) and isinstance(base_value, dict):
            sub = compute_diff(base_value, value)
            if sub:
                diff[key] = sub
        elif key not in base or base_value != value:
            diff[key] = deepcopy(value)
    return diff


# ─── 解析器 ──────────────────────────────────────────────

class ConfigResolver:
    """system/local 双层配置解析器

    不带参构造时各层根目录动态取自 constants（monkeypatch 友好）；
    测试可显式传入 tmp_path 与 dev_mode 构造隔离实例。
    """

    def __init__(
        self,
        system_dir: Path | str | None = None,
        local_dir: Path | str | None = None,
        dev_mode: bool | None = None,
    ):
        self._system_dir = Path(system_dir) if system_dir else None
        self._local_dir = Path(local_dir) if local_dir else None
        self._dev_mode = dev_mode if dev_mode is not None else self._compute_dev_mode()
        self._listeners: list[Callable[[str], None]] = []

    @staticmethod
    def _compute_dev_mode() -> bool:
        """计算开发模式：环境变量强制 > .git 探测"""
        env = os.environ.get("LVJIANG_DEV_MODE", "").strip().lower()
        if env in ("1", "true", "yes"):
            return True
        if env in ("0", "false", "no"):
            return False
        from ... import constants
        return (constants.PROJECT_ROOT / ".git").exists()

    # ─── 层根目录与模式 ─────────────────────────────────

    @property
    def system_dir(self) -> Path:
        if self._system_dir is not None:
            return self._system_dir
        from ... import constants
        return constants.SYSTEM_CONFIG_DIR

    @property
    def local_dir(self) -> Path:
        if self._local_dir is not None:
            return self._local_dir
        from ... import constants
        return constants.LOCAL_CONFIG_DIR

    def is_dev_mode(self) -> bool:
        """开发模式（写 system）or 用户模式（写 local）

        构造时已计算并缓存，此处直接返回。
        """
        return self._dev_mode

    def write_dir(self, rel_dir: str = "") -> Path:
        """当前模式的可写目录（确保存在），编辑器默认目录等场景用"""
        root = self.system_dir if self.is_dev_mode() else self.local_dir
        target = root / rel_dir if rel_dir else root
        target.mkdir(parents=True, exist_ok=True)
        return target

    # ─── 失效通知 ────────────────────────────────────────

    def add_change_listener(self, cb: Callable[[str], None]):
        if cb not in self._listeners:
            self._listeners.append(cb)

    def remove_change_listener(self, cb: Callable[[str], None]):
        if cb in self._listeners:
            self._listeners.remove(cb)

    def _notify(self, rel_path: str):
        for cb in list(self._listeners):
            try:
                cb(rel_path)
            except Exception as e:  # noqa: BLE001 监听器异常不阻断写入方
                logger.warning(f"配置变更监听器异常: {e}")

    # ─── 实体文件（整文件影子 + 墓碑）──────────────────────

    def _tombstone(self, rel_path: str) -> Path:
        return self.local_dir / (rel_path + TOMBSTONE_SUFFIX)

    def resolve_read(self, rel_path: str) -> Path | None:
        """实体读解析：local 影子优先 → system；墓碑返回 None"""
        if self._tombstone(rel_path).exists():
            return None
        local = self.local_dir / rel_path
        if local.exists():
            return local
        system = self.system_dir / rel_path
        return system if system.exists() else None

    def enumerate_entities(self, rel_dir: str, pattern: str) -> list[str]:
        """枚举实体文件名：system ∪ local 并集，剔除墓碑，跳过 _ 前缀

        Returns:
            排序后的文件名列表（不含目录），local 遮盖同名天然成立。
        """
        names: set[str] = set()
        for root in (self.system_dir, self.local_dir):
            base = root / rel_dir
            if not base.is_dir():
                continue
            for p in base.glob(pattern):
                if p.is_file() and not p.name.startswith("_"):
                    names.add(p.name)
        alive = [n for n in sorted(names)
                 if not self._tombstone(f"{rel_dir}/{n}" if rel_dir else n).exists()]
        return alive

    def write_entity(self, rel_path: str, data: str | bytes) -> Path:
        """按模式写实体文件（开发→system，用户→local 影子并清同名墓碑）"""
        root = self.system_dir if self.is_dev_mode() else self.local_dir
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            target.write_bytes(data)
        else:
            target.write_text(data, encoding="utf-8")
        tomb = self._tombstone(rel_path)
        if tomb.exists():
            tomb.unlink()
        self._notify(rel_path)
        return target

    def delete_entity(self, rel_path: str):
        """按模式删实体：开发→直删 system；用户→删 local 影子，
        system 存在同名则落墓碑"""
        if self.is_dev_mode():
            target = self.system_dir / rel_path
            if target.exists():
                target.unlink()
        else:
            local = self.local_dir / rel_path
            if local.exists():
                local.unlink()
            if (self.system_dir / rel_path).exists():
                tomb = self._tombstone(rel_path)
                tomb.parent.mkdir(parents=True, exist_ok=True)
                tomb.touch()
        self._notify(rel_path)

    # ─── 聚合键值文件（键级 diff 深合并）──────────────────

    def _load_yaml(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:  # noqa: BLE001
            logger.error(f"配置解析失败 {path}: {e}")
            return {}

    def load_merged(self, rel_path: str) -> dict:
        """聚合读：system 文档 ← local diff 深合并"""
        base = self._load_yaml(self.system_dir / rel_path)
        overlay_path = self.local_dir / rel_path
        if not overlay_path.exists():
            return base
        return merge_doc(base, self._load_yaml(overlay_path))

    def save_merged(self, rel_path: str, full_doc: dict):
        """聚合写：开发→全量写 system；用户→逆向求 diff 写 local
        （diff 为空则删 local 覆盖文件）"""
        if self.is_dev_mode():
            target = self.system_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                yaml.dump(full_doc, allow_unicode=True,
                          default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
        else:
            base = self._load_yaml(self.system_dir / rel_path)
            diff = compute_diff(base, full_doc)
            overlay_path = self.local_dir / rel_path
            if diff:
                overlay_path.parent.mkdir(parents=True, exist_ok=True)
                overlay_path.write_text(
                    yaml.dump(diff, allow_unicode=True,
                              default_flow_style=False, sort_keys=False),
                    encoding="utf-8",
                )
            elif overlay_path.exists():
                overlay_path.unlink()
        self._notify(rel_path)


# ─── 模块级单例 ──────────────────────────────────────────

_resolver: ConfigResolver | None = None


def get_resolver() -> ConfigResolver:
    global _resolver
    if _resolver is None:
        _resolver = ConfigResolver()
    return _resolver
