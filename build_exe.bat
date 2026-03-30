@echo off
chcp 65001 >nul

echo ======================================================
echo  STS2 Adviser - PyInstaller Build Script
echo ======================================================

REM -- 1. Create or reuse virtual environment
REM    Try 'python' first; fall back to known install path if not in PATH.
if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating virtual environment .venv ...
    python -m venv .venv 2>nul
    if errorlevel 1 (
        echo     'python' not in PATH, trying known install location ...
        "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" -m venv .venv
        if errorlevel 1 (
            echo [!] Failed to create venv. Python 3.10 not found.
            pause
            exit /b 1
        )
    )
) else (
    echo [1/4] Reusing existing .venv
)

REM -- 2. Install production deps + PyInstaller
echo [2/4] Installing dependencies ...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet --upgrade -r requirements-prod.txt
.venv\Scripts\python.exe -m pip install --quiet --upgrade pyinstaller

if errorlevel 1 (
    echo [!] Dependency installation failed.
    pause
    exit /b 1
)

REM -- 3. Clean old build, run PyInstaller
echo [3/4] Running PyInstaller ...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

.venv\Scripts\python.exe -m PyInstaller sts2_adviser.spec

if errorlevel 1 (
    echo [!] Build failed. Check the output above for errors.
    pause
    exit /b 1
)

REM -- 4. Done
echo [4/4] Build complete!
echo.
echo Output : dist\sts2_adviser\
echo Run    : dist\sts2_adviser\sts2_adviser.exe
echo.
echo Tip: zip the entire dist\sts2_adviser\ folder for distribution.
echo      Users can run sts2_adviser.exe without installing Python.
echo.
pause
