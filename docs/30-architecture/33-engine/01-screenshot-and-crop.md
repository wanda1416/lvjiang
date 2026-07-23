# scan / recognize 的截图-裁剪-识别数据流

`scan` 和 `recognize` 在 DSL 中看起来是"对场景做 OCR / 图像识别",但引擎底层实际执行的是**"截一张大图 → 按场景定义裁出多个小图 → 逐个识别"**。理解这条数据流,对写出性能合理的工作流至关重要。

## 一、共同的数据流骨架

两条指令在引擎里走的是同一套"截图 + 裁剪"管线,只是裁剪后的识别器不同:

```
DSL 指令
   │
   ▼
WorkflowEngine._exec_scan / _exec_recognize     (engine.py)
   │  解析 scene_name 与 field_keys
   ▼
BaseWorkflow.ocr_scene / recognize_materials    (workflows/base.py)
   │  ① 调用 ScreenCapture.capture() 截全屏大图（仅 1 次）
   │  ② 从 layout 取出该场景的 Region 列表
   │  ③ 按 field_keys 过滤（若指定）
   │  ④ 对每个 Region：归一化坐标 → 像素坐标 → 裁剪小图
   ▼
OCREngine.ocr_scene_regions / MaterialRecognizer.recognize
   │  对裁剪后的小图做识别
   ▼
返回 dict[field_key, 识别结果]
```

**关键事实:一次 DSL 指令 = 一次截屏 + N 次裁剪 + N 次识别。**

## 二、截屏:全屏大图只截一次

`capture()` 是整个管线的唯一入口,它通过持久化工作线程调用 `mss.grab()`:

```python
# capture.py
def capture(self, timeout=5.0) -> np.ndarray | None:
    # 推任务给持久工作线程
    self._task_queue.put((self._monitor, result_holder))
    # 工作线程内: sct.grab(target) → np.array → img[:,:,:3]
    # 返回 BGR numpy 数组
```

- 若设置了 `_monitor`(定位窗口后),截取该窗口的矩形区域
- 否则截取主显示器全屏
- 返回的是一张**完整的 BGR numpy 数组**,后续所有裁剪都基于这张图

`mss.grab()` 底层是 Win32 GDI 的 `BitBlt`,是相对昂贵的系统调用。所以**截屏次数是性能关键**。

## 三、裁剪:归一化坐标 → 像素切片

场景的每个 Region 在 layout YAML 里用**画布归一化比例**定义(`x_ratio`, `y_ratio`, `w_ratio`, `h_ratio`)。裁剪时按大图尺寸反算像素:

```python
# base.py: recognize_materials / ocr_scene 共用
h, w = img.shape[:2]
canvas_x = canvas.x_ratio * w
canvas_y = canvas.y_ratio * h
canvas_w = canvas.w_ratio * w
canvas_h = canvas.h_ratio * h

for region in regions:
    x1 = int(canvas_x + region.x_ratio * canvas_w)
    y1 = int(canvas_y + region.y_ratio * canvas_h)
    x2 = int(canvas_x + (region.x_ratio + region.w_ratio) * canvas_w)
    y2 = int(canvas_y + (region.y_ratio + region.h_ratio) * canvas_h)
    crop = img[y1:y2, x1:x2]    # numpy 切片,零拷贝
```

numpy 切片是**视图**,不复制像素数据,所以裁剪本身几乎零成本。真正贵的是截屏那一下。

## 四、识别:两条分支

裁剪出的小图进入不同的识别器:

### scan → OCR
```python
# ocr.py: ocr_scene_regions
for region in regions:
    if region.key not in text_keys:   # 跳过 is_text=False 的区域
        continue
    crop = image[y1:y2, x1:x2]
    ocr_results = self.recognize(crop)   # RapidOCR 一次推理
    results[region.key] = " | ".join(r.text for r in ocr_results)
```

### recognize → 材料识别
```python
# material_recognizer.py: recognize
def recognize(self, slot_img):
    if self._is_empty(slot_img):             # 1. 空槽检测(Laplacian 方差)
        return empty
    mat_type, conf = self._match_type(slot_img)  # 2. ORB 特征匹配类型
    level = self._ocr_level(slot_img)            # 3. OCR 左上角等级
    count, owned = self._ocr_count(slot_img)     # 4. OCR 右下角数量
    return MaterialInfo(...)
```

注意 `recognize` 单 slot 内部会调 2 次 OCR(等级 + 数量),但都是对**裁剪后的小图**做,不会再截屏。

## 五、_coord_meta:点击坐标的来源

`scan` 和 `recognize` 执行后,引擎会把参与识别的 Region 对象按 field_key 存入 `self._coord_meta[var_name]`:

```python
# engine.py: _exec_scan / _exec_recognize
self._coord_meta[var_name] = {r.key: r for r in regions}
```

后续 `click [scene].$key` 解析坐标时:
1. 先从 `_coord_meta` 查 `$key` 对应的 Region(由 scan/recognize 自动存入)
2. 找到则直接点击其屏幕坐标
3. 找不到则回退到场景配置中查找同名 Area

这就是为什么 `click [scene].$key` 必须**先 scan/recognize 过**才能工作。

## 六、DSL 写法对截屏次数的影响

**这是最容易踩坑的地方。** DSL 语法上支持"单字段识别",但引擎层面**每次 recognize 指令都独立截屏一次**。

### ❌ 反模式:循环里单次识别

```
eval $slots = ["material_1", "material_2", "material_3", "material_4",
               "material_5", "material_6", "material_7"]
for slot_name in $slots
    recognize [equip_tune_detail].$slot_name as $m   # ← 每次循环都截一次屏!
    if $m[$slot_name] equals $material_name
        ...
    end
end
```

实际代价:
- 截屏 **7 次**(每次完整的 `mss.grab()` GDI 调用)
- 裁剪 7 次(每次只切 1 个 slot,**其余 6 个 slot 的像素被丢弃**)
- 识别 7 次

### ✅ 正确写法:一次识别全部字段

```
recognize [equip_tune_detail].[material_1, material_2, material_3, material_4,
                              material_5, material_6, material_7] as $m
for slot_name in $slots
    if $m[$slot_name] equals $material_name
        eval $found = $slot_name
        collect $found as "slot_name"
        return
    end
end
```

实际代价:
- 截屏 **1 次**
- 裁剪 7 次(从同一张图切)
- 识别 7 次

**收益:截屏从 7 次降到 1 次,省 6 次 GDI 调用 + 6 次工作线程任务调度。**

### 判断标准

> **同一次"观察"能拿到的所有字段,就应该用一条 `scan` / `recognize` 一次性拿完。**
>
> 只有当**两次识别之间夹着会改变画面的操作**(click / drag / wait 等),才值得再截一次。

## 七、常见疑问

**Q: 既然 `recognize` 支持多字段,为什么 DSL 还允许单字段写法?**
A: 语法上允许是为了灵活性,比如只想识别一个字段时少写点字。但性能敏感的循环里应该用多字段形式。

**Q: `scan` 和 `recognize` 能共享同一张截图吗?**
A: 不能。两条 DSL 指令各自独立截屏。如果同一工作流里既需要 OCR 又需要材料识别,且画面没变,目前仍会截两次屏。这是已知限制,未来可以通过"缓存最近一次截图"优化,但需要引入过期策略。

**Q: 裁剪的小图会不会很大,占内存?**
A: numpy 切片是视图,不复制像素。真正占内存的是那张全屏大图,截屏后由 `capture()` 返回,被多个裁剪视图共享引用,直到指令结束才释放。

**Q: 截屏失败会怎样?**
A: `capture()` 返回 None,`ocr_scene` / `recognize_materials` 直接返回空 dict,工作流继续执行(不会抛异常)。日志里会看到"截图失败"。
