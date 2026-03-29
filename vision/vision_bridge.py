"""
vision/vision_bridge.py
视觉识别桥接器 —— 将截图识别结果转化为后端可用的 RunState

职责：
  - 整合 window_capture / screen_detector / card_extractor /
    ocr_engine / card_normalizer 五个模块
  - 以固定频率轮询游戏窗口
  - 多帧投票确认选卡界面
  - OCR 识别三张卡名并规范化
  - 对外提供与 GameWatcher 相同的回调接口
    （可作为 GameWatcher 的平行替代数据源）

状态机：
  IDLE ──find_window──► WATCHING
  WATCHING ──detect_reward──► RECOGNIZING
  RECOGNIZING ──stable_N_frames──► NOTIFY（触发回调）
  NOTIFY ──界面消失──► WATCHING
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

import numpy as np

from .window_capture import WindowCapture, WindowInfo
from .screen_detector import ScreenDetector, ScreenType
from .card_extractor import CardExtractor, CardRegion
from .ocr_engine import WindowsOcrEngine, get_ocr_engine
from .card_normalizer import CardNormalizer, MatchResult, get_card_normalizer

log = logging.getLogger(__name__)


# -----------------------------------------------------------------------
# 数据结构
# -----------------------------------------------------------------------

class BridgeState(str, Enum):
    IDLE        = "idle"         # 未找到游戏窗口
    WATCHING    = "watching"     # 监视中，未进入选卡界面
    RECOGNIZING = "recognizing"  # 检测到选卡界面，正在 OCR
    CONFIRMED   = "confirmed"    # 识别结果已确认，等待界面消失


@dataclass
class RecognizedCards:
    """一次识别的三张卡结果"""
    card_ids: list[Optional[str]]      # 长度 3，识别失败为 None
    card_names: list[str]              # 匹配到的标准名称
    confidences: list[float]           # 每张卡的置信度
    ocr_texts: list[str]               # OCR 原始文字（调试用）
    all_reliable: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_card_choices(self) -> list[str]:
        """返回可信的 card_id 列表（用于填入 RunState.card_choices）"""
        return [cid for cid in self.card_ids if cid is not None]


# -----------------------------------------------------------------------
# VisionBridge
# -----------------------------------------------------------------------

class VisionBridge:
    """
    视觉识别桥接器。

    与 GameWatcher 接口兼容：
        bridge = VisionBridge()
        bridge.on_state_change(callback)  # callback(state_dict)
        bridge.start()
        bridge.stop()

    state_dict 格式（发送给前端）：
    {
        "source": "vision",
        "screen_type": "card_reward",
        "card_choices": ["CATALYST", "NINJUTSU", "REFLEX"],
        "card_names": ["Catalyst", "Ninjutsu", "Reflex"],
        "confidences": [0.92, 0.87, 0.95],
        "ocr_texts": ["Catalyst", "忍术", "Reflex"],
        "all_reliable": true,
        "timestamp": "2026-03-29T12:00:00.000Z"
    }
    """

    def __init__(
        self,
        poll_interval: float = 1.0,       # 轮询间隔（秒）
        vote_frames: int = 3,             # 多帧投票窗口
        confidence_threshold: float = 0.55,
        ocr_engine: Optional[WindowsOcrEngine] = None,
        normalizer: Optional[CardNormalizer] = None,
    ) -> None:
        self._poll_interval = poll_interval
        self._vote_frames = vote_frames
        self._confidence_threshold = confidence_threshold

        # 子模块
        self._capture = WindowCapture()
        self._detector = ScreenDetector(vote_frames=vote_frames)
        self._extractor = CardExtractor()
        self._ocr = ocr_engine or get_ocr_engine()
        self._normalizer = normalizer or get_card_normalizer()

        # 状态
        self._state = BridgeState.IDLE
        self._last_cards: Optional[RecognizedCards] = None
        self._confirmed_cards: Optional[RecognizedCards] = None

        # 多帧 OCR 投票缓冲（每张卡独立）
        self._ocr_votes: list[deque[Optional[str]]] = [
            deque(maxlen=vote_frames) for _ in range(3)
        ]

        # 线程控制
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._last_window_scan: float = 0.0   # 上次 find_window 时间戳
        self._window_scan_interval: float = 5.0  # 窗口重扫最小间隔（秒）
        self._window_miss_count: int = 0         # 连续未找到窗口次数（防抖）
        self._window_miss_threshold: int = 3     # 连续几次才认为窗口消失

        # 回调
        self._on_state_change: Optional[Callable[[dict], None]] = None
        self._on_status_change: Optional[Callable[[dict], None]] = None

    # ------------------------------------------------------------------
    # 公开接口（与 GameWatcher 兼容）
    # ------------------------------------------------------------------

    def on_state_change(self, callback: Callable[[dict], None]) -> None:
        """注册游戏状态变化回调"""
        self._on_state_change = callback

    def on_log_status_change(self, callback: Callable[[dict], None]) -> None:
        """注册状态信息回调（兼容 GameWatcher 接口）"""
        self._on_status_change = callback

    def start(self) -> None:
        """启动后台轮询线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="VisionBridge",
        )
        self._thread.start()
        log.info("VisionBridge 已启动")
        self._emit_status("started", "视觉识别已启动")

    def stop(self) -> None:
        """停止轮询"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
        log.info("VisionBridge 已停止")
        self._emit_status("stopped", "视觉识别已停止")

    def get_current_state(self) -> dict:
        """返回当前识别状态快照（供 WebSocket 初始推送使用）"""
        with self._lock:
            cards = self._confirmed_cards or self._last_cards
        if cards is None:
            return {"source": "vision", "screen_type": "unknown"}
        return self._build_state_dict(cards)

    @property
    def bridge_state(self) -> BridgeState:
        return self._state

    # ------------------------------------------------------------------
    # 轮询主循环
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception as e:
                log.error(f"VisionBridge tick 异常: {e}", exc_info=True)
            time.sleep(self._poll_interval)

    def _tick(self) -> None:
        """一次轮询周期"""
        # 1. 确保有窗口（防抖 + 限速重扫）
        if not self._capture.is_window_available():
            self._window_miss_count += 1
            if self._window_miss_count < self._window_miss_threshold:
                # 短暂看不到窗口，先不切换状态（防抖）
                return
            # 连续多次看不到，才认为窗口真正消失
            if self._state != BridgeState.IDLE:
                log.debug("游戏窗口消失，切换到 IDLE")
                self._set_state(BridgeState.IDLE)
                self._detector.reset_votes()
                self._reset_ocr_votes()
            now = time.time()
            if now - self._last_window_scan < self._window_scan_interval:
                return
            self._last_window_scan = now
            if self._capture.find_window() is None:
                return
            self._window_miss_count = 0
            log.info(f"找到游戏窗口: {self._capture.get_window_info().title}")
            self._set_state(BridgeState.WATCHING)
        else:
            self._window_miss_count = 0  # 窗口可见，重置防抖计数

        # 2. 截图
        screenshot = self._capture.capture()
        if screenshot is None:
            return

        # 3. 界面检测
        det = self._detector.detect(screenshot)

        if det.screen_type == ScreenType.CARD_REWARD:
            self._set_state(BridgeState.RECOGNIZING)
            self._try_recognize(screenshot, det_ocr_result=det.ocr_result)
        else:
            # 不是选卡界面
            if self._state == BridgeState.CONFIRMED:
                # 界面已消失，重置
                log.debug("选卡界面消失，重置状态")
                self._confirmed_cards = None
                self._reset_ocr_votes()
            if self._state != BridgeState.WATCHING:
                self._set_state(BridgeState.WATCHING)

    def _try_recognize(self, screenshot: np.ndarray, det_ocr_result=None) -> None:
        """
        执行 OCR 识别并做多帧投票。

        Args:
            screenshot: 游戏窗口截图
            det_ocr_result: 界面检测时已产生的全图 OcrResult（避免重复 OCR）
        """
        # 动态定位三张卡名区域（复用全图 OCR 结果）
        if det_ocr_result is not None:
            regions = self._extractor.extract_from_ocr(screenshot, det_ocr_result)
        else:
            regions = self._extractor.extract(screenshot)

        if len(regions) < 3:
            return

        # 优先使用动态定位时已提取的 OCR hint；否则对裁剪图重新 OCR
        ocr_texts: list[str] = []
        for region in regions:
            if region.ocr_hint:
                # 动态定位已从全图 OCR 行中提取到文字，直接使用
                ocr_texts.append(region.ocr_hint.strip())
                continue
            if region.image.size == 0:
                ocr_texts.append("")
                continue
            result = self._ocr.recognize(region.image)
            first_line = result.lines[0].text if result.lines else result.full_text
            ocr_texts.append(first_line.strip())

        log.debug(f"OCR 结果: {ocr_texts}")

        # 规范化
        normalize_result = self._normalizer.normalize(ocr_texts)

        # 更新 OCR 投票缓冲
        for i, match in enumerate(normalize_result.cards):
            cid = match.card_id if match and match.is_reliable else None
            self._ocr_votes[i].append(cid)

        # 检查投票稳定性
        stable_ids: list[Optional[str]] = []
        for vote_buf in self._ocr_votes:
            stable_ids.append(self._vote_winner(vote_buf))

        # 构建识别结果
        card_names: list[str] = []
        confidences: list[float] = []
        for i, match in enumerate(normalize_result.cards):
            if match:
                card_names.append(match.matched_name)
                confidences.append(match.confidence)
            else:
                card_names.append("")
                confidences.append(0.0)

        recognized = RecognizedCards(
            card_ids=stable_ids,
            card_names=card_names,
            confidences=confidences,
            ocr_texts=ocr_texts,
            all_reliable=all(cid is not None for cid in stable_ids),
        )

        self._last_cards = recognized

        # 若三张卡全部稳定，触发通知
        if recognized.all_reliable and self._state != BridgeState.CONFIRMED:
            log.info(f"选卡识别稳定: {stable_ids}")
            self._confirmed_cards = recognized
            self._set_state(BridgeState.CONFIRMED)
            self._emit_cards(recognized)
        elif not recognized.all_reliable:
            log.debug(f"投票未稳定: {stable_ids}")

    # ------------------------------------------------------------------
    # 投票工具
    # ------------------------------------------------------------------

    @staticmethod
    def _vote_winner(buf: deque) -> Optional[str]:
        """从投票缓冲中取最多数值；未满或不一致返回 None"""
        if len(buf) == 0:
            return None
        counts: dict = {}
        for v in buf:
            if v is not None:
                counts[v] = counts.get(v, 0) + 1
        if not counts:
            return None
        best, cnt = max(counts.items(), key=lambda x: x[1])
        # 要求超过半数
        if cnt > len(buf) / 2:
            return best
        return None

    def _reset_ocr_votes(self) -> None:
        for buf in self._ocr_votes:
            buf.clear()

    # ------------------------------------------------------------------
    # 状态与回调
    # ------------------------------------------------------------------

    def _set_state(self, new_state: BridgeState) -> None:
        if self._state != new_state:
            log.debug(f"状态: {self._state.value} → {new_state.value}")
            self._state = new_state

    def _emit_cards(self, cards: RecognizedCards) -> None:
        """触发卡牌识别结果回调"""
        if self._on_state_change:
            try:
                self._on_state_change(self._build_state_dict(cards))
            except Exception as e:
                log.error(f"state_change 回调异常: {e}")

    def _emit_status(self, status: str, message: str) -> None:
        """触发状态信息回调"""
        if self._on_status_change:
            try:
                self._on_status_change({
                    "source": "vision",
                    "status": status,
                    "message": message,
                    "bridge_state": self._state.value,
                })
            except Exception as e:
                log.error(f"status_change 回调异常: {e}")

    @staticmethod
    def _build_state_dict(cards: RecognizedCards) -> dict:
        """构建标准状态字典（供 WebSocket 广播）"""
        import datetime
        return {
            "source": "vision",
            "screen_type": "card_reward",
            "card_choices": cards.to_card_choices(),
            "card_names": cards.card_names,
            "confidences": cards.confidences,
            "ocr_texts": cards.ocr_texts,
            "all_reliable": cards.all_reliable,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        }
