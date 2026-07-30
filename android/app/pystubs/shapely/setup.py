"""shapely 占位包：仅用于满足 rapidocr_onnxruntime 的依赖声明。

真包依赖 GEOS 原生库。Chaquopy 仓库虽有 shapely 1.8.5 + chaquopy-geos，但它在
rapidocr_onnxruntime 里唯一的用途是 ch_ppocr_det/utils.py 的 DBPostProcess.unclip
用 Polygon 算 area/length，而该方法已由 src/lvjiang/core/ondevice/rapidocr_adapter.py
改为 float64 手算（鞋带公式求面积 + 边长求和）。装真包只会白占 2.3MB APK 体积，
故用占位包顶掉。

版本号取 99.0.0 以满足上游的 Shapely!=2.0.4,>=1.7.1 约束。
"""

from setuptools import setup

setup(name="shapely", version="99.0.0", packages=["shapely"])
