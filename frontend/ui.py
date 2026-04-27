"""
frontend/ui.py
PyQt6 浮窗主界面（CardAdviserWindow）

特性：
  - 永远置顶（WindowStaysOnTopHint）
  - 无边框 + 半透明背景（适合游戏覆盖）
  - 可拖拽移动
  - 显示评估结果卡片列表
  - 通过 HTTP 调用后端 /api/evaluate

布局：
  ┌──────────────────────────┐
  │  [STS2 Adviser]   [×]    │  ← 标题栏（可拖拽）
  ├──────────────────────────┤
  │  [刷新] [状态指示]        │  ← 工具栏
  ├──────────────────────────┤
  │  卡名        分数  推荐   │
  │  ─────────────────────── │
  │  Catalyst     82  强烈推荐│
  │  Ninjutsu     65  推荐    │
  │  Reflex       40  可选    │
  └──────────────────────────┘
"""

import logging
import sys
import json
import requests
import subprocess
import os
import re
import websocket
import threading
import time

from utils.paths import get_app_root

log = logging.getLogger(__name__)

from PyQt6.QtCore import (
    Qt, QPoint, QThread, pyqtSignal, QTimer,
)
from PyQt6.QtGui import QFont, QColor, QPixmap, QIcon, QPainter, QAction
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QFrame,
    QSizePolicy, QDialog, QLineEdit, QFileDialog, QGroupBox,
    QGridLayout, QComboBox, QSizeGrip, QSlider, QSystemTrayIcon, QMenu,
)

_port = os.environ.get("STS2_BACKEND_PORT", "8000")
BACKEND_URL = f"http://127.0.0.1:{_port}"

VERSION = "1.6.5"

# 套路名映射（archetype_id → {"zh": name_zh, "en": name}），后端连通后懒加载
# 路径影响标签按当前 UI 语言从内层字典取名，避免英文界面下残留中文
_ARCHETYPE_NAME_MAP: dict[str, dict[str, str]] = {}
_GITHUB_REPO = "Skyerolic/sts2-adviser"


# ---------------------------------------------------------------------------
# UI 翻译字典
# ---------------------------------------------------------------------------

_UI_STRINGS: dict[str, dict[str, str]] = {
    # 标题栏
    "title":                  {"zh": "⚔ STS2 Adviser",         "en": "⚔ STS2 Adviser"},
    "minimize_tip":           {"zh": "最小化到托盘",              "en": "Minimize to tray"},
    # 工具栏按钮
    "btn_detect":             {"zh": "🔄 检测",                  "en": "🔄 Detect"},
    "btn_ocr":                {"zh": "📷 截图识别",               "en": "📷 OCR Capture"},
    "btn_settings":           {"zh": "⚙ 设置",                  "en": "⚙ Settings"},
    "btn_ocr_tip":            {"zh": "手动截一次图做OCR识别",      "en": "Manually trigger OCR capture"},
    "btn_detect_tip":         {"zh": "重新检测游戏和日志",          "en": "Re-detect game and log files"},
    "btn_settings_tip":       {"zh": "路径设置",                  "en": "Path settings"},
    # 指示器
    "indicator_backend":      {"zh": "● 后端",                   "en": "● Backend"},
    "indicator_game":         {"zh": "● 游戏",                   "en": "● Game"},
    "indicator_log":          {"zh": "● 日志",                   "en": "● Log"},
    "indicator_ocr":          {"zh": "● 视觉",                   "en": "● Vision"},
    "game_waiting":           {"zh": "等待游戏数据...",            "en": "Waiting for game data..."},
    "game_not_detected":      {"zh": "未检测到游戏运行",           "en": "Game not detected"},
    # 列表头
    "list_header":            {"zh": "候选卡评估",                "en": "Card Evaluation"},
    # OCR 面板
    "ocr_title":              {"zh": "📷 视觉识别",               "en": "📷 Vision OCR"},
    "ocr_recognizing":        {"zh": "识别中...",                 "en": "Recognizing..."},
    "ocr_locked":             {"zh": "已锁定 ✓",                 "en": "Locked ✓"},
    "ocr_hint_stable":        {"zh": "识别稳定，已自动填入候选卡并触发评估", "en": "Stable — auto-filled candidates and triggered evaluation"},
    "ocr_hint_waiting":       {"zh": "正在等待多帧稳定以确认卡名...", "en": "Waiting for multi-frame confirmation..."},
    "ocr_card_placeholder":   {"zh": "— 卡 {i} —",              "en": "— Card {i} —"},
    # 状态栏
    "status_ready":           {"zh": "就绪 — 等待游戏数据",        "en": "Ready — waiting for game data"},
    "status_evaluating":      {"zh": "评估中...",                 "en": "Evaluating..."},
    "status_loaded":          {"zh": "已加载 {n} 张卡牌，请选择候选卡", "en": "Loaded {n} cards — select candidates"},
    "status_loading":         {"zh": "加载 {char} 卡牌中...",     "en": "Loading {char} cards..."},
    "status_load_fail":       {"zh": "加载卡牌失败: {e}",         "en": "Failed to load cards: {e}"},
    # 侧边抽屉
    "drawer_title":           {"zh": "手动选牌",                  "en": "Manual Pick"},
    "drawer_toggle_tip":      {"zh": "展开/收起手动选牌面板",       "en": "Expand/collapse card picker"},
    "search_placeholder":     {"zh": "搜索卡牌...",               "en": "Search cards..."},
    "tray_prefix":            {"zh": "候选:",                    "en": "Pick:"},
    "btn_evaluate":           {"zh": "⟳ 评估",                   "en": "⟳ Evaluate"},
    "tray_deselect_tip":      {"zh": "点击取消选中",               "en": "Click to deselect"},
    # 占位符
    "placeholder":            {"zh": "点击「刷新」加载评估结果",    "en": "Click Detect to load results"},
    # 设置对话框
    "settings_title":         {"zh": "路径设置",                  "en": "Settings"},
    "settings_help":          {"zh": "设置游戏文件所在的文件夹。\n• 存档文件夹：应包含 current_run.save 等存档文件\n• 日志文件夹：应包含 godot.log 等日志文件",
                               "en": "Set the folders where game files are located.\n• Save folder: should contain current_run.save\n• Log folder: should contain godot.log"},
    "settings_lang":          {"zh": "🌐 卡牌显示语言:",           "en": "🌐 Card display language:"},
    "settings_font":          {"zh": "🔡 字体大小:",              "en": "🔡 Font size:"},
    "settings_hotkey":        {"zh": "⌨ 呼出快捷键:",             "en": "⌨ Show/hide hotkey:"},
    "settings_hotkey_ph":     {"zh": "例如: ctrl+shift+s",       "en": "e.g. ctrl+shift+s"},
    "settings_opacity":       {"zh": "🪟 窗口透明度:",             "en": "🪟 Opacity:"},
    "settings_save_folder":   {"zh": "📂 存档文件夹:",             "en": "📂 Save folder:"},
    "settings_log_folder":    {"zh": "📋 日志文件夹:",             "en": "📋 Log folder:"},
    "settings_browse":        {"zh": "浏览...",                   "en": "Browse..."},
    "settings_save_btn":      {"zh": "保存配置",                  "en": "Save"},
    "settings_cancel_btn":    {"zh": "取消",                      "en": "Cancel"},
    "settings_save_ph":       {"zh": "选择存档文件所在的文件夹...",  "en": "Select save folder..."},
    "settings_log_ph":        {"zh": "选择日志文件所在的文件夹...",  "en": "Select log folder..."},
    "settings_save_dialog":   {"zh": "选择存档文件所在的文件夹",    "en": "Select save folder"},
    "settings_log_dialog":    {"zh": "选择日志文件所在的文件夹",    "en": "Select log folder"},
    # 系统托盘
    "tray_show":              {"zh": "显示窗口",                  "en": "Show window"},
    "tray_quit":              {"zh": "退出",                      "en": "Quit"},
    # 检查更新
    "update_available":       {"zh": "🆕 发现新版本 {ver}，点击下载", "en": "🆕 New version {ver} available — click to download"},
    "update_checking":        {"zh": "检查更新中...",              "en": "Checking for updates..."},
    "update_latest":          {"zh": "已是最新版本 {ver}",         "en": "Already up to date ({ver})"},
    "update_failed":          {"zh": "检查更新失败",               "en": "Update check failed"},
    "update_btn_tip":         {"zh": "点击前往下载页",             "en": "Click to open download page"},
    # 关于菜单
    "btn_about":              {"zh": "?",                         "en": "?"},
    "btn_about_tip":          {"zh": "关于 / 帮助",               "en": "About / Help"},
    "about_github":           {"zh": "📦 GitHub 项目页",          "en": "📦 GitHub Repository"},
    "about_steam":            {"zh": "🎮 Steam 创意工坊页",        "en": "🎮 Steam Workshop Page"},
}


def _t(key: str, lang: str, **kwargs) -> str:
    """获取翻译字符串，支持 format 参数"""
    s = _UI_STRINGS.get(key, {}).get(lang) or _UI_STRINGS.get(key, {}).get("zh", key)
    return s.format(**kwargs) if kwargs else s


# ---------------------------------------------------------------------------
# 检查更新线程
# ---------------------------------------------------------------------------

class UpdateChecker(QThread):
    """
    后台查询 GitHub Releases API，比较版本号。
    update_found(latest_ver, url)  — 有新版本
    up_to_date(current_ver)        — 已是最新
    check_failed()                 — 网络错误 / 解析失败（静默）
    """
    update_found = pyqtSignal(str, str)   # (latest_version, release_url)
    up_to_date   = pyqtSignal(str)        # (current_version)
    check_failed = pyqtSignal()

    def __init__(self, repo: str, current_ver: str) -> None:
        super().__init__()
        self._repo = repo
        self._current_ver = current_ver

    @staticmethod
    def _parse_ver(ver: str) -> tuple[int, ...]:
        """'v1.2.3' or '1.2.3' → (1, 2, 3)"""
        ver = ver.lstrip("vV").strip()
        try:
            return tuple(int(x) for x in ver.split("."))
        except ValueError:
            return (0,)

    def run(self) -> None:
        try:
            api_url = f"https://api.github.com/repos/{self._repo}/releases/latest"
            resp = requests.get(api_url, timeout=8,
                                headers={"Accept": "application/vnd.github+json",
                                         "X-GitHub-Api-Version": "2022-11-28"})
            if resp.status_code != 200:
                self.check_failed.emit()
                return
            data = resp.json()
            latest_tag = data.get("tag_name", "")
            html_url   = data.get("html_url", f"https://github.com/{self._repo}/releases")
            if not latest_tag:
                self.check_failed.emit()
                return
            if self._parse_ver(latest_tag) > self._parse_ver(self._current_ver):
                self.update_found.emit(latest_tag.lstrip("vV"), html_url)
            else:
                self.up_to_date.emit(self._current_ver)
        except Exception as e:
            log.debug(f"检查更新失败: {e}")
            self.check_failed.emit()


# ---------------------------------------------------------------------------
# 后台 HTTP 请求线程（避免阻塞 UI）
# ---------------------------------------------------------------------------

class EvaluateWorker(QThread):
    """
    在独立线程中调用后端 /api/evaluate，
    完成后通过信号返回结果。
    """
    result_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, run_state: dict, language: str = "zh") -> None:
        super().__init__()
        self.run_state = run_state
        self.language = language

    def run(self) -> None:
        try:
            resp = requests.post(
                f"{BACKEND_URL}/api/evaluate",
                json={"run_state": self.run_state, "language": self.language},
                timeout=5,
            )
            resp.raise_for_status()
            self.result_ready.emit(resp.json())
        except requests.exceptions.ConnectionError:
            self.error_occurred.emit(
                "无法连接后端服务（请先启动 main.py）"
                if self.language == "zh"
                else "Cannot connect to backend (start main.py first)"
            )
        except requests.exceptions.Timeout:
            self.error_occurred.emit(
                "请求超时" if self.language == "zh" else "Request timed out"
            )
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class _OcrSnapshotWorker(QThread):
    """
    手动触发一次 OCR 截图识别。
    直接在前端进程调用 vision 模块（不走后端 HTTP），
    结果通过 result_ready 信号返回。
    """
    result_ready = pyqtSignal(dict)

    def __init__(self, backend_url: str) -> None:
        super().__init__()
        self._backend_url = backend_url

    def run(self) -> None:
        try:
            from vision.window_capture import WindowCapture
            from vision.screen_detector import ScreenDetector, ScreenType
            from vision.card_normalizer import get_card_normalizer
            import datetime

            capture = WindowCapture()
            if capture.find_window() is None:
                self.result_ready.emit({
                    "screen_type": "unknown",
                    "error": "未找到 STS2 窗口",
                })
                return

            screenshot = capture.capture()
            if screenshot is None:
                self.result_ready.emit({
                    "screen_type": "unknown",
                    "error": "截图失败",
                })
                return

            # 界面检测（单帧，不投票）
            detector = ScreenDetector(vote_frames=1)
            det = detector.detect(screenshot)

            if det.screen_type == ScreenType.CARD_REWARD:
                # 用比例坐标裁剪三个卡名区域分别 OCR
                from vision.vision_bridge import VisionBridge
                from vision.ocr_engine import get_ocr_engine
                normalizer = get_card_normalizer()

                ocr_engine = get_ocr_engine()
                title_y = VisionBridge._find_title_y(det.ocr_result)
                ocr_texts = VisionBridge._extract_card_names_combined(
                    screenshot, ocr_engine, det.ocr_result, title_y
                )
                norm = normalizer.normalize(ocr_texts)
                card_ids = [m.card_id if m else None for m in norm.cards]
                card_names = [m.matched_name if m else "" for m in norm.cards]
                confidences = [m.confidence if m else 0.0 for m in norm.cards]

                self.result_ready.emit({
                    "source": "vision_snapshot",
                    "screen_type": "card_reward",
                    "card_choices": [c for c in card_ids if c],
                    "card_names": card_names,
                    "confidences": confidences,
                    "ocr_texts": ocr_texts,
                    "all_reliable": norm.all_reliable,
                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                })

            elif det.screen_type == ScreenType.SHOP:
                self.result_ready.emit({
                    "source": "vision_snapshot",
                    "screen_type": "shop",
                    "matched_keywords": det.matched_keywords,
                })
            else:
                self.result_ready.emit({
                    "source": "vision_snapshot",
                    "screen_type": det.screen_type.value,
                    "ocr_text": det.ocr_text[:200] if det.ocr_text else "",
                })

        except Exception as e:
            log.error(f"OCR 截图识别失败: {e}")
            self.result_ready.emit({
                "screen_type": "unknown",
                "error": str(e),
            })


class CardsFetchWorker(QThread):
    """在独立线程中拉取指定角色的卡牌列表（含无色卡）"""
    cards_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, character: str) -> None:
        super().__init__()
        self.character = character

    def run(self) -> None:
        try:
            char_resp = requests.get(
                f"{BACKEND_URL}/api/cards",
                params={"character": self.character},
                timeout=5,
            )
            char_resp.raise_for_status()
            char_cards = char_resp.json().get("cards", [])

            # 同时拉取无色卡牌
            colorless_resp = requests.get(
                f"{BACKEND_URL}/api/cards",
                params={"character": "colorless"},
                timeout=5,
            )
            colorless_cards = []
            if colorless_resp.status_code == 200:
                colorless_cards = colorless_resp.json().get("cards", [])

            self.cards_ready.emit(char_cards + colorless_cards)
        except requests.exceptions.ConnectionError:
            self.error_occurred.emit("无法连接后端")
        except Exception as exc:
            self.error_occurred.emit(str(exc))


# ---------------------------------------------------------------------------
# 卡牌选择器组件
# ---------------------------------------------------------------------------

_RARITY_CHIP_PARAMS = {
    "common":   ("rgba(35,28,18,0.8)",  "#3A2E1E", "#9A8A6A", "rgba(50,80,30,0.85)",  "#5A8A2E", "#A8D870"),
    "uncommon": ("rgba(20,35,50,0.8)",  "#2E5A8A", "#64B5F6", "rgba(20,55,90,0.9)",   "#4A8ABA", "#90CAF9"),
    "rare":     ("rgba(50,30,10,0.8)",  "#8A5A1E", "#FFD54F", "rgba(80,50,10,0.9)",   "#C8901E", "#FFE082"),
    "basic":    ("rgba(30,30,30,0.8)",  "#444",    "#888",    "rgba(50,50,50,0.9)",   "#666",    "#bbb"),
    "ancient":  ("rgba(40,20,50,0.8)",  "#7A3A9A", "#CC88FF", "rgba(70,30,90,0.9)",   "#AA60CC", "#EEB8FF"),
}


def _get_rarity_chip_style(rarity: str, scale: float) -> dict:
    params = _RARITY_CHIP_PARAMS.get(rarity, _RARITY_CHIP_PARAMS["common"])
    return _build_chip_style(*params, scale=scale)


class CardChipButton(QPushButton):
    """单张卡牌的可切换按钮（颜色按稀有度区分）"""
    toggled_card = pyqtSignal(dict, bool)  # (card, is_selected)

    def __init__(self, card: dict, display_name: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._card = card
        self._selected = False
        self._display_name = display_name or card.get("name", "")
        rarity_raw = card.get("rarity", "common").lower()
        self._rarity = rarity_raw if rarity_raw in _RARITY_CHIP_PARAMS else "common"

        cost = card.get("cost", 0)
        cost_str = "X" if cost == -1 else str(cost)
        self.setText(f"[{cost_str}] {self._display_name}")
        self.setObjectName("CardChip")
        self._apply_style()
        self.setMinimumWidth(100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.clicked.connect(self._on_click)

    def _apply_style(self) -> None:
        key = "selected" if self._selected else "normal"
        self.setStyleSheet(_get_rarity_chip_style(self._rarity, _get_ui_scale())[key])

    @property
    def card(self) -> dict:
        return self._card

    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, value: bool, emit: bool = True) -> None:
        self._selected = value
        self._apply_style()
        if emit:
            self.toggled_card.emit(self._card, value)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton and self._selected:
            self.set_selected(False)
        else:
            super().mousePressEvent(event)

    def _on_click(self) -> None:
        self.set_selected(not self._selected)


class CardPickerPanel(QScrollArea):
    """按大类（可折叠）+ 费用分段显示的卡牌选择面板"""
    selection_changed = pyqtSignal(list, list)  # (已选卡列表, 显示名列表)

    _TYPE_ORDER = ["attack", "skill", "power"]
    _TYPE_LABELS_ZH = {"attack": "攻击", "skill": "技能", "power": "能力", "colorless": "无色"}
    _TYPE_LABELS_EN = {"attack": "Attack", "skill": "Skill", "power": "Power", "colorless": "Colorless"}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CardPickerScroll")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._content = QWidget()
        self._content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(2)
        self._layout.addStretch()
        self.setWidget(self._content)

        self._chips: list[CardChipButton] = []
        # 平铺列表，供 filter_cards 使用：每项 (header_or_btn, container, chips_in_section)
        self._sections: list[tuple[QWidget, QWidget, list[CardChipButton]]] = []
        # 大类折叠状态：大类标签 → 其下所有子 section 的 container 列表
        self._group_bodies: list[tuple[QPushButton, list[QWidget]]] = []
        self._language: str = "en"

    def set_language(self, lang: str) -> None:
        self._language = lang

    def populate(self, cards: list[dict]) -> None:
        """按大类（可折叠）+ 费用子分段填充卡牌"""
        self.clear_cards()

        # 加载中文名映射（仅中文模式）
        locale_map: dict[str, str] = {}
        if self._language == "zh":
            try:
                try:
                    from frontend.card_locale import get_card_locale
                except ImportError:
                    from card_locale import get_card_locale
                lc = get_card_locale()
                locale_map = {cid: lc.get_chinese_name(cid) or "" for cid in [c.get("id", "") for c in cards]}
            except Exception:
                pass

        type_labels = self._TYPE_LABELS_ZH if self._language == "zh" else self._TYPE_LABELS_EN

        def cost_key(c):
            cost = c.get("cost", 0)
            return 99 if cost == -1 else cost

        def cost_label(cost: int) -> str:
            if cost == -1:
                return "X"
            if cost >= 3:
                return "3+" if self._language == "en" else "3费+"
            return str(cost)

        cols = 3

        def _make_cost_sections(group_cards: list[dict]) -> list[tuple[int, list[dict]]]:
            """按费用分桶：0, 1, 2, 3+（-1归入3+）"""
            buckets: dict[int, list[dict]] = {0: [], 1: [], 2: [], 3: []}
            for c in group_cards:
                cost = c.get("cost", 0)
                key = min(cost, 3) if cost >= 0 else 3
                buckets[key].append(c)
            return [(k, v) for k, v in buckets.items() if v]

        def _add_type_group(type_label: str, group_cards: list[dict]) -> None:
            """添加一个大类折叠块（含费用子分段）"""
            cost_sections = _make_cost_sections(sorted(group_cards, key=cost_key))
            if not cost_sections:
                return

            # 折叠按钮（大类标题）
            btn = QPushButton(f"▼  {type_label}  ({len(group_cards)})")
            btn.setObjectName("CardTypeGroupHeader")
            btn.setCheckable(False)
            btn.setFlat(True)
            self._layout.insertWidget(self._layout.count() - 1, btn)

            # 大类下所有子容器
            group_body_widgets: list[QWidget] = []

            for cost_val, section_cards in cost_sections:
                # 费用子标题 + 卡片网格打包进 sub_body（直接构建，不经过 _layout 中转）
                sub_body = QWidget()
                sub_vbox = QVBoxLayout(sub_body)
                sub_vbox.setContentsMargins(0, 0, 0, 0)
                sub_vbox.setSpacing(1)

                cost_lbl_text = cost_label(cost_val)
                cost_header = QLabel(f"  {cost_lbl_text}")
                cost_header.setObjectName("CardPickerSectionHeader")
                sub_vbox.addWidget(cost_header)

                grid = QGridLayout()
                grid.setSpacing(3)
                section_chips: list[CardChipButton] = []
                for i, card in enumerate(section_cards):
                    card_id = card.get("id", "")
                    display = (locale_map.get(card_id) or card.get("name", "")
                               if self._language == "zh" else card.get("name", ""))
                    chip = CardChipButton(card, display_name=display)
                    chip.toggled_card.connect(self._on_chip_toggled)
                    self._chips.append(chip)
                    section_chips.append(chip)
                    grid.addWidget(chip, i // cols, i % cols)

                grid_container = QWidget()
                grid_container.setLayout(grid)
                sub_vbox.addWidget(grid_container)

                self._layout.insertWidget(self._layout.count() - 1, sub_body)
                group_body_widgets.append(sub_body)
                self._sections.append((cost_header, grid_container, section_chips))

            # 折叠逻辑
            def _toggle(checked, b=btn, widgets=group_body_widgets, lbl=type_label, n=len(group_cards)):
                collapsed = b.text().startswith("▶")
                if collapsed:
                    b.setText(f"▼  {lbl}  ({n})")
                    for w in widgets:
                        w.setVisible(True)
                else:
                    b.setText(f"▶  {lbl}  ({n})")
                    for w in widgets:
                        w.setVisible(False)

            btn.clicked.connect(_toggle)
            self._group_bodies.append((btn, group_body_widgets))

        def _add_flat_group(label_text: str, group_cards: list[dict]) -> None:
            """无色/先古：不按费用分段，直接平铺（无折叠）"""
            if not group_cards:
                return
            header = QLabel(label_text)
            header.setObjectName("CardPickerSectionHeader")
            self._layout.insertWidget(self._layout.count() - 1, header)

            grid = QGridLayout()
            grid.setSpacing(3)
            section_chips: list[CardChipButton] = []
            for i, card in enumerate(group_cards):
                card_id = card.get("id", "")
                display = (locale_map.get(card_id) or card.get("name", "")
                           if self._language == "zh" else card.get("name", ""))
                chip = CardChipButton(card, display_name=display)
                chip.toggled_card.connect(self._on_chip_toggled)
                self._chips.append(chip)
                section_chips.append(chip)
                grid.addWidget(chip, i // cols, i % cols)

            grid_container = QWidget()
            grid_container.setLayout(grid)
            self._layout.insertWidget(self._layout.count() - 1, grid_container)
            self._sections.append((header, grid_container, section_chips))

        # 按类型分组（排除无色和先古）
        groups: dict[str, list[dict]] = {t: [] for t in self._TYPE_ORDER}
        for card in cards:
            ct = card.get("card_type", "").lower()
            if ct in groups and card.get("character", "").lower() != "colorless":
                groups[ct].append(card)

        for type_key in self._TYPE_ORDER:
            if groups[type_key]:
                _add_type_group(type_labels.get(type_key, type_key), groups[type_key])

        colorless_cards = sorted(
            [c for c in cards if c.get("character", "").lower() == "colorless"],
            key=cost_key
        )
        if colorless_cards:
            _add_flat_group(type_labels.get("colorless", "Colorless"), colorless_cards)

        ancient_cards = sorted(
            [c for c in cards if c.get("rarity", "").lower() == "ancient"],
            key=lambda c: c.get("name", "")
        )
        if ancient_cards:
            _add_flat_group("先古" if self._language == "zh" else "Ancient", ancient_cards)

    def clear_cards(self) -> None:
        """清除所有卡片和标题"""
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._chips.clear()
        self._sections.clear()
        self._group_bodies.clear()

    def filter_cards(self, query: str) -> None:
        """按名称实时过滤卡牌，隐藏不匹配的 chip 和空分组"""
        q = query.strip().lower()

        # 搜索时展开所有大类（便于看到结果）
        if q:
            for btn, widgets in self._group_bodies:
                for w in widgets:
                    w.setVisible(True)
                if btn.text().startswith("▶"):
                    btn.setText(btn.text().replace("▶", "▼", 1))

        # 先按卡片匹配更新每个费用分段的可见性
        sub_body_visible: dict[int, bool] = {}  # id(sub_body) → any visible
        for cost_header, grid_container, chips in self._sections:
            any_visible = False
            for chip in chips:
                visible = not q or q in chip._display_name.lower() or q in chip.card.get("id", "").lower()
                chip.setVisible(visible)
                if visible:
                    any_visible = True
            cost_header.setVisible(any_visible)
            grid_container.setVisible(any_visible)
            sub_body = cost_header.parent()
            if sub_body and sub_body is not self._content:
                prev = sub_body_visible.get(id(sub_body), False)
                sub_body_visible[id(sub_body)] = prev or any_visible
                sub_body.setVisible(sub_body_visible[id(sub_body)])

    def deselect_by_id(self, card_id: str) -> None:
        """根据 card id 取消对应 chip 的选中状态"""
        for chip in self._chips:
            if chip.card.get("id", "") == card_id and chip.is_selected():
                chip.set_selected(False)

    def clear_selection(self) -> None:
        """取消所有选中（不触发信号）"""
        for chip in self._chips:
            chip.set_selected(False, emit=False)

    def selected_cards(self) -> list[dict]:
        return [chip.card for chip in self._chips if chip.is_selected()]

    def selected_display_names(self) -> list[str]:
        return [chip._display_name for chip in self._chips if chip.is_selected()]

    def _on_chip_toggled(self, card: dict, selected: bool) -> None:
        self.selection_changed.emit(self.selected_cards(), self.selected_display_names())


class SelectionTrayWidget(QWidget):
    """显示已选卡牌的托盘 + 评估按钮"""
    evaluate_requested = pyqtSignal(list)
    deselect_requested = pyqtSignal(str)  # card_id

    def __init__(self, parent=None, language: str = "zh") -> None:
        super().__init__(parent)
        self.setObjectName("SelectionTray")
        self._selected_cards: list[dict] = []
        self._language = language

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        self._prefix_label = QLabel(_t("tray_prefix", self._language))
        self._prefix_label.setStyleSheet("color: #888;")
        layout.addWidget(self._prefix_label)

        # 动态卡名区域
        self._chips_widget = QWidget()
        self._chips_layout = QHBoxLayout(self._chips_widget)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(4)
        layout.addWidget(self._chips_widget, 1)

        self._count_label = QLabel("0/4")
        self._count_label.setStyleSheet("color: #666;")
        layout.addWidget(self._count_label)

        self._evaluate_btn = QPushButton(_t("btn_evaluate", self._language))
        self._evaluate_btn.setObjectName("EvaluateButton")
        self._evaluate_btn.setEnabled(False)
        self._evaluate_btn.clicked.connect(
            lambda: self.evaluate_requested.emit(self._selected_cards)
        )
        layout.addWidget(self._evaluate_btn)

    def set_language(self, language: str) -> None:
        self._language = language
        self._prefix_label.setText(_t("tray_prefix", self._language))
        self._evaluate_btn.setText(_t("btn_evaluate", self._language))

    def update_selection(self, cards: list[dict], display_names: list[str] | None = None) -> None:
        self._selected_cards = cards

        # 清除旧的卡名标签
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        names = display_names if display_names else [c.get("name", "?") for c in cards]
        for card, name in zip(cards, names):
            if len(name) > 12:
                name = name[:11] + "…"
            lbl = QLabel(name)
            lbl.setObjectName("TrayCardLabel")
            lbl.setStyleSheet(
                "color: #A8D870; background: rgba(60,90,40,0.7); "
                "border: 1px solid #6A9A3E; border-radius: 3px; "
                f"padding: 1px 5px; font-size: {_fs(13)}px;"
            )
            lbl.setToolTip(_t("tray_deselect_tip", self._language))
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            card_id = card.get("id", "")
            lbl.mousePressEvent = lambda event, cid=card_id: self.deselect_requested.emit(cid)
            self._chips_layout.addWidget(lbl)

        self._count_label.setText(f"{len(cards)}/4")
        self._evaluate_btn.setEnabled(len(cards) >= 1)


# ---------------------------------------------------------------------------
# 游戏状态实时监视 WebSocket 线程
# ---------------------------------------------------------------------------

class GameStateWatcher(QThread):
    """
    连接到后端 WebSocket，接收实时游戏状态更新

    信号：
    - game_state_updated: 游戏状态更新
    - connection_status: 连接状态变化
    - log_status_updated: 日志监视状态更新
    """
    game_state_updated = pyqtSignal(dict)
    connection_status = pyqtSignal(str, bool)  # (状态消息, 是否连接)
    log_status_updated = pyqtSignal(dict)  # 日志状态
    vision_state_updated = pyqtSignal(dict)  # OCR 识别状态

    def __init__(self, backend_url: str) -> None:
        super().__init__()
        self.backend_url = backend_url.replace("http://", "ws://").replace("https://", "wss://")
        self.ws = None
        self.is_running = False
        self.current_state = {}

    def run(self) -> None:
        """连接 WebSocket 并监听游戏状态更新"""
        ws_url = f"{self.backend_url}/ws/game-state"
        log.info(f"连接到 WebSocket: {ws_url}")
        self.is_running = True

        while self.is_running:
            try:
                import websocket as ws_lib
                self.ws = ws_lib.WebSocketApp(
                    ws_url,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close,
                    on_open=self.on_open,
                )

                self.ws.run_forever()

            except Exception as e:
                log.error(f"WebSocket 连接失败: {e}")
                self.connection_status.emit(f"连接失败: {e}", False)

                # 重试连接
                if self.is_running:
                    time.sleep(3)

    def on_open(self, ws):
        """WebSocket 连接打开"""
        log.info("✓ WebSocket 已连接")
        self.connection_status.emit("已连接游戏监视", True)

    def on_message(self, ws, message: str):
        """接收 WebSocket 消息"""
        try:
            data = json.loads(message)

            if data.get("type") == "game_state":
                state = data.get("data", {})
                self.current_state.update(state)
                log.debug(f"游戏状态更新: {state}")

                # 发出信号更新UI
                self.game_state_updated.emit(self.current_state)

            elif data.get("type") == "log_status":
                log_status = data.get("data", {})
                log.debug(f"日志状态更新: {log_status}")
                self.log_status_updated.emit(log_status)

            elif data.get("type") == "vision_state":
                vision_data = data.get("data", {})
                log.debug(f"OCR 识别结果: {vision_data}")
                self.vision_state_updated.emit(vision_data)

        except json.JSONDecodeError as e:
            log.warning(f"无效的 WebSocket 消息: {e}")
        except Exception as e:
            log.error(f"处理 WebSocket 消息失败: {e}")

    def on_error(self, ws, error):
        """WebSocket 错误"""
        log.error(f"WebSocket 错误: {error}")
        self.connection_status.emit(f"连接错误: {error}", False)

    def on_close(self, ws, close_status_code, close_msg):
        """WebSocket 关闭"""
        log.info("WebSocket 已关闭")
        if self.is_running:
            self.connection_status.emit("连接已断开，尝试重新连接...", False)

    def stop(self):
        """停止 WebSocket 连接"""
        self.is_running = False
        if self.ws:
            self.ws.close()

    def send_ping(self):
        """发送 ping 保持连接"""
        if self.ws:
            try:
                self.ws.send("ping")
            except Exception as e:
                log.debug(f"Ping 失败: {e}")


# ---------------------------------------------------------------------------
# 路径设置对话框
# ---------------------------------------------------------------------------

class PathSettingsDialog(QDialog):
    """允许用户设置存档和日志路径"""

    def __init__(self, parent: QWidget | None = None, backend_url: str = "") -> None:
        super().__init__(parent)
        self.backend_url = backend_url
        # inherit language from parent window if available
        self._language = getattr(parent, "_language", "zh")
        self.setWindowTitle(_t("settings_title", self._language))
        self.setModal(True)
        self.setMinimumWidth(500)
        # 记录打开时的初始值，供取消时还原
        try:
            from scripts.config_manager import get_font_scale, get_opacity, get_language
            self._orig_font_scale = get_font_scale()
            self._orig_opacity = get_opacity()
            self._orig_language = get_language()
        except Exception:
            self._orig_font_scale = 1.0
            self._orig_opacity = 0.95
            self._orig_language = self._language
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 帮助信息
        help_text = QLabel(_t("settings_help", self._language))
        help_text.setStyleSheet("color: #666; font-size: 12pt; padding: 8px;")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        # ===== 语言设置 =====
        lang_layout = QHBoxLayout()
        lang_label = QLabel(_t("settings_lang", self._language))
        lang_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        lang_layout.addWidget(lang_label)

        self._lang_combo = QComboBox()
        self._lang_combo.addItem("English", "en")
        self._lang_combo.addItem("简体中文", "zh")
        try:
            from scripts.config_manager import get_language
            current_lang = get_language()
            idx = self._lang_combo.findData(current_lang)
            if idx >= 0:
                self._lang_combo.setCurrentIndex(idx)
        except Exception:
            pass
        self._lang_combo.setMaximumWidth(150)
        def _on_lang_changed(idx: int) -> None:
            new_lang = self._lang_combo.currentData()
            if not new_lang:
                return
            parent = self.parent()
            if parent and hasattr(parent, '_reload_ui_language'):
                from scripts.config_manager import set_language
                set_language(new_lang)
                parent._language = new_lang
                parent._reload_ui_language()
        self._lang_combo.currentIndexChanged.connect(_on_lang_changed)
        lang_layout.addWidget(self._lang_combo)
        lang_layout.addStretch()
        layout.addLayout(lang_layout)

        # ===== 字体缩放 =====
        scale_layout = QHBoxLayout()
        scale_label = QLabel(_t("settings_font", self._language))
        scale_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        scale_layout.addWidget(scale_label)

        self._scale_slider = QSlider(Qt.Orientation.Horizontal)
        self._scale_slider.setRange(80, 160)
        self._scale_slider.setSingleStep(5)
        self._scale_slider.setMaximumWidth(160)
        try:
            from scripts.config_manager import get_font_scale
            self._scale_slider.setValue(int(get_font_scale() * 100))
        except Exception:
            self._scale_slider.setValue(100)

        current_pct = self._scale_slider.value()
        self._scale_value_label = QLabel(f"{current_pct}%")
        self._scale_value_label.setMinimumWidth(40)
        def _on_scale_changed(v: int) -> None:
            self._scale_value_label.setText(f"{v}%")
            # 实时预览：直接更新父窗口字体
            parent = self.parent()
            if parent and hasattr(parent, '_load_stylesheet'):
                from scripts.config_manager import set_font_scale
                set_font_scale(v / 100.0)
                parent._load_stylesheet()
        self._scale_slider.valueChanged.connect(_on_scale_changed)
        scale_layout.addWidget(self._scale_slider)
        scale_layout.addWidget(self._scale_value_label)
        scale_layout.addStretch()
        layout.addLayout(scale_layout)

        # ===== 全局快捷键 =====
        hotkey_layout = QHBoxLayout()
        hotkey_label = QLabel(_t("settings_hotkey", self._language))
        hotkey_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        hotkey_layout.addWidget(hotkey_label)

        self._hotkey_input = QLineEdit()
        self._hotkey_input.setMaximumWidth(200)
        self._hotkey_input.setPlaceholderText(_t("settings_hotkey_ph", self._language))
        try:
            from scripts.config_manager import get_hotkey
            self._hotkey_input.setText(get_hotkey())
        except Exception:
            self._hotkey_input.setText("ctrl+shift+s")
        hotkey_layout.addWidget(self._hotkey_input)
        hotkey_layout.addStretch()
        layout.addLayout(hotkey_layout)

        # ===== 窗口透明度 =====
        opacity_layout = QHBoxLayout()
        opacity_label = QLabel(_t("settings_opacity", self._language))
        opacity_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        opacity_layout.addWidget(opacity_label)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(40, 100)
        self._opacity_slider.setSingleStep(5)
        self._opacity_slider.setMaximumWidth(160)
        try:
            from scripts.config_manager import get_opacity
            self._opacity_slider.setValue(int(get_opacity() * 100))
        except Exception:
            self._opacity_slider.setValue(95)

        current_opacity_pct = self._opacity_slider.value()
        self._opacity_value_label = QLabel(f"{current_opacity_pct}%")
        self._opacity_value_label.setMinimumWidth(40)
        def _on_opacity_changed(v: int) -> None:
            self._opacity_value_label.setText(f"{v}%")
            # 实时预览：直接更新父窗口透明度
            parent = self.parent()
            if parent and hasattr(parent, 'setWindowOpacity'):
                parent.setWindowOpacity(v / 100.0)
        self._opacity_slider.valueChanged.connect(_on_opacity_changed)
        opacity_layout.addWidget(self._opacity_slider)
        opacity_layout.addWidget(self._opacity_value_label)
        opacity_layout.addStretch()
        layout.addLayout(opacity_layout)

        # 分隔线
        separator1 = QFrame()
        separator1.setFrameShape(QFrame.Shape.HLine)
        separator1.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator1)

        # ===== 存档路径 =====
        save_header_layout = QHBoxLayout()
        save_label = QLabel(_t("settings_save_folder", self._language))
        save_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        save_header_layout.addWidget(save_label)

        self._save_indicator = QLabel("●")
        self._save_indicator.setStyleSheet("color: #aaa; font-size: 16pt;")
        save_header_layout.addWidget(self._save_indicator)
        save_header_layout.addStretch()
        layout.addLayout(save_header_layout)

        save_input_layout = QHBoxLayout()
        self._save_path_input = QLineEdit()
        self._save_path_input.setPlaceholderText(_t("settings_save_ph", self._language))
        self._save_path_input.setMinimumHeight(32)
        self._save_path_input.textChanged.connect(self._validate_save_path)
        save_browse_btn = QPushButton(_t("settings_browse", self._language))
        save_browse_btn.setMaximumWidth(80)
        save_browse_btn.clicked.connect(self._browse_save_path)
        save_input_layout.addWidget(self._save_path_input)
        save_input_layout.addWidget(save_browse_btn)
        layout.addLayout(save_input_layout)

        # 存档文件夹验证提示
        self._save_status = QLabel()
        self._save_status.setStyleSheet("color: #666; font-size: 9pt; margin-left: 4px;")
        self._save_status.setWordWrap(True)
        layout.addWidget(self._save_status)
        self._update_save_hint()

        # 分隔线
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.Shape.HLine)
        separator2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator2)

        # ===== 日志路径 =====
        log_header_layout = QHBoxLayout()
        log_label = QLabel(_t("settings_log_folder", self._language))
        log_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        log_header_layout.addWidget(log_label)

        self._log_indicator = QLabel("●")
        self._log_indicator.setStyleSheet("color: #aaa; font-size: 16pt;")
        log_header_layout.addWidget(self._log_indicator)
        log_header_layout.addStretch()
        layout.addLayout(log_header_layout)

        log_input_layout = QHBoxLayout()
        self._log_path_input = QLineEdit()
        self._log_path_input.setPlaceholderText(_t("settings_log_ph", self._language))
        self._log_path_input.setMinimumHeight(32)
        self._log_path_input.textChanged.connect(self._validate_log_path)
        log_browse_btn = QPushButton(_t("settings_browse", self._language))
        log_browse_btn.setMaximumWidth(80)
        log_browse_btn.clicked.connect(self._browse_log_path)
        log_input_layout.addWidget(self._log_path_input)
        log_input_layout.addWidget(log_browse_btn)
        layout.addLayout(log_input_layout)

        # 日志文件夹验证提示
        self._log_status = QLabel()
        self._log_status.setStyleSheet("color: #666; font-size: 9pt; margin-left: 4px;")
        self._log_status.setWordWrap(True)
        layout.addWidget(self._log_status)
        self._update_log_hint()

        layout.addStretch()

        # 按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        save_btn = QPushButton(_t("settings_save_btn", self._language))
        save_btn.setMinimumWidth(100)
        save_btn.clicked.connect(self._save_settings)
        button_layout.addWidget(save_btn)

        cancel_btn = QPushButton(_t("settings_cancel_btn", self._language))
        cancel_btn.setMinimumWidth(100)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

    def _validate_save_path(self) -> None:
        """实时验证存档路径"""
        path_text = self._save_path_input.text().strip()
        self._check_save_folder(path_text)

    def _validate_log_path(self) -> None:
        """实时验证日志路径"""
        path_text = self._log_path_input.text().strip()
        self._check_log_folder(path_text)

    def _check_save_folder(self, path_text: str) -> bool:
        """检查存档文件夹并搜索合适的文件"""
        from pathlib import Path

        if not path_text:
            self._save_indicator.setStyleSheet("color: #aaa;")
            self._save_status.setText("未设置" if self._language == "zh" else "Not set")
            return False

        try:
            folder = Path(path_text)
            if not folder.exists():
                self._save_indicator.setStyleSheet("color: #F44336;")
                self._save_status.setText(
                    f"❌ 文件夹不存在: {path_text}" if self._language == "zh"
                    else f"❌ Folder not found: {path_text}"
                )
                return False

            if not folder.is_dir():
                self._save_indicator.setStyleSheet("color: #F44336;")
                self._save_status.setText(
                    f"❌ 不是文件夹: {path_text}" if self._language == "zh"
                    else f"❌ Not a folder: {path_text}"
                )
                return False

            # 搜索合适的存档文件
            save_files = list(folder.glob("*.save")) + list(folder.glob("current_run.save*"))
            if save_files:
                found_file = save_files[0]
                self._save_indicator.setStyleSheet("color: #4CAF50;")
                self._save_status.setText(
                    f"✓ 找到存档文件: {found_file.name}" if self._language == "zh"
                    else f"✓ Save file found: {found_file.name}"
                )
                return True
            else:
                self._save_indicator.setStyleSheet("color: #FFC107;")
                self._save_status.setText(
                    "⚠ 文件夹存在但未找到 *.save 文件" if self._language == "zh"
                    else "⚠ Folder exists but no *.save file found"
                )
                return False

        except Exception as e:
            self._save_indicator.setStyleSheet("color: #F44336;")
            self._save_status.setText(
                f"❌ 检查失败: {e}" if self._language == "zh" else f"❌ Check failed: {e}"
            )
            return False

    def _check_log_folder(self, path_text: str) -> bool:
        """检查日志文件夹并搜索合适的文件"""
        from pathlib import Path

        if not path_text:
            self._log_indicator.setStyleSheet("color: #aaa;")
            self._log_status.setText("未设置" if self._language == "zh" else "Not set")
            return False

        try:
            folder = Path(path_text)
            if not folder.exists():
                self._log_indicator.setStyleSheet("color: #F44336;")
                self._log_status.setText(
                    f"❌ 文件夹不存在: {path_text}" if self._language == "zh"
                    else f"❌ Folder not found: {path_text}"
                )
                return False

            if not folder.is_dir():
                self._log_indicator.setStyleSheet("color: #F44336;")
                self._log_status.setText(
                    f"❌ 不是文件夹: {path_text}" if self._language == "zh"
                    else f"❌ Not a folder: {path_text}"
                )
                return False

            # 搜索合适的日志文件
            log_files = list(folder.glob("*.log")) + list(folder.glob("*.txt"))
            if log_files:
                # 找最新的日志文件
                latest = max(log_files, key=lambda p: p.stat().st_mtime)
                self._log_indicator.setStyleSheet("color: #4CAF50;")
                self._log_status.setText(
                    f"✓ 找到日志文件: {latest.name}" if self._language == "zh"
                    else f"✓ Log file found: {latest.name}"
                )
                return True
            else:
                self._log_indicator.setStyleSheet("color: #FFC107;")
                self._log_status.setText(
                    "⚠ 文件夹存在但未找到 *.log 或 *.txt 文件" if self._language == "zh"
                    else "⚠ Folder exists but no *.log or *.txt file found"
                )
                return False

        except Exception as e:
            self._log_indicator.setStyleSheet("color: #F44336;")
            self._log_status.setText(
                f"❌ 检查失败: {e}" if self._language == "zh" else f"❌ Check failed: {e}"
            )
            return False

    def _update_save_hint(self) -> None:
        """更新存档路径的自动检测提示"""
        try:
            from pathlib import Path
            steam_path = Path.home() / "AppData" / "Roaming" / "SlayTheSpire2" / "steam"
            if steam_path.exists():
                for save_dir in steam_path.glob("*/profile*/saves"):
                    if save_dir.is_dir():
                        _def = "默认位置" if self._language == "zh" else "Default"
                        self._save_path_input.setPlaceholderText(f"{_def}: {save_dir}")
                        self._check_save_folder(str(save_dir))
                        return
        except Exception:
            pass

    def _update_log_hint(self) -> None:
        """更新日志路径的自动检测提示"""
        try:
            from pathlib import Path
            log_path = Path.home() / "AppData" / "Roaming" / "SlayTheSpire2" / "logs"
            if log_path.exists():
                _def = "默认位置" if self._language == "zh" else "Default"
                self._log_path_input.setPlaceholderText(f"{_def}: {log_path}")
                self._check_log_folder(str(log_path))
                return
        except Exception:
            pass

    def _browse_save_path(self) -> None:
        """浏览并选择存档文件夹"""
        from pathlib import Path
        # 默认打开Steam存档目录
        default_dir = str(Path.home() / "AppData" / "Roaming" / "SlayTheSpire2")

        path = QFileDialog.getExistingDirectory(
            self,
            _t("settings_save_dialog", self._language),
            default_dir,
            QFileDialog.Option.ShowDirsOnly
        )
        if path:
            self._save_path_input.setText(path)

    def _browse_log_path(self) -> None:
        """浏览并选择日志文件夹"""
        from pathlib import Path
        # 默认打开日志目录
        default_dir = str(Path.home() / "AppData" / "Roaming" / "SlayTheSpire2")

        path = QFileDialog.getExistingDirectory(
            self,
            _t("settings_log_dialog", self._language),
            default_dir,
            QFileDialog.Option.ShowDirsOnly
        )
        if path:
            self._log_path_input.setText(path)

    def reject(self) -> None:
        """取消时还原实时预览已应用的设置"""
        parent = self.parent()
        try:
            from scripts.config_manager import set_font_scale, set_opacity, set_language
            set_font_scale(self._orig_font_scale)
            set_opacity(self._orig_opacity)
            set_language(self._orig_language)
            if parent:
                if hasattr(parent, '_load_stylesheet'):
                    parent._load_stylesheet()
                if hasattr(parent, 'setWindowOpacity'):
                    parent.setWindowOpacity(self._orig_opacity)
                if hasattr(parent, '_language') and parent._language != self._orig_language:
                    parent._language = self._orig_language
                    if hasattr(parent, '_reload_ui_language'):
                        parent._reload_ui_language()
        except Exception:
            pass
        super().reject()

    def _save_settings(self) -> None:
        try:
            # 保存语言设置
            self._saved_lang = self._lang_combo.currentData()
            from scripts.config_manager import set_language, set_font_scale, set_hotkey, set_opacity
            set_language(self._saved_lang)

            # 保存字体缩放
            self._saved_font_scale = self._scale_slider.value() / 100.0
            set_font_scale(self._saved_font_scale)

            # 保存快捷键
            self._saved_hotkey = self._hotkey_input.text().strip()
            if self._saved_hotkey:
                set_hotkey(self._saved_hotkey)

            # 保存透明度
            self._saved_opacity = self._opacity_slider.value() / 100.0
            set_opacity(self._saved_opacity)

            # 尝试通知后端更新路径（失败不阻止关闭）
            save_path = self._save_path_input.text().strip()
            log_path = self._log_path_input.text().strip()
            payload = {}
            if save_path:
                payload["save_path"] = save_path
            if log_path:
                payload["log_path"] = log_path
            if payload:
                try:
                    resp = requests.post(
                        f"{self.backend_url}/api/config",
                        json=payload,
                        timeout=5,
                    )
                    resp.raise_for_status()
                except Exception as e:
                    log.warning(f"后端路径更新失败（不影响其他设置）: {e}")

            log.info("✓ 设置已保存")
            self.accept()
        except Exception as e:
            log.error(f"保存设置失败: {e}")


# ---------------------------------------------------------------------------
# 单张卡评估结果 Widget
# ---------------------------------------------------------------------------

_ROLE_ZH = {
    "core":       "套路核心",
    "enabler":    "使能卡",
    "transition": "过渡卡",
    "filler":     "补件",
    "pollution":  "污染",
    "unknown":    "未知",
    "ancient":    "先古之民",
    "curse":      "诅咒",
}

_BEYOND_SCORING_ROLES = {"ancient", "curse"}

_REC_COLORS = {
    "强烈推荐": "#A8D870", "Highly Recommended": "#A8D870",
    "推荐":     "#64B5F6", "Recommended":        "#64B5F6",
    "可选":     "#FFD54F", "Optional":           "#FFD54F",
    "谨慎":     "#FFB74D", "Caution":            "#FFB74D",
    "不推荐":   "#FF7043", "Not Recommended":    "#FF7043",
    "跳过":     "#EF5350", "Skip":               "#EF5350",
}

_GRADE_COLORS = {
    "S":  "#FFD700",  # 黄金色
    "A+": "#66BB6A",  # 亮绿
    "A":  "#66BB6A",  # 绿色
    "A-": "#43A047",  # 深绿
    "B+": "#42A5F5",  # 亮蓝
    "B":  "#42A5F5",  # 蓝色
    "B-": "#1E88E5",  # 深蓝
    "C+": "#FFB74D",  # 橙色
    "C":  "#FF7043",  # 红橙
}


class CardResultWidget(QFrame):
    """
    垂直布局的单张卡评估结果块：
      卡牌名（中文，粗体大字）
      定位（中文角色标签） | 分数 | 推荐
      推荐理由（绿色小字）
      不推荐理由（橙红色小字）
    """

    def __init__(
        self,
        result: dict,
        language: str = "en",
        archetype_name_map: dict[str, dict[str, str]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CardResultWidget")
        self._language = language
        self._archetype_name_map: dict[str, dict[str, str]] = archetype_name_map or {}
        self._fs = _fs   # 字体缩放快捷引用
        self._build_ui(result)

    def _build_ui(self, result: dict) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 6)
        outer.setSpacing(3)

        # ── 行1：卡牌名 + 路径影响标签 ───────────────────────────────────
        raw_name = result.get("card_name", "?")
        card_id = result.get("card_id", "")
        if self._language == "zh":
            try:
                try:
                    from frontend.card_locale import get_card_locale
                except ImportError:
                    from card_locale import get_card_locale
                zh_name = get_card_locale().get_chinese_name(card_id)
                raw_name = zh_name or raw_name
            except Exception:
                pass

        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        name_row.setContentsMargins(0, 0, 0, 0)

        name_label = QLabel(raw_name)
        name_label.setObjectName("cardName")
        name_label.setStyleSheet("font-weight:bold;")
        name_label.setSizePolicy(
            name_label.sizePolicy().horizontalPolicy(),
            name_label.sizePolicy().verticalPolicy(),
        )
        name_row.addWidget(name_label, 0, Qt.AlignmentFlag.AlignVCenter)

        path_impact: dict = result.get("path_impact", {})
        if path_impact:
            path_widget = self._build_path_impact_row(path_impact)
            if path_widget:
                name_row.addWidget(path_widget, 1, Qt.AlignmentFlag.AlignVCenter)
            else:
                name_row.addStretch()
        else:
            name_row.addStretch()

        outer.addLayout(name_row)

        # ── 行2：定位 | 分数 | 推荐 ─────────────────────────────────────
        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)

        role_en = result.get("role", "unknown")
        _ROLE_EN = {
            "core": "Core", "enabler": "Enabler", "transition": "Transition",
            "filler": "Filler", "pollution": "Pollution", "unknown": "Unknown",
            "ancient": "Ancient", "curse": "Curse",
        }
        role_display = _ROLE_ZH.get(role_en, role_en) if self._language == "zh" else _ROLE_EN.get(role_en, role_en)
        role_label = QLabel(role_display)
        role_label.setObjectName("cardRole")
        role_label.setStyleSheet("color:#A09070;")
        meta_row.addWidget(role_label)

        meta_row.addStretch()

        grade = result.get("grade", "")
        score = result.get("total_score", 0)
        is_beyond = result.get("role", "") in _BEYOND_SCORING_ROLES
        grade_text = grade if grade else f"{score:.0f}"
        grade_color = "#7A6A8A" if is_beyond else _GRADE_COLORS.get(grade, "#C8A96E")
        score_label = QLabel(grade_text)
        score_label.setObjectName("cardScore")
        score_label.setStyleSheet(f"color:{grade_color};font-weight:bold;")
        meta_row.addWidget(score_label)

        rec = result.get("recommendation", "")
        rec_color = "#7A6A8A" if is_beyond else _REC_COLORS.get(rec, "#9A8A6A")
        rec_label = QLabel(rec)
        rec_label.setObjectName("cardRecommendation")
        rec_label.setStyleSheet(f"color:{rec_color};font-weight:bold;")
        meta_row.addWidget(rec_label)

        outer.addLayout(meta_row)

        # ── 行3：融合文字块（总结 + 推荐理由 + 不推荐理由）────────────────
        summary_zh      = result.get("summary_zh", "") if self._language == "zh" else ""
        reasons_for     = result.get("reasons_for", [])
        reasons_against = result.get("reasons_against", [])

        beyond_color = "#9A80AA"

        parts: list[str] = []

        if summary_zh:
            escaped = summary_zh.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            parts.append(f'<span style="color:#C8BAA0;">{escaped}</span>')

        _sep = "；" if self._language == "zh" else " · "
        if reasons_for:
            text = ("▸ " + _sep.join(reasons_for)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            parts.append(f'<span style="color:#8BC34A;font-size:85%;"> {text}</span>')

        if reasons_against:
            text = _sep.join(reasons_against).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            color = beyond_color if is_beyond else "#FF8A65"
            italic = "font-style:italic;" if is_beyond else ""
            parts.append(f'<span style="color:{color};font-size:85%;{italic}"> {text}</span>')

        if parts:
            lbl = QLabel()
            lbl.setObjectName("cardBody")
            lbl.setWordWrap(True)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setText("".join(parts))
            lbl.setStyleSheet("padding-top:3px;")
            font = lbl.font()
            font.setPixelSize(_fs(20))
            lbl.setFont(font)
            outer.addWidget(lbl)

    def _build_path_impact_row(self, path_impact: dict[str, str]) -> QWidget | None:
        """
        构建套路路径影响标签行。
        按 core→enabler→filler→pollution 顺序排列，各角色用颜色区分。
        """
        _ROLE_ORDER = {"core": 0, "enabler": 1, "filler": 2, "pollution": 3}
        _ROLE_STYLE = {
            "core":     ("✦", "#C8A96E", "rgba(60,45,20,0.6)"),
            "enabler":  ("●", "#6EB8C8", "rgba(20,45,60,0.6)"),
            "filler":   ("·", "#888888", "rgba(30,30,30,0.5)"),
            "pollution":("✗", "#C86E6E", "rgba(60,20,20,0.6)"),
        }

        sorted_items = sorted(
            path_impact.items(),
            key=lambda kv: _ROLE_ORDER.get(kv[1], 99)
        )

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        added = 0
        for arch_id, role in sorted_items:
            style = _ROLE_STYLE.get(role)
            if not style:
                continue
            prefix, fg, bg = style
            names = self._archetype_name_map.get(arch_id, {})
            short_name = names.get(self._language) or names.get("en") or arch_id
            max_len = 6 if self._language == "zh" else 14
            if len(short_name) > max_len:
                short_name = short_name[:max_len]

            tag = QLabel(f"{prefix} {short_name}")
            tag.setStyleSheet(
                f"color:{fg};background:{bg};border:1px solid {fg}66;"
                f"border-radius:4px;padding:2px 7px;font-size:{_fs(13)}px;font-weight:bold;"
            )
            layout.addWidget(tag)
            added += 1

        layout.addStretch()

        if added == 0:
            return None
        return container


# ---------------------------------------------------------------------------
# 字体缩放辅助函数
# ---------------------------------------------------------------------------

def _get_ui_scale() -> float:
    """
    读取用户配置的字体缩放，并叠加系统 DPI 缩放。
    - 配置值是用户在 100% DPI 下的期望比例（默认 1.2）
    - devicePixelRatio > 1 时（高 DPI 屏）额外乘以该比例，避免文字过小
    """
    try:
        from scripts.config_manager import get_font_scale
        user_scale = get_font_scale()
    except Exception:
        user_scale = 1.2  # 默认 120%
    try:
        app = QApplication.instance()
        if app:
            dpr = app.primaryScreen().devicePixelRatio()
            # 只在逻辑 DPI 真正放大时才叠加（Qt 在高 DPI 屏通常已自动处理，
            # 这里仅对 dpr > 1.25 的情况做轻微补偿，避免双重放大）
            if dpr > 1.25:
                user_scale = user_scale * min(dpr / 1.25, 1.3)
    except Exception:
        pass
    return user_scale


def _scale_px(px: int, scale: float) -> int:
    return max(9, round(px * scale))


def _fs(px: int) -> int:
    """快捷方式：将基础 px 按当前 UI 缩放比例换算（用于动态 setStyleSheet）"""
    return _scale_px(px, _get_ui_scale())


def _build_scaled_stylesheet(base_qss: str, scale: float) -> str:
    """将 QSS 中所有 font-size: Npx 乘以 scale 并取整（最小 9px）"""
    def replacer(m: re.Match) -> str:
        orig = int(m.group(1))
        return f"font-size: {_scale_px(orig, scale)}px"
    return re.sub(r'font-size:\s*(\d+)px', replacer, base_qss)


def _build_chip_style(bg_normal: str, border_normal: str, color_normal: str,
                      bg_selected: str, border_selected: str, color_selected: str,
                      scale: float) -> dict:
    """动态生成 chip 的内联样式（字体随缩放）"""
    fs = _scale_px(13, scale)
    tpl = (
        "background:{bg};border:1px solid {bd};border-radius:3px;"
        "color:{col};font-size:{fs}px;padding:3px 6px;text-align:left;"
    )
    return {
        "normal":   tpl.format(bg=bg_normal,   bd=border_normal,   col=color_normal,   fs=fs),
        "selected": tpl.format(bg=bg_selected, bd=border_selected, col=color_selected, fs=fs),
    }


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class CardAdviserWindow(QWidget):
    """
    永远置顶的浮窗主窗口。
    支持鼠标拖拽移动（无边框模式）。
    """

    _toggle_visibility_sig = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._drag_pos: QPoint | None = None
        self._dragging_from_title: bool = False
        self._worker: EvaluateWorker | None = None
        self._game_watcher: GameStateWatcher | None = None
        self._run_state = {}
        self._current_character: str = ""
        self._card_picker: CardPickerPanel | None = None
        self._selection_tray: SelectionTrayWidget | None = None
        self._cards_fetch_worker: CardsFetchWorker | None = None

        # 读取语言配置
        try:
            from scripts.config_manager import get_language
            self._language = get_language()
        except Exception:
            self._language = "en"

        self._hotkey_str: str = ""
        self._hotkey_active: bool = False

        self._init_window()
        self._build_ui()
        self._load_stylesheet()
        # 样式加载后让窗口根据内容自适应高度，宽度保持 _init_window 中设定的值
        QTimer.singleShot(0, self._auto_fit_height)

        # 启动时检查后端连通性
        QTimer.singleShot(500, self._check_backend)

        # 启动游戏状态监视
        QTimer.singleShot(1000, self._start_game_watcher)

        # 系统托盘 + 全局快捷键
        self._setup_tray_icon()
        self._toggle_visibility_sig.connect(self._toggle_visibility)
        self._setup_hotkey()

        # 读取并应用窗口透明度
        try:
            from scripts.config_manager import get_opacity
            self.setWindowOpacity(get_opacity())
        except Exception:
            pass

        # 启动时静默检查更新（延迟 3s，不影响启动速度）
        self._release_url: str = ""
        QTimer.singleShot(3000, self._check_for_updates)

    # ------------------------------------------------------------------
    # 窗口初始化
    # ------------------------------------------------------------------

    def _init_window(self) -> None:
        self.setWindowTitle("STS2 Card Adviser")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool   # 不在任务栏显示
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(460, 600)
        self.resize(600, 750)   # 初始高度由 _auto_fit_height 在布局完成后自动调整
        self._drawer_open = False   # 侧边抽屉初始收起

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # 最外层水平：主面板 | 拨片按钮 | 侧边抽屉
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 主面板 ──────────────────────────────────────────────────────
        main_panel = QWidget()
        main_panel.setObjectName("MainContainer")
        main_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        root.addWidget(main_panel, 0)   # stretch=0：主面板宽度不被抽屉挤压

        main_layout = QVBoxLayout(main_panel)
        main_layout.setContentsMargins(0, 0, 0, 8)
        main_layout.setSpacing(0)

        # ---- 标题栏 ----
        title_bar = self._build_title_bar()
        main_layout.addWidget(title_bar)

        # ---- 工具栏 ----
        toolbar = self._build_toolbar()
        main_layout.addWidget(toolbar)

        # ---- 分隔线 ----
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("Separator")
        main_layout.addWidget(sep)

        # ---- 评分区（含 OCR 识别预览 + 列表头 + 卡牌列表）----
        score_section = QWidget()
        score_section.setObjectName("ScoreSection")
        score_layout = QVBoxLayout(score_section)
        score_layout.setContentsMargins(0, 0, 0, 0)
        score_layout.setSpacing(0)

        self._ocr_preview_panel = self._build_ocr_preview_panel()
        score_layout.addWidget(self._ocr_preview_panel)

        header = self._build_list_header()
        score_layout.addWidget(header)

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_area.setObjectName("CardScrollArea")

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(4, 4, 4, 4)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()

        self._scroll_area.setWidget(self._list_container)
        score_layout.addWidget(self._scroll_area)

        main_layout.addWidget(score_section)

        # ---- 套路提示 + 状态栏 + 缩放手柄 ----
        bottom_bar = QWidget()
        bottom_bar.setObjectName("BottomBar")
        bottom_bar.setStyleSheet("background:rgba(22,18,14,0.85);border-top:1px solid #2E2416;")
        bottom_layout = QVBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(8, 4, 8, 2)
        bottom_layout.setSpacing(2)

        self._archetype_label = QLabel("")
        self._archetype_label.setObjectName("ArchetypeLabel")
        self._archetype_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._archetype_label.setWordWrap(True)
        self._archetype_label.setStyleSheet(
            f"color:#C8A96E;font-size:{_fs(18)}px;font-weight:bold;padding:2px 0px;"
        )
        self._archetype_label.setVisible(False)
        bottom_layout.addWidget(self._archetype_label)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(0)

        self._status_label = QLabel(_t("status_ready", self._language))
        self._status_label.setObjectName("StatusBar")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_row.addWidget(self._status_label, 1)

        grip_wrap = QWidget()
        grip_wrap.setFixedSize(32, 32)
        grip = QSizeGrip(grip_wrap)
        grip.setObjectName("ResizeGrip")
        grip.setFixedSize(32, 32)
        grip.move(0, 0)
        self._grip_sym_label = QLabel("⤡", grip_wrap)
        self._grip_sym_label.setStyleSheet(
            f"color: rgba(220,200,100,0.9); font-size: {_fs(16)}px; "
            "font-weight: bold; background: transparent;"
        )
        self._grip_sym_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._grip_sym_label.setFixedSize(32, 32)
        self._grip_sym_label.move(0, 0)
        self._grip_sym_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        status_row.addWidget(grip_wrap, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        bottom_layout.addLayout(status_row)

        main_layout.addWidget(bottom_bar)

        # ── 拨片按钮（主面板右侧，始终可见）───────────────────────────
        self._drawer_toggle_btn = QPushButton("◀")
        self._drawer_toggle_btn.setObjectName("DrawerToggleBtn")
        self._drawer_toggle_btn.setToolTip(_t("drawer_toggle_tip", self._language))
        self._drawer_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drawer_toggle_btn.clicked.connect(self._toggle_side_drawer)
        self._drawer_toggle_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self._drawer_toggle_btn)

        # ── 侧边抽屉（嵌入式，初始隐藏）────────────────────────────────
        self._side_drawer = self._build_side_drawer()
        self._side_drawer.setVisible(False)
        root.addWidget(self._side_drawer)

        # 展示占位数据
        self._show_placeholder()

    def _build_title_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TitleBar")
        bar.setMinimumHeight(38)
        # 鼠标事件转发给主窗口，实现标题栏拖拽
        bar.mousePressEvent = self._title_mouse_press
        bar.mouseMoveEvent = self._title_mouse_move
        bar.mouseReleaseEvent = self._title_mouse_release
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 0, 6, 0)

        self._title_label = QLabel(_t("title", self._language))
        self._title_label.setObjectName("TitleLabel")
        layout.addWidget(self._title_label)
        layout.addStretch()

        # 更新提示按钮（默认隐藏，检测到新版本时显示）
        self._update_btn = QPushButton("🆕")
        self._update_btn.setObjectName("UpdateButton")
        self._update_btn.setFixedSize(26, 26)
        self._update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_btn.setVisible(False)
        self._update_btn.clicked.connect(self._open_release_page)
        layout.addWidget(self._update_btn)

        minimize_btn = QPushButton("−")
        minimize_btn.setObjectName("MinimizeButton")
        minimize_btn.setFixedSize(26, 26)
        minimize_btn.setToolTip(_t("minimize_tip", self._language))
        minimize_btn.clicked.connect(self.hide)
        layout.addWidget(minimize_btn)

        close_btn = QPushButton("×")
        close_btn.setObjectName("CloseButton")
        close_btn.setFixedSize(26, 26)
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

        return bar

    def _build_toolbar(self) -> QWidget:
        toolbar = QWidget()
        toolbar.setObjectName("Toolbar")
        toolbar.setMinimumHeight(100)
        main_layout = QVBoxLayout(toolbar)
        main_layout.setContentsMargins(8, 4, 8, 4)
        main_layout.setSpacing(4)

        # ===== 第一行：按钮 =====
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(4)

        # 刷新检测按钮
        self._detect_btn = QPushButton(_t("btn_detect", self._language))
        self._detect_btn.setObjectName("RefreshDetectButton")
        self._detect_btn.setToolTip(_t("btn_detect_tip", self._language))
        self._detect_btn.clicked.connect(self._on_refresh_detect)
        btn_layout.addWidget(self._detect_btn)

        # 手动截图识别按钮（单次触发，与后台自动轮询独立）
        self._ocr_btn = QPushButton(_t("btn_ocr", self._language))
        self._ocr_btn.setObjectName("OcrButton")
        self._ocr_btn.setToolTip(_t("btn_ocr_tip", self._language))
        self._ocr_btn.clicked.connect(self._on_ocr_snapshot)
        btn_layout.addWidget(self._ocr_btn)

        # 设置按钮
        self._settings_btn = QPushButton(_t("btn_settings", self._language))
        self._settings_btn.setObjectName("SettingsButton")
        self._settings_btn.setToolTip(_t("btn_settings_tip", self._language))
        self._settings_btn.clicked.connect(self._on_settings)
        btn_layout.addWidget(self._settings_btn)

        # 关于按钮（弹出菜单）
        self._about_btn = QPushButton(_t("btn_about", self._language))
        self._about_btn.setObjectName("AboutButton")
        self._about_btn.setToolTip(_t("btn_about_tip", self._language))
        self._about_btn.clicked.connect(self._show_about_menu)
        btn_layout.addWidget(self._about_btn)

        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)

        # ===== 第二行：游戏信息 =====
        info_layout = QHBoxLayout()
        info_layout.setSpacing(8)

        self._game_info_label = QLabel(
            f"<span style='color:#999'>{_t('game_waiting', self._language)}</span>"
        )
        self._game_info_label.setObjectName("GameInfoLabel")
        self._game_info_label.setStyleSheet(f"font-size: {_fs(14)}px;")
        self._game_info_label.setTextFormat(Qt.TextFormat.RichText)
        info_layout.addWidget(self._game_info_label)

        info_layout.addStretch()
        main_layout.addLayout(info_layout)

        # ===== 第三行：三个指示灯（分三列） =====
        indicators_layout = QHBoxLayout()
        indicators_layout.setSpacing(16)

        # 后端连接指示器
        backend_box = QVBoxLayout()
        backend_box.setSpacing(2)
        self._backend_indicator = QLabel(_t("indicator_backend", self._language))
        self._backend_indicator.setObjectName("BackendIndicator")
        self._backend_indicator.setStyleSheet("color: #aaa; font-weight: bold;")
        backend_box.addWidget(self._backend_indicator)
        indicators_layout.addLayout(backend_box)

        # 游戏存档指示器
        game_box = QVBoxLayout()
        game_box.setSpacing(2)
        self._game_indicator = QLabel(_t("indicator_game", self._language))
        self._game_indicator.setObjectName("GameIndicator")
        self._game_indicator.setStyleSheet("color: #F44336; font-weight: bold;")
        game_box.addWidget(self._game_indicator)
        indicators_layout.addLayout(game_box)

        # 日志监视指示器
        log_box = QVBoxLayout()
        log_box.setSpacing(2)
        self._log_indicator = QLabel(_t("indicator_log", self._language))
        self._log_indicator.setObjectName("LogIndicator")
        self._log_indicator.setStyleSheet("color: #F44336; font-weight: bold;")
        log_box.addWidget(self._log_indicator)
        indicators_layout.addLayout(log_box)

        # 视觉自动轮询指示器（后台 VisionBridge 状态）
        ocr_box = QHBoxLayout()
        ocr_box.setSpacing(4)
        self._ocr_indicator = QLabel(_t("indicator_ocr", self._language))
        self._ocr_indicator.setObjectName("OcrIndicator")
        self._ocr_indicator.setStyleSheet("color: #aaa; font-weight: bold;")
        self._ocr_indicator.setToolTip(
            "后台自动视觉识别状态（每秒轮询截图）" if self._language == "zh"
            else "Vision OCR status (auto screenshot every second)"
        )
        ocr_box.addWidget(self._ocr_indicator)
        # 轮询状态小字（监视中 / 识别中 / 已锁定）
        self._ocr_state_badge = QLabel("监视中" if self._language == "zh" else "Watching")
        self._ocr_state_badge.setObjectName("OcrStateBadge")
        self._ocr_state_badge.setStyleSheet(
            f"color: #555; font-size: {_fs(10)}px; "
            "border: 1px solid #333; border-radius: 3px; padding: 0px 4px;"
        )
        ocr_box.addWidget(self._ocr_state_badge)
        indicators_layout.addLayout(ocr_box)

        indicators_layout.addStretch()
        main_layout.addLayout(indicators_layout)

        # ===== 第四行：OCR 界面识别提示 =====
        self._ocr_screen_label = QLabel("")
        self._ocr_screen_label.setObjectName("OcrScreenLabel")
        self._ocr_screen_label.setStyleSheet(
            f"color: #888; font-size: {_fs(14)}px; padding: 2px 0px;"
        )
        self._ocr_screen_label.setVisible(False)
        main_layout.addWidget(self._ocr_screen_label)

        return toolbar

    def _run_debug(self) -> None:
        log.info("调试按钮被点击，开始执行调试逻辑...")
        self._status_label.setText("调试中，请查看日志...")

    def _open_log_directory(self) -> None:
        log_dir = str(get_app_root())
        subprocess.Popen(f'explorer "{log_dir}"')
        log.info("打开日志目录窗口。")

    def _build_ocr_preview_panel(self) -> QWidget:
        """
        OCR 视觉识别 + 评分提示合并面板。
        默认隐藏，检测到选卡界面时显示在评分列表上方。
        """
        panel = QWidget()
        panel.setObjectName("OcrPreviewPanel")
        panel.setVisible(False)
        panel.setStyleSheet(
            "background:rgba(15,22,35,0.85);border-bottom:1px solid #1E3A5A;"
        )

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # 标题行：图标 + "视觉识别" + 状态
        title_row = QHBoxLayout()
        lbl_title = QLabel(_t("ocr_title", self._language))
        lbl_title.setStyleSheet(f"color:#64B5F6;font-size:{_fs(12)}px;font-weight:bold;")
        title_row.addWidget(lbl_title)
        title_row.addStretch()

        self._ocr_preview_status = QLabel(_t("ocr_recognizing", self._language))
        self._ocr_preview_status.setStyleSheet(f"color:#888;font-size:{_fs(11)}px;")
        title_row.addWidget(self._ocr_preview_status)
        layout.addLayout(title_row)

        # 三张候选卡名（大字，作为识别结果展示）
        cards_row = QHBoxLayout()
        cards_row.setSpacing(6)
        self._ocr_preview_cards: list[QLabel] = []
        for i in range(3):
            card_lbl = QLabel(_t("ocr_card_placeholder", self._language, i=i + 1))
            card_lbl.setObjectName(f"OcrPreviewCard{i}")
            card_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card_lbl.setStyleSheet(
                f"color:#555;font-size:{_fs(13)}px;"
                "border:1px solid #1E3A5A;border-radius:4px;"
                "padding:4px 6px;background:#0d1520;"
            )
            card_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self._ocr_preview_cards.append(card_lbl)
            cards_row.addWidget(card_lbl)
        layout.addLayout(cards_row)

        # 提示文字（解释性）
        self._ocr_hint_label = QLabel(_t("ocr_hint_waiting", self._language))
        self._ocr_hint_label.setStyleSheet(f"color:#556672;font-size:{_fs(11)}px;padding-top:2px;")
        self._ocr_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._ocr_hint_label)

        return panel

    def _build_list_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("ListHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(10, 2, 10, 2)

        self._list_header_label = QLabel(_t("list_header", self._language))
        self._list_header_label.setObjectName("HeaderLabel")
        layout.addWidget(self._list_header_label)
        layout.addStretch()

        return header

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------

    def _check_backend(self) -> None:
        try:
            resp = requests.get(f"{BACKEND_URL}/", timeout=2)
            if resp.status_code == 200:
                self._set_backend_connected(True)
            else:
                self._set_backend_connected(False)
        except Exception:
            self._set_backend_connected(False)

    def _set_backend_connected(self, connected: bool) -> None:
        if connected:
            self._backend_indicator.setText(
                "● 后端已连接" if self._language == "zh" else "● Backend connected"
            )
            self._backend_indicator.setStyleSheet("color: #4CAF50;")
            self._load_archetype_names()
        else:
            self._backend_indicator.setText(
                "● 后端未连接" if self._language == "zh" else "● Backend offline"
            )
            self._backend_indicator.setStyleSheet("color: #F44336;")

    def _load_archetype_names(self) -> None:
        """从后端拉取套路名映射（id → name_zh），仅需调用一次"""
        global _ARCHETYPE_NAME_MAP
        if _ARCHETYPE_NAME_MAP:
            return
        try:
            resp = requests.get(f"{BACKEND_URL}/api/archetypes", timeout=4)
            if resp.status_code == 200:
                for arch in resp.json().get("archetypes", []):
                    arch_id = arch["id"]
                    zh_name = arch.get("name_zh") or arch.get("name") or arch_id
                    en_name = arch.get("name") or arch.get("name_zh") or arch_id
                    _ARCHETYPE_NAME_MAP[arch_id] = {"zh": zh_name, "en": en_name}
        except Exception:
            pass

    def _start_game_watcher(self) -> None:
        """启动游戏状态实时监视"""
        if self._game_watcher is not None:
            return

        self._game_watcher = GameStateWatcher(BACKEND_URL)
        self._game_watcher.game_state_updated.connect(self._on_game_state_update)
        self._game_watcher.connection_status.connect(self._on_connection_status)
        self._game_watcher.log_status_updated.connect(self._on_log_status_update)
        self._game_watcher.vision_state_updated.connect(self._on_vision_state_update)
        self._game_watcher.start()

        log.info("✓ 游戏状态监视已启动")

    def _on_game_state_update(self, state: dict) -> None:
        """处理游戏状态更新"""
        log.debug(f"游戏状态更新: {state}")

        # 更新本地状态（规范化前缀）
        raw_char = state.get("character", "silent")
        raw_deck = state.get("deck", [])
        raw_relics = state.get("relics", [])

        norm_char = raw_char.replace("CHARACTER.", "").lower() if isinstance(raw_char, str) else raw_char
        norm_deck = [c.replace("CARD.", "").lower() if isinstance(c, str) else c for c in raw_deck]
        norm_relics = [
            {"id": r.replace("RELIC.", "").lower(), "name": r.replace("RELIC.", ""), "tags": []}
            if isinstance(r, str) else r
            for r in raw_relics
        ]

        self._run_state.update({
            "character": norm_char,
            "floor": state.get("floor", 0),
            "hp": state.get("hp", 0),
            "max_hp": state.get("max_hp", 70),
            "gold": state.get("gold", 0),
            "ascension": state.get("ascension", 0),
            "deck": norm_deck,
            "relics": norm_relics,
            "mode": state.get("mode", "single"),
        })

        # 更新游戏存档指示器
        character = state.get("character", "").replace("CHARACTER.", "").upper()
        floor = state.get("floor", 0)

        if character and floor > 0:
            self._game_indicator.setText(_t("indicator_game", self._language))
            self._game_indicator.setStyleSheet("color: #4CAF50; font-weight: bold;")

            # 显示游戏信息
            hp = state.get("hp", 0)
            max_hp = state.get("max_hp", 70)
            gold = state.get("gold", 0)
            deck_size = len(state.get("deck", []))
            ascension = state.get("ascension", 0)

            # HP 颜色（低血量变红）
            hp_ratio = hp / max(max_hp, 1)
            if hp_ratio > 0.6:
                hp_color = "#4CAF50"   # 绿
            elif hp_ratio > 0.3:
                hp_color = "#FF9800"   # 橙
            else:
                hp_color = "#F44336"   # 红

            asc_text = f" <span style='color:#9C27B0'>A{ascension}</span>" if ascension > 0 else ""
            game_mode = state.get("mode", "single")
            _mode_fs = _fs(12)
            if game_mode == "coop":
                _coop_label = "协作" if self._language == "zh" else "Co-op"
                mode_text = f"  <span style='color:#26C6DA;font-size:{_mode_fs}px'>👥 {_coop_label}</span>"
            else:
                _solo_label = "单人" if self._language == "zh" else "Solo"
                mode_text = f"  <span style='color:#81C784;font-size:{_mode_fs}px'>🧍 {_solo_label}</span>"
            info_html = (
                f"<span style='color:#64B5F6;font-weight:bold'>{character}</span>"
                f"{asc_text}"
                f"  <span style='color:#aaa'>F{floor}</span>"
                f"  <span style='color:{hp_color}'>❤ {hp}/{max_hp}</span>"
                f"  <span style='color:#FFD54F'>💰 {gold}</span>"
                f"  <span style='color:#90CAF9'>🃏 {deck_size}</span>"
                f"{mode_text}"
            )
            self._game_info_label.setText(info_html)
            self._game_info_label.setStyleSheet(f"font-size: {_fs(14)}px;")
        else:
            self._game_indicator.setText(_t("indicator_game", self._language))
            self._game_indicator.setStyleSheet("color: #F44336; font-weight: bold;")
            self._game_info_label.setText(
                f"<span style='color:#999'>{_t('game_not_detected', self._language)}</span>"
            )
            self._game_info_label.setStyleSheet(f"font-size: {_fs(14)}px;")

        # 更新底部状态栏
        if state.get("hand"):
            n = len(state.get("hand", []))
            self._status_label.setText(
                f"手牌: {n} 张" if self._language == "zh" else f"Hand: {n} cards"
            )
        elif character and floor > 0:
            self._status_label.setText(
                "就绪 — 选择候选卡后点击评估" if self._language == "zh"
                else "Ready — select candidates and evaluate"
            )

        # 检测到角色时加载对应卡牌
        if norm_char and norm_char != self._current_character:
            self._fetch_cards_for_character(norm_char)

    def _on_connection_status(self, status: str, connected: bool) -> None:
        """处理 WebSocket 连接状态变化"""
        if connected:
            log.info(f"游戏监视: {status}")
        else:
            log.warning(f"游戏监视: {status}")

    def _on_log_status_update(self, status: dict) -> None:
        """处理日志监视状态更新"""
        active = status.get("active", False)
        if active:
            self._log_indicator.setText(_t("indicator_log", self._language))
            self._log_indicator.setStyleSheet("color: #4CAF50;")
            log.info(f"日志正在监视: {status.get('path')}")
        else:
            self._log_indicator.setText(_t("indicator_log", self._language))
            self._log_indicator.setStyleSheet("color: #F44336;")
            log.warning("日志未被监视")

    def _on_vision_state_update(self, data: dict) -> None:
        """处理 OCR 视觉识别结果"""
        screen_type = data.get("screen_type", "unknown")
        all_reliable = data.get("all_reliable", False)
        card_names = data.get("card_names", [])
        card_choices = data.get("card_choices", [])
        confidences = data.get("confidences", [])

        # 更新视觉轮询指示灯 + badge
        if screen_type == "card_reward":
            if all_reliable:
                self._ocr_indicator.setText(_t("indicator_ocr", self._language))
                self._ocr_indicator.setStyleSheet("color: #4CAF50; font-weight: bold;")
                self._ocr_indicator.setToolTip(
                    "后台视觉识别：已锁定选卡界面（自动轮询）" if self._language == "zh"
                    else "Vision OCR: locked on card reward screen (auto-polling)"
                )
                self._ocr_state_badge.setText(_t("ocr_locked", self._language))
                self._ocr_state_badge.setStyleSheet(
                    f"color: #4CAF50; font-size: {_fs(10)}px; "
                    "border: 1px solid #2E7D32; border-radius: 3px; padding: 0px 4px;"
                )
            else:
                self._ocr_indicator.setText(_t("indicator_ocr", self._language))
                self._ocr_indicator.setStyleSheet("color: #FF9800; font-weight: bold;")
                self._ocr_indicator.setToolTip(
                    "后台视觉识别：识别中（等待多帧稳定）" if self._language == "zh"
                    else "Vision OCR: recognizing (waiting for multi-frame stability)"
                )
                self._ocr_state_badge.setText(_t("ocr_recognizing", self._language))
                self._ocr_state_badge.setStyleSheet(
                    f"color: #FF9800; font-size: {_fs(10)}px; "
                    "border: 1px solid #E65100; border-radius: 3px; padding: 0px 4px;"
                )
        elif screen_type == "shop":
            self._ocr_indicator.setText(_t("indicator_ocr", self._language))
            self._ocr_indicator.setStyleSheet("color: #64B5F6; font-weight: bold;")
            self._ocr_indicator.setToolTip(
                "后台视觉识别：检测到商店界面" if self._language == "zh"
                else "Vision OCR: shop screen detected"
            )
            self._ocr_state_badge.setText("商店" if self._language == "zh" else "Shop")
            self._ocr_state_badge.setStyleSheet(
                f"color: #64B5F6; font-size: {_fs(10)}px; "
                "border: 1px solid #1565C0; border-radius: 3px; padding: 0px 4px;"
            )
        else:
            self._ocr_indicator.setText(_t("indicator_ocr", self._language))
            self._ocr_indicator.setStyleSheet("color: #aaa; font-weight: bold;")
            self._ocr_indicator.setToolTip(
                "后台视觉识别：监视中（每秒自动截图）" if self._language == "zh"
                else "Vision OCR: watching (auto screenshot every second)"
            )
            self._ocr_state_badge.setText("监视中" if self._language == "zh" else "Watching")
            self._ocr_state_badge.setStyleSheet(
                f"color: #555; font-size: {_fs(10)}px; "
                "border: 1px solid #333; border-radius: 3px; padding: 0px 4px;"
            )

        # 更新 OCR 界面提示文字
        if self._language == "zh":
            _SCREEN_ICONS = {
                "card_reward": "🃏 选卡界面",
                "shop":        "🛒 商店界面",
                "other":       "🗺 其他界面",
                "unknown":     "",
            }
        else:
            _SCREEN_ICONS = {
                "card_reward": "🃏 Card Reward",
                "shop":        "🛒 Shop",
                "other":       "🗺 Other Screen",
                "unknown":     "",
            }
        screen_label = _SCREEN_ICONS.get(screen_type, "")

        if screen_type == "card_reward" and card_names:
            # 显示识别到的卡名（置信度颜色区分）
            parts = []
            for name, conf in zip(card_names, confidences):
                if not name:
                    parts.append("<span style='color:#666'>?</span>")
                elif conf >= 0.8:
                    parts.append(f"<span style='color:#A8D870'>{name}</span>")
                elif conf >= 0.55:
                    parts.append(f"<span style='color:#FFD54F'>{name}</span>")
                else:
                    parts.append(f"<span style='color:#FF7043'>{name}?</span>")
            cards_html = "  /  ".join(parts)
            self._ocr_screen_label.setText(
                f"<span style='color:#ccc'>{screen_label}</span>  {cards_html}"
            )
            self._ocr_screen_label.setTextFormat(Qt.TextFormat.RichText)
            self._ocr_screen_label.setVisible(True)
        elif screen_label:
            self._ocr_screen_label.setText(
                f"<span style='color:#888'>{screen_label}</span>"
            )
            self._ocr_screen_label.setTextFormat(Qt.TextFormat.RichText)
            self._ocr_screen_label.setVisible(True)
        else:
            self._ocr_screen_label.setVisible(False)

        # 更新 OCR 预览面板
        self._update_ocr_preview_panel(screen_type, card_names, confidences, all_reliable)

        # 选卡界面且识别稳定 → 自动填入候选卡并触发评估
        if screen_type == "card_reward" and all_reliable and card_choices:
            self._auto_fill_vision_cards(card_choices)

    def _update_ocr_preview_panel(
        self,
        screen_type: str,
        card_names: list,
        confidences: list,
        all_reliable: bool,
    ) -> None:
        """更新 OCR 预览面板的显示内容"""
        if screen_type != "card_reward":
            self._ocr_preview_panel.setVisible(False)
            return

        self._ocr_preview_panel.setVisible(True)

        # 状态文字
        _ocr_fs = _fs(11)
        if all_reliable:
            self._ocr_preview_status.setText(_t("ocr_locked", self._language))
            self._ocr_preview_status.setStyleSheet(f"color:#4CAF50;font-size:{_ocr_fs}px;")
            self._ocr_hint_label.setText(_t("ocr_hint_stable", self._language))
            self._ocr_hint_label.setStyleSheet(f"color:#4A7A40;font-size:{_ocr_fs}px;padding-top:2px;")
        else:
            self._ocr_preview_status.setText(_t("ocr_recognizing", self._language))
            self._ocr_preview_status.setStyleSheet(f"color:#FF9800;font-size:{_ocr_fs}px;")
            self._ocr_hint_label.setText(_t("ocr_hint_waiting", self._language))
            self._ocr_hint_label.setStyleSheet(f"color:#7A6030;font-size:{_ocr_fs}px;padding-top:2px;")

        # 三张卡名标签
        for i, lbl in enumerate(self._ocr_preview_cards):
            name = card_names[i] if i < len(card_names) else ""
            conf = confidences[i] if i < len(confidences) else 0.0
            if not name:
                lbl.setText(_t("ocr_card_placeholder", self._language, i=i + 1))
                lbl.setStyleSheet(
                    f"color: #555; font-size: {_ocr_fs}px; "
                    "border: 1px solid #333; border-radius: 4px; "
                    "padding: 2px 6px; background: #1a1a1a;"
                )
            elif conf >= 0.8:
                lbl.setText(name)
                lbl.setStyleSheet(
                    f"color: #A8D870; font-size: {_ocr_fs}px; font-weight: bold; "
                    "border: 1px solid #4CAF50; border-radius: 4px; "
                    "padding: 2px 6px; background: #0d1a0d;"
                )
            elif conf >= 0.55:
                lbl.setText(name)
                lbl.setStyleSheet(
                    f"color: #FFD54F; font-size: {_ocr_fs}px; "
                    "border: 1px solid #FF9800; border-radius: 4px; "
                    "padding: 2px 6px; background: #1a1200;"
                )
            else:
                lbl.setText(f"{name}?")
                lbl.setStyleSheet(
                    f"color: #FF7043; font-size: {_ocr_fs}px; "
                    "border: 1px solid #BF360C; border-radius: 4px; "
                    "padding: 2px 6px; background: #1a0800;"
                )

    def _on_ocr_snapshot(self) -> None:
        """手动触发一次截图识别（独立于后台自动轮询）"""
        self._ocr_btn.setEnabled(False)
        self._ocr_btn.setText(
            "📷 识别中..." if self._language == "zh" else "📷 Capturing..."
        )
        self._status_label.setText(
            "手动截图识别中..." if self._language == "zh" else "Manual OCR capture in progress..."
        )

        # 在后台线程执行，避免冻结 UI
        worker = _OcrSnapshotWorker(BACKEND_URL)
        worker.result_ready.connect(self._on_ocr_snapshot_result)
        def _restore_btn():
            self._ocr_btn.setEnabled(True)
            self._ocr_btn.setText(_t("btn_ocr", self._language))
        worker.finished.connect(_restore_btn)
        worker.start()
        # 保持引用避免被 GC
        self._ocr_snapshot_worker = worker

    def _on_ocr_snapshot_result(self, data: dict) -> None:
        """处理手动 OCR 截图的结果"""
        self._on_vision_state_update(data)
        screen_type = data.get("screen_type", "unknown")
        _ocr_done = {
            "zh": {
                "card_reward": "OCR 识别完成：选卡界面",
                "shop":        "OCR 识别完成：商店界面",
                "other":       "OCR 识别完成：其他界面",
                "unknown":     "OCR 识别完成：未识别到特定界面",
            },
            "en": {
                "card_reward": "OCR complete: card reward screen",
                "shop":        "OCR complete: shop screen",
                "other":       "OCR complete: other screen",
                "unknown":     "OCR complete: no specific screen detected",
            },
        }
        self._status_label.setText(
            _ocr_done.get(self._language, _ocr_done["zh"]).get(
                screen_type, _ocr_done.get(self._language, _ocr_done["zh"])["unknown"]
            )
        )

    def _auto_fill_vision_cards(self, card_ids: list) -> None:
        """OCR 识别稳定后，自动填入候选卡到当前 run_state 并触发评估"""
        if not card_ids:
            return
        normalized = [cid.lower() for cid in card_ids]
        # 构造虚拟 card dict 列表（只需 id 字段供评估器使用）
        fake_cards = [{"id": cid} for cid in normalized]
        self._status_label.setText(
            f"OCR 自动识别到 {len(normalized)} 张候选卡，正在评估..."
            if self._language == "zh"
            else f"OCR detected {len(normalized)} candidate cards — evaluating..."
        )
        log.info(f"OCR 自动填入候选卡: {normalized}")
        self._on_evaluate_from_picker(fake_cards)

    def _on_refresh_detect(self) -> None:
        """刷新检测：重新初始化游戏和日志检测"""
        try:
            self._status_label.setText(
                "正在重新检测..." if self._language == "zh" else "Re-detecting..."
            )

            # 通过调用配置端点来触发后端重新初始化 GameWatcher
            resp = requests.post(
                f"{BACKEND_URL}/api/config",
                json={},  # 空配置会触发重新检测
                timeout=5,
            )

            if resp.status_code == 200:
                log.info("✓ 已触发重新检测")
                self._status_label.setText(
                    "检测中... 稍候" if self._language == "zh" else "Detecting... please wait"
                )
                done_text = "检测完成" if self._language == "zh" else "Detection complete"
                QTimer.singleShot(1000, lambda: self._status_label.setText(done_text))
            else:
                self._status_label.setText(
                    f"检测失败: {resp.status_code}" if self._language == "zh"
                    else f"Detection failed: {resp.status_code}"
                )

        except Exception as e:
            log.error(f"重新检测失败: {e}")
            prefix = "错误: " if self._language == "zh" else "Error: "
            self._status_label.setText(f"{prefix}{e}")

    def _on_settings(self) -> None:
        """打开设置对话框"""
        dialog = PathSettingsDialog(self, BACKEND_URL)
        dialog.exec()
        # 对话框关闭后：若用户点了保存，重新注册快捷键（hotkey 没有实时预览）
        saved_hotkey = getattr(dialog, '_saved_hotkey', '')
        if saved_hotkey:
            self._reload_hotkey()
        # 确保最终状态与 config 一致（兜底，实时预览可能已应用）
        self._load_stylesheet()
        try:
            from scripts.config_manager import get_opacity, get_language
            self.setWindowOpacity(get_opacity())
            final_lang = get_language()
        except Exception:
            final_lang = self._language
        if final_lang != self._language:
            self._language = final_lang
            self._reload_ui_language()

    def _reload_ui_language(self) -> None:
        """语言切换后立即更新所有静态标签文字（无需重启）"""
        lang = self._language
        # 标题栏
        self._title_label.setText(_t("title", lang))
        # 工具栏
        self._detect_btn.setText(_t("btn_detect", lang))
        self._detect_btn.setToolTip(_t("btn_detect_tip", lang))
        self._ocr_btn.setText(_t("btn_ocr", lang))
        self._ocr_btn.setToolTip(_t("btn_ocr_tip", lang))
        self._settings_btn.setText(_t("btn_settings", lang))
        self._settings_btn.setToolTip(_t("btn_settings_tip", lang))
        self._about_btn.setToolTip(_t("btn_about_tip", lang))
        # 指示器
        self._backend_indicator.setText(_t("indicator_backend", lang))
        self._game_indicator.setText(_t("indicator_game", lang))
        self._log_indicator.setText(_t("indicator_log", lang))
        self._ocr_indicator.setText(_t("indicator_ocr", lang))
        self._ocr_indicator.setToolTip(
            "后台自动视觉识别状态（每秒轮询截图）" if lang == "zh"
            else "Vision OCR status (auto screenshot every second)"
        )
        # OCR badge 初始状态
        self._ocr_state_badge.setText("监视中" if lang == "zh" else "Watching")
        # 状态/游戏信息
        self._status_label.setText(_t("status_ready", lang))
        self._game_info_label.setText(f"<span style='color:#999'>{_t('game_waiting', lang)}</span>")
        # OCR 预览面板内的文字（若面板不可见则更新占位符）
        if not self._ocr_preview_panel.isVisible():
            self._ocr_preview_status.setText(_t("ocr_recognizing", lang))
            self._ocr_hint_label.setText(_t("ocr_hint_waiting", lang))
            for i, lbl in enumerate(self._ocr_preview_cards):
                lbl.setText(_t("ocr_card_placeholder", lang, i=i + 1))
        # 列表头 + 侧边抽屉按钮
        self._list_header_label.setText(_t("list_header", lang))
        self._drawer_toggle_btn.setToolTip(_t("drawer_toggle_tip", lang))
        # 侧边抽屉
        self._drawer_title_label.setText(_t("drawer_title", lang))
        self._search_box.setPlaceholderText(_t("search_placeholder", lang))
        # 托盘标签
        self._selection_tray.set_language(lang)
        # 卡牌选择器语言 + 重新加载
        self._card_picker.set_language(lang)
        char = self._current_character
        self._current_character = ""
        if char:
            self._fetch_cards_for_character(char)

    # ------------------------------------------------------------------
    # 卡牌选择器
    # ------------------------------------------------------------------

    def _build_side_drawer(self) -> QWidget:
        """嵌入式侧边手动选牌抽屉"""
        drawer = QWidget()
        drawer.setObjectName("SideDrawerPanel")
        drawer.setMinimumWidth(340)

        layout = QVBoxLayout(drawer)
        layout.setContentsMargins(6, 8, 6, 8)
        layout.setSpacing(4)

        self._drawer_title_label = QLabel(_t("drawer_title", self._language))
        self._drawer_title_label.setObjectName("DrawerTitle")
        layout.addWidget(self._drawer_title_label)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText(_t("search_placeholder", self._language))
        self._search_box.setObjectName("CardSearchBox")
        layout.addWidget(self._search_box)

        self._card_picker = CardPickerPanel()
        self._card_picker.selection_changed.connect(self._on_card_selection_changed)
        self._search_box.textChanged.connect(self._card_picker.filter_cards)
        layout.addWidget(self._card_picker, 1)

        self._selection_tray = SelectionTrayWidget()
        self._selection_tray.evaluate_requested.connect(self._on_evaluate_from_picker)
        self._selection_tray.deselect_requested.connect(self._card_picker.deselect_by_id)
        layout.addWidget(self._selection_tray, 0)

        return drawer

    def _auto_fit_height(self) -> None:
        """根据内容自适应窗口高度（宽度保持不变）"""
        hint = self.sizeHint()
        new_h = max(hint.height(), self.minimumHeight())
        self.resize(self.width(), new_h)

    def _toggle_side_drawer(self) -> None:
        self._drawer_open = not self._drawer_open
        self._side_drawer.setVisible(self._drawer_open)
        self._drawer_toggle_btn.setText("▶" if self._drawer_open else "◀")
        drawer_w = self._side_drawer.minimumWidth()
        delta = drawer_w if self._drawer_open else -drawer_w
        self.resize(self.width() + delta, self.height())

    def _fetch_cards_for_character(self, character: str) -> None:
        if not character:
            return
        self._current_character = character

        if self._cards_fetch_worker and self._cards_fetch_worker.isRunning():
            self._cards_fetch_worker.terminate()

        self._card_picker.set_language(self._language)
        self._selection_tray.set_language(self._language)
        self._card_picker.clear_cards()
        self._selection_tray.update_selection([])
        self._status_label.setText(_t("status_loading", self._language, char=character.upper()))

        self._cards_fetch_worker = CardsFetchWorker(character)
        self._cards_fetch_worker.cards_ready.connect(self._on_cards_fetched)
        self._cards_fetch_worker.error_occurred.connect(
            lambda e: self._status_label.setText(_t("status_load_fail", self._language, e=e))
        )
        self._cards_fetch_worker.start()

    def _on_cards_fetched(self, cards: list[dict]) -> None:
        playable_types = {"attack", "skill", "power"}
        playable = [
            c for c in cards
            if c.get("card_type", "").lower() in playable_types
            or c.get("rarity", "").lower() == "ancient"
        ]
        self._card_picker.populate(playable)
        self._status_label.setText(_t("status_loaded", self._language, n=len(playable)))

    def _on_card_selection_changed(self, selected_cards: list[dict], display_names: list[str]) -> None:
        self._selection_tray.update_selection(selected_cards, display_names)

    def _on_evaluate_from_picker(self, selected_cards: list[dict]) -> None:
        if not selected_cards:
            self._status_label.setText(
                "请先选择候选卡" if self._language == "zh" else "Select candidate cards first"
            )
            return

        card_ids = [c["id"].lower() for c in selected_cards]
        run_state = self._run_state.copy() if self._run_state else {}
        run_state.setdefault("character", "silent")
        run_state.setdefault("floor", 1)
        run_state.setdefault("hp", 70)
        run_state.setdefault("max_hp", 70)
        run_state.setdefault("gold", 0)
        run_state.setdefault("ascension", 0)
        run_state.setdefault("deck", [])
        run_state.setdefault("relics", [])
        run_state["card_choices"] = card_ids

        # 规范化前缀
        run_state["character"] = run_state["character"].replace("CHARACTER.", "").lower()
        run_state["deck"] = [
            c.replace("CARD.", "").lower() if isinstance(c, str) else c
            for c in run_state["deck"]
        ]
        run_state["relics"] = [
            {"id": r.replace("RELIC.", "").lower(), "name": r.replace("RELIC.", ""), "tags": []}
            if isinstance(r, str) else r
            for r in run_state["relics"]
        ]

        self._status_label.setText(_t("status_evaluating", self._language))
        self._selection_tray._evaluate_btn.setEnabled(False)

        self._worker = EvaluateWorker(run_state, language=self._language)
        self._worker.result_ready.connect(self._on_result)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.finished.connect(
            lambda: self._selection_tray._evaluate_btn.setEnabled(True)
        )
        self._worker.start()

    def _on_refresh(self) -> None:
        """
        触发评估请求。
        使用 GameWatcher 推送的实时游戏状态。
        """
        # 确保有完整的 run_state 数据
        run_state = getattr(self, '_run_state', {
            "character": "silent",
            "floor": 8,
            "hp": 55,
            "max_hp": 70,
            "gold": 99,
            "deck": ["blade_dance", "cloak_and_dagger"],
            "relics": [],
            "card_choices": ["catalyst", "ninjutsu", "reflex"],
        })

        # 确保必需字段存在
        if "character" not in run_state:
            run_state["character"] = "silent"
        if "floor" not in run_state:
            run_state["floor"] = 1
        if "hp" not in run_state or run_state["hp"] == 0:
            run_state["hp"] = 70
        if "max_hp" not in run_state or run_state["max_hp"] == 0:
            run_state["max_hp"] = 70
        if "gold" not in run_state:
            run_state["gold"] = 0
        if "ascension" not in run_state:
            run_state["ascension"] = 0
        if "deck" not in run_state:
            run_state["deck"] = []
        if "card_choices" not in run_state or not run_state["card_choices"]:
            # 无法进行评估，因为没有选卡池
            self._status_label.setText(
                "等待选卡数据... (需要日志文件或游戏运行中)"
                if self._language == "zh"
                else "Waiting for card data... (requires log file or game running)"
            )
            return

        # 规范化字段（去除游戏前缀）
        run_state["character"] = run_state.get("character", "silent").replace("CHARACTER.", "").lower()
        run_state["deck"] = [c.replace("CARD.", "").lower() if isinstance(c, str) else c for c in run_state.get("deck", [])]
        raw_relics = run_state.get("relics", [])
        run_state["relics"] = [
            {"id": r.replace("RELIC.", "").lower(), "name": r.replace("RELIC.", ""), "tags": []}
            if isinstance(r, str) else r
            for r in raw_relics
        ]
        self._status_label.setText(_t("status_evaluating", self._language))

        self._worker = EvaluateWorker(run_state, language=self._language)
        self._worker.result_ready.connect(self._on_result)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def _on_result(self, data: dict) -> None:
        results = data.get("results", [])
        archetypes = data.get("detected_archetypes", [])
        self._render_results(results)

        if not results:
            self._status_label.setText(
                "❌ 未找到匹配的卡牌" if self._language == "zh" else "❌ No matching cards found"
            )
            self._archetype_label.setVisible(False)
        else:
            if archetypes:
                sep = "、" if self._language == "zh" else " / "
                arch_text = sep.join(archetypes)
                prefix = "⚔ 套路：" if self._language == "zh" else "⚔ Archetype: "
                self._archetype_label.setText(f"{prefix}{arch_text}")
                self._archetype_label.setVisible(True)
            else:
                self._archetype_label.setVisible(False)
            self._status_label.setText(
                "评估完成" if self._language == "zh" else "Evaluation complete"
            )

    def _on_error(self, message: str) -> None:
        prefix = "错误：" if self._language == "zh" else "Error: "
        self._status_label.setText(f"{prefix}{message}")

    def _render_results(self, results: list[dict]) -> None:
        """清空列表并重新渲染评估结果"""
        # 移除旧 widget（保留 stretch）
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for result in results:
            widget = CardResultWidget(result, language=self._language, archetype_name_map=_ARCHETYPE_NAME_MAP)
            self._list_layout.insertWidget(self._list_layout.count() - 1, widget)

    def _show_placeholder(self) -> None:
        """初始占位内容"""
        placeholder = QLabel(_t("placeholder", self._language))
        placeholder.setObjectName("Placeholder")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._list_layout.insertWidget(0, placeholder)

    # ------------------------------------------------------------------
    # 样式加载
    # ------------------------------------------------------------------

    def _load_stylesheet(self) -> None:
        qss_path = get_app_root() / "frontend" / "styles.qss"
        if qss_path.exists():
            with open(qss_path, encoding="utf-8") as f:
                base = f.read()
            self.setStyleSheet(_build_scaled_stylesheet(base, _get_ui_scale()))
        self._refresh_inline_styles()

    def _refresh_inline_styles(self) -> None:
        """重新应用所有绕过 QSS 的 inline setStyleSheet（字体缩放后需调用）"""
        # 底部状态栏缩放手柄符号
        if hasattr(self, '_grip_sym_label'):
            self._grip_sym_label.setStyleSheet(
                f"color: rgba(220,200,100,0.9); font-size: {_fs(16)}px; "
                "font-weight: bold; background: transparent;"
            )
        # 套路提示标签
        if hasattr(self, '_archetype_label'):
            self._archetype_label.setStyleSheet(
                f"color:#C8A96E;font-size:{_fs(18)}px;font-weight:bold;padding:2px 0px;"
            )
        # 游戏信息标签
        if hasattr(self, '_game_info_label'):
            self._game_info_label.setStyleSheet(f"font-size: {_fs(14)}px;")
        # OCR 监视状态 badge
        if hasattr(self, '_ocr_state_badge'):
            self._ocr_state_badge.setStyleSheet(
                f"color: #555; font-size: {_fs(10)}px; "
                "border: 1px solid #333; border-radius: 3px; padding: 0px 4px;"
            )
        # OCR 界面提示标签
        if hasattr(self, '_ocr_screen_label'):
            self._ocr_screen_label.setStyleSheet(
                f"color: #888; font-size: {_fs(14)}px; padding: 2px 0px;"
            )
        # OCR 预览面板内的标签
        if hasattr(self, '_ocr_preview_status'):
            self._ocr_preview_status.setStyleSheet(f"color:#888;font-size:{_fs(11)}px;")
        if hasattr(self, '_ocr_hint_label'):
            self._ocr_hint_label.setStyleSheet(
                f"color:#556672;font-size:{_fs(11)}px;padding-top:2px;"
            )
        if hasattr(self, '_ocr_preview_cards'):
            for card_lbl in self._ocr_preview_cards:
                card_lbl.setStyleSheet(
                    f"color:#555;font-size:{_fs(13)}px;"
                    "border:1px solid #1E3A5A;border-radius:4px;"
                    "padding:4px 6px;background:#0d1520;"
                )

    # ------------------------------------------------------------------
    # 系统托盘
    # ------------------------------------------------------------------

    def _setup_tray_icon(self) -> None:
        px = QPixmap(16, 16)
        px.fill(QColor(0, 0, 0, 0))
        p = QPainter(px)
        p.setPen(QColor("#C8A96E"))
        p.setBrush(QColor("#C8A96E"))
        p.drawRect(7, 2, 2, 10)
        p.drawRect(3, 10, 10, 2)
        p.end()

        self._tray = QSystemTrayIcon(QIcon(px), self)
        self._tray.setToolTip("STS2 Adviser")

        tray_menu = QMenu()
        show_act = QAction(_t("tray_show", self._language), self)
        show_act.triggered.connect(self._restore_from_tray)
        quit_act = QAction(_t("tray_quit", self._language), self)
        quit_act.triggered.connect(QApplication.instance().quit)
        tray_menu.addAction(show_act)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_act)

        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._restore_from_tray()

    def _restore_from_tray(self) -> None:
        self.show()
        self.activateWindow()
        self.raise_()

    # ------------------------------------------------------------------
    # 检查更新
    # ------------------------------------------------------------------

    def _check_for_updates(self) -> None:
        """启动后台更新检查线程（静默，仅有新版本时提示）"""
        self._update_checker = UpdateChecker(_GITHUB_REPO, VERSION)
        self._update_checker.update_found.connect(self._on_update_found)
        self._update_checker.up_to_date.connect(lambda v: log.debug(f"已是最新版本 {v}"))
        self._update_checker.check_failed.connect(lambda: log.debug("更新检查失败（网络）"))
        self._update_checker.start()

    def _on_update_found(self, latest_ver: str, url: str) -> None:
        """有新版本时在标题栏显示提示按钮"""
        self._release_url = url
        tip = _t("update_available", self._language, ver=latest_ver)
        self._update_btn.setToolTip(tip)
        self._update_btn.setVisible(True)
        # 同时在系统托盘发气泡通知
        if hasattr(self, '_tray'):
            self._tray.showMessage(
                "STS2 Adviser",
                _t("update_available", self._language, ver=latest_ver),
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
        log.info(f"发现新版本: {latest_ver}  {url}")

    def _open_release_page(self) -> None:
        """用系统浏览器打开 GitHub Releases 页面"""
        import webbrowser
        url = self._release_url or f"https://github.com/{_GITHUB_REPO}/releases"
        webbrowser.open(url)

    def _show_about_menu(self) -> None:
        """在「?」按钮下方弹出关于菜单"""
        import webbrowser
        menu = QMenu(self)
        github_act = QAction(_t("about_github", self._language), self)
        github_act.triggered.connect(
            lambda: webbrowser.open(f"https://github.com/{_GITHUB_REPO}")
        )
        steam_act = QAction(_t("about_steam", self._language), self)
        steam_act.triggered.connect(
            lambda: webbrowser.open(
                "https://steamcommunity.com/sharedfiles/filedetails/?id=3696131100"
            )
        )
        menu.addAction(github_act)
        menu.addAction(steam_act)
        # 在按钮正下方弹出
        pos = self._about_btn.mapToGlobal(
            self._about_btn.rect().bottomLeft()
        )
        menu.exec(pos)

    # ------------------------------------------------------------------
    # 全局快捷键
    # ------------------------------------------------------------------

    def _setup_hotkey(self) -> None:
        try:
            import keyboard
            from scripts.config_manager import get_hotkey
            self._hotkey_str = get_hotkey()
            keyboard.add_hotkey(self._hotkey_str, self._emit_toggle_visibility)
            self._hotkey_active = True
            log.info(f"全局快捷键已注册: {self._hotkey_str}")
        except Exception as e:
            log.warning(f"全局快捷键注册失败: {e}")
            self._hotkey_active = False

    def _reload_hotkey(self) -> None:
        if self._hotkey_active and self._hotkey_str:
            try:
                import keyboard
                keyboard.remove_hotkey(self._hotkey_str)
            except Exception:
                pass
        self._setup_hotkey()

    def _emit_toggle_visibility(self) -> None:
        """keyboard 回调在后台线程，通过信号转到 Qt 主线程"""
        self._toggle_visibility_sig.emit()

    def _toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self._restore_from_tray()

    # ------------------------------------------------------------------
    # 拖拽移动（无边框窗口）
    # ------------------------------------------------------------------

    def _title_mouse_press(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def _title_mouse_move(self, event) -> None:
        if self._drag_pos is not None and event.buttons() == Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()

    def _title_mouse_release(self, event) -> None:
        self._drag_pos = None


# ---------------------------------------------------------------------------
# 独立运行入口（调试用）
# ---------------------------------------------------------------------------

def run_ui() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    window = CardAdviserWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_ui()
