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
from .archetype_inference import infer_weight
from .models import (
    Card, Archetype, CardRole, GamePhase, Rarity,
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
    cross_validate,
    Alignment,
    CrossValidationResult,
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
        raw_card_db: Optional[dict[str, dict]] = None,
        community_db: Optional[dict] = None,
    ) -> None:
        """
        card_db:      card_id -> Card（全卡库）
        library:      套路库（默认使用模块级单例）
        raw_card_db:  card_id -> 原始 JSON dict（含 powers_applied / keywords_key）
        community_db: card_id -> CommunityStats（社区统计数据，可选）
        """
        self.card_db = card_db
        self.library = library or archetype_library
        self.raw_card_db: dict[str, dict] = raw_card_db or {}
        self.community_db: dict = community_db or {}

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
        # 精确层：手动 card_weights 定义（权重 0.40~0.98）
        # 推断层：基于 powers_applied / keywords / desc 自动推断（上限 0.35）
        archetype_weights: list[float] = []
        matched_archetype_ids: list[str] = []
        inferred_archetype_ids: list[str] = []   # 仅推断层命中（用于日志区分）
        is_exact_match = False  # 是否有精确层命中

        raw = self.raw_card_db.get(card.id)      # 原始 JSON dict（可能为 None）

        for archetype in detected_archetypes:
            weight_info = self.library.get_card_weight(archetype.id, card.id)
            if weight_info:
                # 精确层命中
                archetype_weights.append(weight_info.weight)
                matched_archetype_ids.append(archetype.id)
                is_exact_match = True
            elif raw is not None:
                # 推断层兜底
                inferred_w = infer_weight(raw, archetype.id)
                if inferred_w > 0.0:
                    archetype_weights.append(inferred_w)
                    matched_archetype_ids.append(archetype.id)
                    inferred_archetype_ids.append(archetype.id)
                    log.debug(
                        f"推断权重 {card.id} → {archetype.id}: {inferred_w:.2f}"
                    )

        # 2. 确定卡牌角色
        # 推断层命中的卡最低为 FILLER，不判定为 POLLUTION（推断层设计上限 0.35 < 精确层最低 0.40）
        role = self._determine_role(card, detected_archetypes, archetype_weights,
                                    inferred_only=not is_exact_match and bool(inferred_archetype_ids))

        # 3. 计算套路完成度贡献
        comp_before = 0.0
        comp_after = 0.0
        if detected_archetypes:
            primary = detected_archetypes[0]
            comp_before = self._calc_completion(primary, deck_set)
            new_deck = deck_set | {card.id}
            comp_after = self._calc_completion(primary, new_deck)

        # 4. 各维度评分
        # v0.7: bloat_penalty 显式计算；rarity_score 字段改存 community_score
        bloat_pen = deck_bloat_penalty(card, len(run_state.deck), role)

        # 查社区数据
        community_stats = self.community_db.get(card.id)
        community_norm: Optional[float] = (
            community_stats.community_score if community_stats is not None else None
        )

        breakdown = ScoreBreakdown(
            base_score=score_base_dimension(card, run_state.phase),
            rarity_score=community_norm if community_norm is not None else 0.0,  # community_score
            archetype_score=score_archetype_dimension(card, archetype_weights),
            completion_score=score_completion_dimension(comp_before, comp_after),
            phase_score=score_phase_dimension(card, run_state.phase, role),
            synergy_bonus=score_synergy_bonus(card, run_state, relic_synergy_tags),
            pollution_penalty=pollution_penalty(card, len(run_state.deck), role),
        )

        # algo_score（原有流程）
        algo_score_100 = combine_scores(breakdown, bloat_penalty=bloat_pen)

        # 社区交叉验证（post-processing）
        algo_norm = algo_score_100 / 100.0
        cv_result = cross_validate(algo_norm, community_norm)
        total = round(cv_result.blended_norm * 100, 1)

        # 5. 生成解释
        reasons_for, reasons_against = self._build_reasons(
            card, role, breakdown, matched_archetype_ids, run_state,
            inferred_ids=inferred_archetype_ids,
            community_stats=community_stats,
            cv_result=cv_result,
            algo_score=algo_score_100,
        )

        recommendation = self._make_recommendation(total, role)

        return EvaluationResult(
            card_id=card.id,
            card_name=card.name,
            rarity=card.rarity.value,
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
        inferred_only: bool = False,
    ) -> CardRole:
        """
        根据套路匹配结果推断卡牌在当前 run 中的角色。

        inferred_only: 若为 True，表示所有权重来自推断层（非手动定义），
                       最低角色保底为 FILLER，不判定为 POLLUTION。
        """
        if not detected_archetypes or not archetype_weights:
            # 未匹配任何套路 → 按稀有度做保守判断
            from .models import Rarity
            if card.rarity in (Rarity.RARE, Rarity.ANCIENT):
                return CardRole.FILLER
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
            # 精确层明确标注 pollution（如 weight=0.15）→ 保留判断
            # 推断层低权重（如 attack 类型命中 LOW 规则 0.10）→ 保底 FILLER
            if inferred_only:
                return CardRole.FILLER
            return CardRole.POLLUTION

    def _build_reasons(
        self,
        card: Card,
        role: CardRole,
        breakdown: ScoreBreakdown,
        matched_archetypes: list[str],
        run_state: RunState,
        inferred_ids: Optional[list[str]] = None,
        community_stats=None,
        cv_result: Optional[CrossValidationResult] = None,
        algo_score: float = 0.0,
    ) -> tuple[list[str], list[str]]:
        """
        生成中文可解释理由。
        返回 (reasons_for, reasons_against)。
        """
        inferred_ids = inferred_ids or []
        reasons_for: list[str] = []
        reasons_against: list[str] = []

        # 套路契合：区分精确层和推断层
        exact_ids = [aid for aid in matched_archetypes if aid not in inferred_ids]
        if exact_ids:
            archetype_names = [
                a.name
                for a in [self.library.get_archetype(aid) for aid in exact_ids]
                if a is not None
            ]
            reasons_for.append(f"契合套路：{', '.join(archetype_names)}")
        if inferred_ids:
            inferred_names = [
                a.name
                for a in [self.library.get_archetype(aid) for aid in inferred_ids]
                if a is not None
            ]
            reasons_for.append(f"推断与套路相关（关键词匹配）：{', '.join(inferred_names)}")

        # 稀有度（直接读 card.rarity，不依赖 breakdown.rarity_score）
        if card.rarity in (Rarity.RARE, Rarity.ANCIENT):
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

        # 仅推断匹配时，补充置信度说明
        if matched_archetypes and not exact_ids and inferred_ids:
            reasons_against.append("仅关键词推断匹配，非手动定义的套路核心卡，实际价值以游戏判断为准")

        # 无任何匹配
        if not matched_archetypes:
            reasons_against.append("未匹配任何已检测套路，当前 run 中价值不明")

        # 社区数据理由
        if cv_result is not None:
            if cv_result.has_community_data and community_stats is not None:
                wr = f"{community_stats.win_rate_pct:.1f}%"
                pr = f"{community_stats.pick_rate_pct:.1f}%"
                cs = cv_result.community_score

                if cv_result.alignment == Alignment.AGREEMENT:
                    if cs >= 0.70:
                        reasons_for.append(
                            f"社区数据支持：胜率 {wr}，选取率 {pr}，与算法评估一致"
                        )
                    elif cs <= 0.35:
                        reasons_against.append(
                            f"社区数据警示：胜率 {wr}，选取率 {pr}，玩家普遍跳过此卡"
                        )
                elif cv_result.alignment == Alignment.SOFT_CONFLICT:
                    reasons_against.append(
                        f"社区数据与算法存在分歧（差值 {cv_result.delta:.0%}），评分已折中处理"
                    )
                elif cv_result.alignment == Alignment.CONFLICT:
                    if algo_score / 100.0 > cv_result.community_score:
                        reasons_against.append(
                            f"社区数据与算法显著分歧：算法评分偏高，但社区胜率 {wr} 较低，建议参考套路情况"
                        )
                    else:
                        reasons_for.append(
                            f"社区数据提示潜力被低估：胜率 {wr} 较高，算法评分偏低"
                        )
            elif not cv_result.has_community_data and 40 <= algo_score <= 65:
                reasons_against.append("缺少社区统计数据，评分仅基于算法判断")

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
                            "community_score":     round(r.breakdown.rarity_score, 4),
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
