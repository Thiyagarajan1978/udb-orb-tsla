@echo off
REM Alerts-only live loop for the TRADED profiles (TSLA 5m ORB, close-stop).
REM
REM B1 and C1 run in ONE process: same symbol, same bars, different management. One process
REM means one Task Scheduler entry, one FMP fetch per cycle instead of two, and no chance of
REM one profile being silently dead while the other looks healthy.
REM
REM Task Scheduler: trigger weekly Mon-Fri ~9:25 AM ET, action = this file.
REM Starting late is safe -- the runner seeds from the DB and re-alerts nothing.
REM This system NEVER places broker orders; it only emails/webhooks alerts.
REM Output goes to a per-day log. Without this the runner's console is discarded, and the console
REM is where the correction machinery reports itself -- the `!! SUPERSEDED` lines that say FMP
REM revised a bar after we alerted on it. On 2026-08-12 two of those fired and nobody could see
REM them after the fact; the defect had to be reconstructed from the DB instead.
cd /d "%~dp0.."
if not exist logs mkdir logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i
python cli.py live --profiles B1,C1 >> "logs\live_%TODAY%.log" 2>&1
set RC=%ERRORLEVEL%
echo [run_live] exited rc=%RC% >> "logs\live_%TODAY%.log"
exit /b %RC%
