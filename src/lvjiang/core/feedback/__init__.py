"""反馈与建议：分类、上限、脱敏、日志片段与提交通道。

三个设计约束（改动前先读）：

1. **反馈 ≠ Bug**。功能建议、文档问题、性能、账号同步、其它都是同等的一类，
   全部走同一条提交路径。``kinds`` 里的 key 是稳定协议值，展示文案由 UI 用
   ``tr()`` 包原文；类型专属字段（复现步骤等）永远可选，服务端也不因缺它拒绝。
2. **门槛是邮箱，不是登录**。匿名可提交，反馈的回复走邮箱；登录只用来给作者
   一条追问的近路，缺了就按匿名提交，不因此拦住用户。
3. **本包不依赖 Qt**。图片压缩需要 QImage，所以放在 UI 层
   （``lvjiang.ui.notices.feedback_attachments``），这里只做纯逻辑，便于测试。
"""

from .kinds import DEFAULT_KIND, FEEDBACK_KINDS, KIND_BY_KEY, FeedbackKind
from .limits import (
    LOG_TAIL_LINES,
    LOG_TAIL_MAX_BYTES,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS,
    MAX_SCREENSHOT_BYTES,
    MAX_SCREENSHOTS,
    SCREENSHOT_MAX_EDGE,
)
from .payload import collect_environment, is_valid_email, normalize_email
from .redact import redact_text

__all__ = [
    "DEFAULT_KIND",
    "FEEDBACK_KINDS",
    "KIND_BY_KEY",
    "FeedbackKind",
    "LOG_TAIL_LINES",
    "LOG_TAIL_MAX_BYTES",
    "MAX_ATTACHMENTS",
    "MAX_SCREENSHOTS",
    "MAX_SCREENSHOT_BYTES",
    "MAX_ATTACHMENT_BYTES",
    "SCREENSHOT_MAX_EDGE",
    "collect_environment",
    "is_valid_email",
    "normalize_email",
    "redact_text",
]
