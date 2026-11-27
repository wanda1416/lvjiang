"""安卓应用内更新的本地校验入口（供 PyBridge 调用）。

下载完成后由 Kotlin 侧把文件路径与版本号传进来，复用
:mod:`lvjiang.core.android.apk_release` 的哈希能力：本地算 SHA-256，能联网就
和发布页的 ``SHA256SUMS.txt`` 比对。

比对结论分三态，与 ``ApkFile.verified`` 一致：
``true`` 通过、``false`` 明确不匹配、``null`` 没取到期望值（离线/清单缺失）。
调用方据此决定「装 / 拒装 / 提示后继续装」——取不到清单不该拦着用户更新。
"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger


def verify_apk(path: str, version: str) -> str:
    """校验本地 APK，返回 ``{ok, verified, sha256, size, message}`` 的 JSON 文本。"""
    from ..android.apk_release import inspect_local_apk

    target = Path(str(path or "").strip())
    if not target.is_file():
        return json.dumps({"ok": False, "message": "更新包不存在，请重新下载"},
                          ensure_ascii=False)
    try:
        info = inspect_local_apk(target, str(version or "").strip())
    except (OSError, ValueError) as exc:
        logger.warning(f"[update] 读取更新包失败: {target} ({exc})")
        return json.dumps({"ok": False, "message": f"无法读取更新包：{exc}"},
                          ensure_ascii=False)

    if info.verified is True:
        message = "更新包校验通过"
    elif info.verified is False:
        message = "更新包校验和不匹配，已放弃安装，请重新下载"
        logger.error(f"[update] 更新包校验和不匹配: {target} sha256={info.sha256}")
    else:
        message = "未能获取官方校验和，已按本地哈希继续安装"
        logger.warning(f"[update] 未能比对校验和，本地 sha256={info.sha256}")
    return json.dumps({
        "ok": True,
        "verified": info.verified,
        "sha256": info.sha256,
        "size": info.size,
        "message": message,
    }, ensure_ascii=False)


__all__ = ["verify_apk"]
