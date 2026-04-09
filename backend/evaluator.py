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
from typing import Optional

from utils.paths import get_app_root

_LOGS_DIR = get_app_root() / "logs"
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
    ascension_modifier,
    Alignment,
    CrossValidationResult,
)


def score_to_grade(score: float) -> str:
    """将 0~100 的数字分转换为字母等级（仅展示用，不参与计算）。"""
    if score >= 90: return "S"
    if score >= 80: return "A+"
    if score >= 72: return "A"
    if score >= 65: return "A-"
    if score >= 58: return "B+"
    if score >= 50: return "B"
    if score >= 43: return "B-"
    if score >= 35: return "C+"
    if score >= 25: return "C"
    return "D"


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
        summaries_db: Optional[dict[str, dict]] = None,
    ) -> None:
        """
        card_db:      card_id -> Card（全卡库）
        library:      套路库（默认使用模块级单例）
        raw_card_db:  card_id -> 原始 JSON dict（含 powers_applied / keywords_key）
        community_db: card_id -> CommunityStats（社区统计数据，可选）
        summaries_db: card_id (大写) -> {summary_zh, tier, ...}（来自 card_summaries.json）
        """
        self.card_db = card_db
        self.library = library or archetype_library
        self.raw_card_db: dict[str, dict] = raw_card_db or {}
        self.community_db: dict = community_db or {}
        self.summaries_db: dict[str, dict] = summaries_db or {}

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def rank_cards(self, run_state: RunState, language: str = "zh") -> list[EvaluationResult]:
        """
        对 run_state.card_choices 中的所有候选卡进行评估并排序。
        返回按 total_score 降序排列的 EvaluationResult 列表。
        """
        log.debug(f"card_choices: {run_state.card_choices}")
        detected = self.detect_archetypes(run_state)
        relic_tags = self._extract_relic_tags(run_state)
        relic_boosts = self._build_relic_synergy(run_state, detected)

        results: list[EvaluationResult] = []
        for card_id in run_state.card_choices:
            card = self._resolve_card(card_id)
            if card is None:
                log.warning(f"Card not found in DB: {card_id}")
                continue
            log.debug(f"Evaluating card: {card_id} -> {card.name}")
            result = self.evaluate_card(card, run_state, detected, relic_tags, relic_boosts,
                                        language=language)
            results.append(result)

        log.debug(f"Evaluation results: {[r.card_name for r in results]}")
        results.sort(key=lambda r: r.total_score, reverse=True)
        self._save_score_log(results, run_state, detected)
        return results

    def detect_archetypes(self, run_state: RunState) -> list[Archetype]:
        """
        根据当前牌组，检测玩家正在走的套路。

        检测条件（同时满足）：
          1. 必须持有至少 1 张该套路定义的 CORE 牌（精确层）
          2. 整体完成度 >= _DETECT_THRESHOLD（防止只靠通用 filler 触发）

        过滤逻辑：
          - 按完成度降序排列
          - 只返回不超过 _MAX_ARCHETYPES 个套路
          - 第 2 个以后的套路，其完成度必须 >= 领先套路的 _SECONDARY_RATIO 倍
            （避免只有 1 张 filler 牌就并列检测出多个套路）
        """
        _DETECT_THRESHOLD   = 0.04   # 整体完成度最低门槛（主要靠 CORE 门槛过滤，此值仅排除极低噪音）
        _MAX_ARCHETYPES     = 2      # 最多同时检测几个套路
        _SECONDARY_RATIO    = 0.55   # 次选套路至少是领先套路完成度的 55%

        candidate_archetypes = self.library.get_by_character(run_state.character)
        deck_set = set(self._normalize_card_id(cid) for cid in run_state.deck)

        scored: list[tuple[float, Archetype]] = []
        for archetype in candidate_archetypes:
            # 门槛1：整体完成度
            completion = self._calc_completion(archetype, deck_set)
            if completion < _DETECT_THRESHOLD:
                continue

            # 门槛2：必须持有至少 1 张精确定义的 CORE 牌
            has_core = any(
                w.role.value == "core" and w.card_id.lower() in deck_set
                for w in archetype.card_weights
            )
            if not has_core:
                continue

            scored.append((completion, archetype))

        scored.sort(key=lambda x: x[0], reverse=True)

        # 过滤：次选套路完成度不能太低于领先套路
        if not scored:
            return []

        top_score = scored[0][0]
        result: list[Archetype] = []
        for completion, archetype in scored[:_MAX_ARCHETYPES]:
            if completion >= top_score * _SECONDARY_RATIO:
                result.append(archetype)

        return result

    def evaluate_card(
        self,
        card: Card,
        run_state: RunState,
        detected_archetypes: list[Archetype],
        relic_synergy_tags: list[str],
        relic_boosts: dict[str, float] | None = None,
        language: str = "zh",
    ) -> EvaluationResult:
        """
        对单张卡进行全维度评估，返回 EvaluationResult。
        """
        # 超出评分体系的卡牌：先古之民、诅咒等，直接返回统一提示
        _BEYOND_SCORING = {Rarity.ANCIENT, Rarity.CURSE}
        if card.rarity in _BEYOND_SCORING:
            _msg = "塔的意志深不可测" if language == "zh" else "The Tower's will is inscrutable"
            return EvaluationResult(
                card_id=card.id,
                card_name=card.name,
                total_score=0,
                grade="—",
                recommendation="—",
                role=card.rarity.value,  # "ancient" / "curse"
                reasons_for=[],
                reasons_against=[_msg],
                breakdown=ScoreBreakdown(),
            )

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
                                    inferred_only=not is_exact_match and bool(inferred_archetype_ids),
                                    deck_size=len(run_state.deck))

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
            phase_score=score_phase_dimension(card, run_state.phase, role, hp_ratio=run_state.hp_ratio),
            synergy_bonus=score_synergy_bonus(
                card, run_state, relic_synergy_tags,
                relic_boosts=relic_boosts or {},
                matched_archetype_ids=matched_archetype_ids,
            ),
            pollution_penalty=pollution_penalty(card, len(run_state.deck), role),
        )

        # 判断是否为"无套路早期过渡牌"场景（用于专属地板和社区权重提升）
        _is_transition_no_arch = (
            role == CardRole.TRANSITION
            and not matched_archetype_ids
        )
        _is_transition_early = (
            _is_transition_no_arch
            and run_state.phase.value == "early"
        )

        # algo_score（原有流程）
        algo_score_100 = combine_scores(
            breakdown, bloat_penalty=bloat_pen, role=role,
            is_transition_early=_is_transition_early,
        )

        # Ascension 修正（在社区交叉验证之前，基于算法分施加）
        asc_delta = ascension_modifier(role, run_state.ascension, breakdown.archetype_score)
        algo_score_100 = round(max(0.0, min(100.0, algo_score_100 + asc_delta)), 1)

        # 社区交叉验证（post-processing）
        algo_norm = algo_score_100 / 100.0
        cv_result = cross_validate(
            algo_norm, community_norm,
            is_transition_no_archetype=_is_transition_no_arch,
        )
        total = round(cv_result.blended_norm * 100, 1)

        # 5. 生成解释
        reasons_for, reasons_against = self._build_reasons(
            card, role, breakdown, matched_archetype_ids, run_state,
            inferred_ids=inferred_archetype_ids,
            community_stats=community_stats,
            cv_result=cv_result,
            algo_score=algo_score_100,
            language=language,
        )

        recommendation = self._make_recommendation(total, role, language=language)

        summary_entry = self.summaries_db.get(card.id.upper(), {})
        summary_zh = summary_entry.get("summary_zh", "")

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
            grade=score_to_grade(total),
            summary_zh=summary_zh,
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
        deck_size: int = 0,
    ) -> CardRole:
        """
        根据套路匹配结果推断卡牌在当前 run 中的角色。

        inferred_only: 若为 True，表示所有权重来自推断层（非手动定义），
                       最低角色保底为 FILLER，不判定为 POLLUTION。
        deck_size: 当前牌库大小，用于过渡牌判断。

        过渡牌规则（TRANSITION）：
          - 费用 ≤ 1（廉价，早期实用）
          - 仅 COMMON/UNCOMMON，排除 STATUS/CURSE 类型和套路有匹配的牌
          - 无套路命中 + 牌库 ≤ 15 张 → TRANSITION
          - 有套路匹配时一律 FILLER（不强行判过渡，避免伤害套路边缘牌）
        """
        from .models import Rarity, CardType

        # 过渡牌判断：不限费用——高费过渡牌（3费 Common Skill 等）同样有前期过渡价值。
        # 费用影响的是该牌在过渡期间的分数（phase_score + value_score 已处理），
        # 不应影响是否被识别为 TRANSITION。
        is_transition_eligible = (
            card.rarity in (Rarity.COMMON, Rarity.UNCOMMON, Rarity.RARE)
            and card.card_type not in (CardType.STATUS, CardType.CURSE)
        )

        if not detected_archetypes or not archetype_weights:
            # 未匹配任何套路
            if is_transition_eligible and deck_size <= 15:
                return CardRole.TRANSITION
            return CardRole.FILLER

        max_weight = max(archetype_weights)

        # 根据权重阈值映射角色（有套路匹配时不判 TRANSITION）
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
        language: str = "zh",
    ) -> tuple[list[str], list[str]]:
        """生成可解释理由，支持中英文。返回 (reasons_for, reasons_against)。"""
        zh = (language == "zh")
        inferred_ids = inferred_ids or []
        reasons_for: list[str] = []
        reasons_against: list[str] = []

        # 套路契合：区分精确层和推断层
        exact_ids = [aid for aid in matched_archetypes if aid not in inferred_ids]
        if exact_ids:
            archetype_names = [
                a.name for a in [self.library.get_archetype(aid) for aid in exact_ids]
                if a is not None
            ]
            names = ", ".join(archetype_names)
            reasons_for.append(f"契合套路：{names}" if zh else f"Fits archetype: {names}")
        if inferred_ids:
            inferred_names = [
                a.name for a in [self.library.get_archetype(aid) for aid in inferred_ids]
                if a is not None
            ]
            names = ", ".join(inferred_names)
            reasons_for.append(
                f"推断与套路相关（关键词匹配）：{names}" if zh
                else f"Inferred archetype match (keyword): {names}"
            )

        # 稀有度
        if card.rarity == Rarity.RARE:
            reasons_for.append(
                f"高稀有度（Rare），基础价值较高" if zh
                else "High rarity (Rare), strong base value"
            )

        # 套路完成度贡献
        if breakdown.completion_score > 0.05:
            pct = round(breakdown.completion_score * 100, 1)
            reasons_for.append(
                f"提升主套路完成度 +{pct}%" if zh
                else f"Advances archetype completion +{pct}%"
            )

        # 协同
        if breakdown.synergy_bonus > 0.0:
            reasons_for.append(
                "与当前遗物或卡组存在协同" if zh
                else "Synergizes with current relics or deck"
            )

        # 阶段适配（过渡牌）
        if role == CardRole.TRANSITION:
            if run_state.phase == GamePhase.EARLY:
                reasons_for.append(
                    "早期过渡牌：费用低、见效快，牌库尚小时性价比高" if zh
                    else "Early transition card: cheap and effective while deck is small"
                )
            else:
                reasons_against.append(
                    "过渡牌：套路成型后价值下降，可考虑替换" if zh
                    else "Transition card: value drops as archetype develops — consider replacing later"
                )

        # 污染
        if role == CardRole.POLLUTION:
            reasons_against.append(
                "该卡与当前套路无协同，会稀释牌组" if zh
                else "No synergy with current archetype; dilutes the deck"
            )

        # 仅推断匹配
        if matched_archetypes and not exact_ids and inferred_ids:
            reasons_against.append(
                "仅关键词推断匹配，实际价值以游戏判断为准" if zh
                else "Keyword inference only; verify value in-game"
            )

        # 无任何匹配
        if not matched_archetypes:
            reasons_against.append(
                "未匹配任何已检测套路，当前 run 中价值不明" if zh
                else "No archetype match detected; value unclear for this run"
            )

        # 社区数据理由
        if cv_result is not None:
            if cv_result.has_community_data and community_stats is not None:
                wr = f"{community_stats.win_rate_pct:.1f}%"
                pr = f"{community_stats.pick_rate_pct:.1f}%"
                cs = cv_result.community_score

                if cv_result.alignment == Alignment.AGREEMENT:
                    if cs >= 0.70:
                        reasons_for.append(
                            f"社区数据支持：胜率 {wr}，选取率 {pr}，与算法评估一致" if zh
                            else f"Community data agrees: win rate {wr}, pick rate {pr}"
                        )
                    elif cs <= 0.35:
                        reasons_against.append(
                            f"社区数据警示：胜率 {wr}，选取率 {pr}，玩家普遍跳过此卡" if zh
                            else f"Community warning: win rate {wr}, pick rate {pr} — widely skipped"
                        )
                elif cv_result.alignment == Alignment.SOFT_CONFLICT:
                    reasons_against.append(
                        f"社区数据与算法存在分歧（差值 {cv_result.delta:.0%}），评分已折中处理" if zh
                        else f"Community data diverges from algorithm (delta {cv_result.delta:.0%}); blended"
                    )
                elif cv_result.alignment == Alignment.CONFLICT:
                    if algo_score / 100.0 > cv_result.community_score:
                        reasons_against.append(
                            f"社区数据与算法显著分歧：社区胜率 {wr} 较低，建议参考套路情况" if zh
                            else f"Significant conflict: community win rate {wr} is low vs algorithm score"
                        )
                    else:
                        reasons_for.append(
                            f"社区数据提示潜力被低估：胜率 {wr} 较高，算法评分偏低" if zh
                            else f"Community data suggests undervalued: win rate {wr} exceeds algorithm score"
                        )
            elif not cv_result.has_community_data and 40 <= algo_score <= 65:
                reasons_against.append(
                    "缺少社区统计数据，评分仅基于算法判断" if zh
                    else "No community data; score is algorithm-only"
                )

        return reasons_for, reasons_against

    @staticmethod
    def _make_recommendation(total_score: float, role: CardRole, language: str = "zh") -> str:
        """根据分数和角色生成推荐语（与 scoring.py 分档对应）"""
        zh = (language == "zh")
        if role == CardRole.POLLUTION:
            return "跳过" if zh else "Skip"
        if total_score >= 80:
            return "强烈推荐" if zh else "Highly Recommended"
        elif total_score >= 65:
            return "推荐" if zh else "Recommended"
        elif total_score >= 50:
            return "可选" if zh else "Viable"
        elif total_score >= 30:
            return "谨慎" if zh else "Caution"
        else:
            return "跳过" if zh else "Skip"

    @staticmethod
    def _build_relic_synergy(
        run_state: RunState,
        detected_archetypes: list,
    ) -> dict[str, float]:
        """
        根据当前持有遗物和已检测套路，构建遗物→套路 boost 映射。
        返回 {archetype_id: max_boost_score}。
        只返回已检测到的套路的 boost，避免未走的套路被误激活。
        """
        from .relic_archetype_map import RELIC_ARCHETYPE_MAP
        detected_ids = {a.id for a in detected_archetypes}
        boosts: dict[str, float] = {}
        for relic in run_state.relics:
            relic_key = relic.id.upper()
            for archetype_id, score in RELIC_ARCHETYPE_MAP.get(relic_key, []):
                if archetype_id in detected_ids:
                    boosts[archetype_id] = max(boosts.get(archetype_id, 0.0), score)
        return boosts

    @staticmethod
    def _extract_relic_tags(run_state: RunState) -> list[str]:
        """旧接口保留：返回 tags 字段（当前始终为空列表）。"""
        tags: list[str] = []
        for relic in run_state.relics:
            tags.extend(relic.tags)
        return tags

    @staticmethod
    def _save_score_log(
        results: list[EvaluationResult],
        run_state: RunState,
        detected_archetypes: list | None = None,
    ) -> None:
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
                "ascension": run_state.ascension,
                "floor": run_state.floor,
                "deck_size": len(run_state.deck),
                "deck": run_state.deck,
                "detected_archetypes": [a.id for a in (detected_archetypes or [])],
                "relics": [r.id for r in run_state.relics],
                "results": [
                    {
                        "card_id": r.card_id,
                        "card_name": r.card_name,
                        "rarity": r.rarity,
                        "total_score": r.total_score,
                        "grade": r.grade,
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
