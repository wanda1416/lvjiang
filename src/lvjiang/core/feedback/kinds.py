"""反馈分类。

``key`` 是**协议值**：落库、跨端传输、控制台筛选都用它，永远不要翻译或改名。
``label`` 只是中文原文，UI 侧 ``tr()`` 包一层后展示（AGENTS 2.4：稳定 key 与
展示文案分离）。

``detail_fields`` 只决定 UI 上要不要展开「复现步骤 / 预期 / 实际 / 出现频率」
那一块，**不是受理条件**：全部可选，非 bug 类根本不显示。任何把某一类排除在
外、或要求其它类补齐 bug 字段的改动，都是在把反馈做成「只有 Bug 才收」。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeedbackKind:
    """一类反馈的元数据。"""

    key: str
    label: str
    detail_fields: bool = False
    evidence_hint: str = ""


FEEDBACK_KINDS: tuple[FeedbackKind, ...] = (
    FeedbackKind(
        "suggestion", "功能建议", False,
        "有参考截图或界面示意可以直接贴进来",
    ),
    FeedbackKind(
        "bug", "功能异常", True,
        "建议附出错前后的截图与日志；能稳定复现的请写明步骤",
    ),
    FeedbackKind(
        "recognition", "识别或点击不准", True,
        "这类问题几乎离不开截图：请贴一张出错时的画面",
    ),
    FeedbackKind(
        "workflow", "工作流或脚本问题", True,
        "写清工作流名称；日志默认会一起附上",
    ),
    FeedbackKind(
        "crash", "崩溃或闪退", True,
        "请说明崩溃前在做什么；崩溃日志可在 logs/crashes 里找到",
    ),
    FeedbackKind(
        "performance", "卡顿或性能", False,
        "说明卡在哪个页面或哪个任务，以及大致持续多久",
    ),
    FeedbackKind(
        "account_sync", "账号与同步", False,
        "说明是登录、同步还是数据不一致；不必提供密码",
    ),
    FeedbackKind(
        "doc", "文档问题", False,
        "写明是哪个文档或哪一页，有截图更好",
    ),
    FeedbackKind(
        "other", "其它", False,
        "",
    ),
)

KIND_BY_KEY = {kind.key: kind for kind in FEEDBACK_KINDS}

#: 默认类型。反馈通道的第一印象不该是「报 Bug」，所以默认落在建议上。
DEFAULT_KIND = "suggestion"
