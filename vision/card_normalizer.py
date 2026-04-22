"""
vision/card_normalizer.py
将 OCR 识别到的原始文字规范化为标准 card_id

流程：
  1. 文本清洗（去噪、修正常见 OCR 错误）
  2. 在中英文卡名索引中做模糊匹配（rapidfuzz）
  3. 返回最佳匹配的 card_id + 置信度分数

支持：
  - 中英文双语卡名
  - OCR 常见错误修正（O/0, l/1, rn/m 等）
  - 置信度阈值过滤
  - 多候选返回（供投票模块使用）

依赖：
  - rapidfuzz
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from utils.paths import get_app_root

log = logging.getLogger(__name__)

# 置信度阈值（低于此值的匹配视为失败）
DEFAULT_CONFIDENCE_THRESHOLD = 0.55


@dataclass
class MatchResult:
    """单次卡名匹配结果"""
    card_id: str            # e.g. "CATALYST"
    matched_name: str       # 数据库中的标准名称
    input_text: str         # OCR 原始文字
    confidence: float       # 0.0 ~ 1.0
    language: str           # "en" 或 "zh"

    @property
    def is_reliable(self) -> bool:
        return self.confidence >= DEFAULT_CONFIDENCE_THRESHOLD


@dataclass
class NormalizeResult:
    """三张卡的规范化结果"""
    cards: list[Optional[MatchResult]]   # 长度 3，识别失败的位置为 None
    all_reliable: bool = False           # 三张卡是否全部可靠

    def card_ids(self) -> list[Optional[str]]:
        return [c.card_id if c else None for c in self.cards]

    def reliable_card_ids(self) -> list[str]:
        return [c.card_id for c in self.cards if c and c.is_reliable]


class CardNameIndex:
    """
    卡名索引：支持英文 + 中文双语模糊匹配。

    数据来源：
      - data/cards.json        → 英文卡名（card.name）
      - data/card_locale_zh.json → 中文卡名（*.title 键）
    """

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self._data_dir = data_dir or (get_app_root() / "data")
        # card_id -> (英文名, 中文名)
        self._index: dict[str, tuple[str, str]] = {}
        # 搜索列表：[(标准化文本, card_id, language)]
        self._en_list: list[tuple[str, str]] = []  # (normalized_name, card_id)
        self._zh_list: list[tuple[str, str]] = []
        self._loaded = False

    def load(self) -> bool:
        """加载卡名数据。返回是否成功。"""
        if self._loaded:
            return True

        try:
            cards_path = self._data_dir / "cards.json"
            locale_path = self._data_dir / "card_locale_zh.json"

            # 读取英文卡名
            en_map: dict[str, str] = {}  # card_id -> en_name
            if cards_path.exists():
                with open(cards_path, "r", encoding="utf-8") as f:
                    cards = json.load(f)
                for card in cards:
                    cid = card.get("id", "").upper()
                    name = card.get("name", "")
                    if cid and name:
                        en_map[cid] = name

            # 读取中文卡名
            zh_map: dict[str, str] = {}  # card_id -> zh_name
            if locale_path.exists():
                with open(locale_path, "r", encoding="utf-8") as f:
                    locale = json.load(f)
                for key, val in locale.items():
                    if key.endswith(".title"):
                        cid = key[: -len(".title")].upper()
                        zh_map[cid] = val

            # 合并
            all_ids = set(en_map.keys()) | set(zh_map.keys())
            for cid in all_ids:
                en_name = en_map.get(cid, "")
                zh_name = zh_map.get(cid, "")
                self._index[cid] = (en_name, zh_name)

                if en_name:
                    self._en_list.append((_normalize_text(en_name), cid))
                if zh_name:
                    self._zh_list.append((_normalize_text(zh_name), cid))

            self._loaded = True
            log.info(
                f"卡名索引加载完成: {len(self._index)} 张卡, "
                f"英文 {len(self._en_list)} 条, 中文 {len(self._zh_list)} 条"
            )
            return True

        except Exception as e:
            log.error(f"卡名索引加载失败: {e}")
            return False

    def search(
        self,
        query: str,
        top_k: int = 3,
        threshold: Optional[float] = None,
    ) -> list[MatchResult]:
        """
        模糊搜索卡名（按语言分区 + 精确匹配快速通道 + 长度感知阈值 + 歧义拒绝）。

        Args:
            query: OCR 识别的原始文字
            top_k: 返回前 N 个候选
            threshold: 最低置信度。传 None 则根据 query 语言+长度自适应
                       （见 _threshold_for）。

        Returns:
            按置信度降序的 MatchResult 列表；若 top-1/top-2 置信度差距 < 0.05
            且均非精确匹配，视为歧义，返回空列表。
        """
        if not self._loaded:
            self.load()

        if not query or not query.strip():
            return []

        cleaned = _clean_ocr_text(query)
        normalized = _normalize_text(cleaned)

        if not normalized:
            return []

        effective_threshold = (
            threshold if threshold is not None else _threshold_for(normalized)
        )

        # 语言分区：CJK 查询只搜中文列表，纯 ASCII 只搜英文列表
        is_cjk = bool(_CJK_PATTERN.search(normalized))
        if is_cjk:
            search_list = self._zh_list
            lang = "zh"
            name_field_idx = 1  # _index[cid] = (en_name, zh_name)
        else:
            search_list = self._en_list
            lang = "en"
            name_field_idx = 0

        if not search_list:
            return []

        # 精确匹配快速通道：规范化后完全相等则 confidence=1.0 立即返回
        for norm_name, card_id in search_list:
            if norm_name == normalized:
                return [MatchResult(
                    card_id=card_id,
                    matched_name=self._index[card_id][name_field_idx],
                    input_text=query,
                    confidence=1.0,
                    language=lang,
                )]

        try:
            from rapidfuzz import process, fuzz
        except ImportError:
            log.error("rapidfuzz 未安装，请运行: pip install rapidfuzz")
            return []

        names = [name for name, _ in search_list]
        # 至少取 2 个用于歧义检查
        matches = process.extract(
            normalized,
            names,
            scorer=fuzz.token_sort_ratio,
            limit=max(top_k, 2),
        )

        candidates: list[MatchResult] = []
        for match_name, score, idx in matches:
            confidence = score / 100.0
            if confidence >= effective_threshold:
                _, card_id = search_list[idx]
                candidates.append(MatchResult(
                    card_id=card_id,
                    matched_name=self._index[card_id][name_field_idx],
                    input_text=query,
                    confidence=confidence,
                    language=lang,
                ))

        # 歧义拒绝：top-1 与 top-2 都过阈值且置信度差距 < 0.05 → 拒绝
        # 宁可让槽位保持未锁定等下一帧，也不锁入错误结果
        if len(candidates) >= 2 and candidates[0].confidence < 1.0:
            if candidates[0].confidence - candidates[1].confidence < 0.05:
                log.debug(
                    f"歧义拒绝: '{query}' → {candidates[0].card_id}"
                    f"({candidates[0].confidence:.2f}) vs {candidates[1].card_id}"
                    f"({candidates[1].confidence:.2f})"
                )
                return []

        return candidates[:top_k]

    def best_match(
        self,
        query: str,
        threshold: Optional[float] = None,
    ) -> Optional[MatchResult]:
        """返回最佳匹配，低于阈值或歧义时返回 None"""
        results = self.search(query, top_k=1, threshold=threshold)
        return results[0] if results else None


class CardNormalizer:
    """
    将 OCR 结果列表规范化为 card_id 列表。

    用法：
        normalizer = CardNormalizer()
        result = normalizer.normalize(["Catalyst", "忍术", "Reflex"])
    """

    def __init__(
        self,
        index: Optional[CardNameIndex] = None,
        threshold: Optional[float] = None,
    ) -> None:
        self._index = index or CardNameIndex()
        # None 表示使用 _threshold_for 的长度感知自适应阈值（推荐）
        self._threshold = threshold
        if not self._index._loaded:
            self._index.load()

    def normalize(self, ocr_texts: list[str]) -> NormalizeResult:
        """
        对 OCR 识别的 3 个文字串做规范化。

        Args:
            ocr_texts: 长度 3 的列表，每项是一张卡的 OCR 原始文字

        Returns:
            NormalizeResult
        """
        cards: list[Optional[MatchResult]] = []

        for text in ocr_texts:
            if not text or not text.strip():
                cards.append(None)
                continue

            match = self._index.best_match(text, self._threshold)
            cards.append(match)

            if match:
                log.debug(
                    f"匹配: '{text}' → '{match.matched_name}' "
                    f"(id={match.card_id}, conf={match.confidence:.2f}, lang={match.language})"
                )
            else:
                log.debug(f"匹配失败: '{text}' (低于阈值 {self._threshold})")

        all_reliable = all(c is not None and c.is_reliable for c in cards)
        return NormalizeResult(cards=cards, all_reliable=all_reliable)

    def normalize_single(self, ocr_text: str) -> Optional[MatchResult]:
        """对单个文字做规范化"""
        return self._index.best_match(ocr_text, self._threshold)


# -----------------------------------------------------------------------
# 文本处理工具函数
# -----------------------------------------------------------------------

# OCR 常见错误映射（小写）
_OCR_CORRECTIONS = {
    "0": "o",    # 数字0 → 字母o（在卡名中字母更常见）
    "1": "l",    # 数字1 → 字母l
    "|": "l",    # 竖线 → l
    "rn": "m",   # rn 粘连 → m
    "vv": "w",   # vv → w
    "ii": "n",   # ii → n（部分字体）
}

# 中文 OCR 常见误字映射（字形相近 / 严重乱码）
_ZH_OCR_CORRECTIONS = {
    # 已有
    "米槌": "头槌",
    "米锤": "头槌",
    # 熔融之拳 常见误读
    "煊融之拳": "熔融之拳",
    "厴覯之拳": "熔融之拳",
    "熔融之碎": "熔融之拳",
    # 双重打击
    "双重打吉": "双重打击",
    "双重打击击": "双重打击",
    # 御血术
    "御皿术": "御血术",
    "御血木": "御血术",
    # 煊/熔 单字（用于短文本修正）
    "煊融": "熔融",
    "厴覯": "熔融",
}

_NOISE_PATTERN = re.compile(r"[^\w\s\u4e00-\u9fff]")  # 保留字母/数字/空格/中文

# CJK 基本统一表意文字（用于语言分区）
_CJK_PATTERN = re.compile(r"[一-鿿]")


def _threshold_for(normalized_query: str) -> float:
    """
    长度 + 语言自适应置信度阈值。

    - 纯 ASCII 查询（英文卡名）     -> 0.55（保留原行为）
    - 中文，总字符数 <= 3           -> 0.85
      （防 2 字 ⊂ 4 字卡名误匹配：如 OCR 读到 "打击"，"双重打击" 的 fuzz.ratio=0.667）
    - 中文，总字符数 >= 4           -> 0.70

    若调用方显式传 threshold，将覆盖此函数的返回值。
    """
    if not _CJK_PATTERN.search(normalized_query):
        return DEFAULT_CONFIDENCE_THRESHOLD
    cjk_len = len(normalized_query.replace(" ", ""))
    if cjk_len <= 3:
        return 0.85
    return 0.70


def _clean_ocr_text(text: str) -> str:
    """
    清洗 OCR 文字：
    1. 去除前后空白
    2. 修正常见 OCR 错误字符（含中文误字）
    3. 去除噪声符号
    4. 合并多余空格
    """
    if not text:
        return ""

    # 去除前后空白
    text = text.strip()

    # Unicode 规范化
    text = unicodedata.normalize("NFC", text)

    # 修正中文 OCR 常见误字
    for wrong, correct in _ZH_OCR_CORRECTIONS.items():
        text = text.replace(wrong, correct)

    # 去除汉字之间的空格（Windows OCR 常在汉字间插入空格）
    text = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', text)

    # 修正常见 OCR 错误（仅对纯 ASCII 部分）
    text_lower = text.lower()
    for wrong, correct in _OCR_CORRECTIONS.items():
        text_lower = text_lower.replace(wrong, correct)

    # 去除噪声符号（保留字母、数字、空格、中文）
    cleaned = _NOISE_PATTERN.sub(" ", text_lower)

    # 合并多余空格
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned


def _normalize_text(text: str) -> str:
    """
    进一步规范化文本用于模糊匹配：
    - 统一小写
    - 去除多余空格
    - 中文去除空格
    """
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


# 模块级单例
_card_index: Optional[CardNameIndex] = None
_normalizer: Optional[CardNormalizer] = None


def get_card_normalizer() -> CardNormalizer:
    """获取全局 CardNormalizer 单例"""
    global _card_index, _normalizer
    if _normalizer is None:
        _card_index = CardNameIndex()
        _normalizer = CardNormalizer(_card_index)
    return _normalizer
