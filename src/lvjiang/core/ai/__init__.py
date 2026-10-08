"""游戏无关的 AI 连接配置与文本请求能力。"""

from .service import AIError, AIReply, AIService, AISettings
from .store import AIStore

__all__ = ["AIError", "AIReply", "AIService", "AISettings", "AIStore"]
