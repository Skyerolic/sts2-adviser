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
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

import numpy as np

from utils.paths import get_app_root

_LOGS_DIR = get_app_root() / "logs"
_LOGS_DIR.mkdir(exist_ok=True)

from .window_capture import WindowCapture, WindowInfo
from .screen_detector import ScreenDetector, ScreenType
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
        poll_interval: float = 5.0,       # 轮询间隔（秒）
        vote_frames: int = 1,             # 单帧即确认（CONFIRMED后不再重复识别）
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
        # 中文 OCR 做界面检测（识别"选择一张牌"/"choose a card"），卡名从全图 OCR 行解析
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
        # 已锁定的槽位：一旦某槽位识别成功，锁定后不再覆盖（直到界面消失重置）
        self._slot_locks: list[Optional[str]] = [None, None, None]

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

        # OCR 并发锁（防止上一次 OCR 尚未结束就启动新一轮）
        self._ocr_running = False

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
            if self._state == BridgeState.CONFIRMED:
                # 已确认，无需重复识别，等界面消失
                return
            self._set_state(BridgeState.RECOGNIZING)
            if self._ocr_running:
                log.debug("OCR 上一帧尚未完成，跳过本轮识别")
                return
            self._try_recognize(screenshot, det_ocr_result=det.ocr_result)
        else:
            # 不是选卡界面：只要曾经进入识别/确认状态就重置
            if self._state in (BridgeState.CONFIRMED, BridgeState.RECOGNIZING):
                log.debug("选卡界面消失，重置状态")
                self._confirmed_cards = None
                self._reset_ocr_votes()
            if self._state != BridgeState.WATCHING:
                self._set_state(BridgeState.WATCHING)

    def _try_recognize(self, screenshot: np.ndarray, det_ocr_result=None) -> None:
        """
        通过比例坐标裁剪三个卡名区域，分别做 OCR。

        Args:
            screenshot: 游戏窗口截图
            det_ocr_result: 界面检测时已产生的全图 OcrResult（用于定位标题 Y）
        """
        self._ocr_running = True
        try:
            self._try_recognize_inner(screenshot, det_ocr_result)
        finally:
            self._ocr_running = False

    def _try_recognize_inner(self, screenshot: np.ndarray, det_ocr_result=None) -> None:
        # 已全部锁定则无需再识别
        if all(cid is not None for cid in self._slot_locks):
            return

        # 综合全图OCR行坐标 + 区域补全，提取三张卡名
        title_y_rel = VisionBridge._find_title_y(det_ocr_result)
        ocr_texts = VisionBridge._extract_card_names_combined(
            screenshot, self._ocr, det_ocr_result, title_y_rel,
            skip_slots=[i for i, cid in enumerate(self._slot_locks) if cid is not None],
        )
        log.debug(f"OCR 结果: {ocr_texts}")

        # 规范化
        normalize_result = self._normalizer.normalize(ocr_texts)

        # 构建识别结果，已锁定槽位用锁定值，新识别槽位尝试锁定
        card_names: list[str] = []
        confidences: list[float] = []
        stable_ids: list[Optional[str]] = list(self._slot_locks)

        for i, match in enumerate(normalize_result.cards):
            if self._slot_locks[i] is not None:
                # 槽位已锁定，沿用锁定值
                card_names.append(match.matched_name if match else "")
                confidences.append(match.confidence if match else 1.0)
                continue
            # 未锁定槽位：尝试锁定
            if match and match.is_reliable:
                self._slot_locks[i] = match.card_id
                stable_ids[i] = match.card_id
                log.info(f"槽位 {i} 锁定: {match.card_id} (conf={match.confidence:.2f})")
            card_names.append(match.matched_name if match else "")
            confidences.append(match.confidence if match else 0.0)

        recognized = RecognizedCards(
            card_ids=stable_ids,
            card_names=card_names,
            confidences=confidences,
            ocr_texts=ocr_texts,
            all_reliable=all(cid is not None for cid in stable_ids),
        )

        self._last_cards = recognized

        if recognized.all_reliable and self._state != BridgeState.CONFIRMED:
            log.info(f"选卡识别完成: {stable_ids}")
            self._confirmed_cards = recognized
            self._set_state(BridgeState.CONFIRMED)
            self._save_ocr_snapshot(screenshot, recognized)
            self._emit_cards(recognized)
        else:
            locked = [i for i, cid in enumerate(stable_ids) if cid is not None]
            log.debug(f"已锁定槽位: {locked} / 3")

    # ------------------------------------------------------------------
    # 卡名提取（全图 OCR + 区域补全双策略）
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_card_names_combined(
        screenshot: np.ndarray,
        ocr_engine: WindowsOcrEngine,
        ocr_result,
        title_y_rel: Optional[float],
        skip_slots: Optional[list[int]] = None,
    ) -> list[str]:
        """
        综合两种策略提取三张卡名：
          1. 先从全图 OCR 行坐标（按X聚类）取名称
          2. 对识别失败的槽位，用区域 OCR 补全
        skip_slots: 已锁定槽位索引，跳过不识别
        """
        import re

        def normalize_zh(t: str) -> str:
            return re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', t)

        # 描述文字特征词（含这些词的行几乎不可能是卡名，且文字较长，提前排除）
        _DESC_KW = re.compile(
            r"造成|获得|敌人|伤害|抽.{0,2}张|如果|则.*|额外|该敌|将其|打出|消耗"
            r"|\d\s*层|层易伤|层力量|层护甲|层中毒|免费打出|加入你|手牌|本回合|翻倍"
            r"|deal \d|gain \d|take \d|draw \d",
            re.IGNORECASE,
        )
        _NOISE = re.compile(r"^[\d\W\s]{0,5}$")

        def is_noise(t: str) -> bool:
            # 白名单策略：只做最小过滤（空/短/纯符号/明确的描述词）
            # 具体卡名验证交由 card_normalizer 的 fuzzy 匹配（置信度 < 0.55 自动丢弃）
            t = t.strip()
            if not t or len(t) < 2:
                return True
            if _NOISE.match(t):
                return True
            if _DESC_KW.search(t):
                return True
            return False

        # ── 步骤1：从全图 OCR 行坐标用 X 聚类找卡名 ──────────────────
        full_ocr_names = ["", "", ""]
        card_x_from_ocr: list[Optional[float]] = [None, None, None]  # 每组X中心

        if ocr_result is not None and ocr_result.lines and title_y_rel is not None:
            y_min = title_y_rel + 0.10
            y_max = title_y_rel + 0.32   # 只取卡名横幅（约+15%~+30%），排除下方类型标签行

            candidates = []
            for line in ocr_result.lines:
                if line.bbox is None:
                    continue
                txt = normalize_zh(line.text).strip()
                if is_noise(txt):
                    continue
                line_y = (line.bbox[1] + line.bbox[3]) / 2
                if not (y_min <= line_y <= y_max):
                    continue
                candidates.append(line)

            if len(candidates) >= 2:
                sorted_c = sorted(candidates, key=lambda l: (l.bbox[0] + l.bbox[2]) / 2)
                x_ctrs = [(l.bbox[0] + l.bbox[2]) / 2 for l in sorted_c]
                gaps = sorted(
                    [(x_ctrs[i+1] - x_ctrs[i], i) for i in range(len(x_ctrs)-1)],
                    reverse=True,
                )
                n_split = min(2, len(gaps))
                split_idx = sorted(idx for _, idx in gaps[:n_split])
                groups: list[list] = []
                prev = 0
                for si in split_idx:
                    groups.append(sorted_c[prev:si+1])
                    prev = si + 1
                groups.append(sorted_c[prev:])

                for g in groups[:3]:
                    if not g:
                        continue
                    best = min(g, key=lambda l: len(normalize_zh(l.text)))
                    cx = (best.bbox[0] + best.bbox[2]) / 2
                    # 按X坐标判断槽位：左(<0.40)→0, 中(0.40~0.60)→1, 右(>0.60)→2
                    slot = 0 if cx < 0.40 else (1 if cx < 0.60 else 2)
                    full_ocr_names[slot] = normalize_zh(best.text).strip()
                    card_x_from_ocr[slot] = cx

            log.debug(f"全图OCR聚类结果: {full_ocr_names}, X中心: {card_x_from_ocr}")

        # ── 步骤2：推算三张卡的 X 中心（用已知坐标推算缺失的）──────────
        h_px, w_px = screenshot.shape[:2]
        if title_y_rel is not None:
            card_y_top = title_y_rel + 0.12
            card_y_bot = title_y_rel + 0.28  # 扩展卡名带以提升小窗口识别率
        else:
            card_y_top = 0.38
            card_y_bot = 0.53

        # 默认三列X中心（基于 2273x1202 实测：左≈0.21, 中≈0.50, 右≈0.79）
        default_centers = [0.21, 0.50, 0.79]
        # 用全图OCR坐标覆盖已知的
        resolved_centers = list(default_centers)
        for i, xc in enumerate(card_x_from_ocr):
            if xc is not None:
                resolved_centers[i] = xc

        # 若知道两个点，用间距推算第三个
        known = [(i, c) for i, c in enumerate(card_x_from_ocr) if c is not None]
        if len(known) == 2:
            i0, c0 = known[0]
            i1, c1 = known[1]
            span = c1 - c0
            if i0 == 0 and i1 == 1:
                resolved_centers[2] = c1 + span  # 右侧卡
            elif i0 == 0 and i1 == 2:
                resolved_centers[1] = (c0 + c1) / 2  # 中间卡
            elif i0 == 1 and i1 == 2:
                resolved_centers[0] = c0 - span  # 左侧卡
        elif len(known) == 1:
            i0, c0 = known[0]
            # 假设三张等间距，间距约0.28
            gap = 0.28
            if i0 == 0:
                resolved_centers[1] = c0 + gap
                resolved_centers[2] = c0 + 2 * gap
            elif i0 == 1:
                resolved_centers[0] = c0 - gap
                resolved_centers[2] = c0 + gap
            elif i0 == 2:
                resolved_centers[0] = c0 - 2 * gap
                resolved_centers[1] = c0 - gap

        log.debug(f"最终X中心: {resolved_centers}")

        # ── 步骤3：对空槽位做区域 OCR ──────────────────────────────────
        # 优先：用列亮度投影做像素级精确分割（暗背景 + 亮卡片对比明显）
        # 失败则回退：基于 resolved_centers 的中点法
        detected_px_bounds = VisionBridge._detect_card_x_bounds(
            screenshot, card_y_top, card_y_bot, expected_count=3
        )
        if detected_px_bounds is not None:
            log.debug(f"亮度投影检测到精确卡边界(px): {detected_px_bounds}")
            _slot_bands_px = detected_px_bounds
            use_px_bands = True
        else:
            _margin = 0.06
            mid01 = (resolved_centers[0] + resolved_centers[1]) / 2
            mid12 = (resolved_centers[1] + resolved_centers[2]) / 2
            _slot_bands = [
                (0.0,             mid01 + _margin),
                (mid01 - _margin, mid12 + _margin),
                (mid12 - _margin, 1.0),
            ]
            use_px_bands = False
        _skip = set(skip_slots or [])
        result = list(full_ocr_names)
        for i, cx in enumerate(resolved_centers[:3]):
            if i in _skip:  # 已锁定，跳过
                continue
            if result[i]:  # 已从全图OCR获得，跳过
                continue
            if use_px_bands:
                x0, x1 = _slot_bands_px[i]
            else:
                x_lo, x_hi = _slot_bands[i]
                x0 = max(0, int(x_lo * w_px))
                x1 = min(w_px, int(x_hi * w_px))
            y0 = max(0, int(card_y_top * h_px))
            y1 = min(h_px, int(card_y_bot * h_px))
            region = screenshot[y0:y1, x0:x1]
            if region.size == 0:
                continue
            res = ocr_engine.recognize(region)
            if not res.success or not res.full_text.strip():
                log.debug(f"区域OCR slot{i} 无结果: success={res.success} text={repr(res.full_text[:50] if res.full_text else '')}")
                continue
            cands: list[tuple[str, float]] = []  # (text, x_center_in_crop)
            all_lines = []
            for line in res.lines:
                txt = normalize_zh(line.text).strip()
                all_lines.append(txt)
                if not is_noise(txt):
                    if line.bbox is not None:
                        line_cx = (line.bbox[0] + line.bbox[2]) / 2
                    else:
                        line_cx = 0.5
                    cands.append((txt, line_cx))
            log.debug(f"区域OCR slot{i} cx={cx:.2f} 原始行: {all_lines} → 候选: {[(t, f'{lx:.2f}') for t, lx in cands]}")
            if cands:
                best_txt, best_cx = min(cands, key=lambda tc: abs(tc[1] - 0.5))
                result[i] = best_txt
                log.debug(f"区域OCR补全 slot{i} cx={cx:.2f}: {result[i]} (line_cx={best_cx:.2f})")

        log.debug(f"最终卡名: {result}")
        return result

    @staticmethod
    def _detect_card_x_bounds(
        screenshot: np.ndarray,
        y_top_rel: float,
        y_bot_rel: float,
        expected_count: int = 3,
    ) -> Optional[list[tuple[int, int]]]:
        """
        在标题带 Y 范围内做列亮度投影，找到 N 张卡的精确像素 X 边界。

        利用选卡界面的视觉特性：纯黑/暗背景 + 高亮卡牌（绿框 + 彩色标题）。
        每列像素的灰度均值在卡片列高，在卡片间隙列接近 0；阈值化后取连续段。

        Returns:
            按从左到右排序的 [(x0, x1), ...] 像素边界对，或 None 表示检测失败
            （连续段数 < 3 时回退到现有的中点法）
        """
        try:
            import cv2
        except ImportError:
            return None

        h, w = screenshot.shape[:2]
        y0 = max(0, int(y_top_rel * h))
        y1 = min(h, int(y_bot_rel * h))
        if y1 - y0 < 10 or w < 100:
            return None

        band = screenshot[y0:y1, :]
        if band.ndim == 3:
            gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
        else:
            gray = band

        col_mean = gray.mean(axis=0)
        peak = float(col_mean.max())
        if peak < 30:
            log.debug(f"亮度投影：标题带过暗 (peak={peak:.0f})，跳过")
            return None

        # 阈值：max * 0.3 —— 在 max=200 时阈值 60，黑背景 (~10) 远低于此
        threshold = peak * 0.3
        is_card = col_mean > threshold

        # 找连续 True 段
        runs: list[tuple[int, int]] = []
        in_run = False
        start = 0
        for x in range(w):
            if is_card[x]:
                if not in_run:
                    start = x
                    in_run = True
            else:
                if in_run:
                    runs.append((start, x))
                    in_run = False
        if in_run:
            runs.append((start, w))

        # 滤掉太窄的段（< 5% 窗口宽度，多半是字幕、UI 元素或噪点）
        min_width = max(int(w * 0.05), 20)
        runs = [r for r in runs if (r[1] - r[0]) >= min_width]

        if len(runs) < expected_count:
            log.debug(f"亮度投影：仅检测到 {len(runs)} 段（需要 {expected_count}）")
            return None

        # 多于预期 → 挑最宽的 N 个（卡片通常远比 UI 元素宽）
        if len(runs) > expected_count:
            runs = sorted(runs, key=lambda r: r[1] - r[0], reverse=True)[:expected_count]
            runs.sort(key=lambda r: r[0])

        return runs

    @staticmethod
    def _find_title_y(ocr_result) -> Optional[float]:
        """从全图 OCR 结果中找标题行（"选择一张牌"/"choose a card"）的归一化 Y 中心"""
        import re
        if ocr_result is None or not ocr_result.lines:
            return None
        _TITLE_KW = ["choose a card", "选择一张牌", "选一张牌", "choose one", "pick a card"]
        # 兼容OCR误读，如"选择。张牌"/"选择 。 张牌"（中间可能有噪声字符和空格）
        _TITLE_PAT = re.compile(r"选择.{0,6}张牌|choose.{0,8}card", re.IGNORECASE)

        def normalize_zh(t: str) -> str:
            return re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', t)

        for line in ocr_result.lines:
            txt = normalize_zh(line.text).lower().strip()
            # 去除所有空格后再做正则匹配（兼容每字间有空格的OCR输出）
            txt_nospace = re.sub(r'\s+', '', line.text)
            if any(kw in txt for kw in _TITLE_KW) or _TITLE_PAT.search(txt_nospace):
                if line.bbox is not None:
                    return (line.bbox[1] + line.bbox[3]) / 2
        return None

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
        self._slot_locks = [None, None, None]

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

    def _save_ocr_snapshot(
        self,
        screenshot: np.ndarray,
        recognized: RecognizedCards,
    ) -> None:
        """
        保存 OCR 快照到 logs/ 目录。
        - logs/ocr_YYYYMMDD_HHMMSS.png  截图
        - logs/ocr_YYYYMMDD_HHMMSS.txt  识别详情

        保留最近 20 张快照（按文件名排序，删除最旧的）。
        """
        try:
            import cv2
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            img_path = _LOGS_DIR / f"ocr_{ts}.png"
            txt_path = _LOGS_DIR / f"ocr_{ts}.txt"

            # 保存截图
            cv2.imwrite(str(img_path), screenshot)

            # 保存识别详情
            lines = [
                f"时间: {ts}",
                f"全部可信: {recognized.all_reliable}",
                "",
            ]
            for i, (cid, name, conf, ocr_txt) in enumerate(zip(
                recognized.card_ids,
                recognized.card_names,
                recognized.confidences,
                recognized.ocr_texts,
            )):
                lines.append(f"槽位 {i}:")
                lines.append(f"  card_id  : {cid}")
                lines.append(f"  匹配名称 : {name}")
                lines.append(f"  置信度   : {conf:.3f}")
                lines.append(f"  OCR原文  : {ocr_txt}")
            txt_path.write_text("\n".join(lines), encoding="utf-8")

            log.info(f"OCR快照已保存: {img_path.name}")

            # 清理旧快照，只保留最新 20 份
            snapshots = sorted(_LOGS_DIR.glob("ocr_*.png"))
            for old in snapshots[:-20]:
                old.unlink(missing_ok=True)
                old.with_suffix(".txt").unlink(missing_ok=True)

        except Exception as e:
            log.warning(f"保存OCR快照失败: {e}")

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
