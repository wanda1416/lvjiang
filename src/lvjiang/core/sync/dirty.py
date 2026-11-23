"""本地文档变更感知：写路径立即标脏 + 磁盘戳兜底，同步层消费。

两条来源合并成同一个「本地有改动」信号：

1. **写路径标脏**（`mark_dirty`）：本进程改完文档立刻记名，最快。
2. **磁盘戳比对**（`stamps`）：记录每份文档上次同步时的 ``(size, mtime_ns)``，
   下一轮 stat 一遍就能发现「谁改的」——包括**只读实例**或外部工具改的文件。
   只做 stat，不读内容、不哈希。

状态落 ``account.json`` 的 ``document_sync``；**只读实例不写**（登录态与同步
状态只归主实例），它的改动完全靠主实例的磁盘戳比对自动感知。
"""
from __future__ import annotations

from loguru import logger


def writable() -> bool:
    """本实例是否有权写同步状态（只读实例没有）。"""
    from ..access import is_readonly

    return not is_readonly()


def _read() -> dict:
    """整份 account.json（``document_sync`` 与登录态、profile 水位同住一个文件）。

    读写都经 ``core.account.service``：那边是 account.json 的唯一落点与唯一写
    入口（动态解析路径、进程内 + 跨进程锁、原子写）。此处另开一份读法会与
    写入端解析出不同路径，出现「写一个文件、读另一个文件」。
    """
    from ..account.service import load_profile

    return load_profile()


def _state() -> dict:
    state = _read().get("document_sync")
    return dict(state) if isinstance(state, dict) else {}


def _save(state: dict) -> None:
    """写回 document_sync。

    标记是尽力而为：写失败（未登录、只读实例、测试门禁）只降级为「下次多做
    一次全量对比」，绝不能让用户的一次便利贴保存因为标记写不进去而失败。
    写回经 ``service.mutate_profile``（进程内 + 跨进程锁 + 原子写），因此不会
    覆盖同一份 account.json 里 profile_sync / users_sync 的并发写入。
    """
    if not writable():
        return

    def _set(profile: dict) -> None:
        profile["document_sync"] = state

    try:
        from ..account.service import mutate_profile

        mutate_profile(_set, require_existing=True)
    except Exception as error:      # noqa: BLE001 — 标记失败不能顶掉业务写入
        logger.debug("[sync] 脏标记写回失败（不影响本地数据）：{}", error)


def mark_dirty(label: str, kind: str) -> None:
    """文档被本进程修改后调用；同步成功后由同步层清掉。"""
    state = _state()
    dirty = state.get("dirty")
    if not isinstance(dirty, dict):
        dirty = {}
    dirty[f"{label}/{kind}"] = True
    state["dirty"] = dirty
    _save(state)


def dirty_keys() -> set[str]:
    dirty = _state().get("dirty")
    return set(dirty) if isinstance(dirty, dict) else set()


def remote_document_id() -> int:
    value = _state().get("remote_document_id")
    return value if isinstance(value, int) and value >= 0 else 0


def stamps() -> dict[str, list[int]]:
    """每份文档上次同步时的 ``[size, mtime_ns]``。"""
    raw = _state().get("stamps")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, list[int]] = {}
    for key, value in raw.items():
        if (isinstance(key, str) and isinstance(value, list) and len(value) == 2
                and all(isinstance(item, int) for item in value)):
            result[key] = value
    return result


def save_sync_state(remote_id: int, dirty: set[str],
                    stamps: dict[str, list[int]] | None = None) -> None:
    state = _state()
    state["remote_document_id"] = remote_id
    state["dirty"] = {key: True for key in dirty}
    if stamps is not None:
        state["stamps"] = stamps
    _save(state)


def e2e_migrated_key_fp() -> str:
    value = _state().get("e2e_migrated_key_fp")
    return value if isinstance(value, str) else ""


def set_e2e_migrated_key_fp(fp: str) -> None:
    state = _state()
    state["e2e_migrated_key_fp"] = fp
    _save(state)


def _str_set_field(name: str) -> set[str]:
    raw = _state().get(name)
    return {x for x in raw if isinstance(x, str)} if isinstance(raw, list) else set()


def _set_str_set_field(name: str, values: set[str]) -> None:
    state = _state()
    if values:
        state[name] = sorted(values)
    else:
        state.pop(name, None)
    _save(state)


def pending_decrypt() -> set[str]:
    return _str_set_field("pending_decrypt")


def set_pending_decrypt(values: set[str]) -> None:
    _set_str_set_field("pending_decrypt", values)


def pending_encrypt() -> set[str]:
    return _str_set_field("pending_encrypt")


def set_pending_encrypt(values: set[str]) -> None:
    _set_str_set_field("pending_encrypt", values)
