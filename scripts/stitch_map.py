"""由 UTF-8 探查清单生成地图底图、覆盖掩码和图片位置记录。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from lvjiang.core.json_files import formatted_json
from lvjiang.core.map_stitching import stitch_map_frames
from lvjiang.core.maps import MapError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads(args.manifest.read_text(encoding="utf-8"))
    images, masks = [], []
    for frame in plan["frames"]:
        path = args.manifest.parent / frame["image"]
        image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise MapError(f"图片无法解码: {path}")
        mask = np.full(image.shape[:2], 255, dtype=np.uint8)
        for x, y, width, height in frame.get("exclude", plan.get("exclude", [])):
            mask[y:y+height, x:x+width] = 0
        images.append(image)
        masks.append(mask)
    mosaic = stitch_map_frames(images, masks)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, image in (("base.png", mosaic.image),
                        ("coverage.png", mosaic.coverage.astype(np.uint8) * 255)):
        ok, data = cv2.imencode(".png", image)
        if not ok:
            raise MapError(f"图片编码失败: {name}")
        (args.output / name).write_bytes(bytes(data))
    (args.output / "registration.json").write_text(formatted_json({
        "coverage_note": "覆盖率仅针对本次截图的包围矩形，不代表游戏整张地图探查完成度",
        "size": [mosaic.image.shape[1], mosaic.image.shape[0]],
        "observed_fraction": round(float(mosaic.coverage.mean()), 6),
        "frames": [{"image": frame["image"], "offset": list(offset)}
                   for frame, offset in zip(plan["frames"], mosaic.offsets, strict=True)],
    }), encoding="utf-8")
    print(f"已拼接 {len(images)} 张截图：{args.output / 'base.png'}")


if __name__ == "__main__":
    main()
