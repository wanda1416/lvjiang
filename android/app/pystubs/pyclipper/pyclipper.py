"""pyclipper 占位实现（见同目录 setup.py 的说明）。

上游 DBPostProcess.unclip 用到的是 PyclipperOffset / JT_ROUND / ET_CLOSEDPOLYGON。
该方法在设备端已被替换为 cv2 等价实现，所以这些名字只需存在。
一旦真被调用，说明替换未生效 —— 此时应立即失败并说明原因，而不是静默出错。
"""

JT_ROUND = 1
ET_CLOSEDPOLYGON = 2

_MSG = (
    "pyclipper 在 Android 端不可用（Chaquopy 无此原生包）。"
    "DBPostProcess.unclip 应已被 rapidocr_adapter 替换为 cv2 等价实现，"
    "出现此错误说明替换未生效。"
)


class PyclipperOffset:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(_MSG)
