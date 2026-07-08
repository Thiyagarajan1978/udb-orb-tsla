@echo off
REM Usage: run_backtest.bat 2024-01-02 2024-12-31
cd /d "%~dp0.."
python cli.py backtest --start %1 --end %2
