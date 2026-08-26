"""读取 ``ops/stats-client/vocab/telemetry_vocab.json``——燕云十六声部位/
词条的规范枚举快照，由 ``scripts/export_yysls_vocab.py``（主项目 venv）
生成。放在这里而不是 ``config/system/`` 下：它是 stats-client 私有的构建
产物，不经主项目 ``ConfigResolver`` 的 system/local 合并，混进配置分层
目录会让人误以为它参与合并语义。

这里只读 JSON，不 import ``lvjiang``：直接 import 会经
``lvjiang.apps.yysls.__init__`` 强制拉 PyQt6（``i18n`` 模块要用），与
本工具"零 GUI 依赖的独立轻量 venv"这条设计前提冲突。``game_config.yaml``
改了以后要重新跑一遍导出脚本，见 ``ops/stats-client/README.md``——主项目
测试套件里的 ``tests/yysls/test_telemetry_vocab_export.py`` 会在忘记
重新导出时失败提醒，不用只靠人记着。
"""
from __future__ import annotations

import json
from pathlib import Path

# parents[2]：vocab.py -> stats_client -> src -> stats-client（ops/stats-client）
_VOCAB_PATH = Path(__file__).resolve().parents[2] / "vocab" / "telemetry_vocab.json"

_EMPTY_VOCAB = {
    "parts": [], "part_labels": {}, "normal_affixes": [],
    "normal_affixes_by_part": {},
}


def load_vocab() -> dict:
    """不缓存——快照文件很小（几十 KB），每次请求重读一次的开销可以忽略，
    换来的是"重新导出后不用重启网页进程就能生效"。"""
    if not _VOCAB_PATH.exists():
        return dict(_EMPTY_VOCAB)
    try:
        return json.loads(_VOCAB_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(_EMPTY_VOCAB)


def affixes_for_part(vocab: dict, part: str | None) -> list[str]:
    """给定部位返回该部位能出现的普通词条；不给部位就返回全部普通词条。"""
    if part:
        return vocab.get("normal_affixes_by_part", {}).get(part, [])
    return vocab.get("normal_affixes", [])
