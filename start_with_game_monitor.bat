@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo.
echo ========================================
echo STS2 Adviser - Complete System
echo With Real-time Game State Monitoring
echo ========================================
echo.

:: 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found
    pause
    exit /b 1
)

echo [OK] Python found
echo.

:: 设置环境变量
set PYTHONUNBUFFERED=1

:: 创建必要目录
if not exist "data" mkdir "data"
if not exist "logs" mkdir "logs"

echo.
echo Starting System Components...
echo.

:: 启动游戏监视器 (Terminal 1)
echo [1/4] Starting Game State Watcher...
start "STS2 Adviser - Game Watcher" cmd /k ^
    "cd /d "%cd%" && python scripts/game_watcher.py && pause"

timeout /t 1 /nobreak >nul

:: 启动后端 (Terminal 2)
echo [2/4] Starting Backend API...
start "STS2 Adviser - Backend" cmd /k ^
    "cd /d "%cd%" && python -m main && pause"

timeout /t 2 /nobreak >nul

:: 启动前端 (Terminal 3)
echo [3/4] Starting Frontend UI...
start "STS2 Adviser - Frontend" cmd /k ^
    "cd /d "%cd%" && python -m frontend.main && pause"

echo.
echo ========================================
echo System Started!
echo ========================================
echo.
echo You should see 3-4 new terminal windows:
echo   1. Game State Watcher (monitors STS2)
echo   2. Backend API (http://127.0.0.1:8000)
echo   3. Frontend UI (floating window)
echo.
echo Features:
echo   - Real-time game state monitoring
echo   - WebSocket connection for live updates
echo   - Auto card evaluation
echo   - Always-on-top floating UI
echo.
echo Log files:
echo   - game_watcher.log
echo   - app.log
echo.
echo To stop: Close each terminal window or press Ctrl+C
echo.

timeout /t 2 /nobreak >nul

echo Game Watcher is searching for STS2 installation...
echo Start the game when ready!
echo.
