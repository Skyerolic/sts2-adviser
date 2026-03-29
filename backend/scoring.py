"""
backend/scoring.py
评分引擎 v2

评分逻辑：
  以 60 分为 "无害中性" 基线，向上向下浮动。

  分档定义（对应 recommendation）：
    80~100  强烈推荐（套路核心 or 稀有高价值）
    65~79   推荐
    50~64   可选（有一定价值，但非必需）
    30~49   谨慎（轻微稀释，或与套路无关）
    0~29    跳过（污染或明显不适合当前 run）

  各维度（均归一化 0~1）：
    1. archetype_score   套路契合度     权重 0.40  最核心维度
    2. value_score       卡牌固有价值   权重 0.25  稀有度+费用效率综合
    3. phase_score       阶段适配       权重 0.15  当前楼层适配性
    4. completion_score  完成度贡献     权重 0.15  拿了这张后套路更完整多少
    5. synergy_bonus     额外协同       权重 0.05  遗物/已有卡协同

  惩罚（直接从 raw score 减分）：
    pollution_penalty: 污染牌 -30~-50 分
    deck_bloat_penalty: deck 过厚时对低价值牌额外惩罚
"""

from __future__ import annotations
import logging

from .models import (
    Card, Rarity, GamePhase, RunState,
    ScoreBreakdown, CardRole, Character,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 权重配置
# ---------------------------------------------------------------------------

WEIGHTS: dict[str, float] = {
    "archetype":   0.40,   # 套路契合：最重要
    "value":       0.25,   # 卡牌固有价值（稀有度+费用）
    "phase":       0.15,   # 阶段适配
    "completion":  0.15,   # 完成度贡献
    "synergy":     0.05,   # 协同加成
}
# 合计 = 1.00

# ---------------------------------------------------------------------------
# 维度1：套路契合度
# ---------------------------------------------------------------------------

def score_archetype_dimension(
    card: Card,
    matched_archetype_weights: list[float],
) -> float:
    """
    取该卡在所有匹配套路中的最高权重。
    无匹配时返回 0.0（由 value_score 兜底）。
    """
    if not matched_archetype_weights:
        return 0.0
    return max(matched_archetype_weights)


# ---------------------------------------------------------------------------
# 维度2：卡牌固有价值
# ---------------------------------------------------------------------------

def score_value_dimension(card: Card, phase: GamePhase) -> float:
    """
    卡牌独立于套路的固有价值评估。
    综合：稀有度基线 + 费用效率 + 阶段无关通用性。

    设计原则：
      - rare 卡在任何时候都有底线价值（0.75+）
      - common 给 0.45 左右作为中性值
      - 0 费牌有显著加成（灵活性价值）
    """
    rarity_base: dict[Rarity, float] = {
        Rarity.ANCIENT:  0.90,
        Rarity.RARE:     0.80,
        Rarity.UNCOMMON: 0.60,
        Rarity.COMMON:   0.45,
        Rarity.BASIC:    0.35,
        Rarity.STARTER:  0.20,
        Rarity.SPECIAL:  0.10,
        Rarity.CURSE:    0.00,
        Rarity.STATUS:   0.05,
    }
    base = rarity_base.get(card.rarity, 0.45)

    # 费用效率
    cost_bonus = 0.0
    if card.cost == 0:
        cost_bonus = 0.12
    elif card.cost == 1:
        cost_bonus = 0.05
    elif card.cost >= 3:
        cost_bonus = -0.05   # 高费用略微减分

    return min(1.0, max(0.0, base + cost_bonus))


# ---------------------------------------------------------------------------
# 维度3：阶段适配
# ---------------------------------------------------------------------------

def score_phase_dimension(
    card: Card,
    phase: GamePhase,
    card_role: CardRole,
) -> float:
    """
    当前阶段对该卡的适配度。
      - CORE/ENABLER 任何阶段都高分
      - TRANSITION 早期强，后期弱
      - POLLUTION 所有阶段 0 分
      - FILLER/UNKNOWN 中性 0.55
    """
    if card_role == CardRole.POLLUTION:
        return 0.0
    if card_role == CardRole.TRANSITION:
        return {
            GamePhase.EARLY: 0.85,
            GamePhase.MID:   0.45,
            GamePhase.LATE:  0.15,
        }[phase]
    if card_role in (CardRole.CORE, CardRole.ENABLER):
        # 核心/使能卡后期更有价值（已积累协同）
        return {
            GamePhase.EARLY: 0.75,
            GamePhase.MID:   0.82,
            GamePhase.LATE:  0.88,
        }[phase]
    # FILLER / UNKNOWN
    return 0.55


# ---------------------------------------------------------------------------
# 维度4：完成度贡献
# ---------------------------------------------------------------------------

def score_completion_dimension(
    archetype_completion_before: float,
    archetype_completion_after: float,
) -> float:
    """
    拿了这张卡后套路完成度提升多少。
    完成度 delta 放大 3 倍（因为单张卡通常只提升 5~10%，
    不放大的话贡献微乎其微）。
    """
    delta = archetype_completion_after - archetype_completion_before
    # 放大并上限 1.0
    return min(1.0, max(0.0, delta * 3.0))


# ---------------------------------------------------------------------------
# 维度5：协同加成
# ---------------------------------------------------------------------------

def score_synergy_bonus(
    card: Card,
    run_state: RunState,
    relic_synergy_tags: list[str],
) -> float:
    """
    遗物/已有卡协同加成。
    每个匹配标签贡献 0.2，上限 1.0。
    """
    overlap = set(card.tags) & set(relic_synergy_tags)
    return min(1.0, len(overlap) * 0.20)


# ---------------------------------------------------------------------------
# 惩罚项
# ---------------------------------------------------------------------------

def pollution_penalty(
    card: Card,
    deck_size: int,
    card_role: CardRole,
) -> float:
    """
    污染惩罚（直接减分，不经过权重，单位：0~1）。
    污染牌在合并时会造成约 -30~-50 分的实际分数下降。
    deck 越小，污染代价越大。
    """
    if card_role != CardRole.POLLUTION:
        return 0.0
    # 基础惩罚 0.50，deck 每增加一张卡折扣 0.015
    base_penalty = 0.50
    size_discount = min(0.25, deck_size * 0.015)
    return base_penalty - size_discount


def deck_bloat_penalty(
    card: Card,
    deck_size: int,
    card_role: CardRole,
) -> float:
    """
    厚牌组对低价值牌的额外惩罚。
    deck >= 20 张后，FILLER/UNKNOWN 卡每多一张 deck 给 0.01 惩罚，上限 0.15。
    CORE/ENABLER 不受影响。
    """
    if card_role in (CardRole.CORE, CardRole.ENABLER):
        return 0.0
    if deck_size < 20:
        return 0.0
    extra = deck_size - 20
    return min(0.15, extra * 0.01)


# ---------------------------------------------------------------------------
# 合并：加权求和 → 0~100 分
# ---------------------------------------------------------------------------

def combine_scores(breakdown: "ScoreBreakdown") -> float:
    """
    将 ScoreBreakdown 各维度加权合并，返回 0~100 的最终分。

    合并逻辑：
      raw = Σ(维度得分 × 权重) - 惩罚
      raw 映射到 [0, 1]，×100 取整到 0.1 精度。

    注意：breakdown 中 base_score 字段被复用为 value_score，
    rarity_score 字段被复用为 deck_bloat_penalty（节省模型改动）。
    """
    raw = (
        breakdown.archetype_score  * WEIGHTS["archetype"]
        + breakdown.base_score     * WEIGHTS["value"]        # base_score 存 value_score
        + breakdown.phase_score    * WEIGHTS["phase"]
        + breakdown.completion_score * WEIGHTS["completion"]
        + breakdown.synergy_bonus  * WEIGHTS["synergy"]
        - breakdown.pollution_penalty                        # 污染惩罚
        - breakdown.rarity_score                             # rarity_score 存 bloat_penalty
    )
    total = round(max(0.0, min(1.0, raw)) * 100, 1)
    log.debug(
        f"分数合并: archetype={breakdown.archetype_score:.2f}×{WEIGHTS['archetype']}"
        f" value={breakdown.base_score:.2f}×{WEIGHTS['value']}"
        f" phase={breakdown.phase_score:.2f}×{WEIGHTS['phase']}"
        f" completion={breakdown.completion_score:.2f}×{WEIGHTS['completion']}"
        f" synergy={breakdown.synergy_bonus:.2f}×{WEIGHTS['synergy']}"
        f" poll_pen={breakdown.pollution_penalty:.2f}"
        f" bloat_pen={breakdown.rarity_score:.2f}"
        f" → raw={raw:.3f} total={total}"
    )
    return total


# ---------------------------------------------------------------------------
# 保留旧接口兼容（evaluator.py 调用）
# ---------------------------------------------------------------------------

def score_base_dimension(card: Card, phase: GamePhase) -> float:
    """兼容旧接口：内部调用 score_value_dimension"""
    return score_value_dimension(card, phase)


def score_rarity_dimension(card: Card) -> float:
    """兼容旧接口：返回 0（bloat_penalty 在 evaluator 中单独计算）"""
    return 0.0
