"""
frontend/main.py
独立启动前端 UI

用法：
  python -m frontend.main    # 启动前端（仅 UI，后端需要单独启动）
  python -m frontend         # 调用此文件
"""

import logging
import sys
import os
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import QApplication

from .ui import CardAdviserWindow

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------

_LOG_FILE = Path(__file__).parent.parent / "frontend.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(_LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("frontend")
log.info("=" * 60)
log.info(f"STS2 Adviser 前端启动  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
log.info(f"日志文件：{_LOG_FILE}")


# ---------------------------------------------------------------------------
# 前端启动
# ---------------------------------------------------------------------------

def main() -> None:
    """启动前端 UI"""
    try:
        app = QApplication(sys.argv)
        app.setApplicationName("STS2 Card Adviser")
        app.setQuitOnLastWindowClosed(True)

        window = CardAdviserWindow()
        window.show()

        log.info("前端 UI 已启动，等待连接...")
        exit_code = app.exec()

        log.info("前端已关闭")
        sys.exit(exit_code)

    except Exception:
        log.critical("前端崩溃", exc_info=True)
        raise


if __name__ == "__main__":
    main()
