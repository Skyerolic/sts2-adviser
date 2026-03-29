"""
backend/models.py
核心数据模型定义（基于 Pydantic v2）

模型层次：
  Card        —— 卡牌定义（静态元数据）
  Archetype   —— 套路定义（聚合多张卡的协同关系）
  RunState    —— 当前游戏状态快照（动态）
  EvaluationResult —— 单张卡的评估输出
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Character(str, Enum):
    IRONCLAD = "ironclad"
    SILENT   = "silent"
    DEFECT   = "defect"
    WATCHER  = "watcher"
    ANY      = "any"          # 色彩无关 / 通用卡
    COLORLESS = "colorless"   # 无色卡
    CURSE    = "curse"        # 诅咒
    STATUS   = "status"       # 状态
    EVENT    = "event"        # 事件
    REGENT   = "regent"       # 可能是新角色
    NECROBINDER = "necrobinder"  # 可能是新角色
    QUEST    = "quest"        # 任务？


class Rarity(str, Enum):
    STARTER = "starter"
    BASIC   = "basic"         # 基础卡
    COMMON  = "common"
    UNCOMMON = "uncommon"
    RARE    = "rare"
    SPECIAL = "special"       # 诅咒、状态等
    ANCIENT = "ancient"       # 古老卡
    CURSE   = "curse"         # 诅咒
    STATUS  = "status"        # 状态
    EVENT   = "event"         # 事件
    QUEST   = "quest"         # 任务


class CardType(str, Enum):
    ATTACK  = "attack"
    SKILL   = "skill"
    POWER   = "power"
    STATUS  = "status"
    CURSE   = "curse"
    QUEST   = "quest"         # 任务


class CardRole(str, Enum):
    """卡牌在当前 run 中的功能定位（评估器输出）"""
    CORE        = "core"        # 套路核心
    ENABLER     = "enabler"     # 使能卡（让核心生效的辅助）
    TRANSITION  = "transition"  # 过渡卡（前期强，后期换掉）
    FILLER      = "filler"      # 补件（有用但可替换）
    POLLUTION   = "pollution"   # 污染（降低 deck 质量）
    UNKNOWN     = "unknown"     # 尚未判断


class GamePhase(str, Enum):
    EARLY  = "early"   # floor 1–16
    MID    = "mid"     # floor 17–33
    LATE   = "late"    # floor 34+


# ---------------------------------------------------------------------------
# Card（卡牌静态定义）
# ---------------------------------------------------------------------------

class CardKeywords(BaseModel):
    """卡牌关键词标签，用于套路匹配"""
    exhaust:    bool = False
    innate:     bool = False
    retain:     bool = False
    ethereal:   bool = False
    scry:       bool = False
    # 扩展关键词（用字符串列表兜底）
    extras: list[str] = Field(default_factory=list)


class Card(BaseModel):
    """
    卡牌静态元数据。
    来源：Spire Codex / 手动维护的卡库 JSON。
    """
    id:          str             # 唯一标识，e.g. "silent_shiv"
    name:        str             # 显示名，e.g. "Shiv"
    character:   Character
    rarity:      Rarity
    card_type:   CardType
    cost:        int             # 能量消耗（-1 表示 X）
    upgraded:    bool = False    # 是否为升级版本

    # 文本描述（原始，不解析）
    description: str = ""

    # 数值快照（用于评分，后期可扩展为公式）
    base_damage:  Optional[int] = None
    base_block:   Optional[int] = None
    base_draw:    Optional[int] = None

    keywords: CardKeywords = Field(default_factory=CardKeywords)

    # 标签（用于套路匹配，e.g. ["shiv", "dexterity", "discard"]）
    tags: list[str] = Field(default_factory=list)

    class Config:
        frozen = True   # 卡牌定义不可变


# ---------------------------------------------------------------------------
# Archetype（套路定义）
# ---------------------------------------------------------------------------

class ArchetypeWeight(BaseModel):
    """某张卡在某套路中的权重与角色"""
    card_id: str
    role:    CardRole
    weight:  float = Field(ge=0.0, le=1.0)   # 0~1，越高越核心
    note:    str = ""


class Archetype(BaseModel):
    """
    套路定义。
    描述一种打法路线及其核心卡组成。
    """
    id:          str            # e.g. "silent_shiv"
    name:        str            # e.g. "Silent Shiv"
    character:   Character

    # 套路核心标签（用于快速匹配）
    key_tags:    list[str] = Field(default_factory=list)

    # 卡牌权重表（核心卡 / 使能卡）
    card_weights: list[ArchetypeWeight] = Field(default_factory=list)

    # 自然语言描述（用于解释输出）
    description: str = ""

    # 完整套路预估所需卡数（用于完成度计算）
    target_card_count: int = 12


# ---------------------------------------------------------------------------
# RunState（当前游戏状态快照）
# ---------------------------------------------------------------------------

class RelicInfo(BaseModel):
    id:   str
    name: str
    tags: list[str] = Field(default_factory=list)


class RunState(BaseModel):
    """
    当前 run 的实时状态快照。
    由数据桥接层注入，评估器只读此结构。
    """
    character:   Character
    floor:       int = Field(ge=0)
    hp:          int = Field(ge=0)
    max_hp:      int = Field(ge=1)
    gold:        int = Field(ge=0)

    # 已有牌组（card.id 列表，含升级后缀，如 "shiv+"）
    deck:        list[str] = Field(default_factory=list)

    # 已有遗物
    relics:      list[RelicInfo] = Field(default_factory=list)

    # 当前选卡池（本次选择的 3 张卡 id）
    card_choices: list[str] = Field(default_factory=list)

    @property
    def phase(self) -> GamePhase:
        if self.floor <= 16:
            return GamePhase.EARLY
        elif self.floor <= 33:
            return GamePhase.MID
        return GamePhase.LATE

    @property
    def hp_ratio(self) -> float:
        return self.hp / self.max_hp


# ---------------------------------------------------------------------------
# EvaluationResult（单张卡评估输出）
# ---------------------------------------------------------------------------

class ScoreBreakdown(BaseModel):
    """评分各维度拆解（便于解释）"""
    base_score:        float = 0.0   # 基础分（稀有度兜底，无套路时生效）
    rarity_score:      float = 0.0   # 稀有度加成
    archetype_score:   float = 0.0   # 套路契合度
    completion_score:  float = 0.0   # 套路完成度贡献
    phase_score:       float = 0.0   # 当前阶段适配度
    synergy_bonus:     float = 0.0   # 遗物 / 卡组协同加成
    pollution_penalty: float = 0.0   # 污染惩罚


class EvaluationResult(BaseModel):
    """
    对单张卡的综合评估输出。
    由 CardEvaluator 产生，传给前端显示。
    """
    card_id:    str
    card_name:  str
    rarity:     str = ""  # 卡牌稀有度

    total_score:    float               # 综合分（0~100）
    role:           CardRole
    breakdown:      ScoreBreakdown

    # 匹配到的套路（可能多个）
    matched_archetypes: list[str] = Field(default_factory=list)

    # 可解释理由（中文，直接显示给用户）
    reasons_for:    list[str] = Field(default_factory=list)   # 推荐理由
    reasons_against: list[str] = Field(default_factory=list)  # 不推荐理由

    # 最终建议
    recommendation: str = ""   # e.g. "强烈推荐" / "可选" / "跳过"
