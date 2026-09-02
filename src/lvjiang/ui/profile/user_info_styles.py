"""Shared styles for sections on the User Info page."""

USER_INFO_GROUP_STYLE = """
    QGroupBox {
        font-weight: bold;
        border: 1px solid #cccccc;
        border-radius: 4px;
        margin-top: 8px;
        padding-top: 12px;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
    }
"""

__all__ = ["USER_INFO_GROUP_STYLE"]
