"""图像参考库的合并视图与读写。

参考图是配置资产，走 system/local 双层，每层均按空间组织：

- system 层：``references/{space}.yaml`` + ``references/{space}/{分组}/*.png``
- local 层：``references/{space}.yaml``（条目级 diff：references + deleted）
  + ``references/{space}/{分组}/*.png``
- 激活空间：``interface.json`` 的 actives.space

图片目录按**分组**组织，一个分组一个目录，没有分组的条目落 ``default/``：
写入时取 ``meta.group`` 的第一个分组决定目录（见 ``models.group_dir_name``），
改分组时把可写层里的图片一并挪过去。目录由扫描空间目录下的二级子目录自动
发现，所以手工把图片挪到别的分组目录也能被找到，YAML 的 ``file`` 字段只存
文件名。

读取恒为合并视图：system 条目（deleted 剔除、同 file 被 local 条目替换）+
local 独有条目；meta_schema 用户层存在即整列表替换。写入按模式路由
（开发→system，用户→local），与 ConfigResolver 同一套模式判定。

空间管理（有哪些、激活哪个、增删）在 :class:`.spaces.ReferenceSpaces`。
"""

import uuid
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from lvjiang.core.config import load_yaml, save_yaml
from lvjiang.core.config.localized import resolve_localized
from lvjiang.core.config.resolver import get_resolver

from .models import (
    _PREDEFINED_INPUT_KEYS,
    _SEED_META_SCHEMA,
    DEFAULT_MATCH_THRESHOLD,
    REFERENCE_OUTPUT_RESERVED_KEYS,
    MetaFieldDef,
    ReferenceEntry,
    group_dir_name,
    primary_group,
    validate_crop,
    validate_group_names,
)
from .spaces import ReferenceSpaces

if TYPE_CHECKING:
    import numpy as np


class ReferenceDatabase:
    """图像参考库 - system/local 双层合并视图与按模式路由的读写

    用法：
        db = ReferenceDatabase()
        db.load()
        db.add_entry(label="定音石", meta={"level": 100, "group": "调律材料"}, image_data=bgr_array)
    """

    def __init__(
        self,
        system_dir: Path | str | None = None,
        system_yaml: Path | str | None = None,
        local_dir: Path | str | None = None,
        local_yaml: Path | str | None = None,
        dev_mode: bool | None = None,
        system_spaces_dir: Path | str | None = None,
        local_spaces_dir: Path | str | None = None,
        session_path: Path | str | None = None,
    ):
        self._system_dir_override = Path(system_dir) if system_dir else None
        self._system_yaml_override = Path(system_yaml) if system_yaml else None
        self._local_dir_override = Path(local_dir) if local_dir else None
        self._local_yaml_override = Path(local_yaml) if local_yaml else None
        self._dev_mode = dev_mode
        self._system_spaces_dir_override = (
            Path(system_spaces_dir) if system_spaces_dir else None)
        self._local_spaces_dir_override = (
            Path(local_spaces_dir) if local_spaces_dir else None)
        self._session_path_override = Path(session_path) if session_path else None
        self._interface_store_cache = None  # 仅 session_path override 时使用的独立 store
        # 空间状态：列表、系统空间集合与激活空间都由 ReferenceSpaces 持有
        self.spaces = ReferenceSpaces(
            system_root=lambda: self.system_spaces_dir,
            local_root=lambda: self.local_spaces_dir,
            is_dev=self._is_dev,
            interface_store=self._get_interface_store,
        )
        # 分层状态
        self._system_entries: list[ReferenceEntry] = []
        self._local_entries: list[ReferenceEntry] = []
        self._deleted: set[str] = set()
        self._system_schema: list[MetaFieldDef] = []
        self._local_schema: list[MetaFieldDef] | None = None
        self._system_threshold: float | None = None
        self._local_threshold: float | None = None
        #: 空间 yaml 顶层 ``name`` 的磁盘原值（可能是按语言取值的映射）。
        #: 只用于展示，保存时必须原样写回，见 save()。
        self._system_space_name: object = None
        self._local_space_name: object = None
        # 合并视图（查询方法统一消费）
        self._entries: list[ReferenceEntry] = []
        self._meta_schema: list[MetaFieldDef] = []
        self._image_dirs: list[str] = []  # 目录扫描发现的图片目录（即分组目录）
        self.load()  # 构造时立即加载

    # ─── 属性 ────────────────────────────────────────────

    @property
    def system_dir(self) -> Path:
        """激活空间的 system 层图片目录"""
        if self._system_dir_override is not None:
            return self._system_dir_override
        return get_resolver().system_dir / "references" / self.spaces.active

    @property
    def local_dir(self) -> Path:
        """激活空间的 local 层图片目录"""
        if self._local_dir_override is not None:
            return self._local_dir_override
        return get_resolver().local_dir / "references" / self.spaces.active

    @property
    def system_yaml_path(self) -> Path:
        """激活空间的 system 层配置 yaml"""
        if self._system_yaml_override is not None:
            return self._system_yaml_override
        return get_resolver().system_dir / "references" / f"{self.spaces.active}.yaml"

    @property
    def local_yaml_path(self) -> Path:
        """激活空间的 local 层配置 yaml"""
        if self._local_yaml_override is not None:
            return self._local_yaml_override
        return get_resolver().local_dir / "references" / f"{self.spaces.active}.yaml"

    @property
    def system_spaces_dir(self) -> Path:
        """system 层空间根目录（扫描 *.yaml 得系统空间列表）"""
        if self._system_spaces_dir_override is not None:
            return self._system_spaces_dir_override
        return get_resolver().system_dir / "references"

    @property
    def local_spaces_dir(self) -> Path:
        """local 层空间根目录（覆盖层 yaml + 本地新建空间 yaml）"""
        if self._local_spaces_dir_override is not None:
            return self._local_spaces_dir_override
        return get_resolver().local_dir / "references"

    @property
    def active_space_path(self) -> Path:
        """激活空间（actives.space）的持久化位置：interface.json。"""
        if self._session_path_override is not None:
            return self._session_path_override.with_name("interface.json")
        from lvjiang.core.config.interface import interface_path
        return interface_path()


    def _is_dev(self) -> bool:
        if self._dev_mode is not None:
            return self._dev_mode
        return get_resolver().is_dev_mode()

    def image_path(self, filename: str) -> Path:
        """解析图片绝对路径：遍历全部图片目录查找文件

        Args:
            filename: 文件名（不含分组路径，如 'A1B2C3D4.png'）

        Returns:
            文件绝对路径（local 层优先 → system 层），不存在时返回 system 层路径
        """
        # 先在 local 层的全部图片目录中查找
        for directory in self._image_dirs:
            local = self.local_dir / directory / filename
            if local.exists():
                return local
        # 再在 system 层的全部图片目录中查找
        for directory in self._image_dirs:
            system = self.system_dir / directory / filename
            if system.exists():
                return system
        # 未找到时返回 system 层第一个图片目录的路径（保持向后兼容）
        if self._image_dirs:
            return self.system_dir / self._image_dirs[0] / filename
        return self.system_dir / filename

    @property
    def image_dirs(self) -> list[str]:
        """当前空间的图片目录列表（目录扫描发现，即各分组目录）"""
        return list(self._image_dirs)

    @property
    def entries(self) -> list[ReferenceEntry]:
        return list(self._entries)

    def _get_interface_store(self):
        """激活空间（actives.space）的读写入口。

        缺省路径用全局单例；``session_path`` 覆盖时按同目录的 interface.json
        建独立实例，测试之间互不干扰。
        """
        if self._session_path_override is not None:
            if self._interface_store_cache is None:
                from lvjiang.core.config.interface import InterfaceStore
                self._interface_store_cache = InterfaceStore(
                    self._session_path_override.with_name("interface.json"))
            return self._interface_store_cache
        from lvjiang.core.config.interface import get_interface_store
        return get_interface_store()

    # ─── 加载 / 保存 ─────────────────────────────────────

    @staticmethod
    def _parse_entries(raw_list) -> list[ReferenceEntry]:
        entries = []
        for item in raw_list or []:
            entries.append(ReferenceEntry(
                file=item.get("file", ""),
                label=item.get("label", ""),
                meta=item.get("meta", {}),
                source=item.get("source", ""),
                notes=item.get("notes", ""),
            ))
        return entries

    def load(self):
        """扫描空间列表 + 加载**运行时**空间的 system 层与 local 覆盖层。

        运行时空间 = ``actives.space``（主页面下拉或方案决定）。管理界面要换一份
        查看/编辑用 :meth:`open_space`，它不会改动这个持久化值。
        """
        # 空间列表与激活空间（路径属性依赖 self.spaces.active，必须最先解析）
        self.spaces.refresh()
        if self._local_spaces_dir_override is None:  # override 场景（测试）不体检真实 config
            ReferenceSpaces.warn_legacy_roster(get_resolver().local_dir)
        self.spaces.resolve_active()
        self._load_active_space()

    def open_space(self, name: str) -> bool:
        """切到某个空间查看/编辑，**不写** ``actives.space``（只影响本实例）。

        图库管理器用它切换「正在编辑的空间」；运行时用哪个空间仍由主页面下拉 /
        方案决定，关窗后主页面刷新即可回到那个空间。
        """
        self.spaces.refresh()
        if not self.spaces.open(name):
            return False
        self._load_active_space()
        return True

    def runtime_space(self) -> str:
        """运行时使用的空间 key（``actives.space``）；已失效时回落默认空间。"""
        saved = self.spaces.persisted_active()
        if saved and saved in self.spaces.names:
            return saved
        return self.spaces.default_space

    def _load_active_space(self):
        """按 ``spaces.active`` 重新加载两层内容与合并视图。"""
        # system 层
        if self.system_yaml_path.exists():
            data = load_yaml(self.system_yaml_path)
            self._system_entries = self._parse_entries(data.get("references"))
            self._system_schema = self._parse_schema(data.get("meta_schema"))
            self._system_threshold = self._parse_threshold(data.get("match_threshold"))
            self._system_space_name = data.get("name")
        else:
            logger.info(f"空间 yaml 不存在，使用空数据库: {self.system_yaml_path}")
            self._system_entries = []
            self._system_schema = self._seed_schema()
            self._system_threshold = None
            self._system_space_name = None

        # local 覆盖层（条目级 diff：references + deleted + 可选 meta_schema）
        self._local_entries = []
        self._deleted = set()
        self._local_schema = None
        self._local_threshold = None
        self._local_space_name = None
        if self.local_yaml_path.exists():
            overlay = load_yaml(self.local_yaml_path)
            self._local_entries = self._parse_entries(overlay.get("references"))
            self._deleted = {str(x) for x in overlay.get("deleted") or []}
            if overlay.get("meta_schema"):
                self._local_schema = self._parse_schema(overlay["meta_schema"])
            self._local_threshold = self._parse_threshold(overlay.get("match_threshold"))
            self._local_space_name = overlay.get("name")

        self._rebuild_merged()
        logger.info(
            f"参考图库加载完成（空间={self.spaces.active}）: 合并 {len(self._entries)} 条"
            f"（system {len(self._system_entries)} / local {len(self._local_entries)}"
            f" / 删除 {len(self._deleted)}）"
        )

    def _rebuild_merged(self):
        """重建合并视图：system（剔除 deleted、同 file 被 local 替换）+ local 独有"""
        local_by_file = {e.file: e for e in self._local_entries}
        merged: list[ReferenceEntry] = []
        for e in self._system_entries:
            if e.file in self._deleted:
                continue
            merged.append(local_by_file.pop(e.file, e))
        # local 独有条目按原顺序追加
        merged.extend(e for e in self._local_entries if e.file in local_by_file)
        self._entries = merged
        self._meta_schema = (self._local_schema
                             if self._local_schema is not None
                             else self._system_schema)
        # 图片目录：目录扫描发现（local 层 + system 层合并去重）
        self._image_dirs = self._discover_image_dirs()

    @staticmethod
    def _seed_schema() -> list[MetaFieldDef]:
        """返回种子 meta_schema"""
        return [MetaFieldDef(**d) for d in _SEED_META_SCHEMA]

    @staticmethod
    def _parse_threshold(raw) -> float | None:
        """解析 match_threshold，非法/缺失返回 None（落回下层或默认值）"""
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if 0.0 <= value <= 1.0 else None

    def _discover_image_dirs(self) -> list[str]:
        """扫描 local 层 + system 层目录，发现全部二级子目录作为图片目录

        目录名就是分组名，所以这里刻意不过滤名字：手工把图片挪到新目录、
        或者修复后的历史目录都算数。``default`` 也在这里被发现——它是未分组
        条目的落盘位置，不是分组。
        """
        directories: list[str] = []
        # 先扫描 local 层
        if self.local_dir.exists():
            for d in self.local_dir.iterdir():
                if d.is_dir() and not d.name.startswith('.') and d.name not in directories:
                    directories.append(d.name)
        # 再扫描 system 层
        if self.system_dir.exists():
            for d in self.system_dir.iterdir():
                if d.is_dir() and not d.name.startswith('.') and d.name not in directories:
                    directories.append(d.name)
        return sorted(directories)

    def _parse_schema(self, raw) -> list[MetaFieldDef]:
        """解析 meta_schema 列表，缺失或为空时种子化"""
        if not raw:
            return self._seed_schema()
        # 种子字段索引：用于为旧 YAML 中未指定 type/sort_by 的字段提供默认值
        seed_by_key = {d["key"]: d for d in _SEED_META_SCHEMA}
        result: list[MetaFieldDef] = []
        for item in raw:
            key = item.get("key", "").strip()
            if not key:
                continue
            # 种子里有同名字段就拿它的 type/sort_by 当默认值，否则用通用默认。
            # 不写成 seed_by_key.get(key, {})：空 dict 让 seed 的类型退化成
            # 联合类型，反而把 TypedDict 刚换回来的检查又丢掉。
            seed = seed_by_key.get(key)
            seed_type = seed["type"] if seed else "text"
            seed_sort = seed["sort_by"] if seed else "asc"
            ftype = str(item.get("type", seed_type) or "text").strip().lower()
            if ftype not in ("text", "number"):
                ftype = "text"
            sort_by = str(item.get("sort_by", seed_sort) or "asc").strip().lower()
            if sort_by not in ("asc", "desc"):
                sort_by = "asc"
            result.append(MetaFieldDef(
                key=key,
                name=item.get("name", "") or key,
                filterable=bool(item.get("filterable", False)),
                type=ftype,
                sort_by=sort_by,
                scope=self._parse_scope(item.get("scope")),
                crop=self._parse_crop(item.get("crop")),
            ))
            if (
                result[-1].scope == "output"
                and result[-1].key in REFERENCE_OUTPUT_RESERVED_KEYS
            ):
                raise ValueError(
                    f"output 元数据字段 {key!r} 是参考图识别保留字段"
                )
        return result or self._seed_schema()

    @staticmethod
    def _parse_scope(raw) -> str:
        """解析 scope，非法/缺失默认 input"""
        scope = str(raw or "input").strip().lower()
        return scope if scope in ("input", "output") else "input"

    @staticmethod
    def _parse_crop(raw) -> list[float] | None:
        """解析 crop 区域 [x, y, w, h]（归一化），非法返回 None"""
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            return None
        try:
            values = [float(v) for v in raw]
        except (TypeError, ValueError):
            return None
        return validate_crop(values)

    def save(self):
        """按模式落盘：开发→system 全量；用户→local 条目级 diff（空则删覆盖文件）

        空间 yaml 顶层的 ``name`` 是本地化展示名（``{"zh_CN": …, "en_US": …}``），
        本模块不编辑它，但保存时必须原样写回——否则一次普通的条目保存就会把
        随包的双语名字抹掉。
        """
        if self._is_dev():
            data: dict = {"version": 1}
            if self._system_space_name is not None:
                data["name"] = self._system_space_name
            data["meta_schema"] = [self._schema_to_dict(f) for f in self._system_schema]
            data["references"] = [asdict(e) for e in self._system_entries]
            if self._system_threshold is not None:
                data["match_threshold"] = self._system_threshold
            save_yaml(self.system_yaml_path, data)
            logger.info(f"参考图库保存完成: {len(self._system_entries)} 条记录 -> {self.system_yaml_path}")
            return

        has_overlay = bool(self._local_entries or self._deleted
                           or self._local_schema is not None
                           or self._local_threshold is not None
                           or self._local_space_name is not None)
        if not has_overlay:
            if self.local_yaml_path.exists():
                self.local_yaml_path.unlink()
            return
        overlay: dict = {"version": 1}
        if self._local_space_name is not None:
            overlay["name"] = self._local_space_name
        overlay["references"] = [asdict(e) for e in self._local_entries]
        overlay["deleted"] = sorted(self._deleted)
        if self._local_schema is not None:
            overlay["meta_schema"] = [self._schema_to_dict(f) for f in self._local_schema]
        if self._local_threshold is not None:
            overlay["match_threshold"] = self._local_threshold
        save_yaml(self.local_yaml_path, overlay)
        logger.info(
            f"参考图库覆盖层保存完成: {len(self._local_entries)} 条"
            f" + 删除 {len(self._deleted)} -> {self.local_yaml_path}"
        )

    @staticmethod
    def _schema_to_dict(f: MetaFieldDef) -> dict:
        """序列化字段定义：crop 为 None 时省略该键"""
        d = asdict(f)
        if d.get("crop") is None:
            d.pop("crop", None)
        return d

    @staticmethod
    def _find(entries: list[ReferenceEntry], filename: str) -> "ReferenceEntry | None":
        for e in entries:
            if e.file == filename:
                return e
        return None

    def _coerce_numeric_meta(self, meta: dict) -> dict:
        """把 meta_schema 声明为 ``number`` 的字段从录入文本转成数字。

        录入界面（新增参考图、图库管理编辑、批量设置）收集 meta 的方式是
        「取控件文本」，不转换就会把 ``110`` 存成字符串 ``'110'``：同一图库里
        出现 110 与 '110' 两种写法，按等级筛选、数值排序与业务比较在两者之间
        给出不同答案。这里是唯一的写入口，按 schema 统一，不针对某个字段写死。

        两种特殊情况：
        - 空文本视为「未填」：新增时不落该键，更新时视为未修改（编辑区把值清空
          再保存本来就不提交该字段，这里保持同一语义，不会写入空串）；
        - 解析不出来的文本原样保留（不能悄悄吞掉用户输入），后续按文本处理。
        """
        numeric_keys = {field.key for field in self._meta_schema
                        if field.type == "number"}
        if not numeric_keys:
            return meta
        for key in list(meta):
            if key not in numeric_keys:
                continue
            value = meta[key]
            if isinstance(value, bool) or isinstance(value, (int, float)):
                continue
            text = str(value).strip()
            if not text:
                meta.pop(key)
                continue
            try:
                number = float(text)
            except ValueError:
                continue
            meta[key] = int(number) if number.is_integer() else number
        return meta

    # ─── 条目操作 ─────────────────────────────────────────

    def add_entry(
        self,
        label: str,
        meta: dict | None = None,
        source: str = "",
        notes: str = "",
        image_data: "np.ndarray | None" = None,
    ) -> ReferenceEntry:
        """新增参考图条目（开发→system 层，用户→local 层，图片按分组落目录）

        Args:
            label: 主标识（如 "定音石"）
            meta: 元数据字典（如 {"level": 100, "group": "调律材料"}）
            source: 来源截图名
            notes: 备注
            image_data: 如果提供，自动保存为 PNG 文件（落在第一个分组目录，
                未分组落 ``default/``）

        Returns:
            新建的 ReferenceEntry

        Raises:
            ValueError: 分组名非法（保留名 ``default``、含路径分隔符等）。
                校验在写图片之前完成，失败不会留下半写状态。
        """
        meta = dict(meta or {})
        meta = self._coerce_numeric_meta(meta)
        validate_group_names(meta.get("group"))
        filename = self.next_filename()
        entry = ReferenceEntry(
            file=filename,
            label=label,
            meta=meta,
            source=source,
            notes=notes,
        )

        # 保存图片文件（目录跟随条目分组）
        if image_data is not None:
            layer_dir = self.system_dir if self._is_dev() else self.local_dir
            self._save_image(layer_dir, filename, image_data, meta.get("group"))

        target = self._system_entries if self._is_dev() else self._local_entries
        target.append(entry)
        self.save()
        self._rebuild_merged()
        logger.debug(f"新增参考图: {filename} label={label} meta={meta}")
        return entry

    def update_entry(self, filename: str, **kwargs) -> bool:
        """修改条目元数据；分组变化时把可写层里的图片挪到新分组目录。

        用户模式改 system 条目时复制进 local 做影子（file 不变）。图片只在
        **可写层**里挪动：系统图片属于系统内容，用户模式下既不删也不移动。

        Args:
            filename: 目标文件名（不含分组路径）
            **kwargs: 要修改的字段（label, meta, source, notes）

        Returns:
            是否找到并修改

        Raises:
            ValueError: 新分组名非法（保留名 ``default``、含路径分隔符等）。
                校验先于任何写入，失败不会改到条目或图片。
        """
        local_entry = self._find(self._local_entries, filename)
        system_entry = self._find(self._system_entries, filename)
        if local_entry is None and system_entry is None:
            logger.warning(f"未找到参考图文件: {filename}")
            return False

        incoming_meta = kwargs.get("meta")
        if isinstance(incoming_meta, dict):
            incoming_meta = self._coerce_numeric_meta(dict(incoming_meta))
            if "group" in incoming_meta:
                validate_group_names(incoming_meta.get("group"))

        entry: ReferenceEntry
        if local_entry is not None:
            entry = local_entry
        elif self._is_dev():
            assert system_entry is not None
            entry = system_entry
        else:
            # 用户模式改 system 条目 → 复制进 local 做影子
            assert system_entry is not None
            entry = ReferenceEntry(**asdict(system_entry))
            self._local_entries.append(entry)

        previous_group = primary_group(entry.meta.get("group"))
        if isinstance(incoming_meta, dict):
            entry.meta.update(incoming_meta)
        for key, value in kwargs.items():
            if hasattr(entry, key) and key != "meta":
                setattr(entry, key, value)

        if primary_group(entry.meta.get("group")) != previous_group:
            self._move_image_to_group(filename, entry.meta.get("group"))

        self.save()
        self._rebuild_merged()
        logger.debug(f"更新参考图: {filename} {kwargs}")
        return True

    def remove_entry(self, filename: str, delete_file: bool = True) -> bool:
        """删除条目

        开发模式直删所在层。用户模式下只能删自己的 local 条目；系统条目属于
        system 内容，不允许删除——想要一套自己的图请**新建图库空间**，
        而不是把系统图去掉（详见 docs/design/architecture/overview/04-config-layering.md）。

        Args:
            filename: 目标文件路径（相对路径）
            delete_file: 是否同时删除可写层的图片文件

        Raises:
            SystemContentProtected: 用户模式下试图删除系统条目

        Returns:
            是否找到并删除
        """
        local_entry = self._find(self._local_entries, filename)
        system_entry = self._find(self._system_entries, filename)
        if local_entry is None and system_entry is None:
            return False

        if system_entry is not None and not self._is_dev():
            from ..config.resolver import SystemContentProtected
            raise SystemContentProtected(
                f"参考图 {filename} 由系统图库提供，用户模式下不可删除；"
                f"如需自己的一套请新建图库空间")

        if local_entry is not None:
            self._local_entries.remove(local_entry)
            if delete_file:
                self._delete_image(self.local_dir, filename)
        if system_entry is not None:
            self._system_entries.remove(system_entry)
            if delete_file:
                self._delete_image(self.system_dir, filename)

        self.save()
        self._rebuild_merged()
        logger.debug(f"删除参考图条目: {filename}")
        return True

    # ─── 查询 ─────────────────────────────────────────────

    def list_entries(
        self,
        label_filter: str | None = None,
        group_filter: str | None = None,
        level_filter: int | None = None,
        meta_filters: dict[str, str] | None = None,
    ) -> list[ReferenceEntry]:
        """返回参考图条目列表

        Args:
            label_filter: 按标识过滤，None 返回全部
            group_filter: 按分组过滤，None 返回全部
            level_filter: 按等级过滤（兼容保留），None 返回全部
            meta_filters: 按任意 meta key 文本相等过滤（{key: 文本值}）
        """
        result = list(self._entries)
        if label_filter:
            result = [e for e in result if e.label == label_filter]
        if group_filter:
            result = [e for e in result if group_filter in e.groups]
        if level_filter is not None:
            result = [e for e in result if e.level == level_filter]
        if meta_filters:
            for key, value in meta_filters.items():
                result = [e for e in result if self._meta_text(e, key) == value]
        return result

    def get_labels(self) -> list[str]:
        """返回所有去重标识列表（排序）"""
        labels = sorted({e.label for e in self._entries if e.label})
        return labels


    def get_all_labels_by_group(self) -> dict[str, list[str]]:
        """返回所有分组 -> 标识列表的映射，支持逗号分隔的多组"""
        result: dict[str, set[str]] = {}
        for e in self._entries:
            if e.label:
                for group in e.groups:
                    if group not in result:
                        result[group] = set()
                    result[group].add(e.label)
        return {g: sorted(labels) for g, labels in result.items()}

    def get_groups(self) -> list[str]:
        """返回所有去重分组列表（排序），支持逗号分隔的多组"""
        all_groups: set[str] = set()
        for e in self._entries:
            all_groups.update(e.groups)
        return sorted(all_groups)


    # ─── meta_schema 与通用 meta 查询 ──────────────────────

    def get_meta_schema(self) -> list[MetaFieldDef]:
        """返回 meta 字段定义列表"""
        return list(self._meta_schema)

    def set_meta_schema(self, schema: list[MetaFieldDef]):
        """设置并持久化 meta 字段定义（用户模式整列表替换进 local）"""
        conflicts = sorted(
            field.key for field in schema
            if field.scope == "output"
            and field.key in REFERENCE_OUTPUT_RESERVED_KEYS
        )
        if conflicts:
            raise ValueError(
                f"output 元数据字段占用参考图识别保留字段: {', '.join(conflicts)}"
            )
        if self._is_dev():
            self._system_schema = list(schema)
        else:
            self._local_schema = list(schema)
        self.save()
        self._rebuild_merged()
        logger.debug(f"更新 meta_schema: {[f.key for f in schema]}")

    def get_output_fields(self) -> list[MetaFieldDef]:
        """返回输出元数据字段（scope=output 且 crop 有效），按定义顺序"""
        return [f for f in self._meta_schema
                if f.scope == "output" and f.crop is not None]

    def get_custom_input_fields(self) -> list[MetaFieldDef]:
        """返回非预制的输入元数据字段（排除 label/group/notes）"""
        return [f for f in self._meta_schema
                if f.scope == "input" and f.key not in _PREDEFINED_INPUT_KEYS]

    # ─── 匹配度阈值 ───────────────────────────────────

    def get_match_threshold(self) -> float:
        """返回匹配度阈值：local 覆盖 → system → 默认值"""
        if self._local_threshold is not None:
            return self._local_threshold
        if self._system_threshold is not None:
            return self._system_threshold
        return DEFAULT_MATCH_THRESHOLD

    def set_match_threshold(self, value: float):
        """设置并持久化匹配度阈值（开发→system；用户→local 覆盖，
        回设为 system 有效值时清除覆盖）"""
        value = round(float(value), 4)
        if self._is_dev():
            self._system_threshold = value
        else:
            system_effective = (self._system_threshold
                                if self._system_threshold is not None
                                else DEFAULT_MATCH_THRESHOLD)
            self._local_threshold = None if value == system_effective else value
        self.save()
        logger.debug(f"更新匹配度阈值: {value}")

    @staticmethod
    def _meta_text(entry: ReferenceEntry, key: str) -> str:
        """返回条目某 meta key 的文本值（统一 str，缺失为空）"""
        value = entry.meta.get(key)
        return "" if value is None else str(value)


    def get_meta_options(self, field: MetaFieldDef) -> list[str]:
        """返回某 meta 字段的去重值列表，按 type/sort_by 排序

        - type="number"：按数值排序，数字优先于非数字文本，空值不在列表中
        - type="text"：按字典序升序
        """
        raw = {self._meta_text(e, field.key) for e in self._entries}
        raw.discard("")
        values = list(raw)
        if field.type == "number":
            def _num_key(v: str):
                try:
                    return (0, float(v))
                except (ValueError, TypeError):
                    return (1, v)
            values.sort(key=_num_key, reverse=(field.sort_by == "desc"))
        else:
            values.sort(reverse=(field.sort_by == "desc"))
        return values


    def get_entry(self, filename: str) -> ReferenceEntry | None:
        """按文件路径查找条目"""
        for e in self._entries:
            if e.file == filename:
                return e
        return None

    def next_filename(self) -> str:
        """返回新的文件名（如 'A1B2C3D4.png'）

        使用 UUID 前 8 位（大写字母）作为文件名，防止重复。
        不含目录路径：落盘目录由条目的分组决定（见 ``_save_image``）。
        """
        return f"{uuid.uuid4().hex[:8].upper()}.png"

    # ─── 内部方法 ─────────────────────────────────────────

    def _save_image(self, layer_dir: Path, filename: str,
                    image_data: "np.ndarray", group: object = ""):
        """保存 numpy 图像到指定层的分组目录（未分组落 ``default/``）

        Args:
            layer_dir: 层目录（system_dir 或 local_dir）
            filename: 文件名（不含目录路径）
            image_data: numpy 图像数据
            group: 条目分组文本；取第一个分组当目录名
        """
        import cv2
        target_dir = layer_dir / group_dir_name(group)
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / filename
        # cv2.imwrite 不支持中文路径，用 imencode + 文件写入
        success, buf = cv2.imencode('.png', image_data)
        if success:
            file_path.write_bytes(buf.tobytes())
            logger.debug(f"保存参考图: {file_path} ({image_data.shape[1]}x{image_data.shape[0]})")
        else:
            logger.error(f"图片编码失败: {file_path}")

    def _find_image(self, layer_dir: Path, filename: str) -> Path | None:
        """在某一层的图片目录里找已存在的文件（也认层根目录的老位置）"""
        for directory in self._image_dirs:
            candidate = layer_dir / directory / filename
            if candidate.is_file():
                return candidate
        legacy = layer_dir / filename
        return legacy if legacy.is_file() else None

    def _move_image_to_group(self, filename: str, group: object) -> bool:
        """分组变化时把**可写层**里的图片挪到新分组目录。

        系统图片属于系统内容：用户模式改系统条目的分组只写 local 影子，
        图片原地不动（``image_path`` 仍能按文件名找到它）。
        """
        layer_dir = self.system_dir if self._is_dev() else self.local_dir
        source = self._find_image(layer_dir, filename)
        if source is None:
            return False
        target = layer_dir / group_dir_name(group) / filename
        if source == target:
            return True
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        logger.debug(f"参考图随分组移动: {source} -> {target}")
        return True

    def _delete_image(self, layer_dir: Path, filename: str):
        """删除指定层的图片文件（遍历全部图片目录查找，不存在则静默跳过）

        Args:
            layer_dir: 层目录（system_dir 或 local_dir）
            filename: 文件名（不含目录路径）
        """
        file_path = self._find_image(layer_dir, filename)
        if file_path is None:
            return
        file_path.unlink()
        logger.debug(f"删除参考图文件: {file_path}")

    # ─── 图库空间（委托 ReferenceSpaces） ─────────────────

    def get_spaces(self) -> list[str]:
        """空间 key 列表（system 扫描 ∪ local 扫描，各自按名排序；全空回退默认空间）

        返回的是**稳定 key**（``android`` / ``desktop`` / 用户自建的名字），
        界面显示走 :meth:`space_label`。
        """
        return list(self.spaces.names)

    def space_label(self, name: str) -> str:
        """空间 key → 展示名：读空间 yaml 顶层 ``name``（可本地化），无则回退 key。

        local 层优先（用户自建空间只有 local yaml），再查 system 层。名字只用于
        展示，任何路径、持久化引用与比较都用 key。
        """
        for root in (self.local_spaces_dir, self.system_spaces_dir):
            path = root / f"{name}.yaml"
            if not path.is_file():
                continue
            try:
                doc = load_yaml(path)
            except Exception as exc:  # noqa: BLE001 展示名读失败不该拖垮空间列表
                logger.warning(f"空间展示名读取失败 {path}: {exc}")
                continue
            return resolve_localized(doc.get("name"), fallback=name)
        return name

    def set_space_name(self, name: str, value: object | None) -> bool:
        """写入空间展示名（多语言映射）；``None`` 表示清空、显示名回落 key。

        key 不可改——它是目录名与持久化引用，改名要新建空间。编辑的是**激活**
        空间时重载一次，让内存里的名字缓存与面板一起跟上。
        """
        if not self.spaces.write_name(name, value):
            return False
        if name == self.spaces.active:
            self.load()
        return True

    def is_system_space(self, name: str) -> bool:
        """该空间是否由 system 层定义（系统内容，用户层只能覆盖不能删除）"""
        return self.spaces.is_system(name)

    def is_user_mode(self) -> bool:
        """当前是否为用户模式（写 local 层；系统内容只能覆盖不能删除）"""
        return self.spaces.is_user_mode()

    def get_active_space(self) -> str:
        """当前激活的图库空间名"""
        return self.spaces.active

    def set_active_space(self, name: str) -> bool:
        """切换激活空间（持久化到 interface.json）并重载新空间。

        切换后必须重载：否则旧空间的内存态会被后续 save() 写进新空间 yaml。
        """
        if not self.spaces.set_active(name):
            return False
        self.load()
        return True

    def create_space(self, name: str, display_name: object | None = None) -> bool:
        """新建空图库空间（空 meta_schema + 空条目）；可同时写入多语言展示名。"""
        return self.spaces.create(name, display_name)

    def can_delete_space(self, name: str) -> str:
        """返回该空间不可删除的原因；可删返回空串。"""
        return self.spaces.can_delete(name)

    def delete_space(self, name: str) -> bool:
        """删除图库空间（yaml + 同名图片目录）。"""
        deleted, active_changed = self.spaces.delete(name)
        if active_changed:
            self.load()
        return deleted
