"""
main.py
启动脚本

流程：
  1. 在独立线程中启动 FastAPI + uvicorn 后端（127.0.0.1:8000）
  2. 在主线程启动 PyQt6 前端浮窗
  3. 前端关闭时优雅关闭后端线程

用法：
  python main.py
"""

from __future__ import annotations

import io
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from datetime import datetime

from utils.paths import get_app_root

import uvicorn
from PyQt6.QtWidgets import QApplication

# 设置 UTF-8 编码支持（Windows 终端）
# GUI 模式（EXE console=False）下 stdout/stderr 为 None，跳过封装
if sys.stdout and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# ---------------------------------------------------------------------------
# 日志配置（每次运行覆盖写入根目录 app.log）
# ---------------------------------------------------------------------------

_LOG_FILE = get_app_root() / "app.log"

_log_handlers = [logging.FileHandler(_LOG_FILE, mode="w", encoding="utf-8")]
if sys.stdout:  # None in GUI EXE mode (console=False)
    _log_handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_log_handlers,
)
log = logging.getLogger("main")
log.info("=" * 60)
log.info(f"STS2 Adviser 启动  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
log.info(f"日志文件：{_LOG_FILE}")

# ---------------------------------------------------------------------------
# 后端启动（独立线程）
# ---------------------------------------------------------------------------

_BACKEND_HOST = "127.0.0.1"


def _find_free_port(start: int = 8000, end: int = 8020) -> int:
    """在 [start, end) 范围内找一个可用端口"""
    import socket
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((_BACKEND_HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"找不到可用端口（{start}–{end-1}）")


_BACKEND_PORT = _find_free_port()
os.environ["STS2_BACKEND_PORT"] = str(_BACKEND_PORT)

# 必须在设置端口环境变量之后才导入前端，否则 BACKEND_URL 会被固定为旧端口
from frontend.ui import CardAdviserWindow  # noqa: E402
# 直接导入 app 对象（EXE 打包后字符串形式 "backend.main:app" 无法被 uvicorn 解析）
from backend.main import app as _backend_app  # noqa: E402


def _start_backend() -> None:
    """
    在当前线程中启动 uvicorn。
    该函数应在 daemon 线程中调用，随主线程退出自动结束。
    """
    config = uvicorn.Config(
        _backend_app,
        host=_BACKEND_HOST,
        port=_BACKEND_PORT,
        log_level="warning",   # 减少控制台噪音；改为 "info" 可调试
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    server.run()


def start_backend_thread() -> threading.Thread:
    """启动后端线程并返回线程对象"""
    thread = threading.Thread(
        target=_start_backend,
        name="uvicorn-backend",
        daemon=True,   # 主线程结束时自动结束
    )
    thread.start()
    log.info(f"后端服务启动中 → http://{_BACKEND_HOST}:{_BACKEND_PORT}")
    return thread


def wait_for_backend(timeout: float = 5.0) -> bool:
    """
    等待后端就绪（轮询 /）。
    返回 True 表示就绪，False 表示超时。
    """
    import requests

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = requests.get(
                f"http://{_BACKEND_HOST}:{_BACKEND_PORT}/",
                timeout=1,
            )
            if resp.status_code == 200:
                log.info("后端就绪")
                return True
        except Exception as e:
            log.debug(f"等待后端中：{e}")
        time.sleep(0.2)

    log.warning("等待后端超时，前端将自行重试连接")
    return False


# ---------------------------------------------------------------------------
# 前端启动
# ---------------------------------------------------------------------------

def start_frontend() -> int:
    """启动 PyQt6 前端，返回退出码"""
    app = QApplication(sys.argv)
    app.setApplicationName("STS2 Card Adviser")
    app.setQuitOnLastWindowClosed(True)

    window = CardAdviserWindow()
    window.show()

    return app.exec()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        # 允许 Ctrl+C 终止
        signal.signal(signal.SIGINT, signal.SIG_DFL)

        # 1. 启动后端线程
        start_backend_thread()

        # 2. 等待后端就绪（非阻塞式，超时后前端自行处理）
        wait_for_backend(timeout=5.0)

        # 3. 启动前端（阻塞直到窗口关闭）
        exit_code = start_frontend()

        log.info("前端已关闭，程序退出")
        sys.exit(exit_code)

    except Exception:
        log.critical("程序崩溃", exc_info=True)
        raise


if __name__ == "__main__":
    main()
