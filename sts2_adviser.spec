# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec 文件 — STS2 Adviser
用法：
    pyinstaller sts2_adviser.spec
输出：dist/sts2_adviser/  (onedir 模式，约 200-350 MB)

依赖安装：
    pip install pyinstaller
"""

import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

# ─── winrt 全量收集（DLL + 数据文件） ──────────────────────────────────────────
winrt_packages = [
    "winrt.windows.media.ocr",
    "winrt.windows.globalization",
    "winrt.windows.graphics.imaging",
    "winrt.windows.storage.streams",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
]
winrt_datas, winrt_binaries, winrt_hiddenimports = [], [], []
for pkg in winrt_packages:
    d, b, h = collect_all(pkg)
    winrt_datas     += d
    winrt_binaries  += b
    winrt_hiddenimports += h

# ─── PyQt6 平台插件（确保 qwindows 被收入） ────────────────────────────────────
qt_datas = collect_data_files("PyQt6", includes=["Qt6/plugins/**/*"])

# ─── 项目数据文件 ──────────────────────────────────────────────────────────────
project_datas = [
    ("data",         "data"),         # cards.json, relics.json, card_library.json 等
    ("frontend",     "frontend"),     # styles.qss
    ("utils",        "utils"),        # paths.py
]

# ─── 显式打包 pythonXXX.dll（动态检测版本，避免目标机器缺少此 DLL） ──────────
import os
_py_ver = f"{sys.version_info.major}{sys.version_info.minor}"
_dll_name = f"python{_py_ver}.dll"
# sys._base_executable 在 venv 里指向真正的系统 Python，不受 venv 影响
_base_exe = getattr(sys, "_base_executable", sys.executable)
_py_dll_candidates = [
    os.path.join(os.path.dirname(_base_exe), _dll_name),   # 系统 Python 安装目录
    os.path.join(os.path.dirname(sys.executable), _dll_name),  # venv/Scripts/（兜底）
]
_py_dll = next((p for p in _py_dll_candidates if os.path.exists(p)), None)
extra_binaries = [(_py_dll, ".")] if _py_dll else []

a = Analysis(
    ["main.py"],                       # 入口脚本
    pathex=["."],
    binaries=winrt_binaries + extra_binaries,
    datas=project_datas + winrt_datas + qt_datas,
    hiddenimports=[
        # uvicorn 动态加载模块
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.protocols.websockets.wsproto_impl",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # FastAPI / starlette 依赖
        "anyio",
        "anyio._backends._asyncio",
        "anyio._backends._trio",
        "starlette.routing",
        # pydantic
        "pydantic.v1",
        # websocket
        "websocket",
        # win32 API
        "win32api",
        "win32con",
        "win32gui",
        "pywintypes",
        # PyQt6 核心
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        # 图像/视觉
        "PIL",
        "PIL.Image",
        "numpy",
        "cv2",
        "mss",
        "rapidfuzz",
        "rapidfuzz.fuzz",
        "rapidfuzz.process",
        # 系统监控
        "psutil",
        # scripts 包
        "scripts.game_watcher",
        "scripts.config_manager",
        # asyncio 子进程支持（Windows）
        "multiprocessing.popen_spawn_win32",
        # winrt 全量
        *winrt_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "httpx",
        "python-dotenv",
        "IPython",
        "matplotlib",
        "scipy",
        "sklearn",
        "torch",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # onedir 模式：DLL 放在目录里
    name="sts2_adviser",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,               # DEBUG: 显示控制台窗口以捕获启动错误
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                  # 可选：icon="assets/icon.ico"
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="sts2_adviser",        # 输出目录：dist/sts2_adviser/
)
