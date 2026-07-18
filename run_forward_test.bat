@echo off
REM Daily out-of-sample OPTIONS forward test (Databento shadow). Schedule ~T+1 after close, e.g. 9:00 AM ET.
cd /d C:\Users\TT\udb-orb-tsla
python forward_test.py >> exports\forward_test_runlog.txt 2>&1
