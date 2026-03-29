"""
STS2 游戏状态实时监视系统

功能：
1. 自动检测 STS2 安装目录
2. 监视游戏日志文件变化
3. 解析游戏状态（卡组、遗物、HP、楼层等）
4. 通过回调接口或 WebSocket 发送更新

使用示例：
    watcher = STS2GameWatcher()
    watcher.on_state_change(lambda state: print(f"更新: {state}"))
    watcher.start()
"""

import io
import json
import logging
import os
import sys
import time
import threading
from pathlib import Path
from typing import Dict, Optional, Callable, List
from datetime import datetime

# 导入配置管理器
try:
    from scripts.config_manager import get_save_path, get_log_path
except ImportError:
    # 允许直接运行此脚本时的备用方案
    def get_save_path(): return None
    def get_log_path(): return None

# 配置日志（避免多次调用 basicConfig 的问题）
log = logging.getLogger(__name__)
if not log.handlers:
    try:
        log_file = Path(__file__).parent.parent / "game_watcher.log"
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s [GameWatcher] [%(levelname)s] %(message)s")
        )
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    except Exception:
        pass  # 如果无法创建文件处理器，至少 logger 还能用


class STS2GameWatcher:
    """Slay the Spire 2 游戏状态实时监视"""

    def __init__(self, custom_save_path: Optional[str] = None, custom_log_path: Optional[str] = None):
        self.game_path: Optional[Path] = None
        self.log_path: Optional[Path] = None
        self.save_path: Optional[Path] = None

        # 自定义路径（优先于自动搜索）
        self.custom_save_path = Path(custom_save_path) if custom_save_path else None
        self.custom_log_path = Path(custom_log_path) if custom_log_path else None

        self.current_state: Dict = {
            "character": None,
            "floor": 0,
            "act": 0,
            "hp": 0,
            "max_hp": 0,
            "gold": 0,
            "deck": [],
            "relics": [],
            "hand": [],
            "timestamp": None,
        }
        self.callbacks: List[Callable] = []
        self.log_status_callbacks: List[Callable] = []  # 日志状态变化回调
        self.is_running = False
        self.last_log_position = 0
        self.state_history: Dict = {}
        self.last_save_check_time = 0
        self._log_monitoring_active = False  # 日志监视状态

    def find_game_directory(self) -> Optional[Path]:
        """自动查找 STS2 安装目录"""
        log.info("正在查找 STS2 安装目录...")

        # 常见安装位置
        possible_paths = [
            # Windows
            Path("C:/Users") / os.getenv("USERNAME", "") / "Desktop/sts2",
            Path("C:/Program Files/Steam/steamapps/common/SlayTheSpire2"),
            Path("C:/Program Files (x86)/Steam/steamapps/common/SlayTheSpire2"),
            # Steam 库
            Path.home() / ".steam/steamapps/common/SlayTheSpire2",
            # Mac
            Path.home() / "Library/Application Support/Steam/steamapps/common/SlayTheSpire2",
            # Linux
            Path.home() / ".steam/steamapps/common/SlayTheSpire2",
        ]

        # 添加用户指定的路径
        custom_path = os.getenv("STS2_PATH")
        if custom_path:
            possible_paths.insert(0, Path(custom_path))

        for path in possible_paths:
            if path.exists() and (path / "SlayTheSpire2.exe").exists():
                log.info(f"✓ 找到游戏目录: {path}")
                self.game_path = path
                return path

        log.warning("✗ 找不到 STS2 游戏目录")
        return None

    def find_active_log_file(self) -> Optional[Path]:
        """
        查找日志文件夹

        优先级：
        1. 自定义路径（用户手动设置）
        2. 自动搜索常见位置

        返回日志文件夹路径，而不是具体的日志文件
        """
        # 如果有自定义路径，优先使用
        if self.custom_log_path:
            # 如果自定义路径是文件，返回其所在文件夹
            if self.custom_log_path.is_file():
                log_dir = self.custom_log_path.parent
            else:
                log_dir = self.custom_log_path

            if log_dir.exists():
                log.info(f"✓ 使用自定义日志文件夹: {log_dir}")
                self.log_path = log_dir
                return log_dir
            else:
                log.warning(f"✗ 自定义日志路径不存在: {log_dir}")

        # 日志文件夹位置优先级
        log_locations = [
            # Windows AppData/Roaming (最新版本的主要位置，直接搜索)
            Path.home() / "AppData" / "Roaming" / "SlayTheSpire2",
            # Windows AppData/Roaming 子目录
            Path.home() / "AppData" / "Roaming" / "SlayTheSpire2" / "logs",
            # Windows AppData/Local
            Path.home() / "AppData" / "Local" / "SlayTheSpire2" / "saves",
            Path.home() / "AppData" / "Local" / "SlayTheSpire2" / "logs",
            # 游戏安装目录
            self.game_path / "logs" if self.game_path else None,
            self.game_path / "user://logs" if self.game_path else None,
            # Linux/Mac
            Path.home() / ".local" / "share" / "SlayTheSpire2" / "logs",
            Path.home() / ".config/godot/app_userdata/SlayTheSpire2/logs",
        ]

        for logs_dir in log_locations:
            if logs_dir is None or not logs_dir.exists():
                continue

            # 检查是否有日志文件
            log_files = list(logs_dir.glob("*.log")) + list(logs_dir.glob("*.txt"))
            if log_files:
                log.info(f"✓ 找到活跃日志文件夹: {logs_dir}")
                self.log_path = logs_dir
                return logs_dir

            # 如果这是一个根路径，也检查其子文件夹中的日志
            if logs_dir == Path.home() / "AppData" / "Roaming" / "SlayTheSpire2":
                all_logs = list(logs_dir.rglob("*.log")) + list(logs_dir.rglob("*.txt"))
                if all_logs:
                    # 找到包含日志文件的文件夹
                    log_folder = all_logs[0].parent
                    log.info(f"✓ 找到活跃日志文件夹: {log_folder}")
                    self.log_path = log_folder
                    return log_folder

        log.warning("✗ 找不到游戏日志文件夹")
        return None

    def find_save_file(self) -> Optional[Path]:
        """
        查找存档文件夹

        优先级：
        1. 自定义路径（用户手动设置）
        2. 自动搜索常见位置

        返回存档文件夹路径，而不是具体的存档文件
        """
        # 如果有自定义路径，优先使用
        if self.custom_save_path:
            # 如果自定义路径是文件，返回其所在文件夹
            if self.custom_save_path.is_file():
                save_dir = self.custom_save_path.parent
            else:
                save_dir = self.custom_save_path

            if save_dir.exists():
                log.info(f"✓ 使用自定义存档文件夹: {save_dir}")
                self.save_path = save_dir
                return save_dir
            else:
                log.warning(f"✗ 自定义存档路径不存在: {save_dir}")

        # 搜索 Steam 存档文件夹（新版本优先级最高）
        # 路径结构: AppData/Roaming/SlayTheSpire2/steam/<SteamID>/profile<N>/saves
        steam_save_locations = [
            Path.home() / "AppData" / "Roaming" / "SlayTheSpire2",  # 直接搜索这个路径
        ]

        for base_path in steam_save_locations:
            if not base_path.exists():
                continue

            try:
                # 搜索所有 profile*/saves 文件夹
                save_dirs = list(base_path.glob("**/profile*/saves"))
                if save_dirs:
                    # 优先使用最近修改的
                    save_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    for save_dir in save_dirs:
                        # 检查是否有 .save 文件
                        if list(save_dir.glob("*.save")):
                            log.info(f"✓ 找到 Steam 存档文件夹: {save_dir}")
                            self.save_path = save_dir
                            return save_dir
            except Exception as e:
                log.debug(f"搜索 Steam 存档时出错: {e}")

        # 常规文件夹路径（备用）
        possible_dirs = [
            # Windows AppData
            Path.home() / "AppData" / "Local" / "SlayTheSpire2" / "saves",
            Path.home() / "AppData" / "Local" / "SlayTheSpire2" / "persistence",
            Path.home() / "AppData" / "Roaming" / "SlayTheSpire2" / "saves",
            # 游戏安装目录
            self.game_path / "user://persistence" if self.game_path else None,
            self.game_path / "preferences" if self.game_path else None,
            # Linux/Mac
            Path.home() / ".local" / "share" / "SlayTheSpire2",
            Path.home() / ".config/godot/app_userdata/SlayTheSpire2/persistence",
        ]

        # 检查常规路径
        for path in possible_dirs:
            if path is not None and path.exists():
                # 检查是否有 .save 文件
                if list(path.glob("*.save")):
                    log.info(f"✓ 找到存档文件夹: {path}")
                    self.save_path = path
                    return path

        log.warning("✗ 找不到存档文件夹")
        return None

    def read_save_file_data(self) -> Optional[Dict]:
        """
        从 current_run.save 文件读取游戏进度数据

        返回包含的信息：
        - character: 角色
        - floor: 楼层
        - hp, max_hp: 生命值
        - gold: 金币
        - deck: 卡组
        - relics: 遗物
        """
        if not self.save_path:
            return None

        try:
            # self.save_path 是文件夹，需要找到其中的 current_run.save 文件
            save_file = None

            # 方案 1: 直接查找 current_run.save
            if (self.save_path / 'current_run.save').exists():
                save_file = self.save_path / 'current_run.save'
            else:
                # 方案 2: 在文件夹中查找任何 .save 文件
                save_files = list(self.save_path.glob('*.save'))
                if save_files:
                    save_file = save_files[0]  # 取第一个 .save 文件

            if not save_file:
                log.warning(f"存档文件夹中没有找到 .save 文件: {self.save_path}")
                return None

            log.debug(f"读取存档文件: {save_file}")
            with open(save_file, 'r', encoding='utf-8') as f:
                save_data = json.load(f)
        except Exception as e:
            log.warning(f"无法读取存档文件: {e}")
            return None

        # 提取游戏状态
        try:
            if 'players' not in save_data or not save_data['players']:
                return None

            player = save_data['players'][0]

            return {
                'character': player.get('character_id', 'unknown'),
                'floor': save_data.get('current_act_index', 0) + 1,
                'ascension': save_data.get('ascension', 0),
                'hp': player.get('current_hp', 0),
                'max_hp': player.get('max_hp', 1),
                'gold': player.get('gold', 0),
                'deck': [card.get('id', str(card)) for card in player.get('deck', [])],
                'relics': [relic.get('id', str(relic)) for relic in player.get('relics', [])],
                'timestamp': datetime.now().isoformat(),
            }
        except Exception as e:
            log.warning(f"解析存档数据失败: {e}")
            return None

    def parse_log_line(self, line: str) -> Dict:
        """
        解析单行日志

        尝试多种格式：
        1. JSON 格式
        2. 键值对格式
        3. 时间戳+消息格式
        """
        line = line.strip()
        if not line:
            return {}

        # 尝试 JSON
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass

        # 尝试键值对
        if "=" in line:
            try:
                pairs = line.split("|")
                data = {}
                for pair in pairs:
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        data[k.strip()] = v.strip()
                return data
            except Exception:
                pass

        # 提取关键词
        data = {"raw": line}

        # 检查关键字
        if "floor" in line.lower():
            data["type"] = "floor_update"
        elif "card" in line.lower() and "select" in line.lower():
            data["type"] = "card_select"
        elif "relic" in line.lower():
            data["type"] = "relic_obtain"
        elif "combat" in line.lower():
            data["type"] = "combat_start"

        return data

    def extract_game_state(self, log_data: Dict) -> Optional[Dict]:
        """
        从日志数据中提取游戏状态

        处理多种日志格式，提取：
        - character: 角色
        - floor: 楼层
        - act: 章节
        - hp/max_hp: 生命值
        - gold: 金币
        - deck: 卡组
        - relics: 遗物
        - hand: 当前手牌
        """
        if not log_data:
            return None

        update = {"timestamp": datetime.now().isoformat()}
        changed = False

        # 角色信息
        for key in ["character", "class", "hero"]:
            if key in log_data:
                char = str(log_data[key]).lower()
                if char != self.current_state.get("character"):
                    update["character"] = char
                    self.current_state["character"] = char
                    changed = True
                break

        # 楼层信息
        for key in ["floor", "level"]:
            if key in log_data:
                try:
                    floor = int(log_data[key])
                    if floor != self.current_state.get("floor"):
                        update["floor"] = floor
                        self.current_state["floor"] = floor
                        changed = True
                except (ValueError, TypeError):
                    pass
                break

        # 章节信息
        for key in ["act", "ascension"]:
            if key in log_data:
                try:
                    act = int(log_data[key])
                    if act != self.current_state.get("act"):
                        update["act"] = act
                        self.current_state["act"] = act
                        changed = True
                except (ValueError, TypeError):
                    pass
                break

        # 生命值
        for key in ["hp", "health"]:
            if key in log_data:
                try:
                    hp = int(log_data[key])
                    if hp != self.current_state.get("hp"):
                        update["hp"] = hp
                        self.current_state["hp"] = hp
                        changed = True
                except (ValueError, TypeError):
                    pass
                break

        # 最大生命值
        for key in ["max_hp", "max_health"]:
            if key in log_data:
                try:
                    max_hp = int(log_data[key])
                    if max_hp != self.current_state.get("max_hp"):
                        update["max_hp"] = max_hp
                        self.current_state["max_hp"] = max_hp
                        changed = True
                except (ValueError, TypeError):
                    pass
                break

        # 金币
        for key in ["gold", "money"]:
            if key in log_data:
                try:
                    gold = int(log_data[key])
                    if gold != self.current_state.get("gold"):
                        update["gold"] = gold
                        self.current_state["gold"] = gold
                        changed = True
                except (ValueError, TypeError):
                    pass
                break

        # 卡组
        for key in ["deck", "cards"]:
            if key in log_data:
                deck = log_data[key]
                if isinstance(deck, str):
                    try:
                        deck = json.loads(deck)
                    except json.JSONDecodeError:
                        deck = [x.strip() for x in deck.split(",")]
                if deck != self.current_state.get("deck"):
                    update["deck"] = deck
                    self.current_state["deck"] = deck
                    changed = True
                break

        # 遗物
        for key in ["relics", "artifacts"]:
            if key in log_data:
                relics = log_data[key]
                if isinstance(relics, str):
                    try:
                        relics = json.loads(relics)
                    except json.JSONDecodeError:
                        relics = [x.strip() for x in relics.split(",")]
                if relics != self.current_state.get("relics"):
                    update["relics"] = relics
                    self.current_state["relics"] = relics
                    changed = True
                break

        # 手牌
        for key in ["hand", "current_hand"]:
            if key in log_data:
                hand = log_data[key]
                if isinstance(hand, str):
                    try:
                        hand = json.loads(hand)
                    except json.JSONDecodeError:
                        hand = [x.strip() for x in hand.split(",")]
                if hand != self.current_state.get("hand"):
                    update["hand"] = hand
                    self.current_state["hand"] = hand
                    changed = True
                break

        return update if changed else None

    def read_save_file(self) -> Optional[Dict]:
        """读取游戏存档文件"""
        save_path = self.find_save_file()
        if not save_path:
            return None

        try:
            with open(save_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning(f"读取存档失败: {e}")
            return None

    def watch_logs(self):
        """
        后台线程：监视日志文件变化

        在日志文件夹中找到最新的日志文件，持续读取新内容
        """
        if not self.log_path:
            if not self.find_active_log_file():
                log.error("无法找到日志文件夹，停止监视")
                self.trigger_log_status(False)
                return

        log.info(f"开始监视日志文件夹: {self.log_path}")
        self.trigger_log_status(True, self.log_path)

        # 在日志文件夹中查找最新的日志文件
        current_log_file = None
        f = None

        try:
            while self.is_running:
                try:
                    # 查找日志文件夹中的最新日志文件
                    log_files = list(self.log_path.glob("*.log")) + list(self.log_path.glob("*.txt"))
                    if not log_files:
                        time.sleep(1)
                        continue

                    latest_log = max(log_files, key=lambda p: p.stat().st_mtime)

                    # 如果日志文件变化，打开新文件
                    if latest_log != current_log_file:
                        if f:
                            f.close()
                        current_log_file = latest_log
                        log.info(f"监视日志文件: {current_log_file}")
                        f = open(current_log_file, "r", encoding="utf-8", errors="ignore")
                        # 跳到文件末尾
                        f.seek(0, 2)
                        self.last_log_position = f.tell()

                    # 读取新行
                    if f:
                        line = f.readline()

                        if line:
                            # 新内容出现
                            parsed = self.parse_log_line(line)
                            state_update = self.extract_game_state(parsed)

                            if state_update:
                                log.debug(f"游戏状态更新: {state_update}")
                                self.trigger_callbacks(state_update)
                        else:
                            # 等待新数据
                            time.sleep(0.2)

                except Exception as e:
                    log.error(f"日志读取错误: {e}")
                    time.sleep(1)

        except Exception as e:
            log.error(f"日志监视失败: {e}")
        finally:
            if f:
                f.close()

    def trigger_callbacks(self, state: Dict):
        """触发所有注册的回调"""
        for callback in self.callbacks:
            try:
                callback(state)
            except Exception as e:
                log.error(f"回调执行失败: {e}")

    def on_state_change(self, callback: Callable):
        """注册状态变化回调"""
        self.callbacks.append(callback)
        log.debug(f"注册回调: {callback.__name__ if hasattr(callback, '__name__') else 'unknown'}")

    def on_log_status_change(self, callback: Callable):
        """注册日志监视状态变化回调"""
        self.log_status_callbacks.append(callback)
        log.debug(f"注册日志状态回调: {callback.__name__ if hasattr(callback, '__name__') else 'unknown'}")

    def trigger_log_status(self, active: bool, path: Optional[Path] = None):
        """触发日志状态回调"""
        self._log_monitoring_active = active
        for callback in self.log_status_callbacks:
            try:
                callback({
                    "active": active,
                    "path": str(path) if path else None,
                    "timestamp": datetime.now().isoformat(),
                })
            except Exception as e:
                log.error(f"日志状态回调执行失败: {e}")

    def get_current_state(self) -> Dict:
        """获取当前游戏状态"""
        return self.current_state.copy()

    def start(self):
        """启动后台监视"""
        if self.is_running:
            log.warning("监视已在运行")
            return

        # 自动查找游戏目录
        if not self.game_path:
            self.find_game_directory()
            if not self.game_path:
                log.warning("无法找到游戏目录")

        # 查找存档文件（优先于日志）
        if not self.save_path:
            self.find_save_file()
            if self.save_path:
                log.info("✓ 找到存档文件夹: {self.save_path}")

        # 尝试从存档读取初始状态
        if self.save_path:
            try:
                save_data = self.read_save_file_data()
                if save_data:
                    log.info(f"✓ 成功读取存档数据")
                    self.extract_game_state(save_data)
                    self.trigger_callbacks(save_data)
                    self.current_state.update(save_data)
                else:
                    log.debug("存档数据为空或无法解析")
            except Exception as e:
                log.warning(f"读取存档失败: {e}")

        # 查找日志文件
        if not self.log_path:
            self.find_active_log_file()
            if not self.log_path:
                log.warning("无法找到日志文件夹，将使用存档数据进行监视")

        self.is_running = True

        # 启动监视线程
        thread = threading.Thread(target=self.watch_logs, daemon=True, name="GameWatcherThread")
        thread.start()

        log.info("✓ 游戏状态监视已启动")

    def stop(self):
        """停止监视"""
        self.is_running = False
        log.info("✓ 游戏状态监视已停止")


def main():
    """测试/演示"""
    watcher = STS2GameWatcher()

    def on_state_update(state: Dict):
        """状态更新回调"""
        print(f"🎮 游戏状态: {json.dumps(state, ensure_ascii=False, indent=2)}")

    watcher.on_state_change(on_state_update)
    watcher.start()

    log.info("监视运行中... (按 Ctrl+C 停止)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("正在停止...")
        watcher.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
