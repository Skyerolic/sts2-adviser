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
from pathlib import Path
from typing import Callable, Optional

import numpy as np

# logs 目录（与项目根目录同级）
_LOGS_DIR = Path(__file__).parent.parent / "logs"
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
            if self._state == BridgeState.CONFIRMED:
                # 已确认，无需重复识别，等界面消失
                return
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
        通过比例坐标裁剪三个卡名区域，分别做 OCR。

        Args:
            screenshot: 游戏窗口截图
            det_ocr_result: 界面检测时已产生的全图 OcrResult（用于定位标题 Y）
        """
        # 综合全图OCR行坐标 + 区域补全，提取三张卡名
        title_y_rel = VisionBridge._find_title_y(det_ocr_result)
        ocr_texts = VisionBridge._extract_card_names_combined(
            screenshot, self._ocr, det_ocr_result, title_y_rel
        )
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
            self._save_ocr_snapshot(screenshot, recognized)
            self._emit_cards(recognized)
        elif not recognized.all_reliable:
            log.debug(f"投票未稳定: {stable_ids}")

    # ------------------------------------------------------------------
    # 全图 OCR 卡名解析
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_card_names_from_ocr(ocr_result) -> list[str]:
        """
        从全图 OCR 结果中直接提取三张卡的名称。
        策略：
          1. 找标题行（"choose a card" / "选择一张牌"），确定标题 Y 坐标
          2. 在标题下方一定范围内，找有坐标的 OCR 行
          3. 过滤 UI 噪声行（"skip"/"跳过"/纯数字等）
          4. 按 X 坐标聚类为 3 组，每组取最短文本（卡名比描述短）
          5. 若无坐标，退化为从全文按顺序取前三个非噪声词
        """
        import re

        if ocr_result is None or not ocr_result.lines:
            return ["", "", ""]

        _TITLE_KW = ["choose a card", "选择一张牌", "选一张牌", "choose one", "pick a card"]
        _SKIP_KW  = re.compile(
            r"^(skip|跳过|choose|选择|pass|back|return|cancel|取消"
            r"|攻击|技能|能力|attack|skill|power|curse|status)$",
            re.IGNORECASE,
        )
        _NOISE = re.compile(r"^[\d\W\s]{0,5}$")

        def is_noise(t: str) -> bool:
            t = t.strip()
            if not t or len(t) < 2:
                return True
            if _NOISE.match(t):
                return True
            if _SKIP_KW.match(t.lower()):
                return True
            return False

        def normalize_zh(t: str) -> str:
            return re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', t)

        lines = ocr_result.lines

        # 1. 找标题行 Y
        title_y: Optional[float] = None
        for line in lines:
            txt = normalize_zh(line.text).lower().strip()
            if any(kw in txt for kw in _TITLE_KW):
                if line.bbox is not None:
                    title_y = (line.bbox[1] + line.bbox[3]) / 2
                break

        # 2. 候选行：在标题 Y 下方，有坐标，非噪声
        candidates = []
        for line in lines:
            txt = normalize_zh(line.text).strip()
            if is_noise(txt):
                continue
            if line.bbox is None:
                continue
            line_y = (line.bbox[1] + line.bbox[3]) / 2
            if title_y is not None:
                # 只取标题下方 5%~35% 的行
                if not (title_y + 0.03 <= line_y <= title_y + 0.40):
                    continue
            candidates.append(line)

        # 3. 按 X 聚类为 3 组
        if len(candidates) >= 3:
            sorted_cands = sorted(candidates, key=lambda l: (l.bbox[0] + l.bbox[2]) / 2)
            x_centers = [(l.bbox[0] + l.bbox[2]) / 2 for l in sorted_cands]
            gaps = sorted(
                [(x_centers[i+1] - x_centers[i], i) for i in range(len(x_centers)-1)],
                reverse=True
            )
            split_indices = sorted(idx for _, idx in gaps[:2])
            groups: list[list] = []
            prev = 0
            for si in split_indices:
                groups.append(sorted_cands[prev:si+1])
                prev = si + 1
            groups.append(sorted_cands[prev:])

            result = []
            for g in groups[:3]:
                if not g:
                    result.append("")
                    continue
                # 取最短非空文本（卡名通常比描述短）
                best = min(g, key=lambda l: len(normalize_zh(l.text)))
                result.append(normalize_zh(best.text).strip())
            while len(result) < 3:
                result.append("")
            log.debug(f"全图卡名解析(聚类): {result}")
            return result

        # 4. 退化：无坐标或候选不足 3 个，从全文顺序取前三非噪声词
        fallback = []
        for line in lines:
            txt = normalize_zh(line.text).strip()
            if not is_noise(txt):
                fallback.append(txt)
            if len(fallback) >= 3:
                break
        while len(fallback) < 3:
            fallback.append("")
        log.debug(f"全图卡名解析(fallback顺序): {fallback}")
        return fallback

    @staticmethod
    def _extract_card_names_combined(
        screenshot: np.ndarray,
        ocr_engine: WindowsOcrEngine,
        ocr_result,
        title_y_rel: Optional[float],
    ) -> list[str]:
        """
        综合两种策略提取三张卡名：
          1. 先从全图 OCR 行坐标（按X聚类）取名称
          2. 对识别失败的槽位，用区域 OCR 补全
        """
        import re

        def normalize_zh(t: str) -> str:
            return re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', t)

        _SKIP_KW = re.compile(
            r"^(skip|跳过|choose|选择|pass|back|cancel|取消"
            r"|攻击|技能|能力|诅咒|状态"
            r"|attack|skill|power|curse|status)$",
            re.IGNORECASE,
        )
        # 描述文字特征词（含这些词的行不是卡名）
        _DESC_KW = re.compile(
            r"造成|获得|敌人|伤害|抽.{0,2}张|如果|则.*|额外|该敌|将其|打出|消耗"
            r"|\d\s*层|层易伤|层力量|层护甲|层中毒|免费打出|加入你|手牌|本回合|翻倍"
            r"|deal \d|gain \d|take \d|draw \d",
            re.IGNORECASE,
        )
        _NOISE = re.compile(r"^[\d\W\s]{0,5}$")

        def is_noise(t: str) -> bool:
            t = t.strip()
            if not t or len(t) < 2:
                return True
            if _NOISE.match(t):
                return True
            if _SKIP_KW.match(t.lower()):
                return True
            if _DESC_KW.search(t):
                return True
            return False

        # ── 步骤1：从全图 OCR 行坐标用 X 聚类找卡名 ──────────────────
        full_ocr_names = ["", "", ""]
        card_x_from_ocr: list[Optional[float]] = [None, None, None]  # 每组X中心

        if ocr_result is not None and ocr_result.lines and title_y_rel is not None:
            y_min = title_y_rel + 0.10
            y_max = title_y_rel + 0.45

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
            card_y_top = title_y_rel + 0.14
            card_y_bot = title_y_rel + 0.25  # 只取卡名横幅，不含描述文字
        else:
            card_y_top = 0.40
            card_y_bot = 0.51

        # 默认三列X中心
        default_centers = [0.22, 0.50, 0.78]
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
        half_w = 0.16
        result = list(full_ocr_names)
        for i, cx in enumerate(resolved_centers[:3]):
            if result[i]:  # 已从全图OCR获得，跳过
                continue
            x0 = max(0, int((cx - half_w) * w_px))
            x1 = min(w_px, int((cx + half_w) * w_px))
            y0 = max(0, int(card_y_top * h_px))
            y1 = min(h_px, int(card_y_bot * h_px))
            region = screenshot[y0:y1, x0:x1]
            if region.size == 0:
                continue
            res = ocr_engine.recognize(region)
            if not res.success or not res.full_text.strip():
                continue
            cands = []
            for line in res.lines:
                txt = normalize_zh(line.text).strip()
                if not is_noise(txt):
                    cands.append(txt)
            if cands:
                result[i] = min(cands, key=len)
                log.debug(f"区域OCR补全 slot{i} cx={cx:.2f}: {result[i]}")

        log.debug(f"最终卡名: {result}")
        return result

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

    @staticmethod
    def _ocr_card_name_regions(
        screenshot: np.ndarray,
        ocr_engine: WindowsOcrEngine,
        title_y_rel: Optional[float] = None,
    ) -> list[str]:
        """
        按比例坐标裁剪三张卡的名称区域，分别做 OCR。

        卡名区域位置（相对整个窗口）：
          - 标题"选择一张牌"约在 Y=25%
          - 卡名在标题下方约 Y+14%~Y+26%（名称橙色横幅）
          - 三张卡 X 中心约 22%、50%、78%，宽度约 ±14%
        """
        import re

        if screenshot is None or screenshot.size == 0:
            return ["", "", ""]

        h, w = screenshot.shape[:2]

        # 动态计算卡名 Y 范围：基于标题 Y + 偏移
        if title_y_rel is not None:
            # 卡名橙色横幅在标题下方约 14%~27%
            card_y_top = title_y_rel + 0.14
            card_y_bot = title_y_rel + 0.27
        else:
            # fallback：固定比例（标题约在 25%，卡名在 40%~52%）
            card_y_top = 0.40
            card_y_bot = 0.52

        # 三张卡的 X 中心（相对宽度）
        card_x_centers = [0.22, 0.50, 0.80]
        card_x_half_w  = 0.18  # 每张卡名宽度约 ±18%

        def normalize_zh(t: str) -> str:
            return re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', t)

        results: list[str] = []
        for cx in card_x_centers:
            x0 = max(0, int((cx - card_x_half_w) * w))
            x1 = min(w, int((cx + card_x_half_w) * w))
            y0 = max(0, int(card_y_top * h))
            y1 = min(h, int(card_y_bot * h))

            region = screenshot[y0:y1, x0:x1]
            if region.size == 0:
                results.append("")
                continue

            ocr_res = ocr_engine.recognize(region)
            if not ocr_res.success or not ocr_res.full_text.strip():
                results.append("")
                continue

            # 取所有行中最短的非噪声行（卡名比描述短）
            candidates = []
            for line in ocr_res.lines:
                txt = normalize_zh(line.text).strip()
                if len(txt) >= 2 and not re.match(r'^[\d\W\s]{0,5}$', txt):
                    candidates.append(txt)

            if candidates:
                best = min(candidates, key=len)
                results.append(best)
            else:
                # fallback：整个文本第一行
                first_line = normalize_zh(ocr_res.full_text.split('\n')[0]).strip()
                results.append(first_line if len(first_line) >= 2 else "")

        log.debug(f"区域OCR卡名: {results} (title_y={title_y_rel})")
        return results

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
