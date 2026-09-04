@echo off
REM ===========================================================================
REM  MT5 FOREX BOT - stop the backend and the dashboard.
REM  Open trading positions are NOT touched: closing them is a trading decision,
REM  so use the dashboard's "Close all positions" button for that.
REM ===========================================================================
setlocal
cd /d "%~dp0"

echo.
echo  Stopping the MT5 Forex Bot...
echo.

REM -- ask the backend to stop the strategy engine cleanly ---------------------
powershell -NoProfile -Command ^
  "try { Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/bot/stop' -TimeoutSec 5 | Out-Null; Write-Host '  [1/3] Strategy engine stopped.' } catch { Write-Host '  [1/3] Backend was not running.' }"

REM -- close the console windows started by start.bat --------------------------
taskkill /f /fi "WINDOWTITLE eq MT5 BOT - backend*"   >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq MT5 BOT - dashboard*" >nul 2>&1
echo  [2/3] Console windows closed.

REM -- free the ports in case something is still holding them ------------------
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do taskkill /f /pid %%p >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5173" ^| findstr "LISTENING"') do taskkill /f /pid %%p >nul 2>&1
echo  [3/3] Ports 8000 and 5173 released.

echo.
echo  MT5 Forex Bot stopped. Open positions were left untouched.
echo.
timeout /t 3 /nobreak >nul
endlocal
