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
import websocket
import threading
import time

log = logging.getLogger(__name__)

from PyQt6.QtCore import (
    Qt, QPoint, QThread, pyqtSignal, QTimer,
)
from PyQt6.QtGui import QFont, QColor, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QFrame,
    QSizePolicy, QDialog, QLineEdit, QFileDialog, QGroupBox,
    QGridLayout, QComboBox, QSizeGrip,
)

_port = os.environ.get("STS2_BACKEND_PORT", "8000")
BACKEND_URL = f"http://127.0.0.1:{_port}"


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

    def __init__(self, run_state: dict) -> None:
        super().__init__()
        self.run_state = run_state

    def run(self) -> None:
        try:
            resp = requests.post(
                f"{BACKEND_URL}/api/evaluate",
                json={"run_state": self.run_state},
                timeout=5,
            )
            resp.raise_for_status()
            self.result_ready.emit(resp.json())
        except requests.exceptions.ConnectionError:
            self.error_occurred.emit("无法连接后端服务（请先启动 main.py）")
        except requests.exceptions.Timeout:
            self.error_occurred.emit("请求超时")
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class CardsFetchWorker(QThread):
    """在独立线程中拉取指定角色的卡牌列表"""
    cards_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, character: str) -> None:
        super().__init__()
        self.character = character

    def run(self) -> None:
        try:
            resp = requests.get(
                f"{BACKEND_URL}/api/cards",
                params={"character": self.character},
                timeout=5,
            )
            resp.raise_for_status()
            self.cards_ready.emit(resp.json().get("cards", []))
        except requests.exceptions.ConnectionError:
            self.error_occurred.emit("无法连接后端")
        except Exception as exc:
            self.error_occurred.emit(str(exc))


# ---------------------------------------------------------------------------
# 卡牌选择器组件
# ---------------------------------------------------------------------------

class CardChipButton(QPushButton):
    """单张卡牌的可切换按钮"""
    toggled_card = pyqtSignal(dict, bool)  # (card, is_selected)

    def __init__(self, card: dict, display_name: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._card = card
        self._selected = False
        self._display_name = display_name or card.get("name", "")

        cost = card.get("cost", 0)
        cost_str = "X" if cost == -1 else str(cost)
        self.setText(f"[{cost_str}] {self._display_name}")
        self.setObjectName("CardChip")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.clicked.connect(self._on_click)

    @property
    def card(self) -> dict:
        return self._card

    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, value: bool, emit: bool = True) -> None:
        self._selected = value
        self.setObjectName("CardChipSelected" if value else "CardChip")
        self.style().unpolish(self)
        self.style().polish(self)
        if emit:
            self.toggled_card.emit(self._card, value)

    def _on_click(self) -> None:
        self.set_selected(not self._selected)


class CardPickerPanel(QScrollArea):
    """按费用+类型分组显示的卡牌选择面板"""
    selection_changed = pyqtSignal(list, list)  # (已选卡列表, 显示名列表)

    _TYPE_ORDER = ["attack", "skill", "power"]
    _TYPE_LABELS_ZH = {"attack": "攻击", "skill": "技能", "power": "能力"}
    _TYPE_LABELS_EN = {"attack": "Attack", "skill": "Skill", "power": "Power"}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CardPickerScroll")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(2)
        self._layout.addStretch()
        self.setWidget(self._content)

        self._chips: list[CardChipButton] = []
        self._language: str = "en"

    def set_language(self, lang: str) -> None:
        self._language = lang

    def populate(self, cards: list[dict]) -> None:
        """按类型分组、按费用排序填充卡牌"""
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

        # 按类型分组
        groups: dict[str, list[dict]] = {t: [] for t in self._TYPE_ORDER}
        for card in cards:
            ct = card.get("card_type", "").lower()
            if ct in groups:
                groups[ct].append(card)

        # 每组按费用排序（X费排最后）
        def cost_key(c):
            cost = c.get("cost", 0)
            return 99 if cost == -1 else cost

        cols = 4
        for type_key in self._TYPE_ORDER:
            group = sorted(groups[type_key], key=cost_key)
            if not group:
                continue

            # 章节标题
            header = QLabel(type_labels.get(type_key, type_key))
            header.setObjectName("CardPickerSectionHeader")
            self._layout.insertWidget(self._layout.count() - 1, header)

            # 卡片网格
            grid = QGridLayout()
            grid.setSpacing(3)
            for i, card in enumerate(group):
                card_id = card.get("id", "")
                display = locale_map.get(card_id) or card.get("name", "") if self._language == "zh" else card.get("name", "")
                chip = CardChipButton(card, display_name=display)
                chip.toggled_card.connect(self._on_chip_toggled)
                self._chips.append(chip)
                grid.addWidget(chip, i // cols, i % cols)

            grid_container = QWidget()
            grid_container.setLayout(grid)
            self._layout.insertWidget(self._layout.count() - 1, grid_container)

    def clear_cards(self) -> None:
        """清除所有卡片和标题"""
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._chips.clear()

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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SelectionTray")
        self._selected_cards: list[dict] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        self._prefix_label = QLabel("候选:")
        self._prefix_label.setStyleSheet("color: #888; font-size: 12pt;")
        layout.addWidget(self._prefix_label)

        # 动态卡名区域
        self._chips_widget = QWidget()
        self._chips_layout = QHBoxLayout(self._chips_widget)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(4)
        layout.addWidget(self._chips_widget, 1)

        self._count_label = QLabel("0/4")
        self._count_label.setStyleSheet("color: #666; font-size: 12pt;")
        layout.addWidget(self._count_label)

        self._evaluate_btn = QPushButton("⟳ 评估")
        self._evaluate_btn.setObjectName("EvaluateButton")
        self._evaluate_btn.setEnabled(False)
        self._evaluate_btn.clicked.connect(
            lambda: self.evaluate_requested.emit(self._selected_cards)
        )
        layout.addWidget(self._evaluate_btn)

    def update_selection(self, cards: list[dict], display_names: list[str] | None = None) -> None:
        self._selected_cards = cards

        # 清除旧的卡名标签
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        names = display_names if display_names else [c.get("name", "?") for c in cards]
        for name in names:
            if len(name) > 12:
                name = name[:11] + "…"
            lbl = QLabel(name)
            lbl.setObjectName("TrayCardLabel")
            lbl.setStyleSheet(
                "color: #A8D870; background: rgba(60,90,40,0.7); "
                "border: 1px solid #6A9A3E; border-radius: 3px; "
                "padding: 1px 5px; font-size: 12pt;"
            )
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

                # 发出信号更新UI
                self.log_status_updated.emit(log_status)

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
        self.setWindowTitle("路径设置")
        self.setModal(True)
        self.setMinimumWidth(500)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 帮助信息
        help_text = QLabel(
            "设置游戏文件所在的文件夹。系统会自动在文件夹内搜索对应的文件。\n"
            "• 存档文件夹：应包含 current_run.save 等存档文件\n"
            "• 日志文件夹：应包含 godot.log 等日志文件"
        )
        help_text.setStyleSheet("color: #666; font-size: 12pt; padding: 8px;")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        # ===== 语言设置 =====
        lang_layout = QHBoxLayout()
        lang_label = QLabel("🌐 卡牌显示语言:")
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
        lang_layout.addWidget(self._lang_combo)
        lang_layout.addStretch()
        layout.addLayout(lang_layout)

        # 分隔线
        separator1 = QFrame()
        separator1.setFrameShape(QFrame.Shape.HLine)
        separator1.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator1)

        # ===== 存档路径 =====
        save_header_layout = QHBoxLayout()
        save_label = QLabel("📂 存档文件夹:")
        save_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        save_header_layout.addWidget(save_label)

        self._save_indicator = QLabel("●")
        self._save_indicator.setStyleSheet("color: #aaa; font-size: 16pt;")
        save_header_layout.addWidget(self._save_indicator)
        save_header_layout.addStretch()
        layout.addLayout(save_header_layout)

        save_input_layout = QHBoxLayout()
        self._save_path_input = QLineEdit()
        self._save_path_input.setPlaceholderText("选择存档文件所在的文件夹...")
        self._save_path_input.setMinimumHeight(32)
        self._save_path_input.textChanged.connect(self._validate_save_path)
        save_browse_btn = QPushButton("浏览...")
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
        log_label = QLabel("📋 日志文件夹:")
        log_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        log_header_layout.addWidget(log_label)

        self._log_indicator = QLabel("●")
        self._log_indicator.setStyleSheet("color: #aaa; font-size: 16pt;")
        log_header_layout.addWidget(self._log_indicator)
        log_header_layout.addStretch()
        layout.addLayout(log_header_layout)

        log_input_layout = QHBoxLayout()
        self._log_path_input = QLineEdit()
        self._log_path_input.setPlaceholderText("选择日志文件所在的文件夹...")
        self._log_path_input.setMinimumHeight(32)
        self._log_path_input.textChanged.connect(self._validate_log_path)
        log_browse_btn = QPushButton("浏览...")
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

        save_btn = QPushButton("保存配置")
        save_btn.setMinimumWidth(100)
        save_btn.clicked.connect(self._save_settings)
        button_layout.addWidget(save_btn)

        cancel_btn = QPushButton("取消")
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
            self._save_status.setText("未设置")
            return False

        try:
            folder = Path(path_text)
            if not folder.exists():
                self._save_indicator.setStyleSheet("color: #F44336;")
                self._save_status.setText(f"❌ 文件夹不存在: {path_text}")
                return False

            if not folder.is_dir():
                self._save_indicator.setStyleSheet("color: #F44336;")
                self._save_status.setText(f"❌ 不是文件夹: {path_text}")
                return False

            # 搜索合适的存档文件
            save_files = list(folder.glob("*.save")) + list(folder.glob("current_run.save*"))
            if save_files:
                found_file = save_files[0]
                self._save_indicator.setStyleSheet("color: #4CAF50;")
                self._save_status.setText(f"✓ 找到存档文件: {found_file.name}")
                return True
            else:
                self._save_indicator.setStyleSheet("color: #FFC107;")
                self._save_status.setText(f"⚠ 文件夹存在但未找到 *.save 文件")
                return False

        except Exception as e:
            self._save_indicator.setStyleSheet("color: #F44336;")
            self._save_status.setText(f"❌ 检查失败: {e}")
            return False

    def _check_log_folder(self, path_text: str) -> bool:
        """检查日志文件夹并搜索合适的文件"""
        from pathlib import Path

        if not path_text:
            self._log_indicator.setStyleSheet("color: #aaa;")
            self._log_status.setText("未设置")
            return False

        try:
            folder = Path(path_text)
            if not folder.exists():
                self._log_indicator.setStyleSheet("color: #F44336;")
                self._log_status.setText(f"❌ 文件夹不存在: {path_text}")
                return False

            if not folder.is_dir():
                self._log_indicator.setStyleSheet("color: #F44336;")
                self._log_status.setText(f"❌ 不是文件夹: {path_text}")
                return False

            # 搜索合适的日志文件
            log_files = list(folder.glob("*.log")) + list(folder.glob("*.txt"))
            if log_files:
                # 找最新的日志文件
                latest = max(log_files, key=lambda p: p.stat().st_mtime)
                self._log_indicator.setStyleSheet("color: #4CAF50;")
                self._log_status.setText(f"✓ 找到日志文件: {latest.name}")
                return True
            else:
                self._log_indicator.setStyleSheet("color: #FFC107;")
                self._log_status.setText(f"⚠ 文件夹存在但未找到 *.log 或 *.txt 文件")
                return False

        except Exception as e:
            self._log_indicator.setStyleSheet("color: #F44336;")
            self._log_status.setText(f"❌ 检查失败: {e}")
            return False

    def _update_save_hint(self) -> None:
        """更新存档路径的自动检测提示"""
        try:
            from pathlib import Path
            steam_path = Path.home() / "AppData" / "Roaming" / "SlayTheSpire2" / "steam"
            if steam_path.exists():
                for save_dir in steam_path.glob("*/profile*/saves"):
                    if save_dir.is_dir():
                        self._save_path_input.setPlaceholderText(f"默认位置: {save_dir}")
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
                self._log_path_input.setPlaceholderText(f"默认位置: {log_path}")
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
            "选择存档文件所在的文件夹",
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
            "选择日志文件所在的文件夹",
            default_dir,
            QFileDialog.Option.ShowDirsOnly
        )
        if path:
            self._log_path_input.setText(path)

    def _save_settings(self) -> None:
        try:
            # 保存语言设置
            lang = self._lang_combo.currentData()
            from scripts.config_manager import set_language
            set_language(lang)

            save_path = self._save_path_input.text().strip()
            log_path = self._log_path_input.text().strip()

            payload = {}
            if save_path:
                payload["save_path"] = save_path
            if log_path:
                payload["log_path"] = log_path

            if payload:
                resp = requests.post(
                    f"{self.backend_url}/api/config",
                    json=payload,
                    timeout=5,
                )
                resp.raise_for_status()

            log.info("✓ 设置已保存")
            self.accept()
        except Exception as e:
            log.error(f"保存设置失败: {e}")


# ---------------------------------------------------------------------------
# 单张卡评估结果 Widget
# ---------------------------------------------------------------------------

class CardResultWidget(QFrame):
    """显示单张卡的评估结果行"""

    def __init__(self, result: dict, language: str = "en", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CardResultWidget")
        self._language = language
        self._build_ui(result)

    def _build_ui(self, result: dict) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        # 卡名（根据语言设置显示中文或英文）
        raw_name = result.get("card_name", "?")
        card_id = result.get("card_id", "")
        if self._language == "zh" and card_id:
            try:
                try:
                    from frontend.card_locale import get_card_locale
                except ImportError:
                    from card_locale import get_card_locale
                zh_name = get_card_locale().get_chinese_name(card_id)
                raw_name = zh_name or raw_name
            except Exception:
                pass
        name_label = QLabel(raw_name)
        name_label.setObjectName("cardName")
        name_label.setMinimumWidth(120)
        name_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(name_label)

        # 角色
        role_label = QLabel(result.get("role", ""))
        role_label.setObjectName("cardRole")
        role_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        role_label.setMinimumWidth(60)
        layout.addWidget(role_label)

        # 分数
        score = result.get("total_score", 0)
        score_label = QLabel(f"{score:.0f}")
        score_label.setObjectName("cardScore")
        score_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        score_label.setMinimumWidth(40)
        layout.addWidget(score_label)

        # 推荐标签
        rec = result.get("recommendation", "")
        rec_label = QLabel(rec)
        rec_label.setObjectName("cardRecommendation")
        rec_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rec_label.setMinimumWidth(70)
        # 移除颜色设置，使用默认样式
        layout.addWidget(rec_label)


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class CardAdviserWindow(QWidget):
    """
    永远置顶的浮窗主窗口。
    支持鼠标拖拽移动（无边框模式）。
    """

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

        self._init_window()
        self._build_ui()
        self._load_stylesheet()

        # 启动时检查后端连通性
        QTimer.singleShot(500, self._check_backend)

        # 启动游戏状态监视
        QTimer.singleShot(1000, self._start_game_watcher)

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
        self.setMinimumSize(420, 400)
        self.resize(560, 820)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 外层容器（用于实现圆角 + 半透明）
        container = QWidget()
        container.setObjectName("MainContainer")
        root.addWidget(container)

        main_layout = QVBoxLayout(container)
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

        # ---- 卡牌选择器 ----
        card_section = self._build_card_picker_section()
        main_layout.addWidget(card_section)

        # ---- 列表头 ----
        header = self._build_list_header()
        main_layout.addWidget(header)

        # ---- 卡牌列表（滚动区域）----
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_area.setObjectName("CardScrollArea")

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(4, 4, 4, 4)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch()

        self._scroll_area.setWidget(self._list_container)
        main_layout.addWidget(self._scroll_area)

        # ---- 状态栏 + 缩放手柄 ----
        bottom_bar = QWidget()
        bottom_layout = QHBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(0)

        self._status_label = QLabel("就绪 — 等待游戏数据")
        self._status_label.setObjectName("StatusBar")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bottom_layout.addWidget(self._status_label, 1)

        grip = QSizeGrip(self)
        grip.setObjectName("ResizeGrip")
        bottom_layout.addWidget(grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        main_layout.addWidget(bottom_bar)

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

        title = QLabel("⚔ STS2 Adviser")
        title.setObjectName("TitleLabel")
        layout.addWidget(title)
        layout.addStretch()

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
        refresh_detect_btn = QPushButton("🔄 检测")
        refresh_detect_btn.setObjectName("RefreshDetectButton")
        refresh_detect_btn.setToolTip("重新检测游戏和日志")
        refresh_detect_btn.clicked.connect(self._on_refresh_detect)
        btn_layout.addWidget(refresh_detect_btn)

        # 设置按钮
        settings_btn = QPushButton("⚙ 设置")
        settings_btn.setObjectName("SettingsButton")
        settings_btn.setToolTip("路径设置")
        settings_btn.clicked.connect(self._on_settings)
        btn_layout.addWidget(settings_btn)

        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)

        # ===== 第二行：游戏信息 =====
        info_layout = QHBoxLayout()
        info_layout.setSpacing(8)

        self._game_info_label = QLabel("等待游戏数据...")
        self._game_info_label.setObjectName("GameInfoLabel")
        self._game_info_label.setStyleSheet("font-size: 12pt; color: #666;")
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
        self._backend_indicator = QLabel("● 后端")
        self._backend_indicator.setObjectName("BackendIndicator")
        self._backend_indicator.setStyleSheet("color: #aaa; font-weight: bold;")
        backend_box.addWidget(self._backend_indicator)
        indicators_layout.addLayout(backend_box)

        # 游戏存档指示器
        game_box = QVBoxLayout()
        game_box.setSpacing(2)
        self._game_indicator = QLabel("● 游戏")
        self._game_indicator.setObjectName("GameIndicator")
        self._game_indicator.setStyleSheet("color: #F44336; font-weight: bold;")
        game_box.addWidget(self._game_indicator)
        indicators_layout.addLayout(game_box)

        # 日志监视指示器
        log_box = QVBoxLayout()
        log_box.setSpacing(2)
        self._log_indicator = QLabel("● 日志")
        self._log_indicator.setObjectName("LogIndicator")
        self._log_indicator.setStyleSheet("color: #F44336; font-weight: bold;")
        log_box.addWidget(self._log_indicator)
        indicators_layout.addLayout(log_box)

        indicators_layout.addStretch()
        main_layout.addLayout(indicators_layout)

        return toolbar

    def _run_debug(self) -> None:
        log.info("调试按钮被点击，开始执行调试逻辑...")
        self._status_label.setText("调试中，请查看日志...")

    def _open_log_directory(self) -> None:
        log_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        subprocess.Popen(f'explorer "{log_dir}"')
        log.info("打开日志目录窗口。")

    def _build_list_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("ListHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(8, 2, 8, 2)

        for text, width in [("卡名", 120), ("角色", 60), ("分数", 40), ("推荐", 70)]:
            lbl = QLabel(text)
            lbl.setObjectName("HeaderLabel")
            lbl.setMinimumWidth(width)
            if text in ("分数",):
                lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            elif text in ("角色", "推荐"):
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(lbl)

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
            self._backend_indicator.setText("● 后端已连接")
            self._backend_indicator.setStyleSheet("color: #4CAF50;")
        else:
            self._backend_indicator.setText("● 后端未连接")
            self._backend_indicator.setStyleSheet("color: #F44336;")

    def _start_game_watcher(self) -> None:
        """启动游戏状态实时监视"""
        if self._game_watcher is not None:
            return

        self._game_watcher = GameStateWatcher(BACKEND_URL)
        self._game_watcher.game_state_updated.connect(self._on_game_state_update)
        self._game_watcher.connection_status.connect(self._on_connection_status)
        self._game_watcher.log_status_updated.connect(self._on_log_status_update)
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
            "deck": norm_deck,
            "relics": norm_relics,
        })

        # 更新游戏存档指示器
        character = state.get("character", "").replace("CHARACTER.", "").upper()
        floor = state.get("floor", 0)

        if character and floor > 0:
            self._game_indicator.setText("● 游戏")
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
            info_html = (
                f"<span style='color:#64B5F6;font-weight:bold'>{character}</span>"
                f"{asc_text}"
                f"  <span style='color:#aaa'>F{floor}</span>"
                f"  <span style='color:{hp_color}'>❤ {hp}/{max_hp}</span>"
                f"  <span style='color:#FFD54F'>💰 {gold}</span>"
                f"  <span style='color:#90CAF9'>🃏 {deck_size}</span>"
            )
            self._game_info_label.setText(info_html)
            self._game_info_label.setStyleSheet("font-size: 12pt;")
        else:
            self._game_indicator.setText("● 游戏")
            self._game_indicator.setStyleSheet("color: #F44336; font-weight: bold;")
            self._game_info_label.setText("<span style='color:#999'>未检测到游戏运行</span>")
            self._game_info_label.setStyleSheet("font-size: 12pt;")

        # 更新底部状态栏
        if state.get("hand"):
            self._status_label.setText(f"当前手牌: {len(state.get('hand', []))} 张")
        elif character and floor > 0:
            self._status_label.setText(f"就绪 — 选择候选卡后点击评估")

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
            self._log_indicator.setText("● 日志")
            self._log_indicator.setStyleSheet("color: #4CAF50;")
            log.info(f"日志正在监视: {status.get('path')}")
        else:
            self._log_indicator.setText("● 日志")
            self._log_indicator.setStyleSheet("color: #F44336;")
            log.warning("日志未被监视")

    def _on_refresh_detect(self) -> None:
        """刷新检测：重新初始化游戏和日志检测"""
        try:
            self._status_label.setText("正在重新检测...")

            # 通过调用配置端点来触发后端重新初始化 GameWatcher
            resp = requests.post(
                f"{BACKEND_URL}/api/config",
                json={},  # 空配置会触发重新检测
                timeout=5,
            )

            if resp.status_code == 200:
                log.info("✓ 已触发重新检测")
                self._status_label.setText("检测中... 稍候")
                # 延迟1秒后查看指示灯更新
                QTimer.singleShot(1000, lambda: self._status_label.setText("检测完成"))
            else:
                self._status_label.setText(f"检测失败: {resp.status_code}")

        except Exception as e:
            log.error(f"重新检测失败: {e}")
            self._status_label.setText(f"错误: {e}")

    def _on_settings(self) -> None:
        """打开设置对话框"""
        dialog = PathSettingsDialog(self, BACKEND_URL)
        dialog.exec()
        # 重新读取语言配置，如有变化则刷新卡牌
        try:
            from scripts.config_manager import get_language
            new_lang = get_language()
        except Exception:
            new_lang = "en"
        if new_lang != self._language:
            self._language = new_lang
            # 强制重新加载（重置 _current_character 使 _fetch 不被跳过）
            char = self._current_character
            self._current_character = ""
            if char:
                self._fetch_cards_for_character(char)

    # ------------------------------------------------------------------
    # 卡牌选择器
    # ------------------------------------------------------------------

    def _build_card_picker_section(self) -> QWidget:
        container = QWidget()
        container.setObjectName("CardPickerSection")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._card_picker = CardPickerPanel()
        self._card_picker.selection_changed.connect(self._on_card_selection_changed)
        layout.addWidget(self._card_picker)

        self._selection_tray = SelectionTrayWidget()
        self._selection_tray.evaluate_requested.connect(self._on_evaluate_from_picker)
        layout.addWidget(self._selection_tray)

        return container

    def _fetch_cards_for_character(self, character: str) -> None:
        if not character:
            return
        self._current_character = character

        if self._cards_fetch_worker and self._cards_fetch_worker.isRunning():
            self._cards_fetch_worker.terminate()

        self._card_picker.set_language(self._language)
        self._card_picker.clear_cards()
        self._selection_tray.update_selection([])
        self._status_label.setText(f"加载 {character.upper()} 卡牌中...")

        self._cards_fetch_worker = CardsFetchWorker(character)
        self._cards_fetch_worker.cards_ready.connect(self._on_cards_fetched)
        self._cards_fetch_worker.error_occurred.connect(
            lambda e: self._status_label.setText(f"加载卡牌失败: {e}")
        )
        self._cards_fetch_worker.start()

    def _on_cards_fetched(self, cards: list[dict]) -> None:
        playable_types = {"attack", "skill", "power"}
        playable = [c for c in cards if c.get("card_type", "").lower() in playable_types]
        self._card_picker.populate(playable)
        self._status_label.setText(f"已加载 {len(playable)} 张卡牌，请选择候选卡")

    def _on_card_selection_changed(self, selected_cards: list[dict], display_names: list[str]) -> None:
        self._selection_tray.update_selection(selected_cards, display_names)

    def _on_evaluate_from_picker(self, selected_cards: list[dict]) -> None:
        if not selected_cards:
            self._status_label.setText("请先选择候选卡")
            return

        card_ids = [c["id"].lower() for c in selected_cards]
        run_state = self._run_state.copy() if self._run_state else {}
        run_state.setdefault("character", "silent")
        run_state.setdefault("floor", 1)
        run_state.setdefault("hp", 70)
        run_state.setdefault("max_hp", 70)
        run_state.setdefault("gold", 0)
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

        self._status_label.setText("评估中...")
        self._selection_tray._evaluate_btn.setEnabled(False)

        self._worker = EvaluateWorker(run_state)
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
        if "deck" not in run_state:
            run_state["deck"] = []
        if "card_choices" not in run_state or not run_state["card_choices"]:
            # 无法进行评估，因为没有选卡池
            self._status_label.setText("等待选卡数据... (需要日志文件或游戏运行中)")
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
        self._status_label.setText("评估中...")

        self._worker = EvaluateWorker(run_state)
        self._worker.result_ready.connect(self._on_result)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def _on_result(self, data: dict) -> None:
        results = data.get("results", [])
        archetypes = data.get("detected_archetypes", [])
        self._render_results(results)

        if not results:
            self._status_label.setText("❌ 未找到匹配的卡牌")
        else:
            arch_text = "、".join(archetypes) if archetypes else "未检测到套路"
            self._status_label.setText(f"套路：{arch_text}")

    def _on_error(self, message: str) -> None:
        self._status_label.setText(f"错误：{message}")

    def _render_results(self, results: list[dict]) -> None:
        """清空列表并重新渲染评估结果"""
        # 移除旧 widget（保留 stretch）
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for result in results:
            widget = CardResultWidget(result, language=self._language)
            self._list_layout.insertWidget(self._list_layout.count() - 1, widget)

    def _show_placeholder(self) -> None:
        """初始占位内容"""
        placeholder = QLabel("点击「刷新」加载评估结果")
        placeholder.setObjectName("Placeholder")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._list_layout.insertWidget(0, placeholder)

    # ------------------------------------------------------------------
    # 样式加载
    # ------------------------------------------------------------------

    def _load_stylesheet(self) -> None:
        import os
        qss_path = os.path.join(os.path.dirname(__file__), "styles.qss")
        if os.path.exists(qss_path):
            with open(qss_path, encoding="utf-8") as f:
                self.setStyleSheet(f.read())

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
