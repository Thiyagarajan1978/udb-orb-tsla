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
cd /d "%~dp0.."
python cli.py live --profiles B1,C1
