@echo off
REM build_exe.bat — 一键打包 STS2 Adviser 为可分发 EXE
REM 用法：双击运行，或在项目根目录执行 build_exe.bat

echo ======================================================
echo  STS2 Adviser — PyInstaller 打包脚本
echo ======================================================

REM ── 1. 创建 / 复用虚拟环境 ───────────────────────────────────────────────────
if not exist ".venv\Scripts\python.exe" (
    echo [1/4] 创建虚拟环境 .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [!] 创建虚拟环境失败，请确认 Python 3.10+ 已安装并在 PATH 中。
        pause
        exit /b 1
    )
) else (
    echo [1/4] 复用已有虚拟环境 .venv
)

REM ── 2. 安装生产依赖 + PyInstaller ─────────────────────────────────────────────
echo [2/4] 安装依赖（requirements-prod.txt + pyinstaller）...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet --upgrade -r requirements-prod.txt
.venv\Scripts\python.exe -m pip install --quiet --upgrade pyinstaller

if errorlevel 1 (
    echo [!] 依赖安装失败，请检查网络连接或 requirements-prod.txt。
    pause
    exit /b 1
)

REM ── 3. 清理旧构建，运行打包 ────────────────────────────────────────────────────
echo [3/4] 清理旧构建并运行 PyInstaller...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

.venv\Scripts\python.exe -m PyInstaller sts2_adviser.spec

if errorlevel 1 (
    echo [!] 打包失败，请检查上方错误信息。
    pause
    exit /b 1
)

REM ── 4. 完成提示 ────────────────────────────────────────────────────────────────
echo [4/4] 打包完成！
echo.
echo 输出目录：dist\sts2_adviser\
echo 运行文件：dist\sts2_adviser\sts2_adviser.exe
echo.

REM 显示输出目录大小
for /f "tokens=3" %%a in ('dir /s /a "dist\sts2_adviser" ^| find "个文件"') do set SIZE=%%a
echo 目录大小：约 %SIZE% 字节
echo.

where upx >nul 2>&1
if not errorlevel 1 (
    echo 提示：已检测到 UPX，spec 文件中 upx=True 将自动压缩二进制文件。
) else (
    echo 提示：未检测到 UPX。安装 UPX 并将其加入 PATH 可进一步压缩 EXE 体积。
    echo        下载：https://github.com/upx/upx/releases
)
echo.
echo 将整个 dist\sts2_adviser\ 文件夹压缩为 zip 分发给用户。
echo 用户解压后双击 sts2_adviser.exe 即可运行，无需安装 Python。
echo.
pause
