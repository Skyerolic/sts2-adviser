"""
backend/archetypes.py
套路库（ArchetypeLibrary）

职责：
  - 维护所有已知套路的静态定义
  - 提供按 character / tag 的查询接口
  - 支持从外部 JSON 文件热加载（便于后续扩展）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import Archetype, ArchetypeWeight, CardRole, Character


# ---------------------------------------------------------------------------
# 内置示例套路数据
# ---------------------------------------------------------------------------

_BUILTIN_ARCHETYPES: list[dict] = [

    # =========================================================
    # IRONCLAD — 3 builds (source: StratGG JSON v0.1)
    # =========================================================
    {
        "id": "ironclad_strength",
        "name": "Ironclad: Strength",
        "character": "ironclad",
        "key_tags": ["strength", "scaling", "attack_based", "needs_setup"],
        "description": "通过永久叠加力量（Inflame、Demon Form、Rupture）放大攻击伤害。",
        "target_card_count": 14,
        "card_weights": [
            # core
            {"card_id": "INFLAME",          "role": "core",    "weight": 0.95, "note": "即时+2力量，核心来源"},
            {"card_id": "DEMON_FORM",       "role": "core",    "weight": 0.92, "note": "每回合+2力量，后期引擎"},
            {"card_id": "RUPTURE",          "role": "core",    "weight": 0.88, "note": "HP损耗转永久力量"},
            {"card_id": "DOMINATE",         "role": "core",    "weight": 0.88, "note": "施加易伤+每层易伤获得力量，直接力量来源"},
            # support
            {"card_id": "BATTLE_TRANCE",    "role": "enabler", "weight": 0.75, "note": "摸牌引擎"},
            {"card_id": "OFFERING",         "role": "enabler", "weight": 0.72, "note": "HP换费用+摸牌"},
            {"card_id": "ONE_TWO_PUNCH",    "role": "enabler", "weight": 0.68, "note": "双段攻击力量倍化"},
            {"card_id": "TREMBLE",          "role": "enabler", "weight": 0.62, "note": "0费施加脆弱"},
            {"card_id": "TAUNT",            "role": "enabler", "weight": 0.60, "note": "嘲讽+格挡"},
            {"card_id": "VICIOUS",          "role": "enabler", "weight": 0.58, "note": "追加攻击段数"},
            # bridge
            {"card_id": "SHRUG_IT_OFF",     "role": "filler",  "weight": 0.50, "note": "防御+摸牌"},
            {"card_id": "ARMAMENTS",        "role": "filler",  "weight": 0.45, "note": "升级手牌"},
            {"card_id": "INFERNAL_BLADE",   "role": "filler",  "weight": 0.42, "note": "生成随机攻击牌"},
            # finisher
            {"card_id": "PACTS_END",        "role": "core",    "weight": 0.80, "note": "排除堆终结技"},
            # anti-synergy as pollution
            {"card_id": "TRUE_GRIT",        "role": "pollution","weight": 0.15, "note": "排除核心牌风险"},
            {"card_id": "SECOND_WIND",      "role": "pollution","weight": 0.15, "note": "排除技能牌减薄力量套"},
        ],
    },
    {
        "id": "ironclad_self_damage",
        "name": "Ironclad: Self Damage",
        "character": "ironclad",
        "key_tags": ["self_damage", "high_risk", "resource_conversion", "needs_survivability"],
        "description": "主动承受伤害换取能量、牌张和力量，靠Rupture/Inferno转化收益。",
        "target_card_count": 12,
        "card_weights": [
            # core
            {"card_id": "RUPTURE",          "role": "core",    "weight": 0.95, "note": "HP损耗转永久力量"},
            {"card_id": "BLOODLETTING",     "role": "core",    "weight": 0.90, "note": "主动HP损耗+费用"},
            {"card_id": "OFFERING",         "role": "core",    "weight": 0.88, "note": "HP换费+摸牌"},
            {"card_id": "INFERNO",          "role": "core",    "weight": 0.85, "note": "HP损耗转全体AoE"},
            # support
            {"card_id": "BRAND",            "role": "enabler", "weight": 0.78, "note": "减薄+Rupture触发"},
            {"card_id": "CRIMSON_MANTLE",   "role": "enabler", "weight": 0.75, "note": "两张Rupture指数叠加"},
            {"card_id": "SPITE",            "role": "enabler", "weight": 0.72, "note": "自伤触发多次攻击，配合Rupture/Bloodletting"},
            {"card_id": "DEMONIC_SHIELD",   "role": "enabler", "weight": 0.68, "note": "格挡辅助存活"},
            {"card_id": "BATTLE_TRANCE",    "role": "enabler", "weight": 0.65, "note": "摸牌引擎"},
            {"card_id": "SHRUG_IT_OFF",     "role": "enabler", "weight": 0.60, "note": "防御+摸牌"},
            # bridge
            {"card_id": "BLOOD_WALL",       "role": "filler",  "weight": 0.50, "note": "HP转格挡过渡"},
            {"card_id": "ARMAMENTS",        "role": "filler",  "weight": 0.45, "note": "升级手牌"},
            {"card_id": "FLAME_BARRIER",    "role": "filler",  "weight": 0.42, "note": "反伤格挡"},
            # finisher
            {"card_id": "DEMON_FORM",       "role": "core",    "weight": 0.82, "note": "后期力量递增终结"},
            {"card_id": "PACTS_END",        "role": "enabler", "weight": 0.70, "note": "排除堆终结"},
            # pollution
            {"card_id": "TANK",             "role": "pollution","weight": 0.10, "note": "阻止HP损耗触发"},
        ],
    },
    {
        "id": "ironclad_exhaust",
        "name": "Ironclad: Exhaust Engine",
        "character": "ironclad",
        "key_tags": ["exhaust", "engine", "deck_thinning", "combo"],
        "description": "Corruption让技能牌0费排除，Dark Embrace/Feel No Pain形成摸牌+格挡循环。",
        "target_card_count": 13,
        "card_weights": [
            # core
            {"card_id": "CORRUPTION",       "role": "core",    "weight": 0.98, "note": "技能牌0费+排除，引擎核心"},
            {"card_id": "DARK_EMBRACE",     "role": "core",    "weight": 0.95, "note": "每排除摸1张"},
            {"card_id": "FEEL_NO_PAIN",     "role": "core",    "weight": 0.92, "note": "每排除获得格挡"},
            {"card_id": "BURNING_PACT",     "role": "core",    "weight": 0.85, "note": "排除换摸牌"},
            {"card_id": "SECOND_WIND",      "role": "core",    "weight": 0.82, "note": "大量技能牌时价值高"},
            {"card_id": "TRUE_GRIT",        "role": "core",    "weight": 0.80, "note": "主动排除+格挡"},
            # support
            {"card_id": "HAVOC",            "role": "enabler", "weight": 0.70, "note": "排除顶牌获取效果"},
            {"card_id": "FORGOTTEN_RITUAL", "role": "enabler", "weight": 0.65, "note": "排除触发生成"},
            {"card_id": "STOKE",            "role": "core",    "weight": 0.78, "note": "排除手牌生成升级牌，引擎核心"},
            {"card_id": "NOT_YET",          "role": "enabler", "weight": 0.68, "note": "Exhaust+恢复HP，Corruption流存活保障"},
            {"card_id": "DRUM_OF_BATTLE",   "role": "enabler", "weight": 0.60, "note": "多段攻击辅助"},
            {"card_id": "SHRUG_IT_OFF",     "role": "enabler", "weight": 0.58, "note": "技能牌，Corruption下0费"},
            # bridge
            {"card_id": "ARMAMENTS",        "role": "filler",  "weight": 0.50, "note": "升级手牌"},
            {"card_id": "BATTLE_TRANCE",    "role": "filler",  "weight": 0.48, "note": "摸牌补充"},
            {"card_id": "OFFERING",         "role": "filler",  "weight": 0.45, "note": "HP换费用过渡"},
            # finisher
            {"card_id": "PACTS_END",        "role": "core",    "weight": 0.90, "note": "排除堆终结技"},
            {"card_id": "JUGGERNAUT",       "role": "enabler", "weight": 0.75, "note": "格挡转伤害终结"},
            # pollution
            {"card_id": "DEMON_FORM",       "role": "pollution","weight": 0.15, "note": "与Corruption核心机制冲突"},
        ],
    },

    # =========================================================
    # SILENT — 3 builds (source: StratGG JSON v0.1)
    # =========================================================
    {
        "id": "silent_poison",
        "name": "Silent: Poison",
        "character": "silent",
        "key_tags": ["poison", "dot", "scaling", "control_friendly"],
        "description": "持续叠加毒素并用触发/倍化机制放大毒伤，辅以防御撑到毒素起效。",
        "target_card_count": 13,
        "card_weights": [
            # core
            {"card_id": "DEADLY_POISON",    "role": "core",    "weight": 0.95, "note": "高效叠5毒"},
            {"card_id": "POISONED_STAB",    "role": "core",    "weight": 0.90, "note": "攻击+叠毒"},
            {"card_id": "NOXIOUS_FUMES",    "role": "core",    "weight": 0.88, "note": "每回合被动叠毒"},
            {"card_id": "OUTBREAK",         "role": "core",    "weight": 0.85, "note": "毒扩散全体"},
            # support
            {"card_id": "MIRAGE",           "role": "enabler", "weight": 0.75, "note": "复制技能牌效果"},
            {"card_id": "EXPOSE",           "role": "enabler", "weight": 0.70, "note": "施加易伤放大毒伤"},
            {"card_id": "LEG_SWEEP",        "role": "enabler", "weight": 0.68, "note": "削弱+格挡"},
            {"card_id": "BACKFLIP",         "role": "enabler", "weight": 0.62, "note": "防御+摸牌"},
            {"card_id": "NIGHTMARE",        "role": "enabler", "weight": 0.60, "note": "复制毒牌终结"},
            # bridge
            {"card_id": "PREPARED",         "role": "filler",  "weight": 0.50, "note": "0费摸牌循环"},
            {"card_id": "ACROBATICS",       "role": "filler",  "weight": 0.48, "note": "摸牌循环"},
            {"card_id": "PIERCING_WAIL",    "role": "filler",  "weight": 0.42, "note": "全体削弱"},
            # finisher
            {"card_id": "TRACKING",         "role": "core",    "weight": 0.80, "note": "毒伤终结"},
            # anti-synergy as pollution
            {"card_id": "BLADE_DANCE",      "role": "pollution","weight": 0.15, "note": "Shiv方向与毒无协同"},
            {"card_id": "ACCURACY",         "role": "pollution","weight": 0.15, "note": "仅强化Shiv，毒套无用"},
            {"card_id": "PHANTOM_BLADES",   "role": "pollution","weight": 0.15, "note": "Shiv专用，稀释毒套"},
        ],
    },
    {
        "id": "silent_shiv",
        "name": "Silent: Shiv",
        "character": "silent",
        "key_tags": ["shiv", "high_apm", "attack_volume", "needs_payoffs"],
        "description": "批量生成Shiv并用Accuracy/Infinite Blades放大伤害，高频出牌触发Afterimage防御。",
        "target_card_count": 13,
        "card_weights": [
            # core
            {"card_id": "BLADE_DANCE",      "role": "core",    "weight": 0.95, "note": "批量生成3 Shiv"},
            {"card_id": "ACCURACY",         "role": "core",    "weight": 0.92, "note": "每张Shiv+伤害，倍化器"},
            {"card_id": "INFINITE_BLADES",  "role": "core",    "weight": 0.90, "note": "每回合生成1 Shiv"},
            {"card_id": "PHANTOM_BLADES",   "role": "core",    "weight": 0.85, "note": "Shiv打全体"},
            # support
            {"card_id": "BLADE_OF_INK",     "role": "enabler", "weight": 0.80, "note": "生成Inky Shiv（+2伤+1虚弱），Shiv流量产核心"},
            {"card_id": "FOLLOW_THROUGH",   "role": "filler",  "weight": 0.55, "note": "5张手牌阈值触发追击，Shiv流次选"},
            {"card_id": "BACKFLIP",         "role": "enabler", "weight": 0.70, "note": "防御+摸牌"},
            {"card_id": "PREPARED",         "role": "enabler", "weight": 0.68, "note": "0费循环"},
            {"card_id": "ADRENALINE",       "role": "enabler", "weight": 0.65, "note": "摸牌+费用"},
            {"card_id": "UNTOUCHABLE",      "role": "enabler", "weight": 0.62, "note": "弃牌获格挡"},
            # bridge
            {"card_id": "ACROBATICS",       "role": "filler",  "weight": 0.50, "note": "手牌循环"},
            {"card_id": "HAND_TRICK",       "role": "filler",  "weight": 0.45, "note": "Shiv辅助过渡"},
            {"card_id": "POUNCE",           "role": "filler",  "weight": 0.40, "note": "轻量攻击补充"},
            # finisher
            {"card_id": "TRACKING",         "role": "enabler", "weight": 0.72, "note": "终结技"},
            {"card_id": "MEMENTO_MORI",     "role": "enabler", "weight": 0.68, "note": "计数终结"},
            # anti-synergy
            {"card_id": "NOXIOUS_FUMES",    "role": "pollution","weight": 0.15, "note": "毒方向分散资源"},
            {"card_id": "DEADLY_POISON",    "role": "pollution","weight": 0.15, "note": "毒方向分散资源"},
            {"card_id": "OUTBREAK",         "role": "pollution","weight": 0.15, "note": "毒方向分散资源"},
        ],
    },
    {
        "id": "silent_sly_discard",
        "name": "Silent: Sly / Discard",
        "character": "silent",
        "key_tags": ["sly", "discard", "engine", "hand_filtering", "combo", "resource_loop"],
        "description": "利用Sly关键词让弃牌触发效果，Tactician弃牌恢复费用，高速循环薄牌组。",
        "target_card_count": 12,
        "card_weights": [
            # core
            {"card_id": "ACROBATICS",       "role": "core",    "weight": 0.95, "note": "摸牌循环引擎"},
            {"card_id": "PREPARED",         "role": "core",    "weight": 0.92, "note": "0费摸1弃1"},
            {"card_id": "REFLEX",           "role": "core",    "weight": 0.90, "note": "弃牌摸2张"},
            {"card_id": "TOOLS_OF_THE_TRADE","role": "core",   "weight": 0.88, "note": "每回合免费摸1弃1"},
            {"card_id": "HAND_TRICK",       "role": "core",    "weight": 0.82, "note": "Sly触发辅助"},
            {"card_id": "UNTOUCHABLE",      "role": "core",    "weight": 0.80, "note": "弃牌获格挡"},
            # support
            {"card_id": "ADRENALINE",       "role": "enabler", "weight": 0.72, "note": "摸牌+费用"},
            {"card_id": "BACKFLIP",         "role": "enabler", "weight": 0.68, "note": "防御+摸牌"},
            {"card_id": "PINPOINT",         "role": "enabler", "weight": 0.62, "note": "弃牌触发加成"},
            {"card_id": "POUNCE",           "role": "enabler", "weight": 0.58, "note": "轻量攻击补充"},
            {"card_id": "MEMENTO_MORI",     "role": "enabler", "weight": 0.55, "note": "弃牌终结"},
            {"card_id": "FOLLOW_THROUGH",   "role": "enabler", "weight": 0.68, "note": "5张手牌阈值与Sly/弃牌手牌循环契合"},
            # bridge
            {"card_id": "BLADE_DANCE",      "role": "filler",  "weight": 0.45, "note": "攻击补充"},
            {"card_id": "LEG_SWEEP",        "role": "filler",  "weight": 0.42, "note": "削弱过渡"},
            {"card_id": "PIERCING_WAIL",    "role": "filler",  "weight": 0.38, "note": "全体削弱过渡"},
            # finisher
            {"card_id": "MASTER_PLANNER",   "role": "core",    "weight": 0.85, "note": "所有技能获得Sly"},
            {"card_id": "NIGHTMARE",        "role": "enabler", "weight": 0.65, "note": "复制关键牌终结"},
            # anti-synergy
            {"card_id": "NOXIOUS_FUMES",    "role": "pollution","weight": 0.15, "note": "毒套方向，分散资源"},
        ],
    },

    # =========================================================
    # DEFECT — 4 builds (source: StratGG JSON v0.1)
    # =========================================================
    {
        "id": "defect_orb_focus",
        "name": "Defect: Orb / Focus",
        "character": "defect",
        "key_tags": ["orb", "focus", "engine", "scaling", "stable"],
        "description": "叠加Focus提升所有Orb效果，Capacitor扩槽实现更多被动，Multi-Cast爆发终结。",
        "target_card_count": 14,
        "card_weights": [
            # core
            {"card_id": "DEFRAGMENT",       "role": "core",    "weight": 0.95, "note": "永久Focus，Orb效果倍化"},
            {"card_id": "LOOP",             "role": "core",    "weight": 0.92, "note": "Orb被动额外触发"},
            {"card_id": "CAPACITOR",        "role": "core",    "weight": 0.90, "note": "增加Orb槽位"},
            {"card_id": "GLACIER",          "role": "core",    "weight": 0.88, "note": "格挡+双Frost通道"},
            {"card_id": "COOLHEADED",       "role": "core",    "weight": 0.85, "note": "摸牌+Frost通道"},
            {"card_id": "DUALCAST",         "role": "core",    "weight": 0.82, "note": "双次触发Orb"},
            # support
            {"card_id": "BALL_LIGHTNING",   "role": "enabler", "weight": 0.72, "note": "通道Lightning+伤害"},
            {"card_id": "COLD_SNAP",        "role": "enabler", "weight": 0.68, "note": "通道Frost"},
            {"card_id": "RAINBOW",          "role": "enabler", "weight": 0.65, "note": "通道三色Orb"},
            {"card_id": "CHARGE_BATTERY",   "role": "enabler", "weight": 0.62, "note": "通道Lightning+格挡"},
            {"card_id": "SKIM",             "role": "enabler", "weight": 0.60, "note": "摸牌"},
            {"card_id": "COMPILE_DRIVER",   "role": "enabler", "weight": 0.58, "note": "根据Orb种类摸牌"},
            {"card_id": "CHAOS",            "role": "enabler", "weight": 0.55, "note": "随机Orb通道"},
            # bridge
            {"card_id": "BOOT_SEQUENCE",    "role": "filler",  "weight": 0.50, "note": "前期格挡过渡"},
            {"card_id": "LEAP",             "role": "filler",  "weight": 0.45, "note": "0费格挡"},
            {"card_id": "HOLOGRAM",         "role": "filler",  "weight": 0.42, "note": "弃牌堆取回特定牌"},
            # finisher
            {"card_id": "BIASED_COGNITION", "role": "core",    "weight": 0.85, "note": "大量Focus，后期关键"},
            {"card_id": "ELECTRODYNAMICS",  "role": "enabler", "weight": 0.72, "note": "Lightning打全体"},
            {"card_id": "SHATTER",          "role": "enabler", "weight": 0.68, "note": "Orb终结技"},
            {"card_id": "MULTI_CAST",       "role": "core",    "weight": 0.88, "note": "多次触发Orb，Dark爆发"},
            # anti-synergy
            {"card_id": "CLAW",             "role": "pollution","weight": 0.15, "note": "0费方向分散Focus资源"},
            {"card_id": "SCRAPE",           "role": "pollution","weight": 0.15, "note": "0费方向分散资源"},
        ],
    },
    {
        "id": "defect_dark_evoke",
        "name": "Defect: Dark / Evoke Burst",
        "character": "defect",
        "key_tags": ["dark", "evoke", "burst", "setup_required", "orb_specialist"],
        "description": "充能Dark Orb后用多次Evoke触发造成巨量伤害，Dualcast/Multi-Cast是核心爆发手段。",
        "target_card_count": 13,
        "card_weights": [
            # core
            {"card_id": "DARKNESS",         "role": "core",    "weight": 0.95, "note": "Dark Orb充能核心"},
            {"card_id": "SHADOW_SHIELD",    "role": "core",    "weight": 0.90, "note": "Dark专用格挡"},
            {"card_id": "CONSUMING_SHADOW", "role": "core",    "weight": 0.88, "note": "Dark Orb增强"},
            {"card_id": "DUALCAST",         "role": "core",    "weight": 0.85, "note": "双次触发Orb爆发"},
            {"card_id": "MULTI_CAST",       "role": "core",    "weight": 0.92, "note": "多次触发Orb，Dark爆发"},
            {"card_id": "QUADCAST",         "role": "core",    "weight": 0.88, "note": "四次触发Orb终结"},
            # support
            {"card_id": "RAINBOW",          "role": "enabler", "weight": 0.72, "note": "通道三色Orb"},
            {"card_id": "LOOP",             "role": "enabler", "weight": 0.68, "note": "Orb额外触发"},
            {"card_id": "CAPACITOR",        "role": "enabler", "weight": 0.65, "note": "增加Orb槽位"},
            {"card_id": "COOLHEADED",       "role": "enabler", "weight": 0.62, "note": "摸牌+通道Frost"},
            {"card_id": "SKIM",             "role": "enabler", "weight": 0.58, "note": "摸牌"},
            {"card_id": "HOLOGRAM",         "role": "enabler", "weight": 0.55, "note": "取回关键牌"},
            # bridge
            {"card_id": "GLACIER",          "role": "filler",  "weight": 0.50, "note": "格挡+Frost通道"},
            {"card_id": "CHARGE_BATTERY",   "role": "filler",  "weight": 0.45, "note": "Lightning+格挡"},
            # finisher
            {"card_id": "SHATTER",          "role": "enabler", "weight": 0.72, "note": "Orb爆发终结"},
            {"card_id": "HYPERBEAM",        "role": "enabler", "weight": 0.68, "note": "高伤终结，重置Focus代价"},
            # anti-synergy
            {"card_id": "CLAW",             "role": "pollution","weight": 0.15, "note": "0费方向与Dark方向冲突"},
            {"card_id": "ALL_FOR_ONE",      "role": "pollution","weight": 0.15, "note": "0费牌回手，稀释Dark套"},
        ],
    },
    {
        "id": "defect_zero_cost_cycle",
        "name": "Defect: 0 Cost / Cycle",
        "character": "defect",
        "key_tags": ["claw", "zero_cost", "tempo", "combo", "density_sensitive"],
        "description": "Claw永久增长伤害，All for One将弃牌堆0费牌全回手实现爆发，精简牌组密度关键。",
        "target_card_count": 13,
        "card_weights": [
            # core
            {"card_id": "CLAW",             "role": "core",    "weight": 0.98, "note": "永久+2伤害核心"},
            {"card_id": "ALL_FOR_ONE",      "role": "core",    "weight": 0.95, "note": "0费牌全回手爆发"},
            {"card_id": "BEAM_CELL",        "role": "core",    "weight": 0.88, "note": "0费攻击+易伤"},
            {"card_id": "SCRAPE",           "role": "core",    "weight": 0.85, "note": "循环过滤非0费牌"},
            {"card_id": "TURBO",            "role": "core",    "weight": 0.82, "note": "0费生成费用"},
            {"card_id": "OVERCLOCK",        "role": "core",    "weight": 0.80, "note": "0费摸牌+状态"},
            # support
            {"card_id": "GO_FOR_THE_EYES",  "role": "enabler", "weight": 0.70, "note": "0费施加虚弱"},
            {"card_id": "SKIM",             "role": "enabler", "weight": 0.65, "note": "摸牌"},
            {"card_id": "HOLOGRAM",         "role": "enabler", "weight": 0.62, "note": "取回特定牌"},
            {"card_id": "DOUBLE_ENERGY",    "role": "enabler", "weight": 0.60, "note": "费用翻倍"},
            {"card_id": "REBOOT",           "role": "enabler", "weight": 0.55, "note": "重置摸牌堆"},
            # bridge
            {"card_id": "CHARGE_BATTERY",   "role": "filler",  "weight": 0.48, "note": "过渡通道"},
            {"card_id": "BOOT_SEQUENCE",    "role": "filler",  "weight": 0.42, "note": "前期格挡"},
            # finisher
            {"card_id": "HYPERBEAM",        "role": "enabler", "weight": 0.68, "note": "高伤终结"},
            # anti-synergy
            {"card_id": "GLACIER",          "role": "pollution","weight": 0.15, "note": "Focus方向，稀释0费密度"},
            {"card_id": "RAINBOW",          "role": "pollution","weight": 0.15, "note": "Focus方向，稀释0费密度"},
            {"card_id": "CONSUMING_SHADOW", "role": "pollution","weight": 0.15, "note": "Dark方向，稀释0费密度"},
        ],
    },
    {
        "id": "defect_status_fuel",
        "name": "Defect: Status / Fuel",
        "character": "defect",
        "key_tags": ["status", "conversion", "status_synergy", "niche"],
        "description": "利用状态牌的生成、转化和相关触发机制形成资源循环。",
        "target_card_count": 11,
        "card_weights": [
            # core
            {"card_id": "COMPACT",          "role": "core",    "weight": 0.90, "note": "排除状态牌升级"},
            {"card_id": "SMOKESTACK",       "role": "core",    "weight": 0.88, "note": "状态牌生成+格挡"},
            {"card_id": "ITERATION",        "role": "core",    "weight": 0.85, "note": "状态牌循环触发"},
            # support
            {"card_id": "OVERCLOCK",        "role": "enabler", "weight": 0.72, "note": "生成状态+摸牌"},
            {"card_id": "FIGHT_THROUGH",    "role": "enabler", "weight": 0.65, "note": "状态牌配合攻击"},
            {"card_id": "BOOST_AWAY",       "role": "enabler", "weight": 0.60, "note": "状态牌转化增强"},
            # bridge
            {"card_id": "CHARGE_BATTERY",   "role": "filler",  "weight": 0.52, "note": "过渡通道"},
            {"card_id": "COOLHEADED",       "role": "filler",  "weight": 0.48, "note": "摸牌+Frost通道"},
            # finisher
            {"card_id": "CREATIVE_AI",      "role": "core",    "weight": 0.80, "note": "每回合生成随机能力"},
            # anti-synergy
            {"card_id": "CLAW",             "role": "pollution","weight": 0.15, "note": "0费方向与状态方向冲突"},
        ],
    },

    # =========================================================
    # NECROBINDER — 4 builds (source: StratGG JSON v0.1)
    # =========================================================
    {
        "id": "necrobinder_osty_attack",
        "name": "Necrobinder: Osty Attack",
        "character": "necrobinder",
        "key_tags": ["osty", "companion_based", "board_presence", "attack_scaling"],
        "description": "围绕Osty伙伴的攻击频率和攻击强化构建，Fetch/Flatten/Sic'Em是核心触发链。",
        "target_card_count": 12,
        "card_weights": [
            # core
            {"card_id": "POKE",             "role": "core",    "weight": 0.90, "note": "触发Osty攻击"},
            {"card_id": "FETCH",            "role": "core",    "weight": 0.88, "note": "0费Osty攻击+摸牌"},
            {"card_id": "FLATTEN",          "role": "core",    "weight": 0.85, "note": "Osty攻击后0费高伤"},
            {"card_id": "HIGH_FIVE",        "role": "core",    "weight": 0.82, "note": "Summon+摸牌"},
            {"card_id": "CALCIFY",          "role": "core",    "weight": 0.80, "note": "格挡核心"},
            {"card_id": "NECRO_MASTERY",    "role": "core",    "weight": 0.85, "note": "攻击强化核心"},
            # support
            {"card_id": "INVOKE",           "role": "enabler", "weight": 0.72, "note": "Summon触发"},
            {"card_id": "LEGION_OF_BONE",   "role": "enabler", "weight": 0.68, "note": "群体Summon"},
            {"card_id": "FRIENDSHIP",       "role": "enabler", "weight": 0.65, "note": "Summon+格挡"},
            {"card_id": "DANSE_MACABRE",    "role": "enabler", "weight": 0.60, "note": "Summon辅助"},
            # bridge
            {"card_id": "GRAVE_WARDEN",     "role": "filler",  "weight": 0.52, "note": "格挡+Soul辅助"},
            {"card_id": "GRAVEBLAST",       "role": "filler",  "weight": 0.48, "note": "伤害+Soul"},
            # finisher
            {"card_id": "PROTECTOR",        "role": "enabler", "weight": 0.72, "note": "防御终结"},
            {"card_id": "ERADICATE",        "role": "enabler", "weight": 0.68, "note": "攻击终结"},
            # anti-synergy
            {"card_id": "PAGESTORM",        "role": "pollution","weight": 0.15, "note": "Ethereal方向，稀释Osty套"},
            {"card_id": "SPIRIT_OF_ASH",    "role": "pollution","weight": 0.15, "note": "Ethereal方向，稀释Osty套"},
        ],
    },
    {
        "id": "necrobinder_soul_engine",
        "name": "Necrobinder: Soul Engine",
        "character": "necrobinder",
        "key_tags": ["soul", "engine", "resource_generation", "synergy_stack"],
        "description": "通过Soul的生成与消费触发持续效果，Haunt/Soul Storm是主要伤害来源。",
        "target_card_count": 13,
        "card_weights": [
            # core
            {"card_id": "REAVE",            "role": "core",    "weight": 0.92, "note": "Soul生成核心"},
            {"card_id": "GRAVE_WARDEN",     "role": "core",    "weight": 0.90, "note": "格挡+Soul生成"},
            {"card_id": "GLIMPSE_BEYOND",   "role": "core",    "weight": 0.88, "note": "摸牌+Soul循环"},
            {"card_id": "DEVOUR_LIFE",      "role": "core",    "weight": 0.85, "note": "Soul消耗+吸血"},
            {"card_id": "HAUNT",            "role": "core",    "weight": 0.95, "note": "每打出Soul对随机敌伤害"},
            # support
            {"card_id": "BORROWED_TIME",    "role": "enabler", "weight": 0.70, "note": "能量加速为Soul重压提供节奏爆发"},
            {"card_id": "GRAVEBLAST",       "role": "enabler", "weight": 0.72, "note": "伤害+Soul"},
            {"card_id": "INVOKE",           "role": "enabler", "weight": 0.68, "note": "Summon触发辅助"},
            {"card_id": "LEGION_OF_BONE",   "role": "enabler", "weight": 0.65, "note": "群体Summon"},
            {"card_id": "SHROUD",           "role": "enabler", "weight": 0.60, "note": "格挡辅助"},
            # bridge
            {"card_id": "DIRGE",            "role": "filler",  "weight": 0.52, "note": "X费批量生成Soul"},
            # finisher
            {"card_id": "MISERY",           "role": "core",    "weight": 0.82, "note": "Soul大量消耗终结"},
            # anti-synergy
            {"card_id": "CALCIFY",          "role": "pollution","weight": 0.15, "note": "纯防御，稀释Soul引擎"},
        ],
    },
    {
        "id": "necrobinder_doom_execute",
        "name": "Necrobinder: Doom / Execute",
        "character": "necrobinder",
        "key_tags": ["doom", "execute", "debuff_scaling", "boss_kill_plan"],
        "description": "持续施加Doom，用终结技在Doom超过敌人HP时触发即死或转伤害。",
        "target_card_count": 13,
        "card_weights": [
            # core
            {"card_id": "COUNTDOWN",        "role": "core",    "weight": 0.95, "note": "持续叠Doom核心"},
            {"card_id": "NO_ESCAPE",        "role": "core",    "weight": 0.92, "note": "已有Doom时额外叠加"},
            {"card_id": "OBLIVION",         "role": "core",    "weight": 0.88, "note": "Doom施加核心"},
            {"card_id": "REAPER_FORM",      "role": "core",    "weight": 0.85, "note": "Doom效果强化"},
            {"card_id": "END_OF_DAYS",      "role": "core",    "weight": 0.82, "note": "大范围Doom终结"},
            # support
            {"card_id": "SHROUD",           "role": "enabler", "weight": 0.72, "note": "格挡辅助"},
            {"card_id": "SLEIGHT_OF_FLESH", "role": "enabler", "weight": 0.68, "note": "Doom转化辅助"},
            {"card_id": "MISERY",           "role": "enabler", "weight": 0.65, "note": "Soul消耗辅助"},
            {"card_id": "NEUROSURGE",       "role": "enabler", "weight": 0.62, "note": "Doom增强"},
            # bridge
            {"card_id": "FEAR",             "role": "filler",  "weight": 0.52, "note": "弱化过渡"},
            {"card_id": "ENFEEBLING_TOUCH", "role": "filler",  "weight": 0.48, "note": "Ethereal减益过渡"},
            # finisher
            {"card_id": "TIMES_UP",         "role": "core",    "weight": 0.90, "note": "Doom值转直接伤害终结"},
            # anti-synergy
            {"card_id": "CALCIFY",          "role": "pollution","weight": 0.15, "note": "纯防御，稀释Doom引擎"},
            {"card_id": "FLATTEN",          "role": "pollution","weight": 0.15, "note": "Osty方向，稀释Doom套"},
        ],
    },
    {
        "id": "necrobinder_ethereal_engine",
        "name": "Necrobinder: Ethereal Engine",
        "character": "necrobinder",
        "key_tags": ["ethereal", "draw_engine", "timing_sensitive", "advanced"],
        "description": "利用Ethereal牌的被动排除触发毒/伤害/摸牌，Pull from Below计数Ethereal实现爆发终结。",
        "target_card_count": 12,
        "card_weights": [
            # core
            {"card_id": "PAGESTORM",        "role": "core",    "weight": 0.92, "note": "Ethereal摸牌引擎"},
            {"card_id": "SPIRIT_OF_ASH",    "role": "core",    "weight": 0.90, "note": "每打出Ethereal获格挡"},
            {"card_id": "PARSE",            "role": "core",    "weight": 0.85, "note": "Ethereal摸牌循环"},
            {"card_id": "ENFEEBLING_TOUCH", "role": "core",    "weight": 0.82, "note": "Ethereal减益"},
            {"card_id": "LETHALITY",        "role": "core",    "weight": 0.80, "note": "Ethereal高伤攻击"},
            # support
            {"card_id": "FEAR",             "role": "enabler", "weight": 0.70, "note": "Ethereal弱化辅助"},
            {"card_id": "CALL_OF_THE_VOID", "role": "enabler", "weight": 0.65, "note": "Ethereal辅助"},
            {"card_id": "DEMESNE",          "role": "enabler", "weight": 0.60, "note": "Ethereal增强"},
            # bridge
            {"card_id": "GRAVEBLAST",       "role": "filler",  "weight": 0.52, "note": "伤害补充"},
            {"card_id": "GRAVE_WARDEN",     "role": "filler",  "weight": 0.48, "note": "格挡辅助"},
            # finisher
            {"card_id": "PULL_FROM_BELOW",  "role": "core",    "weight": 0.88, "note": "Ethereal计数×伤害终结"},
            # anti-synergy
            {"card_id": "CALCIFY",          "role": "pollution","weight": 0.15, "note": "纯防御，稀释Ethereal引擎"},
        ],
    },

    # =========================================================
    # REGENT — 3 builds (source: StratGG JSON v0.1)
    # =========================================================
    {
        "id": "regent_star_engine",
        "name": "Regent: Star Engine",
        "character": "regent",
        "key_tags": ["stars", "resource_engine", "scaling", "foundational"],
        "description": "建立星资源引擎，利用spend/gain star机制形成稳定循环和爆发。",
        "target_card_count": 13,
        "card_weights": [
            # core
            {"card_id": "GLOW",             "role": "core",    "weight": 0.92, "note": "基础星生成"},
            {"card_id": "GENESIS",          "role": "core",    "weight": 0.95, "note": "每回合被动生星"},
            {"card_id": "BLACK_HOLE",       "role": "core",    "weight": 0.88, "note": "星消耗高爆发"},
            {"card_id": "CHILD_OF_THE_STARS","role": "core",   "weight": 0.85, "note": "星资源转化核心"},
            # support
            {"card_id": "GUIDING_STAR",     "role": "enabler", "weight": 0.75, "note": "星生成辅助"},
            {"card_id": "GAMMA_BLAST",      "role": "enabler", "weight": 0.72, "note": "0费削弱+脆弱"},
            {"card_id": "REFLECT",          "role": "enabler", "weight": 0.68, "note": "格挡转化"},
            {"card_id": "RESONANCE",        "role": "enabler", "weight": 0.65, "note": "星共鸣触发"},
            # bridge
            {"card_id": "GLITTERSTREAM",    "role": "filler",  "weight": 0.52, "note": "星流过渡"},
            # finisher
            {"card_id": "HEAVENLY_DRILL",   "role": "core",    "weight": 0.80, "note": "星爆发终结"},
            {"card_id": "ROYAL_GAMBLE",     "role": "enabler", "weight": 0.70, "note": "高风险高收益星消耗"},
            # anti-synergy
            {"card_id": "ARSENAL",          "role": "pollution","weight": 0.15, "note": "无色方向，稀释星引擎"},
        ],
    },
    {
        "id": "regent_sovereign_blade_forge",
        "name": "Regent: Sovereign Blade / Forge",
        "character": "regent",
        "key_tags": ["forge", "sovereign_blade", "single_target_pressure", "midgame_spike"],
        "description": "持续Forge强化Sovereign Blade，配合脆弱/削弱叠加一刀秒杀单体目标。",
        "target_card_count": 12,
        "card_weights": [
            # core
            {"card_id": "SOVEREIGN_BLADE",  "role": "core",    "weight": 0.95, "note": "Forge强化的核心武器"},
            {"card_id": "REFINE_BLADE",     "role": "core",    "weight": 0.92, "note": "Forge+取刀入手"},
            {"card_id": "FURNACE",          "role": "core",    "weight": 0.88, "note": "大量Forge"},
            {"card_id": "PARRY",            "role": "core",    "weight": 0.82, "note": "格挡+触发Forge"},
            # support
            {"card_id": "REFLECT",          "role": "enabler", "weight": 0.72, "note": "格挡转化辅助"},
            {"card_id": "GUIDING_STAR",     "role": "enabler", "weight": 0.68, "note": "星辅助"},
            {"card_id": "GAMMA_BLAST",      "role": "enabler", "weight": 0.65, "note": "0费削弱+脆弱叠加"},
            {"card_id": "KNOCKOUT_BLOW",    "role": "enabler", "weight": 0.60, "note": "击晕控场"},
            # bridge
            {"card_id": "GLOW",             "role": "filler",  "weight": 0.52, "note": "星生成过渡"},
            {"card_id": "GLITTERSTREAM",    "role": "filler",  "weight": 0.45, "note": "过渡"},
            # finisher
            {"card_id": "HEAVENLY_DRILL",   "role": "enabler", "weight": 0.70, "note": "Forge终结"},
            {"card_id": "CRUSH_UNDER",      "role": "enabler", "weight": 0.65, "note": "压制性终结"},
            # anti-synergy
            {"card_id": "ARSENAL",          "role": "pollution","weight": 0.15, "note": "无色方向，稀释Forge套"},
        ],
    },
    {
        "id": "regent_colorless_create",
        "name": "Regent: Colorless / Create",
        "character": "regent",
        "key_tags": ["colorless", "create", "value_engine", "created_card_synergy", "flexible"],
        "description": "通过创造无色卡牌触发相关奖励，用Arsenal/Pillar of Creation建立价值引擎。",
        "target_card_count": 11,
        "card_weights": [
            # core
            {"card_id": "ARSENAL",          "role": "core",    "weight": 0.96, "note": "Innate+任意创造触发力量，创造流核心"},
            {"card_id": "PILLAR_OF_CREATION","role": "core",   "weight": 0.90, "note": "批量创建无色牌"},
            # support
            {"card_id": "GUARDS",           "role": "enabler", "weight": 0.75, "note": "创建无色牌"},
            {"card_id": "FOREGONE_CONCLUSION","role": "enabler","weight": 0.70, "note": "创建无色牌"},
            {"card_id": "PALE_BLUE_DOT",    "role": "enabler", "weight": 0.65, "note": "创建无色牌"},
            # bridge
            {"card_id": "GLOW",             "role": "filler",  "weight": 0.55, "note": "星生成过渡"},
            {"card_id": "GLITTERSTREAM",    "role": "filler",  "weight": 0.50, "note": "过渡"},
            {"card_id": "REFLECT",          "role": "filler",  "weight": 0.45, "note": "格挡辅助"},
            # finisher
            {"card_id": "GENESIS",          "role": "core",    "weight": 0.80, "note": "星+无色双向终结"},
            # anti-synergy
            {"card_id": "PARRY",            "role": "pollution","weight": 0.15, "note": "Forge方向，稀释无色套"},
        ],
    },
]


# ---------------------------------------------------------------------------
# ArchetypeLibrary
# ---------------------------------------------------------------------------

class ArchetypeLibrary:
    """
    套路库单例。
    持有所有套路定义，提供查询接口。
    """

    def __init__(self) -> None:
        self._archetypes: dict[str, Archetype] = {}
        self._load_builtin()

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    def _load_builtin(self) -> None:
        """加载内置套路数据"""
        for raw in _BUILTIN_ARCHETYPES:
            archetype = self._parse_raw(raw)
            self._archetypes[archetype.id] = archetype

    def load_from_json(self, path: str | Path) -> None:
        """
        从外部 JSON 文件追加 / 覆盖套路数据。
        JSON 格式与 _BUILTIN_ARCHETYPES 相同（列表）。
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"套路文件不存在: {path}")
        with path.open(encoding="utf-8") as f:
            raw_list: list[dict] = json.load(f)
        for raw in raw_list:
            archetype = self._parse_raw(raw)
            self._archetypes[archetype.id] = archetype

    @staticmethod
    def _parse_raw(raw: dict) -> Archetype:
        """将原始字典解析为 Archetype 模型"""
        weights = [
            ArchetypeWeight(
                card_id=w["card_id"],
                role=CardRole(w["role"]),
                weight=w["weight"],
                note=w.get("note", ""),
            )
            for w in raw.get("card_weights", [])
        ]
        return Archetype(
            id=raw["id"],
            name=raw["name"],
            name_zh=raw.get("name_zh", ""),
            character=Character(raw["character"]),
            key_tags=raw.get("key_tags", []),
            description=raw.get("description", ""),
            target_card_count=raw.get("target_card_count", 12),
            card_weights=weights,
        )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_archetype(self, archetype_id: str) -> Optional[Archetype]:
        return self._archetypes.get(archetype_id)

    def get_by_character(self, character: Character) -> list[Archetype]:
        return [
            a for a in self._archetypes.values()
            if a.character == character or a.character == Character.ANY
        ]

    def get_by_tag(self, tag: str) -> list[Archetype]:
        return [
            a for a in self._archetypes.values()
            if tag in a.key_tags
        ]

    def all(self) -> list[Archetype]:
        return list(self._archetypes.values())

    def get_card_weight(self, archetype_id: str, card_id: str) -> Optional[ArchetypeWeight]:
        """获取某卡在某套路中的权重定义"""
        archetype = self.get_archetype(archetype_id)
        if archetype is None:
            return None
        card_id_lower = card_id.lower()
        for w in archetype.card_weights:
            if w.card_id.lower() == card_id_lower:
                return w
        return None


# 模块级单例（供其他模块直接导入）
archetype_library = ArchetypeLibrary()
