"""
vision/screen_detector.py
判断当前游戏截图处于哪种界面

界面类型：
  CARD_REWARD   — 奖励选卡界面（出现"选择一张牌"/"Choose a Card"）
  SHOP          — 商店界面（大量价格数字）
  OTHER         — 其他（地图/战斗/事件等）
  UNKNOWN       — 无法判断（低置信度）

策略：
  1. 对全屏截图做 OCR（只取上半部分，速度更快）
  2. 检测关键词：选卡 → 高置信；商店 → 数字特征
  3. 多帧投票：连续 N 帧一致才输出结果（避免 OCR 噪声）
"""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np

from .ocr_engine import WindowsOcrEngine, OcrResult, get_ocr_engine

log = logging.getLogger(__name__)


class ScreenType(str, Enum):
    CARD_REWARD = "card_reward"   # 选卡界面
    SHOP        = "shop"          # 商店界面
    OTHER       = "other"         # 其他界面
    UNKNOWN     = "unknown"       # 无法判断


# -----------------------------------------------------------------------
# 关键词表
# -----------------------------------------------------------------------

# 选卡界面触发词（中英文）
_CARD_REWARD_KEYWORDS_ZH = ["选择一张牌", "选一张牌", "选择卡牌", "选择一张"]
_CARD_REWARD_KEYWORDS_EN = [
    "choose a card", "choose one card", "pick a card",
    "card reward", "select a card",
]

# 商店界面触发词
_SHOP_KEYWORDS_ZH = ["购买", "商店", "售价", "金币"]
_SHOP_KEYWORDS_EN = ["shop", "purchase", "buy", "gold", "price"]

# 数字价格模式：出现多个孤立2-3位数字（商店价格）
_PRICE_PATTERN = re.compile(r"\b\d{2,3}\b")


@dataclass
class DetectionResult:
    """单次检测结果"""
    screen_type: ScreenType
    confidence: float          # 0.0 ~ 1.0
    matched_keywords: list[str]
    ocr_text: str              # 用于调试
    ocr_result: Optional[OcrResult] = None  # 完整 OCR 结果（含行坐标，供卡名定位）


class ScreenDetector:
    """
    界面检测器。

    用法：
        detector = ScreenDetector()
        result = detector.detect(screenshot_bgr)
        if result.screen_type == ScreenType.CARD_REWARD:
            ...

    多帧投票：
        detector = ScreenDetector(vote_frames=3)
        # 连续调用 detect()，只有 N 帧一致才返回稳定结果
        stable = detector.get_stable_type()
    """

    def __init__(
        self,
        ocr_engine: Optional[WindowsOcrEngine] = None,
        vote_frames: int = 3,
        scan_region_ratio: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
    ) -> None:
        """
        Args:
            ocr_engine: OCR 引擎实例（None 时使用全局单例）
            vote_frames: 多帧投票窗口大小（>=1）
            scan_region_ratio: 扫描区域比例 (left, top, right, bottom)
                               默认只扫上 60%，避开底部 UI 噪声
        """
        self._ocr = ocr_engine or get_ocr_engine()
        self._vote_frames = max(1, vote_frames)
        self._scan_region = scan_region_ratio
        self._vote_buffer: deque[ScreenType] = deque(maxlen=self._vote_frames)
        self._last_result: Optional[DetectionResult] = None

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def detect(self, screenshot: np.ndarray) -> DetectionResult:
        """
        对截图做一次界面检测。
        同时更新投票缓冲区。

        Args:
            screenshot: BGR numpy array（来自 WindowCapture.capture()）

        Returns:
            DetectionResult（单帧结果，未经投票平滑）
        """
        if screenshot is None or screenshot.size == 0:
            result = DetectionResult(
                screen_type=ScreenType.UNKNOWN,
                confidence=0.0,
                matched_keywords=[],
                ocr_text="",
            )
            self._vote_buffer.append(ScreenType.UNKNOWN)
            self._last_result = result
            return result

        # 裁剪扫描区域
        region = self._crop_region(screenshot)

        # OCR 识别
        ocr_result = self._ocr.recognize(region)

        # 分析 OCR 结果
        result = self._analyze(ocr_result)
        self._vote_buffer.append(result.screen_type)
        self._last_result = result

        log.debug(
            f"检测: {result.screen_type.value} "
            f"置信度={result.confidence:.2f} "
            f"关键词={result.matched_keywords}"
        )
        return result

    def get_stable_type(self) -> Optional[ScreenType]:
        """
        返回投票缓冲区中一致的界面类型。
        如果 N 帧未达成一致，返回 None。
        """
        if len(self._vote_buffer) < self._vote_frames:
            return None
        # 所有帧必须一致
        types = list(self._vote_buffer)
        if len(set(types)) == 1:
            return types[0]
        return None

    def is_card_reward_stable(self) -> bool:
        """判断是否稳定处于选卡界面"""
        return self.get_stable_type() == ScreenType.CARD_REWARD

    def reset_votes(self) -> None:
        """重置投票缓冲区（界面切换后调用）"""
        self._vote_buffer.clear()

    @property
    def last_result(self) -> Optional[DetectionResult]:
        return self._last_result

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _crop_region(self, screenshot: np.ndarray) -> np.ndarray:
        """按比例裁剪扫描区域"""
        h, w = screenshot.shape[:2]
        l = int(w * self._scan_region[0])
        t = int(h * self._scan_region[1])
        r = int(w * self._scan_region[2])
        b = int(h * self._scan_region[3])
        return screenshot[t:b, l:r]

    @staticmethod
    def _normalize_ocr_text(text: str) -> str:
        """
        去除汉字之间的空格（Windows OCR 常在汉字间插入空格）。
        例："选 择 一 张 牌" → "选择一张牌"
        保留英文单词间的空格。
        """
        import re
        # 将每一行分别处理：若某行全为汉字+空格，合并空格
        lines = text.split("\n")
        normalized = []
        for line in lines:
            # 去除汉字之间的空格（匹配：汉字 空格 汉字）
            collapsed = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', line)
            normalized.append(collapsed)
        return "\n".join(normalized)

    def _analyze(self, ocr_result: OcrResult) -> DetectionResult:
        """分析 OCR 文本，判断界面类型"""
        if not ocr_result.success:
            return DetectionResult(
                screen_type=ScreenType.UNKNOWN,
                confidence=0.0,
                matched_keywords=[],
                ocr_text="",
            )

        text = ocr_result.full_text
        # 规范化：去除汉字间空格，再做关键词匹配
        text_normalized = self._normalize_ocr_text(text)
        text_lower = text_normalized.lower()
        matched: list[str] = []

        # 打印 OCR 原文（只在 DEBUG 级别，帮助排查识别问题）
        if text.strip():
            log.debug(f"OCR全文: {repr(text[:300])}")
            if text_normalized != text:
                log.debug(f"OCR规范化: {repr(text_normalized[:300])}")
        else:
            log.debug("OCR全文: (空)")

        # ---- 检测选卡界面 ----
        for kw in _CARD_REWARD_KEYWORDS_ZH:
            if kw in text_normalized:
                matched.append(kw)

        for kw in _CARD_REWARD_KEYWORDS_EN:
            if kw in text_lower:
                matched.append(kw)

        if matched:
            confidence = min(0.5 + 0.2 * len(matched), 0.99)
            return DetectionResult(
                screen_type=ScreenType.CARD_REWARD,
                confidence=confidence,
                matched_keywords=matched,
                ocr_text=text,
                ocr_result=ocr_result,
            )

        # ---- 检测商店界面 ----
        shop_matched: list[str] = []

        for kw in _SHOP_KEYWORDS_ZH:
            if kw in text_normalized:
                shop_matched.append(kw)
        for kw in _SHOP_KEYWORDS_EN:
            if kw in text_lower:
                shop_matched.append(kw)

        # 价格数字特征：出现 3 个以上孤立 2-3 位数字
        price_hits = _PRICE_PATTERN.findall(text_normalized)
        if len(price_hits) >= 3:
            shop_matched.append(f"price_numbers({len(price_hits)})")

        if len(shop_matched) >= 2:
            confidence = min(0.4 + 0.15 * len(shop_matched), 0.95)
            return DetectionResult(
                screen_type=ScreenType.SHOP,
                confidence=confidence,
                matched_keywords=shop_matched,
                ocr_text=text,
                ocr_result=ocr_result,
            )

        # ---- 其他界面 ----
        return DetectionResult(
            screen_type=ScreenType.OTHER,
            confidence=0.5,
            matched_keywords=[],
            ocr_text=text,
            ocr_result=ocr_result,
        )
