@echo off
rem Start backend + frontend en opent de app in de browser.
cd /d "%~dp0"

rem Start niet nog een tweede server op dezelfde poort. Controleer bewust de
rem StudyGrasp-response (niet alleen of de poort bezet is).
powershell -NoProfile -Command "try { $r = Invoke-RestMethod 'http://127.0.0.1:8000/' -TimeoutSec 2; if ($r.service -eq 'StudyGrasp Backend v3') { exit 0 } }; exit 1" >nul 2>&1
if errorlevel 1 (
  start "StudyGrasp backend" cmd /k venv\Scripts\python.exe -m uvicorn backend:app --port 8000
) else (
  echo StudyGrasp backend draait al op poort 8000.
)

rem Frontend (no-cache server, zie frontend\serve.py). Ook hier geen duplicaat.
powershell -NoProfile -Command "try { $r = Invoke-WebRequest 'http://127.0.0.1:5173/' -TimeoutSec 2; if ($r.StatusCode -eq 200 -and $r.Content -match 'StudyGrasp') { exit 0 } }; exit 1" >nul 2>&1
if errorlevel 1 (
  start "StudyGrasp frontend" cmd /k venv\Scripts\python.exe frontend\serve.py 5173
) else (
  echo StudyGrasp frontend draait al op poort 5173.
)

timeout /t 2 /nobreak >nul
start http://localhost:5173
