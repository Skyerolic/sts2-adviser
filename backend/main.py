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

import json
import uvicorn
import os
import socket
import asyncio
import logging
from pathlib import Path
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Set, Optional

from .archetypes import archetype_library
from .evaluator import CardEvaluator
from .models import Card, RunState, EvaluationResult, Character, Rarity, CardType, CardKeywords

# 导入游戏监视器和配置管理器
import sys
from pathlib import Path as PathlibPath
sys.path.insert(0, str(PathlibPath(__file__).parent.parent / "scripts"))
try:
    from game_watcher import STS2GameWatcher
    from config_manager import get_save_path, get_log_path
    GAME_WATCHER_AVAILABLE = True
except ImportError:
    GAME_WATCHER_AVAILABLE = False
    logging.warning("GameWatcher not available")
    def get_save_path(): return None
    def get_log_path(): return None

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
    json_path = Path(__file__).parent.parent / "data" / "cards.json"
    if not json_path.exists():
        print(f"Warning: Card database not found at {json_path}")
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
                        print(f"[WARN] Unknown character color: {color} for card {r.get('id', '?')}")

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
                        print(f"[WARN] Unknown rarity: {rarity_key} for card {r.get('id', '?')}")

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
                        print(f"[WARN] Unknown card type: {type_key} for card {r.get('id', '?')}")

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
                print(f"[ERROR] Skipping card {r.get('id', 'unknown')}: {e}")
                continue
        print(f"Loaded {len(db)} cards from {json_path}")
        return db
    except Exception as e:
        print(f"Error loading card database: {e}")
        return {}


CARD_DB: dict[str, Card] = _load_card_db_from_json()

# ---------------------------------------------------------------------------
# 请求 / 响应模型
# ---------------------------------------------------------------------------

class EvaluateRequest(BaseModel):
    run_state: RunState


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
        self.is_watching = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None  # 保存事件循环引用

    async def connect(self, websocket: WebSocket):
        """接受新的 WebSocket 连接"""
        await websocket.accept()
        self.active_connections.add(websocket)
        # 保存当前事件循环，供后台线程回调使用
        self._loop = asyncio.get_event_loop()
        print(f"[WebSocket] 客户端已连接 (总共: {len(self.active_connections)})")

        # 启动游戏监视（如果还没启动）
        if not self.is_watching and GAME_WATCHER_AVAILABLE:
            self.start_game_watcher()

        # 连接后立即推送当前游戏状态（不等日志更新）
        if self.game_watcher:
            initial_state = self.game_watcher.get_current_state()
            if initial_state.get("character"):
                await websocket.send_json({"type": "game_state", "data": initial_state})

    def disconnect(self, websocket: WebSocket):
        """移除 WebSocket 连接"""
        self.active_connections.discard(websocket)
        print(f"[WebSocket] 客户端已断开 (剩余: {len(self.active_connections)})")

        # 如果没有客户端，停止游戏监视
        if not self.active_connections and self.game_watcher:
            self.stop_game_watcher()

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

    def start_game_watcher(self):
        """启动游戏状态监视"""
        if self.is_watching:
            return

        try:
            # 从配置文件读取自定义路径
            try:
                from config_manager import get_save_path, get_log_path
                custom_save_path = get_save_path()
                custom_log_path = get_log_path()
            except:
                custom_save_path = None
                custom_log_path = None

            self.game_watcher = STS2GameWatcher(
                custom_save_path=custom_save_path,
                custom_log_path=custom_log_path
            )

            def on_game_update(state: dict):
                """游戏状态更新回调（从后台线程调用）"""
                loop = self._loop
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self.broadcast({"type": "game_state", "data": state}),
                        loop=loop
                    )
                else:
                    print(f"[GameWatcher] 无法广播游戏状态: 事件循环未运行")

            def on_log_status_update(status: dict):
                """日志监视状态更新回调（从后台线程调用）"""
                loop = self._loop
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self.broadcast({"type": "log_status", "data": status}),
                        loop=loop
                    )
                else:
                    print(f"[GameWatcher] 无法广播日志状态: 事件循环未运行")

            self.game_watcher.on_state_change(on_game_update)
            self.game_watcher.on_log_status_change(on_log_status_update)
            self.game_watcher.start()
            self.is_watching = True
            print("[GameWatcher] ✓ 游戏监视已启动")
        except Exception as e:
            print(f"[GameWatcher] ✗ 启动失败: {e}")

    def stop_game_watcher(self):
        """停止游戏状态监视"""
        if self.game_watcher:
            self.game_watcher.stop()
            self.is_watching = False
            print("[GameWatcher] ✓ 游戏监视已停止")


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

    evaluator = CardEvaluator(CARD_DB, archetype_library)

    try:
        detected = evaluator.detect_archetypes(run_state)
        results = evaluator.rank_cards(run_state)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"评估失败: {exc}") from exc

    return EvaluateResponse(
        results=results,
        detected_archetypes=[a.id for a in detected],
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
        from config_manager import set_save_path, set_log_path

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
