"""
卡牌本地化支持模块
加载中文卡牌名称和英文ID的映射
"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger(__name__)

class CardLocale:
    """卡牌中文名称映射"""

    def __init__(self):
        self._zh_to_en: Dict[str, str] = {}  # 中文名 -> 英文ID
        self._en_to_zh: Dict[str, str] = {}  # 英文ID -> 中文名
        self._load_locale()

    def _load_locale(self) -> None:
        """加载本地化文件"""
        locale_file = Path(__file__).parent.parent / "data" / "card_locale_zh.json"

        if not locale_file.exists():
            log.warning(f"本地化文件不存在: {locale_file}")
            return

        try:
            with open(locale_file, "r", encoding="utf-8") as f:
                locale_data = json.load(f)

            # 提取卡牌名称映射
            for key, value in locale_data.items():
                if key.endswith(".title"):
                    # ABRASIVE.title -> 磨蚀
                    card_id = key.replace(".title", "").lower()
                    zh_name = value

                    self._zh_to_en[zh_name] = card_id
                    self._en_to_zh[card_id] = zh_name

            log.info(f"Loaded {len(self._zh_to_en)} Chinese card names")
        except Exception as e:
            log.error(f"Failed to load locale file: {e}")

    def get_english_id(self, chinese_name: str) -> Optional[str]:
        """获取中文名称对应的英文ID"""
        return self._zh_to_en.get(chinese_name)

    def get_chinese_name(self, english_id: str) -> Optional[str]:
        """获取英文ID对应的中文名称"""
        return self._en_to_zh.get(english_id.lower())

    def get_all_chinese_names(self) -> list:
        """获取所有中文卡牌名称列表"""
        return sorted(self._zh_to_en.keys())

    def get_all_english_ids(self) -> list:
        """获取所有英文卡牌ID列表"""
        return sorted(self._en_to_zh.keys())


# 全局实例
_locale = CardLocale()


def get_card_locale() -> CardLocale:
    """获取全局卡牌本地化实例"""
    return _locale
