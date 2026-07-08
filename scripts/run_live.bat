@echo off
REM Start the alerts-only live loop (TSLA 5m Adaptive TP + Reversal).
REM Point Windows Task Scheduler at this to auto-start on trading days ~9:25 AM ET.
cd /d "%~dp0.."
python cli.py live
