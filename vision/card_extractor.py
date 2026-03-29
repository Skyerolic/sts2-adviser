"""
vision/card_extractor.py
从选卡界面截图中动态定位并裁剪三张卡的标题区域

定位策略（无固定坐标，适配任意分辨率/窗口尺寸）：
  1. 对全图做 OCR，找到"选择一张牌"/"Choose a Card" 行的归一化 Y 坐标
  2. 卡名行位于该标题下方的某个偏移范围内；扫描所有 OCR 行，
     筛选出 Y 在 [title_y + min_offset, title_y + max_offset] 之间、
     且文字不是已知 UI 元素（"跳过"/"Skip" 等）的行
  3. 按 X 中心将候选行聚类为左/中/右三组，每组取置信度最高的行作为卡名
  4. 按各行的 bbox 坐标裁剪图像区域返回

fallback（OCR 行无坐标时）：
  - 使用相对比例估算：标题行 Y + 固定偏移量 (0.18~0.30)，
    X 三等分 [0.12~0.32, 0.40~0.60, 0.68~0.88]
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .ocr_engine import OcrResult, OcrLine

log = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# 已知 UI 行过滤（不是卡名）
# -----------------------------------------------------------------------
_UI_TEXT_PATTERNS = re.compile(
    r"^(跳过|skip|choose|选择|pass|choose a card|choose one|pick a card"
    r"|card reward|select a card|选一张|选择一张|选择卡"
    r"|攻击|技能|能力|诅咒|状态|attack|skill|power|curse|status)$",
    re.IGNORECASE,
)

# 卡片描述句子特征词（出现则认为是描述文字而非卡名）
_DESCRIPTION_KEYWORDS = re.compile(
    r"造成|获得|敌人|伤害|抽.{0,3}张|如果|则.*攻击|会增加|力虱|力酰"
    r"|deal \d|gain \d|takes? \d|draw \d",
    re.IGNORECASE,
)

# 纯噪声行（纯数字/符号/单字母/1-2个特殊字符）
_NOISE_PATTERN = re.compile(r"^[\d\W\s]{0,4}$")

# 标题关键词（与 screen_detector 一致）
_TITLE_KEYWORDS_ZH = ["选择一张牌", "选一张牌", "选择卡牌", "选择一张"]
_TITLE_KEYWORDS_EN = ["choose a card", "choose one card", "pick a card",
                      "card reward", "select a card"]

# 卡名行相对于标题行的 Y 偏移范围（归一化，0~1）
# 标题在约 8% 高度，卡名在标题下方约 15%~45% 处（视窗口比例）
_CARD_NAME_Y_MIN_OFFSET = 0.08   # 标题下方至少这么远
_CARD_NAME_Y_MAX_OFFSET = 0.60   # 不超过这么远

# fallback 固定比例（当 OCR 无坐标时使用）
_FALLBACK_CENTERS_X    = [0.22, 0.50, 0.78]
_FALLBACK_TITLE_TOP    = 0.28
_FALLBACK_TITLE_BOTTOM = 0.38
_FALLBACK_HALF_W       = 0.14  # 水平宽度略大，避免卡名截断


@dataclass
class CardRegion:
    """单张卡的裁剪区域描述"""
    index: int              # 0, 1, 2
    image: np.ndarray       # 裁剪出的卡名区域 BGR
    rel_rect: tuple[float, float, float, float]   # 相对坐标 (l,t,r,b)
    abs_rect: tuple[int, int, int, int]           # 绝对像素坐标
    ocr_hint: str = ""      # 如果 OCR 已有候选文字，填入供参考


class CardExtractor:
    """
    从选卡界面截图中动态提取三张卡的标题区域。

    用法：
        extractor = CardExtractor()
        # 方式1：已有全图 OCR 结果（推荐，避免重复 OCR）
        regions = extractor.extract_from_ocr(screenshot, ocr_result)
        # 方式2：只有截图（内部会做 fallback 裁剪）
        regions = extractor.extract(screenshot)
    """

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def extract_from_ocr(
        self,
        screenshot: np.ndarray,
        ocr_result: OcrResult,
    ) -> list[CardRegion]:
        """
        基于已有的全图 OCR 结果动态定位卡名区域。
        优先使用 OCR 行坐标；若坐标缺失则 fallback 到比例估算。

        Args:
            screenshot: 游戏窗口的 BGR numpy array
            ocr_result: 全图 OCR 结果（来自 WindowsOcrEngine.recognize()）

        Returns:
            CardRegion 列表（长度 3）
        """
        if screenshot is None or screenshot.size == 0:
            return []

        # 1. 找标题行 Y 坐标
        title_y = self._find_title_y(ocr_result)

        if title_y is not None:
            # 2. 用 OCR 坐标定位卡名行
            regions = self._locate_from_ocr_lines(screenshot, ocr_result, title_y)
            if regions:
                return regions
            log.debug("OCR 行定位失败，使用标题 Y fallback")
            # 3. 知道标题 Y，用固定水平比例 + 动态垂直偏移
            return self._fallback_with_title_y(screenshot, title_y)
        else:
            log.debug("未找到标题行，使用完全 fallback")
            return self._full_fallback(screenshot)

    def extract(self, screenshot: np.ndarray) -> list[CardRegion]:
        """
        不依赖 OCR 结果的纯比例 fallback 裁剪。
        当外部没有 OCR 结果可传入时使用。
        """
        return self._full_fallback(screenshot)

    # ------------------------------------------------------------------
    # 内部：标题定位
    # ------------------------------------------------------------------

    def _find_title_y(self, ocr_result: OcrResult) -> Optional[float]:
        """在 OCR 结果中找到标题行（"选择一张牌"等）的归一化 Y 中心"""
        for line in ocr_result.lines:
            text_norm = self._normalize_zh(line.text).lower()
            is_title = any(kw in text_norm for kw in _TITLE_KEYWORDS_ZH + _TITLE_KEYWORDS_EN)
            if is_title and line.bbox is not None:
                y_center = (line.bbox[1] + line.bbox[3]) / 2
                log.debug(f"找到标题行: {repr(line.text)} y_center={y_center:.3f}")
                return y_center
        return None

    # ------------------------------------------------------------------
    # 内部：OCR 行坐标定位
    # ------------------------------------------------------------------

    def _locate_from_ocr_lines(
        self,
        screenshot: np.ndarray,
        ocr_result: OcrResult,
        title_y: float,
    ) -> list[CardRegion]:
        """
        在标题 Y 下方的偏移范围内，找符合条件的 OCR 行，
        按 X 坐标聚为三组作为三张卡名。
        """
        h, w = screenshot.shape[:2]

        y_min = title_y + _CARD_NAME_Y_MIN_OFFSET
        y_max = title_y + _CARD_NAME_Y_MAX_OFFSET

        # 候选行：在 Y 范围内、有坐标、文字非 UI 噪声、非描述句子、非纯符号
        candidates: list[OcrLine] = []
        for line in ocr_result.lines:
            if line.bbox is None:
                continue
            line_y = (line.bbox[1] + line.bbox[3]) / 2
            if not (y_min <= line_y <= y_max):
                continue
            text_stripped = line.text.strip()
            text_norm = self._normalize_zh(text_stripped)
            if not text_norm:
                continue
            if _UI_TEXT_PATTERNS.match(text_norm.lower()):
                continue
            if _NOISE_PATTERN.match(text_norm):
                continue
            if _DESCRIPTION_KEYWORDS.search(text_norm):
                continue
            candidates.append(line)

        if not candidates:
            log.debug(f"未找到候选卡名行 (y_min={y_min:.2f}, y_max={y_max:.2f})")
            return []

        log.debug(f"候选卡名行 ({len(candidates)}条): "
                  + ", ".join(f"{repr(l.text)} x={l.bbox[0]:.2f}" for l in candidates))

        # 按 X 中心聚类为 3 组
        groups = self._cluster_by_x(candidates, n=3)
        if not all(groups):
            log.debug("X 聚类不足 3 组")
            return []

        # 每组取文本最短的行（卡名比描述文字短）
        regions = []
        for i, group in enumerate(groups):
            best = min(group, key=lambda l: len(self._normalize_zh(l.text)))
            bbox = best.bbox
            # bbox 转绝对像素，纵向适当扩展以确保文字完整
            pad_y = 0.015  # 上下各 1.5% 高度的 padding
            l_px = max(0, int(bbox[0] * w))
            t_px = max(0, int((bbox[1] - pad_y) * h))
            r_px = min(w, int(bbox[2] * w))
            b_px = min(h, int((bbox[3] + pad_y) * h))

            if r_px <= l_px or b_px <= t_px:
                continue

            crop = screenshot[t_px:b_px, l_px:r_px].copy()
            regions.append(CardRegion(
                index=i,
                image=crop,
                rel_rect=(bbox[0], bbox[1] - pad_y, bbox[2], bbox[3] + pad_y),
                abs_rect=(l_px, t_px, r_px, b_px),
                ocr_hint=best.text,
            ))
            log.debug(
                f"卡{i+1} 动态定位: ({l_px},{t_px})-({r_px},{b_px}) "
                f"hint={repr(best.text)}"
            )

        return regions if len(regions) == 3 else []

    # ------------------------------------------------------------------
    # 内部：fallback（知道标题 Y）
    # ------------------------------------------------------------------

    def _fallback_with_title_y(
        self,
        screenshot: np.ndarray,
        title_y: float,
    ) -> list[CardRegion]:
        """
        已知标题 Y，但 OCR 行无坐标或聚类失败。
        使用固定水平三等分 + 标题下方固定垂直偏移。
        卡名通常紧接在标题下方约 3%~10% 处。
        """
        h, w = screenshot.shape[:2]
        top    = title_y + 0.05
        bottom = title_y + 0.18
        regions = []
        for i, cx in enumerate(_FALLBACK_CENTERS_X):
            l_px = max(0, int((cx - _FALLBACK_HALF_W) * w))
            r_px = min(w, int((cx + _FALLBACK_HALF_W) * w))
            t_px = max(0, int(top * h))
            b_px = min(h, int(bottom * h))
            crop = screenshot[t_px:b_px, l_px:r_px].copy()
            regions.append(CardRegion(
                index=i, image=crop,
                rel_rect=(cx - _FALLBACK_HALF_W, top, cx + _FALLBACK_HALF_W, bottom),
                abs_rect=(l_px, t_px, r_px, b_px),
            ))
            log.debug(f"卡{i+1} fallback(title_y): ({l_px},{t_px})-({r_px},{b_px})")
        return regions

    # ------------------------------------------------------------------
    # 内部：完全 fallback（无任何 OCR 信息）
    # ------------------------------------------------------------------

    def _full_fallback(self, screenshot: np.ndarray) -> list[CardRegion]:
        """完全依赖固定比例的 fallback"""
        h, w = screenshot.shape[:2]
        regions = []
        for i, cx in enumerate(_FALLBACK_CENTERS_X):
            l_px = max(0, int((cx - _FALLBACK_HALF_W) * w))
            r_px = min(w, int((cx + _FALLBACK_HALF_W) * w))
            t_px = max(0, int(_FALLBACK_TITLE_TOP * h))
            b_px = min(h, int(_FALLBACK_TITLE_BOTTOM * h))
            crop = screenshot[t_px:b_px, l_px:r_px].copy()
            regions.append(CardRegion(
                index=i, image=crop,
                rel_rect=(cx - _FALLBACK_HALF_W, _FALLBACK_TITLE_TOP,
                           cx + _FALLBACK_HALF_W, _FALLBACK_TITLE_BOTTOM),
                abs_rect=(l_px, t_px, r_px, b_px),
            ))
            log.debug(f"卡{i+1} full_fallback: ({l_px},{t_px})-({r_px},{b_px})")
        return regions

    # ------------------------------------------------------------------
    # 内部：工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_zh(text: str) -> str:
        """去除汉字之间的空格"""
        import re
        return re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', text)

    @staticmethod
    def _cluster_by_x(lines: list[OcrLine], n: int = 3) -> list[list[OcrLine]]:
        """
        将 OCR 行按 X 中心分为 n 组（简单排序后等分）。
        返回 n 个列表，从左到右排列。
        """
        sorted_lines = sorted(lines, key=lambda l: (l.bbox[0] + l.bbox[2]) / 2)

        if len(sorted_lines) < n:
            # 不够 n 组时，尽量填充（可能有组为空）
            groups: list[list[OcrLine]] = [[] for _ in range(n)]
            for j, line in enumerate(sorted_lines):
                groups[j].append(line)
            return groups

        # 用 k-means 风格的简单分桶：找 n-1 个最大 X 间隙作为分割点
        x_centers = [(l.bbox[0] + l.bbox[2]) / 2 for l in sorted_lines]
        gaps = [(x_centers[i+1] - x_centers[i], i) for i in range(len(x_centers) - 1)]
        gaps.sort(reverse=True)
        split_indices = sorted(idx for _, idx in gaps[:n-1])

        groups = []
        prev = 0
        for si in split_indices:
            groups.append(sorted_lines[prev:si+1])
            prev = si + 1
        groups.append(sorted_lines[prev:])

        return groups
