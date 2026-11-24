"""对外展示内容：公开链接与「关于」文本。

桌面与手机共用同一份内容，界面只负责排版；链接集中在这里，避免同一地址散落在
多个界面模块里。
"""
from ..i18n import tr
from .version_info import get_version

GITHUB_REPO = "wanda1416/lvjiang"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"

#: 官方站点（lvjiang/server 的账户服务 Worker 网页端）。赞助页与收款码都挂在这里：
#: 与网页同域直出静态资源，国内可达性优于 GitHub Pages。
SITE_URL = "https://lvjiang.wyxj.net"
SUPPORT_PAGE_URL = f"{SITE_URL}/#/support"
SUPPORT_QRCODE_BASE_URL = f"{SITE_URL}/support"


def about_content() -> dict[str, str]:
    repository = f"https://github.com/{GITHUB_REPO}"
    return {
        "name": tr("律匠"), "version": get_version(),
        "description": tr("通用视觉 RPA 引擎"),
        "workflow": tr("窗口定位截屏 → 区域标注 → OCR识别 → 工作流执行"),
        "technology": "基于 PyQt6 · RapidOCR · OpenCV",
        "source_notice": tr("本项目源码公开、免费使用"),
        "license": "PolyForm Noncommercial License 1.0.0",
        "license_notice": tr("仅限非商业使用"),
        "usage_notice": tr("本项目仅供学习与技术研究使用。使用自动化工具操作游戏可能违反相关游戏的用户协议，由此产生的后果由使用者自行承担。"),
        "warranty_notice": tr("软件按现状提供，不附带任何担保，作者不对使用本软件造成的任何损失负责。"),
        "copyright": "Copyright © 2024-2026 wanda1416",
        "repository_url": repository, "releases_url": RELEASES_URL,
        "license_url": f"{repository}/blob/master/LICENSE",
    }
