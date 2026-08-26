"""主窗口子包 - MainWindow 本体及其功能混入类

MainWindow 由一组 mixin 拼成，每个 mixin 管一块互不重叠的职责：

    window_ops    窗口/设备扫描、定位、DPI 与截图后端
    run_control   工作流加载与启停、暂停恢复、运行状态广播
    capture_ops   录屏/截屏面板
    tray_ops      最小化到系统托盘与托盘状态图标
    startup_ops   启动检查链（公告 → 更新 → 统计同意 → 上报）
    menu_ops      菜单栏、主题按钮与各对话框入口
    ui_state      session.json 的 ui_state / daily 两节点持久化

window.py 只保留构造、UI 骨架搭建、插件宿主 API 与关闭清理。
"""

from .window import MainWindow

__all__ = ["MainWindow"]
