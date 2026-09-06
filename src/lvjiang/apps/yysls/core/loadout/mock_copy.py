"""模拟装备跨用户批量复制。"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from ..equip_parser.models import make_fingerprint
from .models import EQUIPMENT_CREATED_AT, EQUIPMENT_UPDATED_AT
from .repository import LoadoutRepository, stamp_equipment_write

_STORAGE_FIELDS = {
    "_fp", EQUIPMENT_CREATED_AT, EQUIPMENT_UPDATED_AT, "cooldown_expires_at",
}


@dataclass(frozen=True)
class MockCopyResult:
    target_username: str
    copied: int = 0
    existing: int = 0
    conflicts: int = 0
    error: str = ""


def _copy_payload(equip: dict) -> dict:
    value = copy.deepcopy(equip)
    for key in _STORAGE_FIELDS:
        value.pop(key, None)
    value.setdefault("_extra", {})["is_mock"] = True
    fp = make_fingerprint(value, is_mock=True)
    if not fp:
        raise ValueError("模拟装备数据无法生成指纹")
    value["_fp"] = fp
    return value


def _comparable(equip: dict) -> dict:
    value = copy.deepcopy(equip)
    for key in _STORAGE_FIELDS:
        value.pop(key, None)
    return value


def copy_mock_items_to_users(
    source_username: str,
    target_usernames: list[str],
    fingerprints: set[str],
    users_dir: Path | None = None,
) -> list[MockCopyResult]:
    """把来源用户的模拟装备复制到多个目标用户，不迁移方案引用。"""
    source = LoadoutRepository(source_username, users_dir).load()
    payloads: list[dict] = []
    for fp in fingerprints:
        equip = source.equipment_items.get(fp)
        if equip is None:
            raise ValueError(f"待复制模拟装备已不存在: {fp}")
        if not fp.startswith("mock_"):
            raise ValueError(f"只能复制模拟装备: {fp}")
        payloads.append(_copy_payload(equip))

    targets = list(dict.fromkeys(target_usernames))
    if source_username in targets:
        raise ValueError("不能把模拟装备复制给来源用户")

    results: list[MockCopyResult] = []
    for username in targets:
        copied = existing = conflicts = 0
        repo = LoadoutRepository(username, users_dir)

        def mutate(state) -> None:
            nonlocal copied, existing, conflicts
            for payload in payloads:
                fp = payload["_fp"]
                current = state.equipment_items.get(fp)
                if current is None:
                    state.equipment_items[fp] = stamp_equipment_write(
                        payload, fp, None)
                    copied += 1
                elif _comparable(current) == _comparable(payload):
                    existing += 1
                else:
                    conflicts += 1

        try:
            repo.update(mutate)
        except Exception as exc:  # 单个目标失败不能阻断其他用户
            results.append(MockCopyResult(username, error=str(exc)))
            continue
        results.append(MockCopyResult(
            username, copied=copied, existing=existing, conflicts=conflicts))
    return results


__all__ = ["MockCopyResult", "copy_mock_items_to_users"]
