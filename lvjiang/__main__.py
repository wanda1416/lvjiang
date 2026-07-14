"""律匠启动入口 - python -m lvjiang"""

import sys
from loguru import logger

from .ui.app import run_app


def main():
    # 配置 loguru
    logger.remove()  # 移除默认 handler
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}",
        level="DEBUG",
    )
    logger.add(
        "logs/lvjiang_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="7 days",
        encoding="utf-8",
        level="DEBUG",
    )

    logger.info("律匠启动中...")
    run_app()


if __name__ == "__main__":
    main()
