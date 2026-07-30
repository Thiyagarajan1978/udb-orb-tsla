@echo off
REM Daily out-of-sample OPTIONS forward test (Databento shadow). Schedule ~T+1 after close, e.g. 9:00 AM ET.
REM Exits 1 if either script failed, so Task Scheduler's "Last Run Result" shows the fault, and
REM appends every failure to exports\forward_test_FAILED.txt. A day whose OPRA data is not released
REM yet is NOT a failure -- both scripts stop cleanly and exit 0, and both are idempotent.
cd /d C:\Users\TT\udb-orb-tsla

set FAILED=

python forward_test.py >> exports\forward_test_runlog.txt 2>&1
if errorlevel 1 set FAILED=%FAILED% TSLA

python forward_test_spx.py >> exports\forward_spx_runlog.txt 2>&1
if errorlevel 1 set FAILED=%FAILED% SPX

if not "%FAILED%"=="" (
  echo %DATE% %TIME%  FAILED:%FAILED%  -- see exports\forward_test_runlog.txt / forward_spx_runlog.txt >> exports\forward_test_FAILED.txt
  echo FORWARD TEST FAILED:%FAILED%
  exit /b 1
)

echo forward test OK
exit /b 0
