"""
配置文件管理系统

存储用户自定义的路径设置（存档位置、日志位置等）
配置文件位置: ~/.sts2-adviser/config.json
"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".sts2-adviser"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> Dict:
    """加载配置文件"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                log.debug(f"✓ 配置已加载: {CONFIG_FILE}")
                return config
        except Exception as e:
            log.warning(f"无法加载配置文件: {e}")
            return {}
    return {}


def save_config(config: Dict) -> bool:
    """保存配置文件"""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        log.info(f"✓ 配置已保存: {CONFIG_FILE}")
        return True
    except Exception as e:
        log.error(f"无法保存配置文件: {e}")
        return False


def get_config_value(key: str, default: Optional[str] = None) -> Optional[str]:
    """获取配置值"""
    config = load_config()
    return config.get(key, default)


def set_config_value(key: str, value: str) -> bool:
    """设置单个配置值"""
    config = load_config()
    config[key] = value
    return save_config(config)


def get_language() -> str:
    """获取语言设置，默认英文"""
    return get_config_value("language", "en") or "en"


def set_language(lang: str) -> bool:
    """设置语言（'en' 或 'zh'）"""
    return set_config_value("language", lang)


def get_save_path() -> Optional[str]:
    """获取存档路径配置"""
    return get_config_value("save_path")


def set_save_path(path: str) -> bool:
    """设置存档路径配置"""
    return set_config_value("save_path", path)


def get_log_path() -> Optional[str]:
    """获取日志路径配置"""
    return get_config_value("log_path")


def set_log_path(path: str) -> bool:
    """设置日志路径配置"""
    return set_config_value("log_path", path)
