"""材料分类器 - 识别材料槽中的材料类型、等级和数量

识别策略：
- 类型识别：模板匹配（cv2.matchTemplate）对比参考图标库
- 等级识别：OCR 裁剪左上角区域
- 数量识别：OCR 裁剪右下角区域
- 空槽检测：图像方差 + 饱和度判断

参考图片命名格式：{类型}_{等级}级.png 或 {类型}.png
"""

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from loguru import logger
from PIL import Image

from .ocr import OCREngine


@dataclass
class MaterialInfo:
    """单个材料槽识别结果"""
    type: str          # 材料类型（如 "定音石"），空槽为 ""
    level: int | None  # 等级（如 100），无等级标识为 None
    count: int | None  # 投入数量（x/y 中的 x），无法识别为 None
    owned: int | None  # 持有数量（x/y 中的 y），无法识别为 None
    confidence: float  # 类型匹配置信度 0~1


class MaterialRecognizer:
    """材料槽内容识别器

    用法：
        recognizer = MaterialRecognizer(ocr_engine)
        result = recognizer.recognize(slot_img)
        # result.type -> "定音石"
        # result.level -> 100
        # result.count -> 5   (投入数量)
        # result.owned -> 20  (持有数量)
    """

    # ── 子区域比例配置 ─────────────────────────────────────
    # 等级文字区域（左上角）
    LEVEL_REGION = (0.0, 0.0, 0.45, 0.30)
    # 数量文字区域（右下角）
    COUNT_REGION = (0.45, 0.70, 0.55, 0.30)
    # 空槽判定：像素方差阈值
    EMPTY_VARIANCE_THRESHOLD = 50.0
    # 模板匹配最低置信度
    MATCH_THRESHOLD = 0.5

    def __init__(
        self,
        ocr_engine: OCREngine,
        templates_dir: Path | str | None = None,
    ):
        self._ocr = ocr_engine
        self._templates_dir = Path(templates_dir) if templates_dir else (
            Path(__file__).resolve().parent.parent.parent / "data" / "materials"
        )

        # 延迟加载
        self._templates: dict[str, list[tuple[np.ndarray, int | None]]] = {}
        # {type: [(img_bgr, level_or_None), ...]}
        self._template_size: tuple[int, int] | None = None
        self._loaded = False

    # ─── 参考库加载 ──────────────────────────────────────────

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._load_templates()
        self._loaded = True

    def _load_templates(self):
        """从 data/materials 加载所有参考图片，按类型分组"""
        if not self._templates_dir.exists():
            logger.error(f"参考图片目录不存在: {self._templates_dir}")
            return

        # 文件名解析：{类型}_{等级}级.png 或 {类型}.png
        pattern = re.compile(r"^(.+?)(?:_(\d+)级)?\.png$")

        for path in sorted(self._templates_dir.glob("*.png")):
            m = pattern.match(path.name)
            if not m:
                logger.warning(f"跳过无法解析的文件名: {path.name}")
                continue

            mat_type = m.group(1)
            level = int(m.group(2)) if m.group(2) else None

            # PIL 读中文路径 → numpy → cv2 BGR
            try:
                img_rgb = np.array(Image.open(path))
                img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
            except Exception as e:
                logger.warning(f"加载参考图片失败 {path.name}: {e}")
                continue

            if mat_type not in self._templates:
                self._templates[mat_type] = []
            self._templates[mat_type].append((img_bgr, level))

        # 记录参考图尺寸（取第一张）
        for type_list in self._templates.values():
            if type_list:
                h, w = type_list[0][0].shape[:2]
                self._template_size = (w, h)
                break

        total = sum(len(v) for v in self._templates.values())
        logger.info(
            f"材料参考库加载完成: {len(self._templates)} 种类型, "
            f"{total} 张参考图, 尺寸 {self._template_size}"
        )

    def reload(self):
        """强制重新加载参考库"""
        self._templates.clear()
        self._template_size = None
        self._loaded = False
        self._ensure_loaded()

    # ─── 核心识别 ──────────────────────────────────────────

    def recognize(self, slot_img: np.ndarray) -> MaterialInfo:
        """识别单个材料槽

        Args:
            slot_img: 材料槽裁剪图（BGR numpy 数组）

        Returns:
            MaterialInfo
        """
        self._ensure_loaded()

        if not self._templates:
            logger.warning("参考库为空，返回空结果")
            return MaterialInfo(type="", level=None, count=None, owned=None, confidence=0.0)

        # 1. 空槽检测
        if self._is_empty(slot_img):
            return MaterialInfo(type="", level=None, count=None, owned=None, confidence=1.0)

        # 2. 类型识别（模板匹配）
        mat_type, confidence = self._match_type(slot_img)

        # 3. 等级识别（OCR 左上角）
        level = self._ocr_level(slot_img)

        # 4. 数量识别（OCR 右下角）
        count, owned = self._ocr_count(slot_img)

        return MaterialInfo(
            type=mat_type,
            level=level,
            count=count,
            owned=owned,
            confidence=confidence,
        )

    def recognize_batch(
        self,
        slot_images: dict[str, np.ndarray],
    ) -> dict[str, MaterialInfo]:
        """批量识别多个材料槽

        Args:
            slot_images: {slot_key: slot_img, ...}

        Returns:
            {slot_key: MaterialInfo, ...}
        """
        return {
            key: self.recognize(img)
            for key, img in slot_images.items()
        }

    # ─── 空槽检测 ──────────────────────────────────────────

    def _is_empty(self, img: np.ndarray) -> bool:
        """判断材料槽是否为空"""
        if img is None or img.size == 0:
            return True

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        variance = cv2.Laplacian(gray, cv2.CV_64F).var()

        if variance < self.EMPTY_VARIANCE_THRESHOLD:
            logger.debug(f"空槽检测: 方差={variance:.1f} < {self.EMPTY_VARIANCE_THRESHOLD}")
            return True
        return False

    # ─── 类型识别（模板匹配）─────────────────────────────────

    def _match_type(self, slot_img: np.ndarray) -> tuple[str, float]:
        """模板匹配识别材料类型

        Returns:
            (type_name, confidence)
        """
        if self._template_size is None:
            return "", 0.0

        # 将槽图缩放到参考图尺寸
        resized = cv2.resize(slot_img, self._template_size, interpolation=cv2.INTER_AREA)

        best_type = ""
        best_score = -1.0

        for mat_type, variants in self._templates.items():
            # 对同类型的每个等级变体取最高分
            for ref_img, _level in variants:
                score = self._template_match(resized, ref_img)
                if score > best_score:
                    best_score = score
                    best_type = mat_type

        if best_score < self.MATCH_THRESHOLD:
            logger.warning(f"模板匹配置信度过低: {best_score:.3f}，无法识别类型")
            return "", best_score

        logger.debug(f"类型匹配: {best_type} (confidence={best_score:.3f})")
        return best_type, best_score

    @staticmethod
    def _template_match(img: np.ndarray, template: np.ndarray) -> float:
        """归一化模板匹配，返回最高匹配分数"""
        if img.shape[:2] != template.shape[:2]:
            # 尺寸不同，resize 到模板尺寸
            img = cv2.resize(img, (template.shape[1], template.shape[0]))

        result = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
        return float(result.max())

    # ─── 等级 OCR ──────────────────────────────────────────

    def _ocr_level(self, slot_img: np.ndarray) -> int | None:
        """OCR 识别左上角等级"""
        h, w = slot_img.shape[:2]
        x1, y1 = int(self.LEVEL_REGION[0] * w), int(self.LEVEL_REGION[1] * h)
        x2, y2 = int((self.LEVEL_REGION[0] + self.LEVEL_REGION[2]) * w), \
                 int((self.LEVEL_REGION[1] + self.LEVEL_REGION[3]) * h)

        crop = slot_img[y1:y2, x1:x2]
        if crop.size == 0:
            return None

        text = self._ocr_single(crop)
        if not text:
            return None

        # 提取数字（等级通常是纯数字，如 "100"）
        nums = re.findall(r"\d+", text)
        if nums:
            level = int(nums[0])
            logger.debug(f"等级识别: {text!r} -> {level}")
            return level

        logger.debug(f"等级识别: 无法从 {text!r} 中提取数字")
        return None

    # ─── 数量 OCR ──────────────────────────────────────────

    def _ocr_count(self, slot_img: np.ndarray) -> tuple[int | None, int | None]:
        """OCR 识别右下角数量

        右下角格式为 x/y：
        - x = 投入数量
        - y = 持有数量

        Returns:
            (count, owned)，无法识别的字段为 None
        """
        h, w = slot_img.shape[:2]
        x1, y1 = int(self.COUNT_REGION[0] * w), int(self.COUNT_REGION[1] * h)
        x2, y2 = int((self.COUNT_REGION[0] + self.COUNT_REGION[2]) * w), \
                 int((self.COUNT_REGION[1] + self.COUNT_REGION[3]) * h)

        crop = slot_img[y1:y2, x1:x2]
        if crop.size == 0:
            return None, None

        text = self._ocr_single(crop)
        if not text:
            return None, None

        # 解析 "x/y" 或 "x" 格式
        # OCR 可能识别为 "5/10"、"5/70"、"x5/10" 等
        nums = re.findall(r"\d+", text)
        if len(nums) >= 2:
            count, owned = int(nums[0]), int(nums[1])
            logger.debug(f"数量识别: {text!r} -> count={count}, owned={owned}")
            return count, owned
        elif len(nums) == 1:
            count = int(nums[0])
            logger.debug(f"数量识别: {text!r} -> count={count}, owned=None")
            return count, None

        logger.debug(f"数量识别: 无法从 {text!r} 中提取数字")
        return None, None

    # ─── OCR 辅助 ──────────────────────────────────────────

    def _ocr_single(self, crop: np.ndarray) -> str:
        """对小区域做 OCR，返回拼接文本"""
        results = self._ocr.recognize(crop)
        if not results:
            return ""
        return " ".join(r.text for r in results)

    # ─── 工具方法 ──────────────────────────────────────────

    def list_types(self) -> list[str]:
        """返回参考库中所有材料类型"""
        self._ensure_loaded()
        return sorted(self._templates.keys())

    def get_reference_count(self, mat_type: str, level: int | None = None) -> int | None:
        """获取某材料在参考库中的条目数（调试用）"""
        self._ensure_loaded()
        variants = self._templates.get(mat_type, [])
        if level is not None:
            return sum(1 for _, lv in variants if lv == level)
        return len(variants)
