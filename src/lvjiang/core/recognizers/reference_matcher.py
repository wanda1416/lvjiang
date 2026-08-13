"""通用参考图匹配器 - 基于 ORB 特征匹配的图像识别

从参考图库加载图像，预计算 ORB 特征描述子，
对查询图像做特征匹配返回最相似的参考条目。

这是通用引擎能力，不包含任何游戏专属逻辑。
"""

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from loguru import logger
from PIL import Image

from lvjiang.core.reference_db import (
    DEFAULT_MATCH_THRESHOLD,
    ReferenceDatabase,
    ReferenceEntry,
)


@dataclass
class MatchResult:
    """匹配结果"""
    entry: ReferenceEntry | None  # 匹配的参考条目
    label: str                    # 主标识（空字符串表示未匹配）
    confidence: float             # 置信度 0~1
    meta: dict                    # 匹配的元数据副本


class ReferenceMatcher:
    """通用参考图匹配器

    使用 ORB 特征匹配 + Lab 颜色相似度进行图像识别。
    对局部遮挡有容忍度，只要未遮挡区域有足够特征点匹配即可识别。

    用法：
        db = ReferenceDatabase()
        matcher = ReferenceMatcher(db)
        result = matcher.match(query_image)
        # result.label -> "定音石"
        # result.confidence -> 0.85
    """

    # ORB 特征匹配最低置信度（兼容别名，实际默认值读图库配置）
    DEFAULT_THRESHOLD = DEFAULT_MATCH_THRESHOLD
    # ORB 特征点数量
    NFEATURES = 300

    def __init__(
        self,
        db: ReferenceDatabase,
        threshold: float | None = None,
        target_size: tuple[int, int] | None = None,
    ):
        """
        Args:
            db: 参考图库数据库
            threshold: 匹配置信度阈值；None 则动态取图库配置
                （references.yaml 的 match_threshold，local 覆盖 system）
            target_size: 统一缩放尺寸 (w, h)，None 则自动取第一张参考图尺寸
        """
        self._db = db
        self._threshold = threshold
        self._target_size = target_size

        # 预计算缓存
        self._features: list[tuple[ReferenceEntry, np.ndarray | None, int, np.ndarray]] = []
        # [(entry, descriptors, kp_count, lab_avg), ...]
        self._orb: cv2.ORB | None = None
        self._bf: cv2.BFMatcher | None = None
        self._loaded = False

    @property
    def database(self) -> ReferenceDatabase:
        return self._db

    @property
    def threshold(self) -> float:
        """生效阈值：显式传入 > 图库配置（含默认值）"""
        if self._threshold is not None:
            return self._threshold
        return self._db.get_match_threshold()

    # ─── 加载与预计算 ─────────────────────────────────────

    def _ensure_loaded(self):
        if not self._loaded:
            self._load_and_precompute()
            self._loaded = True

    def _load_and_precompute(self):
        """加载参考图并预计算 ORB 特征 + Lab 颜色均值"""
        entries = self._db.list_entries()
        if not entries:
            logger.warning("参考图库为空，无参考图可加载")
            return

        self._orb = cv2.ORB.create(nfeatures=self.NFEATURES)
        self._features.clear()

        # 确定目标尺寸
        if self._target_size is None:
            for entry in entries:
                path = self._db.image_path(entry.file)
                if path.exists():
                    try:
                        img = self._load_image(path)
                        h, w = img.shape[:2]
                        self._target_size = (w, h)
                        break
                    except Exception:
                        continue

        if self._target_size is None:
            logger.warning("无法确定参考图尺寸")
            return

        loaded_count = 0
        for entry in entries:
            path = self._db.image_path(entry.file)
            if not path.exists():
                logger.warning(f"参考图不存在，跳过: {entry.file}")
                continue

            try:
                img = self._load_image(path)
                resized = cv2.resize(img, self._target_size, interpolation=cv2.INTER_AREA)

                kp, des = self._orb.detectAndCompute(resized, None)
                kp_count = len(kp) if kp else 0

                lab = cv2.cvtColor(resized, cv2.COLOR_BGR2Lab).astype(np.float32)
                lab_avg = lab.mean(axis=(0, 1))

                self._features.append((entry, des, kp_count, lab_avg))
                loaded_count += 1
            except Exception as e:
                logger.warning(f"加载参考图失败 {entry.file}: {e}")
                continue

        logger.info(
            f"参考图特征预计算完成: {loaded_count} 张, "
            f"尺寸 {self._target_size}"
        )

    def _load_image(self, path: Path) -> np.ndarray:
        """加载图像文件（支持中文路径）"""
        img_rgb = np.array(Image.open(path))
        return cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)

    def reload(self):
        """强制重新加载参考图"""
        self._features.clear()
        self._orb = None
        self._bf = None
        self._target_size = None
        self._loaded = False
        self._ensure_loaded()

    # ─── 核心匹配 ─────────────────────────────────────────

    def match(
        self,
        query: np.ndarray,
        group: str | None = None,
    ) -> MatchResult:
        """匹配查询图像到参考图库

        Args:
            query: 查询图像（BGR numpy 数组）
            group: 限定匹配范围到指定分组，None 表示匹配所有

        Returns:
            MatchResult
        """
        self._ensure_loaded()

        if not self._features or self._target_size is None:
            return MatchResult(entry=None, label="", confidence=0.0, meta={})

        if self._bf is None:
            self._bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

        # 缩放查询图到统一尺寸
        resized = cv2.resize(query, self._target_size, interpolation=cv2.INTER_AREA)

        # 计算查询图特征
        kp, des = self._orb.detectAndCompute(resized, None)
        if des is None or len(kp) < 5:
            return MatchResult(entry=None, label="", confidence=0.0, meta={})

        # 计算查询图 Lab 均值
        lab1 = cv2.cvtColor(resized, cv2.COLOR_BGR2Lab).astype(np.float32).mean(axis=(0, 1))

        # 过滤候选
        candidates = self._features
        if group:
            candidates = [f for f in self._features if f[0].group == group]
            if not candidates:
                logger.warning(f"分组 '{group}' 中无参考图")
                return MatchResult(entry=None, label="", confidence=0.0, meta={})

        best_entry = None
        best_score = -1.0

        for entry, des2, kp2_count, lab2 in candidates:
            if des2 is None or kp2_count < 5:
                continue

            # ORB 匹配
            matches = self._bf.knnMatch(des, des2, k=2)
            good = sum(
                1 for pair in matches
                if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance
            )
            orb_score = min(good / kp2_count, 1.0) if good else 0.0

            # 颜色相似度
            dist = np.sqrt(np.sum((lab1 - lab2) ** 2))
            color_sim = max(1.0 - dist / 100.0, 0.0)

            # 综合得分
            score = 0.5 * orb_score + 0.5 * color_sim
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_score < self.threshold or best_entry is None:
            logger.debug(f"匹配置信度过低: {best_score:.3f}")
            return MatchResult(entry=None, label="", confidence=best_score, meta={})

        logger.debug(f"匹配成功: {best_entry.label} (group={group}, confidence={best_score:.3f})")
        return MatchResult(
            entry=best_entry,
            label=best_entry.label,
            confidence=best_score,
            meta=dict(best_entry.meta),
        )

    def match_batch(
        self,
        queries: dict[str, np.ndarray],
        group: str | None = None,
    ) -> dict[str, MatchResult]:
        """批量匹配多个查询图像

        Args:
            queries: {key: query_image, ...}
            group: 限定匹配范围

        Returns:
            {key: MatchResult, ...}
        """
        return {key: self.match(img, group) for key, img in queries.items()}
