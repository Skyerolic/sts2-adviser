"""
backend/main.py
FastAPI 服务器

端点：
    POST /api/evaluate    —— 评估当前选卡池
    GET  /api/cards       —— 获取卡牌库（全量）
    GET  /api/archetypes  —— 获取套路库

运行：
    uvicorn backend.main:app --host 127.0.0.1 --port 8000
    # 如需更换端口（如 8001），可用：
    uvicorn backend.main:app --host 127.0.0.1 --port 8001

或设置环境变量 STS2_ADVISER_PORT 覆盖默认端口
"""

from __future__ import annotations

import sys
import io

# Fix Windows console encoding so Chinese/Unicode print() calls don't crash the process
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import asyncio
import json
import logging
import os
import socket
import uvicorn
from typing import Optional, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .archetypes import archetype_library
from .evaluator import CardEvaluator
from .models import Card, RunState, EvaluationResult, Character, Rarity, CardType, CardKeywords
from utils.paths import get_app_root

# 导入游戏监视器和配置管理器
try:
    from scripts.game_watcher import STS2GameWatcher
    from scripts.config_manager import get_save_path, get_log_path
    GAME_WATCHER_AVAILABLE = True
except ImportError:
    GAME_WATCHER_AVAILABLE = False
    logging.warning("GameWatcher not available")
    def get_save_path(): return None  # noqa: E301
    def get_log_path(): return None   # noqa: E301

# 导入视觉识别桥接器
try:
    from vision.vision_bridge import VisionBridge
    VISION_BRIDGE_AVAILABLE = True
except ImportError:
    VISION_BRIDGE_AVAILABLE = False
    logging.warning("VisionBridge not available (missing vision dependencies)")

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def find_free_port(start_port: int = 8000, max_attempts: int = 20) -> int:
    """
    找到一个可用的端口

    Args:
        start_port: 起始端口号
        max_attempts: 最多尝试次数

    Returns:
        可用的端口号
    """
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"无法找到可用端口 ({start_port}-{start_port + max_attempts - 1})")


# ---------------------------------------------------------------------------
# App 初始化
# ---------------------------------------------------------------------------

app = FastAPI(
    title="STS2 Card Adviser API",
    description="杀戮尖塔2 选卡助手后端服务",
    version="0.1.0",
)

# CORS：允许本地前端（PyQt WebEngine / Electron 等）访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # 生产环境改为具体地址
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 全局卡库（从 JSON 文件加载）
# ---------------------------------------------------------------------------

def _load_card_db_from_json() -> dict[str, Card]:
    """
    从 data/cards.json 加载卡牌库。
    如果文件不存在或解析失败，返回空字典。
    """
    json_path = get_app_root() / "data" / "cards.json"
    if not json_path.exists():
        log.warning("Card database not found at %s", json_path)
        return {}

    try:
        with open(json_path, "r", encoding="utf-8", errors="ignore") as f:
            raw_cards = json.load(f)

        db: dict[str, Card] = {}
        for r in raw_cards:
            try:
                # 映射 JSON 字段到 Card 模型
                color = r.get("color", "any").lower()
                if color in Character.__members__.values() or color in [c.value for c in Character]:
                    character = Character(color)
                else:
                    # 尝试 fallback 到 known values
                    fallback_map = {
                        "red": Character.IRONCLAD,
                        "blue": Character.DEFECT,
                        "purple": Character.WATCHER,
                        "green": Character.SILENT,
                        "grey": Character.COLORLESS,
                        "colorless": Character.COLORLESS,
                        "curse": Character.CURSE,
                        "status": Character.STATUS,
                        "event": Character.EVENT,
                        "regent": Character.REGENT,
                        "necrobinder": Character.NECROBINDER,
                        "quest": Character.QUEST,
                        "any": Character.ANY,
                    }
                    character = fallback_map.get(color, Character.ANY)
                    if character == Character.ANY:
                        log.warning("Unknown character color: %s for card %s", color, r.get('id', '?'))

                rarity_key = r.get("rarity_key", "common").lower()
                if rarity_key in Rarity.__members__.values() or rarity_key in [c.value for c in Rarity]:
                    rarity = Rarity(rarity_key)
                else:
                    fallback_map = {
                        "basic": Rarity.BASIC,
                        "starter": Rarity.STARTER,
                        "common": Rarity.COMMON,
                        "uncommon": Rarity.UNCOMMON,
                        "rare": Rarity.RARE,
                        "special": Rarity.SPECIAL,
                        "ancient": Rarity.ANCIENT,
                        "curse": Rarity.CURSE,
                        "status": Rarity.STATUS,
                        "event": Rarity.EVENT,
                        "quest": Rarity.QUEST,
                    }
                    rarity = fallback_map.get(rarity_key, Rarity.COMMON)
                    if rarity == Rarity.COMMON:
                        log.warning("Unknown rarity: %s for card %s", rarity_key, r.get('id', '?'))

                type_key = r.get("type_key", "skill").lower()
                if type_key in CardType.__members__.values() or type_key in [c.value for c in CardType]:
                    card_type = CardType(type_key)
                else:
                    fallback_map = {
                        "attack": CardType.ATTACK,
                        "skill": CardType.SKILL,
                        "power": CardType.POWER,
                        "status": CardType.STATUS,
                        "curse": CardType.CURSE,
                        "quest": CardType.QUEST,
                    }
                    card_type = fallback_map.get(type_key, CardType.SKILL)
                    if card_type == CardType.SKILL:
                        log.warning("Unknown card type: %s for card %s", type_key, r.get('id', '?'))

                card = Card(
                    id=r["id"].lower(),  # 统一小写
                    name=r["name"],
                    character=character,
                    rarity=rarity,
                    card_type=card_type,
                    cost=r.get("cost", 0),
                    description=r.get("description", ""),
                    base_damage=r.get("damage"),
                    base_block=r.get("block"),
                    base_draw=r.get("cards_draw"),
                    keywords=CardKeywords(),
                )
                db[card.id] = card
            except Exception as e:
                # 跳过无法映射的卡牌
                log.error("Skipping card %s: %s", r.get('id', 'unknown'), e)
                continue
        log.info("Loaded %d cards from %s", len(db), json_path)
        return db
    except Exception as e:
        log.error("Error loading card database: %s", e)
        return {}


def _load_raw_card_db() -> dict[str, dict]:
    """加载 cards.json 原始字典（保留 powers_applied / keywords_key 等推断层所需字段）"""
    json_path = get_app_root() / "data" / "cards.json"
    if not json_path.exists():
        return {}
    try:
        with open(json_path, "r", encoding="utf-8", errors="ignore") as f:
            raw_cards = json.load(f)
        return {r["id"].lower(): r for r in raw_cards if "id" in r}
    except Exception as e:
        log.error("Error loading raw card db: %s", e)
        return {}


CARD_DB: dict[str, Card] = _load_card_db_from_json()
RAW_CARD_DB: dict[str, dict] = _load_raw_card_db()


# ---------------------------------------------------------------------------
# 社区数据加载
# ---------------------------------------------------------------------------

from dataclasses import dataclass as _dc

@_dc
class CommunityStats:
    """一张卡的社区统计数据（来自 card_library.json）"""
    card_id:          str
    win_rate_pct:     float   # 原始胜率百分比，e.g. 61.4
    pick_rate_pct:    float   # 原始选取率百分比，e.g. 34.1
    skip_rate_pct:    float   # 原始放弃率百分比，e.g. 65.9
    community_score:  float   # 归一化综合评分 0~1
    divergence:       float   # win_norm - pick_norm；正=潜力股，负=高估风险


def _load_community_db() -> "dict[str, CommunityStats]":
    """
    从 data/card_library.json 加载社区统计数据。
    win_rate/pick_rate 任一为 null 的卡不加入 db（保持缺失语义）。
    """
    from .scoring import community_score_from_raw
    json_path = get_app_root() / "data" / "card_library.json"
    if not json_path.exists():
        log.warning("card_library.json not found at %s", json_path)
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        db: dict[str, CommunityStats] = {}
        for entry in raw:
            cid = entry.get("id", "").lower()
            if not cid:
                continue
            wr_str = entry.get("win_rate")
            pr_str = entry.get("pick_rate")
            if wr_str is None or pr_str is None:
                continue
            wr = float(str(wr_str).rstrip("%"))
            pr = float(str(pr_str).rstrip("%"))
            sr_str = entry.get("skip_rate")
            sr = float(str(sr_str).rstrip("%")) if sr_str is not None else round(100.0 - pr, 1)
            cs, div = community_score_from_raw(wr, pr, sr)
            db[cid] = CommunityStats(cid, wr, pr, sr, cs, div)
        log.info("Loaded %d community records from card_library.json", len(db))
        return db
    except Exception as e:
        log.error("Error loading community db: %s", e)
        return {}


COMMUNITY_DB: "dict[str, CommunityStats]" = _load_community_db()


def _load_summaries_db() -> "dict[str, dict]":
    """从 data/card_summaries.json 加载卡牌文字总结（card_id 大写 -> dict）。"""
    json_path = get_app_root() / "data" / "card_summaries.json"
    if not json_path.exists():
        log.warning("card_summaries.json not found at %s", json_path)
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        log.info("Loaded %d card summaries", len(raw))
        return raw
    except Exception as e:
        log.error("Error loading summaries: %s", e)
        return {}


SUMMARIES_DB: "dict[str, dict]" = _load_summaries_db()


# ---------------------------------------------------------------------------
# 请求 / 响应模型
# ---------------------------------------------------------------------------

class EvaluateRequest(BaseModel):
    run_state: RunState
    language: str = "zh"   # "zh" 或 "en"


class EvaluateResponse(BaseModel):
    results: list[EvaluationResult]
    detected_archetypes: list[str]   # 检测到的套路 id 列表


# ---------------------------------------------------------------------------
# WebSocket 连接管理器
# ---------------------------------------------------------------------------

class ConnectionManager:
    """管理 WebSocket 连接和广播"""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.game_watcher: Optional["STS2GameWatcher"] = None
        self.vision_bridge: Optional["VisionBridge"] = None
        self.is_watching = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None  # 保存事件循环引用

    async def connect(self, websocket: WebSocket):
        """接受新的 WebSocket 连接"""
        await websocket.accept()
        self.active_connections.add(websocket)
        # 保存当前事件循环，供后台线程回调使用
        self._loop = asyncio.get_event_loop()
        print(f"[WebSocket] 客户端已连接 (总共: {len(self.active_connections)})")

        # 启动游戏监视（在 executor 中执行，避免阻塞 asyncio 握手）
        if not self.is_watching:
            loop = asyncio.get_event_loop()
            if GAME_WATCHER_AVAILABLE:
                await loop.run_in_executor(None, self.start_game_watcher)
            if VISION_BRIDGE_AVAILABLE:
                await loop.run_in_executor(None, self.start_vision_bridge)

        # 连接后立即推送当前游戏状态（不等日志更新）
        if self.game_watcher:
            initial_state = self.game_watcher.get_current_state()
            if initial_state.get("character"):
                await websocket.send_json({"type": "game_state", "data": initial_state})

    def disconnect(self, websocket: WebSocket):
        """移除 WebSocket 连接"""
        self.active_connections.discard(websocket)
        print(f"[WebSocket] 客户端已断开 (剩余: {len(self.active_connections)})")

        # 如果没有客户端，停止所有监视
        if not self.active_connections:
            if self.game_watcher:
                self.stop_game_watcher()
            if self.vision_bridge:
                self.stop_vision_bridge()

    async def broadcast(self, message: dict):
        """广播消息到所有已连接的客户端"""
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                print(f"[WebSocket] 广播失败: {e}")
                disconnected.add(connection)

        # 清理断开的连接
        for connection in disconnected:
            self.active_connections.discard(connection)

    def _make_broadcast_callback(self, source_label: str, msg_type: str):
        """创建一个从后台线程安全广播的回调函数"""
        def callback(data: dict):
            loop = self._loop
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self.broadcast({"type": msg_type, "data": data}),
                    loop=loop,
                )
            else:
                print(f"[{source_label}] 无法广播: 事件循环未运行")
        return callback

    def start_game_watcher(self):
        """启动日志文件游戏状态监视"""
        if self.is_watching and self.game_watcher:
            return

        try:
            try:
                from scripts.config_manager import get_save_path, get_log_path
                custom_save_path = get_save_path()
                custom_log_path = get_log_path()
            except Exception:
                custom_save_path = None
                custom_log_path = None

            self.game_watcher = STS2GameWatcher(
                custom_save_path=custom_save_path,
                custom_log_path=custom_log_path,
            )
            self.game_watcher.on_state_change(
                self._make_broadcast_callback("GameWatcher", "game_state")
            )
            self.game_watcher.on_log_status_change(
                self._make_broadcast_callback("GameWatcher", "log_status")
            )
            self.game_watcher.start()
            self.is_watching = True
            print("[GameWatcher] ✓ 游戏监视已启动")
        except Exception as e:
            print(f"[GameWatcher] ✗ 启动失败: {e}")

    def stop_game_watcher(self):
        """停止日志文件监视"""
        if self.game_watcher:
            self.game_watcher.stop()
            self.is_watching = False
            print("[GameWatcher] ✓ 游戏监视已停止")

    def start_vision_bridge(self):
        """启动视觉识别桥接器"""
        if self.vision_bridge:
            return
        try:
            self.vision_bridge = VisionBridge()
            self.vision_bridge.on_state_change(
                self._make_broadcast_callback("VisionBridge", "vision_state")
            )
            self.vision_bridge.on_log_status_change(
                self._make_broadcast_callback("VisionBridge", "vision_status")
            )
            self.vision_bridge.start()
            print("[VisionBridge] ✓ 视觉识别已启动")
        except Exception as e:
            print(f"[VisionBridge] ✗ 启动失败: {e}")

    def stop_vision_bridge(self):
        """停止视觉识别桥接器"""
        if self.vision_bridge:
            self.vision_bridge.stop()
            self.vision_bridge = None
            print("[VisionBridge] ✓ 视觉识别已停止")


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------

@app.get("/", tags=["health"])
async def health_check():
    return {"status": "ok", "service": "STS2 Card Adviser"}


@app.post("/api/evaluate", response_model=EvaluateResponse, tags=["evaluate"])
async def evaluate_cards(request: EvaluateRequest):
    """
    评估当前选卡池，返回每张卡的评分与推荐理由。

    请求体示例：
    {
      "run_state": {
        "character": "silent",
        "floor": 8,
        "hp": 60,
        "max_hp": 70,
        "gold": 120,
        "deck": ["blade_dance", "cloak_and_dagger", "deadly_poison"],
        "relics": [],
        "card_choices": ["catalyst", "ninjutsu", "reflex"]
      }
    }
    """
    run_state = request.run_state

    if not run_state.card_choices:
        raise HTTPException(status_code=400, detail="card_choices 不能为空")

    evaluator = CardEvaluator(CARD_DB, archetype_library,
                              raw_card_db=RAW_CARD_DB,
                              community_db=COMMUNITY_DB,
                              summaries_db=SUMMARIES_DB)

    try:
        detected = evaluator.detect_archetypes(run_state)
        results = evaluator.rank_cards(run_state, language=request.language)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"评估失败: {exc}") from exc

    return EvaluateResponse(
        results=results,
        detected_archetypes=[
            (a.name_zh or a.name) if request.language == "zh" else (a.name or a.id)
            for a in detected
        ],
    )


@app.get("/api/cards", tags=["data"])
async def get_cards(character: str | None = None):
    """
    获取卡牌库。
    可选 character 过滤（e.g. ?character=silent）。
    """
    cards = list(CARD_DB.values())
    if character:
        try:
            char = Character(character)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效 character: {character}")
        cards = [c for c in cards if c.character == char]
    return {"cards": [c.model_dump() for c in cards], "total": len(cards)}


@app.get("/api/archetypes", tags=["data"])
async def get_archetypes(character: str | None = None):
    """
    获取套路库。
    可选 character 过滤。
    """
    archetypes = archetype_library.all()
    if character:
        try:
            char = Character(character)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效 character: {character}")
        archetypes = [a for a in archetypes if a.character == char]
    return {
        "archetypes": [a.model_dump() for a in archetypes],
        "total": len(archetypes),
    }


class ConfigRequest(BaseModel):
    """配置更新请求"""
    save_path: str | None = None
    log_path: str | None = None


@app.post("/api/config", tags=["config"])
async def update_config(request: ConfigRequest):
    """
    更新配置（存档路径、日志路径等）

    请求体示例：
    {
        "save_path": "C:\\Users\\HH275\\AppData\\LocalLow\\Mega Crit Games\\Slay the Spire 2\\Saves",
        "log_path": "C:\\Users\\HH275\\AppData\\LocalLow\\Mega Crit Games\\Slay the Spire 2"
    }
    """
    try:
        from scripts.config_manager import set_save_path, set_log_path

        # 更新配置
        if request.save_path:
            set_save_path(request.save_path)
        if request.log_path:
            set_log_path(request.log_path)

        # 重启 GameWatcher 使用新配置
        if manager.game_watcher:
            manager.stop_game_watcher()

        # 等待一下再重启
        await asyncio.sleep(0.5)
        if GAME_WATCHER_AVAILABLE:
            manager.start_game_watcher()

        return {"status": "success", "message": "配置已更新，GameWatcher 已重启"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"配置更新失败: {e}") from e


@app.websocket("/ws/game-state")
async def websocket_game_state(websocket: WebSocket):
    """
    WebSocket 连接用于实时游戏状态同步

    前端连接后，会自动接收游戏状态更新：
    {
        "type": "game_state",
        "data": {
            "character": "silent",
            "floor": 8,
            "hp": 60,
            "max_hp": 70,
            "gold": 100,
            "deck": ["Shiv", "Shiv", ...],
            "relics": ["Ring of Serpent", ...],
            "hand": ["Shiv"],
            "timestamp": "2026-03-28T12:34:56.789Z"
        }
    }
    """
    await manager.connect(websocket)
    try:
        while True:
            # 保持连接活跃
            data = await websocket.receive_text()

            # 可以处理来自客户端的消息
            if data == "ping":
                await websocket.send_json({"type": "pong"})
            elif data == "get_state":
                # 发送当前游戏状态
                if manager.game_watcher:
                    state = manager.game_watcher.get_current_state()
                    await websocket.send_json({
                        "type": "game_state",
                        "data": state
                    })
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"[WebSocket] 错误: {e}")
        manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# 直接运行入口（供调试用；正式启动走 main.py）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 支持通过环境变量 STS2_ADVISER_PORT 配置端口，默认 8000
    port_env = os.environ.get("STS2_ADVISER_PORT")
    if port_env:
        port = int(port_env)
    else:
        port = find_free_port(8000, 20)
    print(f"[main] 后端服务将启动在端口: {port}")
    uvicorn.run("backend.main:app", host="127.0.0.1", port=port, reload=True)
