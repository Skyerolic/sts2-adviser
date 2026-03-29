"""
vision/window_capture.py
查找STS2游戏窗口并截取图像

功能：
  - 通过窗口标题查找 STS2 进程窗口
  - 获取窗口位置和尺寸
  - 使用 PrintWindow Win32 API 截取窗口内容（不受遮挡影响）
  - 截图结果以 numpy BGR 数组返回（兼容 OpenCV）

依赖：
  - pywin32（win32gui/win32ui/win32con）
  - numpy
  - Pillow
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# STS2 窗口标题关键字（模糊匹配）
_STS2_TITLE_KEYWORDS = [
    "Slay the Spire 2",
    "SlayTheSpire2",
    "slay the spire 2",
]


@dataclass
class WindowInfo:
    """游戏窗口信息"""
    hwnd: int           # 窗口句柄
    title: str          # 窗口标题
    left: int           # 屏幕左边界（像素）
    top: int            # 屏幕上边界
    width: int          # 窗口宽度
    height: int         # 窗口高度

    @property
    def rect(self) -> tuple[int, int, int, int]:
        """返回 (left, top, right, bottom)"""
        return (self.left, self.top, self.left + self.width, self.top + self.height)

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height if self.height > 0 else 0.0


class WindowCapture:
    """
    STS2 窗口捕获器。

    用法：
        cap = WindowCapture()
        info = cap.find_window()
        if info:
            img = cap.capture()   # numpy BGR array
    """

    def __init__(self) -> None:
        self._window: Optional[WindowInfo] = None

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def find_window(self) -> Optional[WindowInfo]:
        """
        搜索 STS2 游戏窗口。
        找到则缓存并返回 WindowInfo，未找到返回 None。
        """
        try:
            import win32gui
        except ImportError:
            log.error("pywin32 未安装，无法查找窗口")
            return None

        found: Optional[WindowInfo] = None

        def _enum_callback(hwnd: int, _: None) -> bool:
            nonlocal found
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return True
            title_lower = title.lower()
            for kw in _STS2_TITLE_KEYWORDS:
                if kw.lower() in title_lower:
                    try:
                        rect = win32gui.GetWindowRect(hwnd)
                        left, top, right, bottom = rect
                        w = right - left
                        h = bottom - top
                        # 过滤最小化或零尺寸窗口
                        if w > 100 and h > 100:
                            found = WindowInfo(
                                hwnd=hwnd,
                                title=title,
                                left=left,
                                top=top,
                                width=w,
                                height=h,
                            )
                    except Exception as e:
                        log.debug(f"GetWindowRect 失败 hwnd={hwnd}: {e}")
                    return False  # 找到后停止枚举
            return True

        try:
            win32gui.EnumWindows(_enum_callback, None)
        except Exception as e:
            log.error(f"EnumWindows 失败: {e}")
            return None

        if found:
            # 仅首次找到时打 INFO，窗口未变化时静默
            if self._window is None or self._window.hwnd != found.hwnd:
                log.debug(f"找到窗口: '{found.title}' {found.width}x{found.height} @ ({found.left},{found.top})")
            self._window = found
        else:
            if self._window is not None:
                log.debug("STS2 窗口消失")
            self._window = None

        return found

    def capture(self, refresh_window: bool = False) -> Optional[np.ndarray]:
        """
        截取游戏窗口图像。

        Args:
            refresh_window: 是否重新搜索窗口位置（默认使用缓存）

        Returns:
            BGR numpy array，失败返回 None
        """
        if refresh_window or self._window is None:
            if self.find_window() is None:
                return None

        win = self._window
        if win is None:
            return None

        # 刷新窗口位置（窗口可能被移动）
        try:
            import win32gui
            rect = win32gui.GetWindowRect(win.hwnd)
            left, top, right, bottom = rect
            win.left = left
            win.top = top
            win.width = right - left
            win.height = bottom - top
        except Exception as e:
            log.warning(f"刷新窗口位置失败: {e}")

        return self._screenshot(win)

    def get_window_info(self) -> Optional[WindowInfo]:
        """返回当前缓存的窗口信息"""
        return self._window

    def is_window_available(self) -> bool:
        """检查窗口是否仍然存在"""
        if self._window is None:
            return False
        try:
            import win32gui
            return win32gui.IsWindowVisible(self._window.hwnd)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _screenshot(self, win: WindowInfo) -> Optional[np.ndarray]:
        """
        使用 PrintWindow Win32 API 截取窗口内容。
        不受其他窗口遮挡影响，直接从窗口缓冲区读取。
        """
        try:
            import ctypes
            import win32gui
            import win32ui
            import win32con
        except ImportError:
            log.error("pywin32 未安装，请运行: pip install pywin32")
            return None

        hwnd = win.hwnd
        w = win.width
        h = win.height

        if w <= 0 or h <= 0:
            log.warning(f"窗口尺寸无效: {w}x{h}")
            return None

        try:
            # 获取窗口 DC
            hwnd_dc = win32gui.GetWindowDC(hwnd)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()

            # 创建兼容位图
            save_bmp = win32ui.CreateBitmap()
            save_bmp.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(save_bmp)

            # PrintWindow(hwnd, hdc, flags=2) — PW_RENDERFULLCONTENT
            # flags=2 适用于使用 DirectComposition/DX 渲染的窗口
            result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
            if not result:
                # 回退 flags=0（传统 GDI 渲染）
                result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)

            # 转换为 numpy BGR array
            bmp_info = save_bmp.GetInfo()
            bmp_bytes = save_bmp.GetBitmapBits(True)
            img = np.frombuffer(bmp_bytes, dtype=np.uint8)
            img = img.reshape((bmp_info["bmHeight"], bmp_info["bmWidth"], 4))
            img_bgr = img[:, :, :3]  # BGRA → BGR

            return img_bgr.copy()  # copy 避免底层内存释放后悬空引用

        except Exception as e:
            log.warning(f"PrintWindow 截图失败: {e}")
            return None
        finally:
            try:
                save_dc.DeleteDC()
                mfc_dc.DeleteDC()
                win32gui.ReleaseDC(hwnd, hwnd_dc)
                win32gui.DeleteObject(save_bmp.GetHandle())
            except Exception:
                pass
