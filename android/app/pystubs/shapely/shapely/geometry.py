"""shapely.geometry 占位实现（见上一级 setup.py 的说明）。

上游 DBPostProcess.unclip 只用到 Polygon(box).area 与 .length。该方法在设备端
已被替换为 cv2 等价实现，所以这个名字只需存在。
一旦真被实例化，说明替换未生效 —— 立即失败并说明原因。
"""

_MSG = (
    "shapely 在 Android 端已被占位包顶替（真包需要 GEOS，且仅 unclip 一处用到）。"
    "DBPostProcess.unclip 应已被 rapidocr_adapter 替换为 cv2 等价实现，"
    "出现此错误说明替换未生效。"
)


class Polygon:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(_MSG)
