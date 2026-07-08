@echo off
REM Launch the UDB-ORB TSLA dashboard on port 8080.
cd /d "%~dp0.."
streamlit run ui\app.py --server.port 8080 --server.headless true
