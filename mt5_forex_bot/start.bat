@echo off
REM ===========================================================================
REM  MT5 FOREX BOT - start the backend, the dashboard and the browser.
REM
REM  1. checks Python
REM  2. installs/verifies dependencies (first run creates a virtual env)
REM  3. starts the FastAPI backend
REM  4. starts the React dashboard
REM  5. opens the dashboard in the default browser
REM ===========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo  ==========================================================
echo    MT5 FOREX BOT - DEMO TRADING TERMINAL
echo  ==========================================================
echo.

REM -------------------------------------------------- 1. Python --------------
where python >nul 2>&1
if errorlevel 1 (
    echo  [X] Python was not found on the PATH.
    echo      Install Python 3.11 or newer from https://www.python.org/downloads/
    echo      and tick "Add python.exe to PATH" during the installation.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  [1/5] Python %PYVER% found.

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo  [X] Python 3.11 or newer is required (found %PYVER%).
    pause
    exit /b 1
)

REM -------------------------------------------------- 2. dependencies --------
if not exist ".venv" (
    echo  [2/5] Creating the virtual environment ^(first run only^)...
    python -m venv .venv
    if errorlevel 1 (
        echo  [X] Could not create the virtual environment.
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat

python -c "import fastapi, pandas, numpy, uvicorn, dotenv" >nul 2>&1
if errorlevel 1 (
    echo  [2/5] Installing Python dependencies...
    python -m pip install --upgrade pip >nul
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo  [X] Dependency installation failed.
        pause
        exit /b 1
    )
) else (
    echo  [2/5] Python dependencies OK.
)

python -c "import MetaTrader5" >nul 2>&1
if errorlevel 1 (
    echo  [!] The MetaTrader5 package is missing - installing it...
    python -m pip install MetaTrader5
)

if not exist ".env" (
    echo  [!] No .env file found - creating one from .env.example
    copy /y ".env.example" ".env" >nul
    echo      Open .env and set your MT5 DEMO account details, then run start.bat again.
)

REM -------------------------------------------------- 3. backend -------------
echo  [3/5] Starting the backend on http://127.0.0.1:8000 ...
start "MT5 BOT - backend" cmd /k "cd /d %~dp0 && call .venv\Scripts\activate.bat && python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"

REM Give uvicorn a moment to bind the port before the frontend proxies to it.
timeout /t 4 /nobreak >nul

REM -------------------------------------------------- 4. frontend ------------
where npm >nul 2>&1
if errorlevel 1 (
    echo  [!] npm was not found - skipping the dashboard.
    echo      Install Node.js 18+ from https://nodejs.org/ to use the UI.
    echo      The API stays available at http://127.0.0.1:8000/docs
    set DASHBOARD_URL=http://127.0.0.1:8000/docs
    goto :openbrowser
)

if not exist "frontend\node_modules" (
    echo  [4/5] Installing dashboard dependencies ^(first run only, may take a minute^)...
    pushd frontend
    call npm install
    popd
)

echo  [4/5] Starting the dashboard on http://localhost:5173 ...
start "MT5 BOT - dashboard" cmd /k "cd /d %~dp0frontend && npm run dev"
set DASHBOARD_URL=http://localhost:5173
timeout /t 6 /nobreak >nul

REM -------------------------------------------------- 5. browser -------------
:openbrowser
echo  [5/5] Opening %DASHBOARD_URL%
start "" "%DASHBOARD_URL%"

echo.
echo  ==========================================================
echo    Running in DEMO mode by default.
echo    Backend    : http://127.0.0.1:8000
echo    API docs   : http://127.0.0.1:8000/docs
echo    Dashboard  : %DASHBOARD_URL%
echo.
echo    Make sure MetaTrader 5 is running and logged into your
echo    DEMO account, with "Algo Trading" enabled.
echo.
echo    Close this window or run stop.bat to shut everything down.
echo  ==========================================================
echo.
pause
endlocal
