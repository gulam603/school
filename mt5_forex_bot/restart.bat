@echo off
REM ===========================================================================
REM  MT5 FOREX BOT - stop everything, then start it again.
REM ===========================================================================
cd /d "%~dp0"

echo.
echo  Restarting the MT5 Forex Bot...
echo.

call stop.bat
timeout /t 3 /nobreak >nul
call start.bat
