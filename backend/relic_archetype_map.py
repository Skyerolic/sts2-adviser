"""
backend/relic_archetype_map.py
遗物→套路强关联映射表 (v0.9)

数据来源：用户提供的 sts2_character_relic_archetype_matches.json（score ≥ 0.85）

格式：relic_id (大写) → [(archetype_id, boost_score), ...]
  - relic_id 以存档文件中实际字段为准（大写，下划线分隔）
  - boost_score 范围 0.0~1.0，表示该遗物对套路的主观协同强度
  - 单个遗物可同时加成多个套路（如通用型遗物）
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Ironclad
# ---------------------------------------------------------------------------

_IRONCLAD: dict[str, list[tuple[str, float]]] = {
    # Burning Blood: end of combat heal 6 HP — 对自伤套路有明显缓冲价值
    "BURNING_BLOOD": [("ironclad_self_damage", 0.72)],

    # Black Blood: end of combat heal 12 HP — 自伤套路更强版升级
    "BLACK_BLOOD": [("ironclad_self_damage", 0.84)],

    # Vajra: start combat with 1 Strength — minor boost to strength builds
    "VAJRA": [("ironclad_strength", 0.50)],

    # Red Skull: ≤50% HP → +3 Strength — 自伤套路常驻低血，收益极高
    "RED_SKULL": [
        ("ironclad_self_damage", 0.90),
        ("ironclad_strength",    0.78),
    ],

    # Paper Phrog: Vulnerable 75% instead of 50%
    "PAPER_PHROG": [
        ("ironclad_strength",    0.82),
        ("ironclad_self_damage", 0.36),
    ],

    # Self-Forming Clay: lose HP → gain 3 Block next turn — 自伤套路防御回收
    "SELF_FORMING_CLAY": [("ironclad_self_damage", 0.92)],

    # Beating Remnant: cannot lose >20 HP/turn — critical for self-damage survival
    "BEATING_REMNANT": [("ironclad_self_damage", 0.40)],

    # Girya: gain Strength at rest sites (3x max) — core strength scaling
    "GIRYA": [("ironclad_strength", 0.70)],

    # Lizard Tail: die → heal to 50% (once) — self-damage safety net
    "LIZARD_TAIL": [("ironclad_self_damage", 0.30)],

    # Meat on the Bone: heal 12 HP at ≤50% HP end of combat — self-damage synergy
    "MEAT_ON_THE_BONE": [("ironclad_self_damage", 0.50)],

    # Shuriken: every 3 attacks → +1 Strength — attack-heavy / strength scaling
    "SHURIKEN": [("ironclad_strength", 0.55)],

    # Charon's Ashes: exhaust a card → deal 3 damage ALL — 排除套最强遗物之一
    "CHARONS_ASHES": [("ironclad_exhaust", 0.97)],

    # Demon Tongue: first HP loss per turn → heal equal amount — 自伤套路近乎免费的逆转
    "DEMON_TONGUE": [("ironclad_self_damage", 0.99)],

    # Ruined Helmet: first Strength gain per combat → double — 力量套路爆发加速器
    "RUINED_HELMET": [
        ("ironclad_strength",    0.98),
        ("ironclad_self_damage", 0.63),
    ],

    # Brimstone: start of turn +2 Strength (enemies +1) — 力量套路核心遗物
    "BRIMSTONE": [
        ("ironclad_strength",     0.96),
        ("ironclad_self_damage",  0.46),
    ],

    # Burning Sticks: exhaust a Skill → copy added to hand — exhaust cycle value
    "BURNING_STICKS": [("ironclad_exhaust", 0.65)],

    # Sling of Courage: +2 Strength at start of elite combats
    "SLING_OF_COURAGE": [("ironclad_strength", 0.45)],

    # Sword of Jade: start each combat with 3 Strength
    "SWORD_OF_JADE": [("ironclad_strength", 0.55)],

    # Forgotten Soul: exhaust a card → 1 dmg to random enemy
    "FORGOTTEN_SOUL": [("ironclad_exhaust", 0.50)],

    # Toasty Mittens (Ancient): exhaust top card each turn + 1 Strength
    "TOASTY_MITTENS": [
        ("ironclad_strength",  0.60),
        ("ironclad_exhaust",   0.55),
    ],

    # Ember Tea: +2 Strength first 5 combats
    "EMBER_TEA": [("ironclad_strength", 0.45)],
}

# ---------------------------------------------------------------------------
# Silent
# ---------------------------------------------------------------------------

_SILENT: dict[str, list[tuple[str, float]]] = {
    # Snecko Skull: apply Poison → +1 extra — 毒套最强遗物之一
    "SNECKO_SKULL": [("silent_poison", 0.99)],

    # Twisted Funnel: start of combat → 4 Poison to ALL enemies
    "TWISTED_FUNNEL": [("silent_poison", 0.95)],

    # Ninja Scroll: start of combat → 3 Shivs in Hand
    "NINJA_SCROLL": [("silent_shiv", 0.98)],

    # Helical Dart: play a Shiv → +1 Dexterity this turn
    "HELICAL_DART": [("silent_shiv", 0.94)],

    # Tingsha: discard card during turn → deal 3 dmg per card discarded
    "TINGSHA": [("silent_sly_discard", 0.97)],

    # Tough Bandages: discard card during turn → gain 3 Block
    "TOUGH_BANDAGES": [("silent_sly_discard", 0.96)],

    # Paper Krane: Weak lasts 3 turns instead of 2 — 弱化延长对Shiv/毒都有价值
    "PAPER_KRANE": [
        ("silent_poison",      0.45),
        ("silent_sly_discard", 0.35),
    ],

    # Kunai: every 3 attacks → +1 Dexterity — Shiv套回避加成
    "KUNAI": [("silent_shiv", 0.45)],

    # Gambling Chip: start of combat discard hand, redraw — 弃牌触发套路起点
    "GAMBLING_CHIP": [("silent_sly_discard", 0.40)],

    # Unsettling Lamp: start each combat channel 1 Poison — 毒套开局加速
    "UNSETTLING_LAMP": [("silent_poison", 0.50)],

    # Unceasing Top: when hand empty, draw 1 card — 弃牌套快速清空手牌后补牌
    "UNCEASING_TOP": [("silent_sly_discard", 0.40)],
}

# ---------------------------------------------------------------------------
# Defect
# ---------------------------------------------------------------------------

_DEFECT: dict[str, list[tuple[str, float]]] = {
    # Infused Core: start of combat → Channel 3 Lightning
    "INFUSED_CORE": [("defect_orb_focus", 0.92)],

    # Data Disk: start each combat with 1 Focus — Orb/Focus套核心遗物
    "DATA_DISK": [
        ("defect_orb_focus",   0.99),
        ("defect_dark_evoke",  0.62),
    ],

    # Gold-Plated Cables: rightmost Orb triggers passive an extra time
    "GOLD_PLATED_CABLES": [
        ("defect_orb_focus",   0.93),
        ("defect_dark_evoke",  0.66),
    ],

    # Symbiotic Virus: start of combat → Channel 1 Dark — Dark套完美起点
    "SYMBIOTIC_VIRUS": [
        ("defect_dark_evoke",  0.98),
        ("defect_orb_focus",   0.48),
    ],

    # Emotion Chip: lost HP last turn → trigger all Orb passives at start
    "EMOTION_CHIP": [
        ("defect_orb_focus",   0.90),
        ("defect_dark_evoke",  0.70),
    ],

    # Metronome: Channel 7 Orbs → deal 30 dmg ALL (once per combat)
    "METRONOME": [
        ("defect_orb_focus",   0.87),
        ("defect_dark_evoke",  0.58),
    ],

    # Power Cell: start of combat → add 2 zero-cost cards from Draw Pile to Hand
    "POWER_CELL": [("defect_zero_cost_cycle", 0.99)],

    # Runic Capacitor: start with 3 extra Orb Slots
    "RUNIC_CAPACITOR": [
        ("defect_orb_focus",   0.96),
        ("defect_dark_evoke",  0.72),
    ],

    # Cracked Core: start of combat Channel 1 Lightning/Frost/Dark (random) — 通用Orb起点
    "CRACKED_CORE": [
        ("defect_orb_focus",   0.55),
        ("defect_dark_evoke",  0.35),
    ],

    # Ice Cream: unspent Energy carries over — zero-cost cycle与Focus套路均受益
    "ICE_CREAM": [
        ("defect_zero_cost_cycle", 0.60),
        ("defect_orb_focus",       0.45),
    ],

    # Screaming Flagon: first time you Channel an Orb each combat → gain 2 Block
    "SCREAMING_FLAGON": [("defect_orb_focus", 0.50)],
}

# ---------------------------------------------------------------------------
# Necrobinder
# ---------------------------------------------------------------------------

_NECROBINDER: dict[str, list[tuple[str, float]]] = {
    # Phylactery Unbound: start of combat Summon 5 + start of turn Summon 2
    "PHYLACTERY_UNBOUND": [
        ("necrobinder_osty_attack", 0.97),
        ("necrobinder_doom_execute", 0.40),
    ],

    # Bone Flute: whenever Osty attacks, gain 2 Block
    "BONE_FLUTE": [("necrobinder_osty_attack", 0.95)],

    # Book Repair Knife: non-Minion enemy dies to Doom → heal 3 HP
    "BOOK_REPAIR_KNIFE": [("necrobinder_doom_execute", 0.98)],

    # Funerary Mask: start of combat → add 3 Souls to Draw Pile
    "FUNERARY_MASK": [("necrobinder_soul_engine", 0.99)],

    # Big Hat: start of combat → add 2 random Ethereal cards to Hand
    "BIG_HAT": [("necrobinder_ethereal_engine", 0.98)],

    # Undying Sigil: enemies with Doom ≥ HP deal 50% less damage
    "UNDYING_SIGIL": [("necrobinder_doom_execute", 0.99)],

    # Bound Phylactery: start of turn Summon 1
    "BOUND_PHYLACTERY": [("necrobinder_osty_attack", 0.86)],

    # Bookmark: retain 1 card each turn — 灵魂引擎/以太引擎保留核心牌
    "BOOKMARK": [
        ("necrobinder_soul_engine",     0.55),
        ("necrobinder_ethereal_engine", 0.50),
    ],

    # Ivory Tile: Doom counters do not reduce below 1 (enemies survive at 1 HP) — Doom套辅助
    "IVORY_TILE": [("necrobinder_doom_execute", 0.60)],
}

# ---------------------------------------------------------------------------
# Regent
# ---------------------------------------------------------------------------

_REGENT: dict[str, list[tuple[str, float]]] = {
    # Divine Destiny: start of combat gain 6 Stars — 星引擎完美起点
    "DIVINE_DESTINY": [
        ("regent_star_engine",              0.98),
        ("regent_sovereign_blade_forge",    0.48),
    ],

    # Fencing Manual: start of combat Forge 10 — Sovereign Blade套核心
    "FENCING_MANUAL": [("regent_sovereign_blade_forge", 0.99)],

    # Galactic Dust: every 10 Stars spent → gain 10 Block
    "GALACTIC_DUST": [("regent_star_engine", 0.96)],

    # Regalite: create a Colorless card → gain 2 Block
    "REGALITE": [("regent_colorless_create", 0.98)],

    # Lunar Pastry: end of turn gain 1 Star
    "LUNAR_PASTRY": [("regent_star_engine", 0.92)],

    # Mini Regent: first time spend Stars each turn → gain 1 Strength
    "MINI_REGENT": [
        ("regent_star_engine",           0.88),
        ("regent_sovereign_blade_forge", 0.62),
    ],

    # Divine Right: start of combat gain 3 Strength — 力量型星套路加速
    "DIVINE_RIGHT": [
        ("regent_star_engine",           0.65),
        ("regent_sovereign_blade_forge", 0.55),
    ],

    # Orange Dough: create a Colorless card → heal 2 HP — 无色创造套回复
    "ORANGE_DOUGH": [("regent_colorless_create", 0.70)],

    # Vitruvian Minion: Forged cards gain +1 Strength — Sovereign Blade套核心
    "VITRUVIAN_MINION": [("regent_sovereign_blade_forge", 0.95)],

    # Toolbox: start of combat → add 1 random Colorless card to Hand
    "TOOLBOX": [("regent_colorless_create", 0.60)],

    # Dingy Rug: every 5 Stars gained → deal 3 damage to all enemies
    "DINGY_RUG": [("regent_star_engine", 0.72)],

    # Vexing Puzzlebox: start of combat → add 2 random cards from another class to Hand
    "VEXING_PUZZLEBOX": [("regent_colorless_create", 0.50)],
}

# ---------------------------------------------------------------------------
# Universal（跨角色通用遗物 / 先古遗物）
# ---------------------------------------------------------------------------

_UNIVERSAL: dict[str, list[tuple[str, float]]] = {
    # Joss Paper: enemies with Doom die at end of their turn — 跨角色Doom协同
    "JOSS_PAPER": [("necrobinder_doom_execute", 0.45)],

    # Game Piece: start of combat → add 1 random card from any class to Hand
    "GAME_PIECE": [
        ("regent_colorless_create",  0.40),
        ("defect_zero_cost_cycle",   0.35),
    ],

    # Frozen Egg: Powers cost 0 this combat — 力量/Focus套Path-of-least-resistance
    "FROZEN_EGG": [
        ("ironclad_strength",  0.50),
        ("defect_orb_focus",   0.50),
    ],

    # Mummified Hand: whenever you play a Power, reduce cost of a random card by 1
    "MUMMIFIED_HAND": [
        ("ironclad_strength",  0.45),
        ("defect_orb_focus",   0.45),
    ],

    # Reptile Trinket: gain 1 Dexterity each combat — 全角色防御加成
    "REPTILE_TRINKET": [
        ("silent_shiv",        0.30),
        ("ironclad_self_damage", 0.25),
    ],

    # Sparkling Rouge: at the start of combat gain 1 Energy — 高费卡套路通用
    "SPARKLING_ROUGE": [
        ("ironclad_exhaust",   0.40),
        ("defect_orb_focus",   0.40),
    ],

    # Rainbow Ring (Ancient): start of combat Channel 1 of each Orb type
    "RAINBOW_RING": [
        ("defect_orb_focus",   0.85),
        ("defect_dark_evoke",  0.70),
    ],

    # Pael's Blood (Ancient): ancient relic — generic combat power
    "PAELS_BLOOD": [
        ("ironclad_strength",  0.55),
        ("ironclad_self_damage", 0.45),
    ],

    # Pael's Tears (Ancient): ancient relic — healing related
    "PAELS_TEARS": [("ironclad_self_damage", 0.50)],

    # Philosopher's Stone (Ancient): gain 1 Energy/turn, enemies gain 1 Strength
    "PHILOSOPHERS_STONE": [
        ("ironclad_strength",      0.60),
        ("defect_orb_focus",       0.60),
        ("regent_star_engine",     0.55),
    ],

    # Ectoplasm (Ancient): gain 1 Energy/turn, cannot gain Gold
    "ECTOPLASM": [
        ("ironclad_exhaust",       0.50),
        ("defect_zero_cost_cycle", 0.50),
        ("defect_orb_focus",       0.45),
    ],
}

# ---------------------------------------------------------------------------
# 合并为全局映射表
# ---------------------------------------------------------------------------

RELIC_ARCHETYPE_MAP: dict[str, list[tuple[str, float]]] = {
    **_IRONCLAD,
    **_SILENT,
    **_DEFECT,
    **_NECROBINDER,
    **_REGENT,
    **_UNIVERSAL,
}
