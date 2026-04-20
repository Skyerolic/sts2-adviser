"""
scripts/generate_card_summaries.py

从现有数据（cards.json、card_library.json、archetypes.json）程序化生成
data/card_summaries.json，无需外部 API。

运行方式：
    py -3 scripts/generate_card_summaries.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


# ---------------------------------------------------------------------------
# 加载数据
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> list | dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_cards() -> dict[str, dict]:
    """card_id (大写) -> raw dict"""
    raw = _load_json(DATA / "cards.json")
    return {r["id"].upper(): r for r in raw if "id" in r}


def load_library() -> dict[str, dict]:
    """card_id (大写) -> library dict (含 win_rate / pick_rate)"""
    raw = _load_json(DATA / "card_library.json")
    return {r["id"].upper(): r for r in raw if "id" in r}


def load_archetypes() -> list[dict]:
    return _load_json(DATA / "archetypes.json")


# ---------------------------------------------------------------------------
# 卡牌-套路索引构建
# ---------------------------------------------------------------------------

def build_card_to_archetypes(archetypes: list[dict]) -> dict[str, list[dict]]:
    """
    返回 card_id (大写) -> [{"arch_id", "arch_name", "role", "weight", "note", "arch_desc"}, ...]
    """
    index: dict[str, list[dict]] = {}
    for arch in archetypes:
        for cw in arch.get("card_weights", []):
            cid = cw.get("card_id", "").upper()
            if not cid:
                continue
            index.setdefault(cid, []).append({
                "arch_id":   arch["id"],
                "arch_name": arch["name"],
                "role":      cw.get("role", "filler"),
                "weight":    cw.get("weight", 0.0),
                "note":      cw.get("note", ""),
                "arch_desc": arch.get("description", ""),
            })
    # 每张卡的套路按 weight 降序
    for cid in index:
        index[cid].sort(key=lambda x: x["weight"], reverse=True)
    return index


# ---------------------------------------------------------------------------
# 评级计算
# ---------------------------------------------------------------------------

def _parse_rate(val) -> float | None:
    if val is None:
        return None
    s = str(val).strip().rstrip("%")
    try:
        return float(s)
    except ValueError:
        return None


def build_tier(wr: float | None, pr: float | None) -> str:
    """
    基于社区数据给出综合评级。
    S ≥ 63%, A ≥ 59%, B ≥ 55%, C ≥ 51%, D < 51%
    pick_rate < 8% 时上限为 B（社区不选的牌）
    """
    if wr is None:
        return "?"
    if wr >= 63:
        tier = "S"
    elif wr >= 59:
        tier = "A"
    elif wr >= 55:
        tier = "B"
    elif wr >= 51:
        tier = "C"
    else:
        tier = "D"

    # pick_rate 极低时降级
    if pr is not None and pr < 8.0 and tier in ("S", "A"):
        tier = "B"

    return tier


# ---------------------------------------------------------------------------
# 句子构建器
# ---------------------------------------------------------------------------

_RARITY_ZH = {
    "Rare": "Rare 稀有",
    "Uncommon": "Uncommon 非凡",
    "Common": "Common 普通",
    "Basic": "Basic 基础",
    "Starter": "Starter 初始",
    "Ancient": "Ancient 先古",
    "Curse": "诅咒",
    "Status": "状态",
    "Event": "事件",
    "Quest": "任务",
    "Special": "特殊",
}

_TYPE_ZH = {
    "Attack": "攻击",
    "Skill": "技能",
    "Power": "能力",
    "Status": "状态",
    "Curse": "诅咒",
    "Quest": "任务",
}

_COST_ZH = {
    -1: "X费",
    0: "0费",
    1: "1费",
    2: "2费",
    3: "3费",
    4: "4费",
}


def build_card_type_sentence(card: dict) -> str:
    """
    生成卡牌定位句，例如：
    "精准是一张1费的 Uncommon 能力牌，永久效果持续整个战斗。"
    """
    name_zh = card.get("name", card.get("id", "?"))
    cost_raw = card.get("cost")
    is_x = card.get("is_x_cost")
    if is_x:
        cost_str = "X费"
    else:
        cost_str = _COST_ZH.get(cost_raw, f"{cost_raw}费") if cost_raw is not None else "未知费用"

    rarity = card.get("rarity", card.get("rarity_key", "Common"))
    rarity_zh = _RARITY_ZH.get(rarity, rarity)
    ctype = card.get("type", card.get("type_key", "Skill"))
    type_zh = _TYPE_ZH.get(ctype, ctype)

    # 附加描述
    extra = ""
    if ctype == "Power":
        extra = "，永久效果持续整个战斗"
    elif ctype == "Status":
        extra = "，通常对牌库有负面影响"
    elif ctype == "Curse":
        extra = "，会削弱你的抽牌效率"

    return f"这是一张{cost_str}的 {rarity_zh} {type_zh}牌{extra}。"


def build_archetype_sentence(archs: list[dict], card_id: str) -> tuple[str, list[str]]:
    """
    生成套路归属句。返回 (句子, best_archetype_ids)
    """
    if not archs:
        return "", []

    # 分类：core / enabler / filler / pollution / transition
    cores    = [a for a in archs if a["role"] == "core"]
    enablers = [a for a in archs if a["role"] == "enabler"]
    fillers  = [a for a in archs if a["role"] == "filler"]
    pollut   = [a for a in archs if a["role"] == "pollution"]

    best_ids = [a["arch_id"] for a in (cores or enablers or fillers)[:2]]
    primary  = (cores or enablers or fillers or pollut)[0]

    arch_name = primary["arch_name"]
    note = primary.get("note", "")
    role_zh = {
        "core": "核心牌",
        "enabler": "关键辅助牌",
        "filler": "补充牌",
        "transition": "过渡牌",
        "pollution": "（在此套路中定位为污染）",
    }.get(primary["role"], "相关牌")

    if pollut and not cores and not enablers:
        # 仅出现在污染定义里
        return (
            f"在 {arch_name} 套路中此牌被标记为污染，通常不建议拿取。",
            [],
        )

    # 多套路
    if len(cores) + len(enablers) > 1:
        all_primary = (cores or enablers)[:2]
        names = " 和 ".join(a["arch_name"] for a in all_primary)
        sentence = f"它同时适用于 {names} 等套路。"
        if note:
            sentence = f"它是 {arch_name} 套路的{role_zh}（{note}），同时兼顾其他相关方向。"
    else:
        if note:
            sentence = f"它是 {arch_name} 套路的{role_zh}（{note}）。"
        else:
            sentence = f"它适合 {arch_name} 套路。"

    return sentence, best_ids


def _sigmoid(x: float, center: float, steepness: float) -> float:
    return 1.0 / (1.0 + math.exp(-steepness * (x - center)))


def build_community_sentence(wr: float | None, pr: float | None) -> str:
    """
    用 sigmoid 偏差值替代原始百分比，加入分歧标签（潜力股/高估风险）。
    格式：社区{分级}（{偏差值}{分歧标签}）。
    """
    if wr is None:
        return ""

    win_norm  = _sigmoid(wr, center=50.0, steepness=0.12)
    pick_norm = _sigmoid(pr, center=18.0, steepness=0.08) if pr is not None else 0.5
    community_score = 0.65 * win_norm + 0.35 * pick_norm + 0.08 * (win_norm - pick_norm)
    divergence = win_norm - pick_norm

    # 分级标签
    if community_score >= 0.75:
        tier = "社区热门"
    elif community_score >= 0.60:
        tier = "社区认可"
    elif community_score >= 0.45:
        tier = "社区中性"
    elif community_score >= 0.30:
        tier = "社区冷淡"
    else:
        tier = "社区跳过"

    # sigmoid 偏差值（以 0.5 为中性基准）
    dev = round((community_score - 0.5) * 100)
    dev_str = f"{dev:+d}"

    # 分歧标签（放弃率/选取率与胜率背离时显示）
    if divergence >= 0.15:
        div_tag = " · 潜力股"
    elif divergence <= -0.15:
        div_tag = " · 高估风险"
    else:
        div_tag = ""

    return f"{tier}（{dev_str}{div_tag}）。"


def build_usage_tip(card: dict, archs: list[dict], tier: str) -> str:
    """
    根据关键词生成使用建议。
    """
    keywords = [k.lower() for k in (card.get("keywords_key") or card.get("keywords") or [])]
    ctype  = (card.get("type_key") or card.get("type") or "").lower()
    cost   = card.get("cost", 1)
    rarity = (card.get("rarity_key") or card.get("rarity") or "").lower()

    tips: list[str] = []

    # 特殊关键词提示
    if "exhaust" in keywords:
        tips.append("带有排除效果（Exhaust），可主动减薄牌组；配合 Feel No Pain 等排除触发遗物效果最佳。")
    if "ethereal" in keywords:
        tips.append("带有以太属性（Ethereal），本回合未打出会被消耗；需确保本回合能使用，或避免在常规牌库中大量囤积。")
    if "retain" in keywords:
        tips.append("带有保留效果（Retain），可以跨回合持有；适合需要特定组合的套路。")
    if "innate" in keywords:
        tips.append("为天赋牌（Innate），每次战斗开局必抽到；需确认此牌值得占用开局手牌位置。")
    if "x" in keywords or card.get("is_x_cost"):
        tips.append("费用为 X，输出随投入能量线性增长；能量遗物或充能类支持可显著提升价值。")

    # 费用建议
    if cost is not None and cost >= 3 and rarity in ("common", "uncommon"):
        tips.append("费用较高，游戏早期使用受限；建议在有能量遗物时才优先考虑。")

    # 套路方向
    pollut_only = archs and all(a["role"] == "pollution" for a in archs)
    if pollut_only:
        tips.append("在当前数据库定义的套路中，此牌与核心机制不协同；除非套路已成型否则建议跳过。")
    elif not archs:
        if tier == "S":
            tips.append("暂无套路精确定义，但社区数据显示此牌强度极高，通常值得考虑。")
        elif tier in ("C", "D"):
            tips.append("此牌无明确套路归属且社区数据偏低，建议谨慎考量。")

    return " ".join(tips) if tips else ""


def derive_synergy_cards(card_id: str, archs: list[dict], all_archetypes: list[dict]) -> list[str]:
    """
    从同套路的其他 core/enabler 牌中推导协同牌列表（最多5张）。
    """
    arch_ids = {a["arch_id"] for a in archs if a["role"] in ("core", "enabler")}
    synergy: set[str] = set()
    for arch in all_archetypes:
        if arch["id"] not in arch_ids:
            continue
        for cw in arch.get("card_weights", []):
            if cw.get("role") in ("core", "enabler") and cw["card_id"].upper() != card_id:
                synergy.add(cw["card_id"].upper())
    return sorted(synergy)[:5]


# ---------------------------------------------------------------------------
# 单卡总结生成
# ---------------------------------------------------------------------------

def generate_summary(
    card_id: str,
    card: dict,
    lib_entry: dict | None,
    archs: list[dict],
    all_archetypes: list[dict],
) -> dict:
    wr = _parse_rate(lib_entry.get("win_rate") if lib_entry else None)
    pr = _parse_rate(lib_entry.get("pick_rate") if lib_entry else None)
    tier = build_tier(wr, pr)

    # 句子组合
    sentences: list[str] = []
    type_sent = build_card_type_sentence(card)
    sentences.append(type_sent)

    arch_sent, best_arch_ids = build_archetype_sentence(archs, card_id)
    if arch_sent:
        sentences.append(arch_sent)

    comm_sent = build_community_sentence(wr, pr)
    if comm_sent:
        sentences.append(comm_sent)

    tip = build_usage_tip(card, archs, tier)
    if tip:
        sentences.append(tip)

    summary_zh = "".join(sentences)

    synergy_cards = derive_synergy_cards(card_id, archs, all_archetypes)

    return {
        "summary_zh":       summary_zh,
        "tier":             tier,
        "best_archetypes":  best_arch_ids,
        "synergy_cards":    synergy_cards,
        "source":           "auto_generated",
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== STS2 Adviser — 卡牌文字总结生成器 ===")

    cards      = load_cards()
    library    = load_library()
    archetypes = load_archetypes()
    card_to_arch = build_card_to_archetypes(archetypes)

    print(f"加载卡牌: {len(cards)} 张")
    print(f"加载社区数据: {len(library)} 条")
    print(f"加载套路: {len(archetypes)} 个")

    summaries: dict[str, dict] = {}
    stats = {
        "with_archetype_and_wr": 0,
        "with_wr_only": 0,
        "no_wr": 0,
        "special": 0,
    }

    for card_id, card in sorted(cards.items()):
        rarity = (card.get("rarity_key") or card.get("rarity") or "").lower()
        ctype  = (card.get("type_key") or card.get("type") or "").lower()

        # 标记特殊卡（curse/status/event）
        is_special = rarity in ("curse", "status", "event", "quest") or ctype in ("curse", "status", "quest")

        lib_entry = library.get(card_id)
        archs = card_to_arch.get(card_id, [])

        has_wr = lib_entry is not None and lib_entry.get("win_rate") is not None
        has_arch = len(archs) > 0

        if is_special:
            stats["special"] += 1
        elif has_arch and has_wr:
            stats["with_archetype_and_wr"] += 1
        elif has_wr:
            stats["with_wr_only"] += 1
        else:
            stats["no_wr"] += 1

        summaries[card_id] = generate_summary(card_id, card, lib_entry, archs, archetypes)

    # 写出 JSON
    out_path = DATA / "card_summaries.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summaries, f, ensure_ascii=False, indent=2)

    total = len(summaries)
    meaningful = stats["with_archetype_and_wr"] + stats["with_wr_only"] + stats["with_archetype_and_wr"]
    # 去重计算：有 archetype 且有 wr 已算在 with_wr_only 里
    meaningful = total - stats["no_wr"] - stats["special"]
    coverage = meaningful / total * 100 if total else 0

    print(f"\n生成完成 → {out_path}")
    print(f"总计: {total} 张")
    print(f"  有套路+胜率: {stats['with_archetype_and_wr']} 张")
    print(f"  仅胜率:      {stats['with_wr_only']} 张")
    print(f"  无胜率:      {stats['no_wr']} 张")
    print(f"  特殊卡:      {stats['special']} 张")
    print(f"有效覆盖率:  {meaningful}/{total} = {coverage:.1f}%")

    # 抽查 3 张重要卡
    print("\n=== 抽查 ===")
    for check_id in ["ACCURACY", "CORRUPTION", "BLADE_DANCE", "INFLAME", "ACCELERANT"]:
        if check_id in summaries:
            s = summaries[check_id]
            print(f"\n[{check_id}] tier={s['tier']}")
            print(f"  {s['summary_zh']}")
            print(f"  best_archetypes: {s['best_archetypes']}")
            print(f"  synergy_cards:   {s['synergy_cards']}")


if __name__ == "__main__":
    main()
