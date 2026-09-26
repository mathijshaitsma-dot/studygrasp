@echo off
rem Start backend + frontend en opent de app in de browser.
cd /d "%~dp0"

rem Backend (als die al draait, sluit het extra venster gewoon)
start "StudyGrasp backend" cmd /k venv\Scripts\python.exe -m uvicorn backend:app --port 8000

rem Frontend (no-cache server, zie frontend\serve.py)
start "StudyGrasp frontend" cmd /k venv\Scripts\python.exe frontend\serve.py 5173

timeout /t 2 /nobreak >nul
start http://localhost:5173
