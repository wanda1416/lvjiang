"""站务通知子包 - 公告、版本更新、关于、反馈与匿名统计同意

这几个对话框共享同一条驱动链：启动时依次弹公告 → 更新 → 匿名统计同意
（见 MainWindow.check_update_on_startup），关于与反馈则挂在帮助菜单上，
关于页内部也会转到更新检查。
"""

from .about_dialog import GITHUB_REPO, AboutDialog
from .announcement_dialog import AnnouncementDialog
from .feedback_dialog import FeedbackDialog
from .telemetry_consent_dialog import maybe_prompt_and_record
from .update_dialog import UpdateDialog

__all__ = [
    "GITHUB_REPO",
    "AboutDialog",
    "AnnouncementDialog",
    "FeedbackDialog",
    "UpdateDialog",
    "maybe_prompt_and_record",
]
