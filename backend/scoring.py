"""
backend/scoring.py
评分引擎

职责：将各维度的原始分数合并为最终 total_score (0~100)。

评分维度：
  1. rarity_score      —— 稀有度基准分
  2. archetype_score   —— 套路契合度（最高权重）
  3. completion_score  —— 对套路完成度的贡献
  4. phase_score       —— 当前游戏阶段适配度
  5. synergy_bonus     —— 遗物 / 已有卡协同加成
  6. pollution_penalty —— 污染惩罚（降低 deck 质量）

各维度均归一化到 0~1，最终由 combine_scores() 加权合并后映射到 0~100。
"""

from __future__ import annotations

from .models import (
    Card, Rarity, GamePhase, RunState,
    ScoreBreakdown, CardRole, Character,
)

# ---------------------------------------------------------------------------
# 权重配置（调整这里改变各维度影响力）
# ---------------------------------------------------------------------------

WEIGHTS: dict[str, float] = {
    "base":        0.20,   # 无套路时的基础分（稀有度 + 阶段适配兜底）
    "rarity":      0.10,
    "archetype":   0.35,
    "completion":  0.20,
    "phase":       0.15,
    "synergy":     0.0,   # 屏蔽遗物评分权重（暂未实现）
}
# 权重合计：0.20+0.10+0.35+0.20+0.15 = 1.00

# ---------------------------------------------------------------------------
# 各维度评分函数（返回 0.0 ~ 1.0）
# ---------------------------------------------------------------------------

def score_base_dimension(card: Card, phase: GamePhase) -> float:
    """
    基础分：无论是否匹配套路都有的底分。
    基于稀有度 + 费用效率。确保任何卡都不会因为套路数据缺失而得极低分。
    该分项权重较高（0.20），是无套路数据时的主要分数来源。
    """
    rarity_base = {
        Rarity.RARE:     1.00,
        Rarity.ANCIENT:  0.95,
        Rarity.UNCOMMON: 0.80,
        Rarity.COMMON:   0.65,
        Rarity.BASIC:    0.45,
        Rarity.STARTER:  0.30,
        Rarity.SPECIAL:  0.15,
    }.get(card.rarity, 0.55)

    # 费用效率加成：0 费或 1 费在早期尤其有价值
    cost_bonus = 0.0
    if card.cost == 0:
        cost_bonus = 0.15
    elif card.cost == 1:
        cost_bonus = 0.05

    return min(1.0, rarity_base + cost_bonus)


def score_rarity_dimension(card: Card) -> float:
    """
    稀有度加成分（在 base 之外的额外加成）。
    """
    mapping = {
        Rarity.RARE:     1.0,
        Rarity.UNCOMMON: 0.70,
        Rarity.COMMON:   0.45,
        Rarity.BASIC:    0.30,
        Rarity.STARTER:  0.20,
        Rarity.ANCIENT:  0.90,
        Rarity.SPECIAL:  0.10,
    }
    return mapping.get(card.rarity, 0.0)


def score_archetype_dimension(
    card: Card,
    matched_archetype_weights: list[float],
) -> float:
    """
    套路契合度。
    matched_archetype_weights: 该卡在所有匹配套路中的权重列表。
    取最高匹配权重作为代表分（该卡在最好的套路中有多核心）。
    """
    if not matched_archetype_weights:
        return 0.0
    return max(matched_archetype_weights)


def score_completion_dimension(
    archetype_completion_before: float,
    archetype_completion_after: float,
) -> float:
    """
    套路完成度贡献。
    衡量"拿了这张卡后，套路完成度提升了多少"。
    before / after 均为 0~1 的完成度比例。
    """
    delta = archetype_completion_after - archetype_completion_before
    # 归一化：完成度从 0 提升到 1 视为满分
    return max(0.0, min(1.0, delta))


def score_phase_dimension(
    card: Card,
    phase: GamePhase,
    card_role: CardRole,
) -> float:
    """
    阶段适配度。
    - 过渡卡在早期得高分，中后期得低分
    - 核心 / 使能卡在任何阶段都有价值
    - 污染卡永远低分
    """
    if card_role == CardRole.POLLUTION:
        return 0.0
    if card_role == CardRole.TRANSITION:
        phase_map = {
            GamePhase.EARLY: 0.85,
            GamePhase.MID:   0.50,
            GamePhase.LATE:  0.20,
        }
        return phase_map[phase]
    if card_role in (CardRole.CORE, CardRole.ENABLER):
        return 0.80
    # FILLER / UNKNOWN
    return 0.50


def score_synergy_bonus(
    card: Card,
    run_state: RunState,
    relic_synergy_tags: list[str],
) -> float:
    """
    遗物 / 已有卡协同加成。
    relic_synergy_tags: 由外部计算好的协同标签列表（卡的 tags 与遗物 tags 的交集）。
    每个协同标签贡献 0.15，上限 1.0。
    """
    overlap = set(card.tags) & set(relic_synergy_tags)
    bonus = len(overlap) * 0.15
    return min(1.0, bonus)


def pollution_penalty(
    card: Card,
    deck_size: int,
    card_role: CardRole,
) -> float:
    """
    污染惩罚。
    只对 POLLUTION 或低价值卡生效。
    deck 越大，每张额外污染牌的边际成本越低。
    """
    if card_role != CardRole.POLLUTION:
        return 0.0
    # deck 越小，污染影响越大
    base_penalty = 0.8
    size_discount = min(0.5, deck_size * 0.02)
    return max(0.0, base_penalty - size_discount)


# ---------------------------------------------------------------------------
# 合并函数
# ---------------------------------------------------------------------------

def combine_scores(breakdown: ScoreBreakdown) -> float:
    """
    将 ScoreBreakdown 各维度加权合并，返回 0~100 的最终分。
    权重合计 = 1.0，确保满分可达 100。
    """
    raw = (
        breakdown.base_score      * WEIGHTS["base"]
        + breakdown.rarity_score  * WEIGHTS["rarity"]
        + breakdown.archetype_score * WEIGHTS["archetype"]
        + breakdown.completion_score * WEIGHTS["completion"]
        + breakdown.phase_score   * WEIGHTS["phase"]
        + breakdown.synergy_bonus * WEIGHTS["synergy"]
        - breakdown.pollution_penalty  # 惩罚直接减分（已归一化）
    )
    total = round(max(0.0, min(1.0, raw)) * 100, 2)
    return total
