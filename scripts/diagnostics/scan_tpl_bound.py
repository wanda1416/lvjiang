"""扫描所有布局模板绑定，报告每处的搜索区 / 模板尺寸 / 缩放比例

运行时搜索区 = Region 按 round 取整；容不下「缩放后模板 + 每边 SEARCH_SLACK_PX（2 px）」时按需外扩；模板按
canvas_w / record_w 精确缩放（同分辨率恒为 1.0）。本脚本按录制画布尺寸
复算这两项，任何一处模板放不进搜索区都属于绑定数据损坏。

用法：
  python scripts/diagnostics/scan_tpl_bound.py
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lvjiang.core.recognizers.template_locator import (  # noqa: E402
    Template,
    _to_gray,
    search_box,
)

bad = 0
for layout_dir, env_label in [
    ('config/system/layouts/android', 'android'),
    ('config/system/layouts/desktop', 'desktop'),
]:
    print(f'=== {env_label} ===')
    for lf in sorted(Path(layout_dir).glob('*.json')):
        d = json.loads(lf.read_text(encoding='utf-8'))
        canvas = SimpleNamespace(**(d.get('crop_canvas') or {
            'x_ratio': 0.0, 'y_ratio': 0.0, 'w_ratio': 1.0, 'h_ratio': 1.0}))
        for r in d.get('regions', []):
            tb = r.get('template')
            if not isinstance(tb, dict):
                continue
            rec_w, rec_h = tb.get('record_w', 0), tb.get('record_h', 0)
            tp = Path('config/system/templates') / (tb['name'] + '.png')
            img = cv2.imread(str(tp), cv2.IMREAD_UNCHANGED) if tp.exists() else None
            if img is None or rec_w <= 0 or rec_h <= 0:
                print(f'  SKIP {lf.stem}/{r["key"]}: 模板缺失或无录制尺寸')
                continue
            tpl = Template(tb['name'], _to_gray(img), rec_w, rec_h)
            # 录制画布 = 整帧时，帧尺寸就是 record；带 crop_canvas 时反推整帧
            frame_w = int(round(rec_w / canvas.w_ratio))
            frame_h = int(round(rec_h / canvas.h_ratio))
            region = SimpleNamespace(**{k: r[k] for k in (
                'x_ratio', 'y_ratio', 'w_ratio', 'h_ratio')})
            x1, y1, x2, y2, scale = search_box((frame_h, frame_w), tpl, canvas, region)
            sw, sh = x2 - x1 + 1, y2 - y1 + 1
            tw, th = round(tpl.w * scale), round(tpl.h * scale)
            fit = tw <= sw and th <= sh
            bad += not fit
            print(f'  {"ok " if fit else "BAD"} {lf.stem}/{r["key"]}: '
                  f'tpl={tw}x{th} search={sw}x{sh} scale={scale:.2f}'
                  f'{"" if r.get("disabled") is not True else " (disabled)"}')
print(f'\n{bad} binding(s) cannot fit their search box')
