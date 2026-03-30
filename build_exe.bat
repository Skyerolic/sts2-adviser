@echo off
chcp 65001 >nul

echo ======================================================
echo  STS2 Adviser - PyInstaller Build Script
echo ======================================================

REM -- 1. Find Python 3.10+
echo [1/4] Locating Python ...
set PYTHON=

REM Try 'python' from PATH first
python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON=python
    goto :found_python
)

REM Try 'py' launcher (Windows Python Launcher)
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON=py -3
    goto :found_python
)

REM Search common per-user install locations
for %%V in (313 312 311 310) do (
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
        set PYTHON="%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
        goto :found_python
    )
)

REM Search common system-wide install locations
for %%V in (313 312 311 310) do (
    if exist "C:\Python%%V\python.exe" (
        set PYTHON="C:\Python%%V\python.exe"
        goto :found_python
    )
)

echo [!] Python 3.10+ not found. Please install Python and re-run this script.
echo     Download: https://www.python.org/downloads/
pause
exit /b 1

:found_python
echo     Found: %PYTHON%

REM -- 2. Create or reuse virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo [2/4] Creating virtual environment .venv ...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo [!] Failed to create venv.
        pause
        exit /b 1
    )
) else (
    echo [2/4] Reusing existing .venv
)

REM -- 3. Install production deps + PyInstaller
echo [3/4] Installing dependencies ...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet --upgrade -r requirements-prod.txt
.venv\Scripts\python.exe -m pip install --quiet --upgrade pyinstaller

if errorlevel 1 (
    echo [!] Dependency installation failed.
    pause
    exit /b 1
)

REM -- 4. Clean old build, run PyInstaller
echo [4/4] Running PyInstaller ...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

.venv\Scripts\python.exe -m PyInstaller sts2_adviser.spec

if errorlevel 1 (
    echo [!] Build failed. Check the output above for errors.
    pause
    exit /b 1
)

echo.
echo Build complete!
echo Output : dist\sts2_adviser\
echo Run    : dist\sts2_adviser\sts2_adviser.exe
echo.
echo Tip: zip the entire dist\sts2_adviser\ folder for distribution.
echo.
pause
