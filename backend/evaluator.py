"""
backend/evaluator.py
卡牌评估器（CardEvaluator）

职责：
  - 接收 RunState + 卡库
  - 检测当前 run 匹配的套路
  - 对每张候选卡评估并打分
  - 输出 EvaluationResult 列表（已排序）

依赖：
  - archetypes.ArchetypeLibrary
  - scoring.*
  - models.*
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

_LOGS_DIR = Path(__file__).parent.parent / "logs"
_LOGS_DIR.mkdir(exist_ok=True)

log = logging.getLogger(__name__)

from .archetypes import ArchetypeLibrary, archetype_library
from .models import (
    Card, Archetype, CardRole, GamePhase,
    RunState, EvaluationResult, ScoreBreakdown,
)
from .scoring import (
    score_base_dimension,
    score_rarity_dimension,
    score_archetype_dimension,
    score_completion_dimension,
    score_phase_dimension,
    score_synergy_bonus,
    pollution_penalty,
    deck_bloat_penalty,
    combine_scores,
)


class CardEvaluator:
    """
    评估器主类。

    使用方式：
        evaluator = CardEvaluator(card_db)
        results = evaluator.rank_cards(run_state)
    """

    def __init__(
        self,
        card_db: dict[str, Card],
        library: Optional[ArchetypeLibrary] = None,
    ) -> None:
        """
        card_db: card_id -> Card 的字典（全卡库）
        library: 套路库（默认使用模块级单例）
        """
        self.card_db = card_db
        self.library = library or archetype_library

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def rank_cards(self, run_state: RunState) -> list[EvaluationResult]:
        """
        对 run_state.card_choices 中的所有候选卡进行评估并排序。
        返回按 total_score 降序排列的 EvaluationResult 列表。
        """
        print(f"[DEBUG] card_choices: {run_state.card_choices}")
        detected = self.detect_archetypes(run_state)
        relic_tags = self._extract_relic_tags(run_state)

        results: list[EvaluationResult] = []
        for card_id in run_state.card_choices:
            card = self._resolve_card(card_id)
            if card is None:
                print(f"[DEBUG] Card not found in DB: {card_id}")
                continue
            print(f"[DEBUG] Evaluating card: {card_id} -> {card.name}")
            result = self.evaluate_card(card, run_state, detected, relic_tags)
            results.append(result)

        print(f"[DEBUG] Evaluation results: {[r.card_name for r in results]}")
        results.sort(key=lambda r: r.total_score, reverse=True)
        self._save_score_log(results, run_state)
        return results

    def detect_archetypes(self, run_state: RunState) -> list[Archetype]:
        """
        根据当前牌组，检测玩家正在走的套路。

        策略：
          - 取与当前 character 匹配的所有套路
          - 计算每个套路的"完成度"（已有卡 / 套路核心卡数）
          - 返回完成度 > 阈值的套路列表（按完成度降序）

        TODO（后续实现）：
          - 更精细的权重加权完成度计算
          - 遗物对套路方向的影响
        """
        candidate_archetypes = self.library.get_by_character(run_state.character)
        deck_set = set(self._normalize_card_id(cid) for cid in run_state.deck)

        scored: list[tuple[float, Archetype]] = []
        for archetype in candidate_archetypes:
            completion = self._calc_completion(archetype, deck_set)
            if completion > 0.0:
                scored.append((completion, archetype))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [a for _, a in scored]

    def evaluate_card(
        self,
        card: Card,
        run_state: RunState,
        detected_archetypes: list[Archetype],
        relic_synergy_tags: list[str],
    ) -> EvaluationResult:
        """
        对单张卡进行全维度评估，返回 EvaluationResult。
        """
        deck_set = set(self._normalize_card_id(cid) for cid in run_state.deck)

        # 1. 收集该卡在各套路中的权重
        archetype_weights: list[float] = []
        matched_archetype_ids: list[str] = []

        for archetype in detected_archetypes:
            weight_info = self.library.get_card_weight(archetype.id, card.id)
            if weight_info:
                archetype_weights.append(weight_info.weight)
                matched_archetype_ids.append(archetype.id)

        # 2. 确定卡牌角色
        role = self._determine_role(card, detected_archetypes, archetype_weights)

        # 3. 计算套路完成度贡献
        comp_before = 0.0
        comp_after = 0.0
        if detected_archetypes:
            primary = detected_archetypes[0]
            comp_before = self._calc_completion(primary, deck_set)
            new_deck = deck_set | {card.id}
            comp_after = self._calc_completion(primary, new_deck)

        # 4. 各维度评分
        # 注意：base_score 字段复用为 value_score，rarity_score 复用为 bloat_penalty
        breakdown = ScoreBreakdown(
            base_score=score_base_dimension(card, run_state.phase),        # value_score
            rarity_score=deck_bloat_penalty(card, len(run_state.deck), role),  # bloat_penalty
            archetype_score=score_archetype_dimension(card, archetype_weights),
            completion_score=score_completion_dimension(comp_before, comp_after),
            phase_score=score_phase_dimension(card, run_state.phase, role),
            synergy_bonus=score_synergy_bonus(card, run_state, relic_synergy_tags),
            pollution_penalty=pollution_penalty(card, len(run_state.deck), role),
        )

        total = combine_scores(breakdown)

        # 5. 生成解释
        reasons_for, reasons_against = self._build_reasons(
            card, role, breakdown, matched_archetype_ids, run_state
        )

        recommendation = self._make_recommendation(total, role)

        return EvaluationResult(
            card_id=card.id,
            card_name=card.name,
            rarity=card.rarity.value,  # 添加稀有度
            total_score=total,
            role=role,
            breakdown=breakdown,
            matched_archetypes=matched_archetype_ids,
            reasons_for=reasons_for,
            reasons_against=reasons_against,
            recommendation=recommendation,
        )

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _resolve_card(self, card_id: str) -> Optional[Card]:
        """将 card_id（含升级标记）解析为 Card 对象"""
        normalized = self._normalize_card_id(card_id)
        return self.card_db.get(normalized)

    @staticmethod
    def _normalize_card_id(card_id: str) -> str:
        """去除升级后缀并统一小写（e.g. 'Shiv+' -> 'shiv', 'DEMON_FORM' -> 'demon_form'）"""
        return card_id.rstrip("+").lower()

    def _calc_completion(self, archetype: Archetype, deck_set: set[str]) -> float:
        """
        计算套路完成度 (0.0 ~ 1.0)。
        加权：核心卡权重更高，filler 权重更低。
        """
        if not archetype.card_weights:
            return 0.0

        total_weight = sum(w.weight for w in archetype.card_weights)
        if total_weight == 0:
            return 0.0

        owned_weight = sum(
            w.weight for w in archetype.card_weights
            if w.card_id.lower() in deck_set
        )
        return owned_weight / total_weight

    def _determine_role(
        self,
        card: Card,
        detected_archetypes: list[Archetype],
        archetype_weights: list[float],
    ) -> CardRole:
        """
        根据套路匹配结果推断卡牌在当前 run 中的角色。

        TODO（后续扩展）：
          - 结合卡牌 tags 做更细粒度判断
          - 污染卡检测（稀释 deck 的卡）
        """
        if not detected_archetypes or not archetype_weights:
            # 未匹配任何套路 → 按稀有度做保守判断
            from .models import Rarity
            if card.rarity in (Rarity.RARE, Rarity.ANCIENT):
                return CardRole.FILLER   # 稀有牌通用价值，不算污染
            elif card.rarity in (Rarity.UNCOMMON, Rarity.COMMON):
                return CardRole.FILLER
            else:
                return CardRole.UNKNOWN

        max_weight = max(archetype_weights)

        # 根据权重阈值映射角色
        if max_weight >= 0.85:
            return CardRole.CORE
        elif max_weight >= 0.60:
            return CardRole.ENABLER
        elif max_weight >= 0.30:
            return CardRole.FILLER
        else:
            return CardRole.POLLUTION

    def _build_reasons(
        self,
        card: Card,
        role: CardRole,
        breakdown: ScoreBreakdown,
        matched_archetypes: list[str],
        run_state: RunState,
    ) -> tuple[list[str], list[str]]:
        """
        生成中文可解释理由。
        返回 (reasons_for, reasons_against)。
        """
        reasons_for: list[str] = []
        reasons_against: list[str] = []

        # 套路契合
        if matched_archetypes:
            archetype_names = [
                a.name
                for a in [self.library.get_archetype(aid) for aid in matched_archetypes]
                if a is not None
            ]
            reasons_for.append(f"契合套路：{', '.join(archetype_names)}")

        # 稀有度
        if breakdown.rarity_score >= 0.7:
            reasons_for.append(f"高稀有度（{card.rarity.value}），基础价值较高")

        # 套路完成度贡献
        if breakdown.completion_score > 0.05:
            pct = round(breakdown.completion_score * 100, 1)
            reasons_for.append(f"提升主套路完成度 +{pct}%")

        # 协同
        if breakdown.synergy_bonus > 0.0:
            reasons_for.append("与当前遗物或卡组存在协同")

        # 阶段适配
        if role == CardRole.TRANSITION and run_state.phase != GamePhase.EARLY:
            reasons_against.append(f"过渡卡在 {run_state.phase.value} 阶段价值下降")

        # 污染
        if role == CardRole.POLLUTION:
            reasons_against.append("该卡与当前套路无协同，会稀释牌组")

        # 无套路匹配
        if not matched_archetypes:
            reasons_against.append("未匹配任何已检测套路，当前 run 中价值不明")

        return reasons_for, reasons_against

    @staticmethod
    def _make_recommendation(total_score: float, role: CardRole) -> str:
        """根据分数和角色生成推荐语（与 scoring.py 分档对应）"""
        if role == CardRole.POLLUTION:
            return "跳过"
        if total_score >= 80:
            return "强烈推荐"
        elif total_score >= 65:
            return "推荐"
        elif total_score >= 50:
            return "可选"
        elif total_score >= 30:
            return "谨慎"
        else:
            return "跳过"

    @staticmethod
    def _extract_relic_tags(run_state: RunState) -> list[str]:
        """
        从当前遗物中提取协同标签（用于 synergy 计算）。
        TODO: 后续可建立遗物 -> tags 映射表
        """
        tags: list[str] = []
        for relic in run_state.relics:
            tags.extend(relic.tags)
        return tags

    @staticmethod
    def _save_score_log(results: list[EvaluationResult], run_state: RunState) -> None:
        """
        将评分细节写入 logs/score_YYYYMMDD_HHMMSS.json。
        每次调用 rank_cards 时生成一份。保留最近 30 份。
        """
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = _LOGS_DIR / f"score_{ts}.json"

            payload = {
                "timestamp": ts,
                "character": run_state.character.value if hasattr(run_state.character, "value") else str(run_state.character),
                "phase": run_state.phase.value if hasattr(run_state.phase, "value") else str(run_state.phase),
                "deck_size": len(run_state.deck),
                "relics": [r.id for r in run_state.relics],
                "results": [
                    {
                        "card_id": r.card_id,
                        "card_name": r.card_name,
                        "rarity": r.rarity,
                        "total_score": r.total_score,
                        "recommendation": r.recommendation,
                        "role": r.role.value if hasattr(r.role, "value") else str(r.role),
                        "matched_archetypes": r.matched_archetypes,
                        "breakdown": {
                            "value_score":        round(r.breakdown.base_score, 4),
                            "archetype_score":    round(r.breakdown.archetype_score, 4),
                            "phase_score":        round(r.breakdown.phase_score, 4),
                            "completion_score":   round(r.breakdown.completion_score, 4),
                            "synergy_bonus":      round(r.breakdown.synergy_bonus, 4),
                            "pollution_penalty":  round(r.breakdown.pollution_penalty, 4),
                            "bloat_penalty":      round(r.breakdown.rarity_score, 4),
                        },
                        "reasons_for": r.reasons_for,
                        "reasons_against": r.reasons_against,
                    }
                    for r in results
                ],
            }

            log_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log.info(f"评分日志已保存: {log_path.name}")

            # 只保留最新 30 份
            old_logs = sorted(_LOGS_DIR.glob("score_*.json"))
            for old in old_logs[:-30]:
                old.unlink(missing_ok=True)

        except Exception as e:
            log.warning(f"保存评分日志失败: {e}")
