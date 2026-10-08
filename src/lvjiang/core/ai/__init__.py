"""游戏无关的 AI 连接配置与文本请求能力。"""

from .service import AIError, AIModelList, AIReply, AIService, AISettings
from .store import AIStore

__all__ = ["AIError", "AIModelList", "AIReply", "AIService", "AISettings", "AIStore"]
