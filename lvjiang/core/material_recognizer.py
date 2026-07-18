"""材料分类器 - 识别材料槽中的材料类型、等级和数量

识别策略：
- 类型识别：ORB 特征匹配对比参考图标库（抗局部遮挡）
- 等级识别：OCR 裁剪左上角区域
- 数量识别：OCR 裁剪右下角区域
- 空槽检测：图像方差 + 饱和度判断

参考库数据源：MaterialDatabase（material.yaml），不再依赖文件名解析。
"""

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from loguru import logger
from PIL import Image

from .ocr import OCREngine
from .material_db import MaterialDatabase


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
    # ORB 特征匹配最低置信度
    MATCH_THRESHOLD = 0.05

    def __init__(
        self,
        ocr_engine: OCREngine,
        material_db: MaterialDatabase | None = None,
    ):
        self._ocr = ocr_engine
        self._db = material_db or MaterialDatabase()

        # 延迟加载
        self._templates: dict[str, list[tuple[np.ndarray, int | None]]] = {}
        # {type: [(img_bgr, level_or_None), ...]}
        self._template_size: tuple[int, int] | None = None
        self._loaded = False

        # 预计算缓存（加载时填充，识别时只读）
        self._cached_orb: list[tuple[str, np.ndarray | None, int, np.ndarray, str]] = []
        # [(mat_type, descriptors, kp_count, lab_avg, group), ...]
        self._orb: cv2.ORB | None = None
        self._bf: cv2.BFMatcher | None = None  # 缓存 BFMatcher，避免每次识别都新建

    @property
    def material_db(self) -> MaterialDatabase:
        return self._db

    # ─── 参考库加载 ──────────────────────────────────────────

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._load_templates()
        self._loaded = True

    def _load_templates(self):
        """从 MaterialDatabase 加载所有参考图片，按类型分组"""
        entries = self._db.list_materials()
        if not entries:
            logger.warning("材料数据库为空，无参考图可加载")
            return

        materials_dir = self._db.dir
        loaded_count = 0

        for entry in entries:
            path = materials_dir / entry.file
            if not path.exists():
                logger.warning(f"参考图片不存在，跳过: {entry.file}")
                continue

            # PIL 读中文路径 → numpy → cv2 BGR
            try:
                img_rgb = np.array(Image.open(path))
                img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
            except Exception as e:
                logger.warning(f"加载参考图片失败 {entry.file}: {e}")
                continue

            mat_type = entry.type
            level = entry.level

            if mat_type not in self._templates:
                self._templates[mat_type] = []
            self._templates[mat_type].append((img_bgr, level))
            loaded_count += 1

        # 记录参考图尺寸（取第一张）
        for type_list in self._templates.values():
            if type_list:
                h, w = type_list[0][0].shape[:2]
                self._template_size = (w, h)
                break

        logger.info(
            f"材料参考库加载完成: {len(self._templates)} 种类型, "
            f"{loaded_count} 张参考图, 尺寸 {self._template_size}"
        )

        # 预计算所有参考图的 ORB 描述子 + Lab 均值
        self._precompute_features()

    def _precompute_features(self):
        """预计算所有参考图的 ORB 特征和 Lab 颜色均值

        识别时只需对 slot 图算一次 ORB，然后与缓存做 knnMatch，
        避免每次识别都重复计算参考图特征。
        """
        if self._template_size is None:
            return

        self._orb = cv2.ORB.create(nfeatures=300)
        self._cached_orb.clear()

        # 遍历数据库条目，直接读取图片并计算特征
        for entry in self._db.list_materials():
            path = self._db.dir / entry.file
            if not path.exists():
                continue
            try:
                img_rgb = np.array(Image.open(path))
                img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
            except Exception:
                continue

            resized = cv2.resize(img_bgr, self._template_size, interpolation=cv2.INTER_AREA)
            kp, des = self._orb.detectAndCompute(resized, None)
            kp_count = len(kp) if kp else 0
            lab = cv2.cvtColor(resized, cv2.COLOR_BGR2Lab).astype(np.float32)
            lab_avg = lab.mean(axis=(0, 1))
            self._cached_orb.append((entry.type, des, kp_count, lab_avg, entry.group))

        logger.debug(f"预计算完成: {len(self._cached_orb)} 条特征缓存")

    def reload(self):
        """强制重新加载参考库"""
        self._templates.clear()
        self._template_size = None
        self._cached_orb.clear()
        self._orb = None
        self._loaded = False
        self._ensure_loaded()

    # ─── 核心识别 ──────────────────────────────────────────

    def recognize(
        self,
        slot_img: np.ndarray,
        group: str | None = None,
    ) -> MaterialInfo:
        """识别单个材料槽

        Args:
            slot_img: 材料槽裁剪图（BGR numpy 数组）
            group: 限定匹配范围到指定分组，None 表示匹配所有分组

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

        # 2. 类型识别（模板匹配，支持分组过滤）
        mat_type, confidence = self._match_type(slot_img, group=group)

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

    # ─── 类型识别（ORB 特征匹配）───────────────────────────────

    def _match_type(
        self,
        slot_img: np.ndarray,
        group: str | None = None,
    ) -> tuple[str, float]:
        """ORB 特征匹配识别材料类型

        对局部遮挡（如减号按钮）有容忍度，
        只要图标未遮挡区域有足够特征点匹配即可识别。

        slot 图只计算一次 ORB，与预计算缓存做匹配。

        Args:
            slot_img: 材料槽图像
            group: 限定匹配范围到指定分组，None 表示匹配所有

        Returns:
            (type_name, confidence)
        """
        if self._template_size is None or not self._cached_orb:
            return "", 0.0

        # 将槽图缩放到参考图尺寸
        resized = cv2.resize(slot_img, self._template_size, interpolation=cv2.INTER_AREA)

        # slot 图 ORB（只算一次）
        kp1, des1 = self._orb.detectAndCompute(resized, None)
        # slot 图 Lab 均值
        lab1 = cv2.cvtColor(resized, cv2.COLOR_BGR2Lab).astype(np.float32).mean(axis=(0, 1))

        if des1 is None or len(kp1) < 5:
            return "", 0.0

        if self._bf is None:
            self._bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        bf = self._bf

        best_type = ""
        best_score = -1.0

        # 根据分组过滤候选模板
        candidates = self._cached_orb
        if group:
            candidates = [c for c in self._cached_orb if c[4] == group]
            if not candidates:
                logger.warning(f"分组 '{group}' 中无参考图")
                return "", 0.0

        for mat_type, des2, kp2_count, lab2, _grp in candidates:
            if des2 is None or kp2_count < 5:
                continue
            # ORB 匹配（复用缓存描述子）
            matches = bf.knnMatch(des1, des2, k=2)
            good = sum(
                1 for pair in matches
                if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance
            )
            orb_score = min(good / kp2_count, 1.0) if good else 0.0

            # 颜色相似度（复用缓存 Lab 均值）
            dist = np.sqrt(np.sum((lab1 - lab2) ** 2))
            color_sim = max(1.0 - dist / 100.0, 0.0)

            score = 0.5 * orb_score + 0.5 * color_sim
            if score > best_score:
                best_score = score
                best_type = mat_type

        if best_score < self.MATCH_THRESHOLD:
            logger.warning(f"匹配置信度过低: {best_score:.3f}，无法识别类型")
            return "", best_score

        logger.debug(f"类型匹配: {best_type} (group={group}, confidence={best_score:.3f})")
        return best_type, best_score

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
