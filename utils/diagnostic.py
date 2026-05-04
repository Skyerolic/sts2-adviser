"""
utils/diagnostic.py
本地诊断信息收集 + 脱敏工具

仅收集本机数据用于本地查看 / 本地打包，**绝不**主动外发。
所有可能含 PII 的字段（Windows 用户名、Steam ID、绝对路径）都提供
脱敏函数。打包对话框默认勾选脱敏。
"""

from __future__ import annotations

import io
import os
import platform
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional


# ------------------------------------------------------------------
# 脱敏
# ------------------------------------------------------------------

# Steam ID（17 位数字，64-bit Steam ID 格式）
_STEAM_ID_PATTERN = re.compile(r'\b(7656119\d{10})\b')


def _current_username() -> str:
    """返回当前 Windows 用户名（用于在路径里识别并替换）"""
    return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def redact(text: str, redact_username: bool = True, redact_steam_id: bool = True) -> str:
    """
    对一段文本做脱敏：
      - C:\\Users\\<username>\\... -> C:\\Users\\<USER>\\...
      - 17 位 Steam ID -> <STEAM_ID>
    """
    if not text:
        return text
    out = text
    if redact_username:
        username = _current_username()
        if username:
            # 路径中的用户名（C:\Users\xxx 或 /home/xxx）
            out = re.sub(
                r'(?i)([\\/])users([\\/])' + re.escape(username) + r'(?=[\\/]|$)',
                r'\1Users\2<USER>',
                out,
            )
            # 直接出现的用户名也替换（保守一点）
            out = re.sub(
                r'\b' + re.escape(username) + r'\b',
                '<USER>',
                out,
            )
    if redact_steam_id:
        out = _STEAM_ID_PATTERN.sub('<STEAM_ID>', out)
    return out


# ------------------------------------------------------------------
# 系统 / 运行环境信息收集
# ------------------------------------------------------------------

def _safe(call):
    """容错执行；任何异常返回 'N/A: <reason>'"""
    try:
        return call()
    except Exception as e:
        return f"N/A ({type(e).__name__}: {e})"


def _check_winrt_ocr() -> str:
    """检查 WinRT OCR 是否可用 + 当前加载的语言"""
    try:
        from vision.ocr_engine import get_ocr_engine
        engine = get_ocr_engine()
        if engine.is_available():
            return f"available, language={engine.language}"
        return "NOT available"
    except Exception as e:
        return f"check failed: {e}"


def _check_cv2() -> str:
    try:
        import cv2  # noqa: F401
        return f"available (version={cv2.__version__})"
    except ImportError:
        return "NOT available"
    except Exception as e:
        return f"check failed: {e}"


def _list_monitors() -> str:
    """列出显示器分辨率（PyQt6 优先；失败回退 ctypes）"""
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return "N/A (no QApplication)"
        screens = app.screens()
        out = []
        for i, s in enumerate(screens):
            geo = s.geometry()
            dpi = s.logicalDotsPerInch()
            out.append(
                f"  [{i}] {geo.width()}x{geo.height()} @ ({geo.x()},{geo.y()}) "
                f"dpi={dpi:.0f} scale={s.devicePixelRatio():.2f}"
            )
        return "\n".join(out) if out else "N/A (no screens)"
    except Exception as e:
        return f"check failed: {e}"


def _game_window_info() -> str:
    """当前 STS2 游戏窗口尺寸/位置"""
    try:
        from vision.window_capture import WindowCapture
        cap = WindowCapture()
        if cap.find_window() is None:
            return "not found"
        info = cap.get_window_info()
        if info is None:
            return "not found"
        return f"title='{info.title}' size={info.width}x{info.height} pos=({info.x},{info.y})"
    except Exception as e:
        return f"check failed: {e}"


def _logs_dir_summary() -> str:
    """logs/ 目录概览：文件数 + 最近 5 个 OCR 快照 + app.log 大小"""
    try:
        from utils.paths import get_app_root
        logs_dir = get_app_root() / "logs"
        out: list[str] = []

        # app.log 状态
        app_log_dir = _resolve_app_log_dir()
        for n in ("app.log", "app.log.1"):
            p = app_log_dir / n
            if p.exists():
                out.append(f"{n}: {p.stat().st_size // 1024} KB ({p})")

        if not logs_dir.exists():
            out.append("logs/ directory not found")
            return "\n".join(out)

        # 仅成功快照（不含 FAIL_）
        png_files = sorted(p for p in logs_dir.glob("ocr_*.png") if not p.name.startswith("ocr_FAIL_"))
        fail_files = sorted(logs_dir.glob("ocr_FAIL_*.png"))
        out.extend([
            f"logs/ path: {logs_dir}",
            f"OCR success snapshots: {len(png_files)}",
            f"OCR FAIL snapshots: {len(fail_files)}",
        ])
        recent = (png_files + fail_files)[-5:]
        if recent:
            out.append("Recent snapshots:")
            for p in recent:
                size_kb = p.stat().st_size // 1024
                out.append(f"  {p.name} ({size_kb} KB)")
        return "\n".join(out)
    except Exception as e:
        return f"check failed: {e}"


def collect_system_info(redact_pii: bool = True) -> str:
    """
    收集本机诊断信息字符串（多行）。
    redact_pii=True 时对路径中的用户名 + Steam ID 做脱敏。

    收集的内容：
      - 应用版本（VERSION）、Python 版本、Windows 版本、CPU 架构
      - WinRT OCR / cv2 可用性
      - 显示器列表（含 DPI / 缩放）
      - 当前 STS2 游戏窗口信息
      - logs/ 目录概览
    """
    try:
        from frontend.ui import VERSION
    except Exception:
        VERSION = "unknown"

    lines = [
        f"=== STS2 Adviser 诊断信息 ({datetime.now().isoformat(timespec='seconds')}) ===",
        "",
        f"App version       : {VERSION}",
        f"Python            : {sys.version.split()[0]}",
        f"Platform          : {platform.platform()}",
        f"Architecture      : {platform.machine()}",
        f"Frozen (EXE)      : {getattr(sys, 'frozen', False)}",
        "",
        f"WinRT OCR         : {_safe(_check_winrt_ocr)}",
        f"OpenCV            : {_safe(_check_cv2)}",
        "",
        "Monitors:",
        _safe(_list_monitors),
        "",
        f"Game window       : {_safe(_game_window_info)}",
        "",
        _safe(_logs_dir_summary),
    ]
    text = "\n".join(lines)
    if redact_pii:
        text = redact(text)
    return text


# ------------------------------------------------------------------
# 打包诊断 zip
# ------------------------------------------------------------------

def _safe_read_text(path: Path, max_bytes: int = 5 * 1024 * 1024) -> str:
    """安全读取文本文件（限大小，避免巨大日志卡死）"""
    try:
        size = path.stat().st_size
        if size > max_bytes:
            with open(path, 'rb') as f:
                f.seek(-max_bytes, 2)
                data = f.read()
            return f"(truncated to last {max_bytes // 1024 // 1024} MB)\n" + data.decode("utf-8", errors="replace")
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"<read failed: {e}>"


def _resolve_app_log_dir() -> Path:
    """
    返回 app.log 实际所在目录：
      - EXE (frozen) 模式：EXE 旁边（`Path(sys.executable).parent`），与 main.py 写入逻辑一致
      - 开发模式：项目根目录
    注意 logs/ 目录走 get_app_root()，跟 vision_bridge 一致；只有 app.log 例外。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def list_packageable_files() -> list[dict]:
    """
    返回可纳入诊断包的文件列表，每项含：
      {path: Path, name: str, size_bytes: int, kind: 'log' | 'snapshot' | 'snapshot_txt'}
    """
    from utils.paths import get_app_root
    root = get_app_root()
    out: list[dict] = []

    # app.log（EXE 模式下在 EXE 旁；含 RotatingFileHandler 的备份 app.log.1）
    log_dir = _resolve_app_log_dir()
    for log_name in ("app.log", "app.log.1"):
        log_path = log_dir / log_name
        if log_path.exists():
            out.append({
                "path": log_path, "name": log_name,
                "size_bytes": log_path.stat().st_size, "kind": "log",
            })

    # OCR snapshots（成功 + 失败），按修改时间倒序
    logs_dir = root / "logs"
    if logs_dir.exists():
        snapshots = sorted(
            list(logs_dir.glob("ocr_*.png")) + list(logs_dir.glob("ocr_FAIL_*.png")),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        for p in snapshots[:20]:  # 最多 20 张避免包过大
            out.append({
                "path": p, "name": f"logs/{p.name}",
                "size_bytes": p.stat().st_size, "kind": "snapshot",
            })
            txt = p.with_suffix(".txt")
            if txt.exists():
                out.append({
                    "path": txt, "name": f"logs/{txt.name}",
                    "size_bytes": txt.stat().st_size, "kind": "snapshot_txt",
                })

    return out


def create_diagnostic_zip(
    out_path: Path,
    selected_files: list[Path],
    redact_pii: bool = True,
) -> Path:
    """
    生成本地诊断 zip：
      - 写入 system_info.txt（脱敏与否取决于 redact_pii）
      - 写入 README_PRIVACY.txt（说明分享前应自查内容）
      - 文本文件（.log / .txt）按 redact_pii 是否脱敏后写入
      - 二进制文件（.png）原样写入（截图无法脱敏）

    返回写入的 zip 路径。
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # README_PRIVACY.txt
        readme = (
            "STS2 Adviser 诊断包\n"
            "=================================================\n"
            f"生成时间: {datetime.now().isoformat(timespec='seconds')}\n"
            f"脱敏: {'已对路径中的用户名和 Steam ID 做替换' if redact_pii else '未脱敏（含原始用户名/Steam ID）'}\n"
            "\n"
            "包内可能包含的内容：\n"
            "  - app.log: 应用运行日志，含路径 / 时间戳 / 错误信息\n"
            "  - logs/ocr_*.png: OCR 截图，**截图本身可能含游戏画面里的玩家名**\n"
            "  - logs/ocr_*.txt: OCR 识别结果文本\n"
            "  - system_info.txt: 系统配置摘要\n"
            "\n"
            "**分享前请打开本 zip 自行检查内容**。\n"
            "本工具不会自动上传任何数据；本 zip 文件仅保存到本地，由你决定是否分享。\n"
        )
        zf.writestr("README_PRIVACY.txt", readme)

        # system_info.txt
        zf.writestr("system_info.txt", collect_system_info(redact_pii=redact_pii))

        # 用户选中的文件
        for src in selected_files:
            try:
                if not src.exists():
                    continue
                arcname = src.name
                # 推测在 zip 内的相对路径
                if src.parent.name == "logs":
                    arcname = f"logs/{src.name}"
                ext = src.suffix.lower()
                if ext in (".log", ".txt"):
                    content = _safe_read_text(src)
                    if redact_pii:
                        content = redact(content)
                    zf.writestr(arcname, content)
                else:
                    # 二进制（截图）原样
                    with open(src, "rb") as f:
                        zf.writestr(arcname, f.read())
            except Exception as e:
                zf.writestr(f"_errors/{src.name}.error.txt", f"package failed: {e}")

    return out_path


def reveal_in_explorer(path: Path) -> None:
    """打开文件管理器并选中指定文件（Windows）"""
    try:
        if sys.platform.startswith("win"):
            import subprocess
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(str(path.parent))  # type: ignore[attr-defined]
    except Exception:
        pass
