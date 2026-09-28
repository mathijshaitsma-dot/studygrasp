@echo off
setlocal
rem Start precies een backend en een frontend en open daarna pas de app.
cd /d "%~dp0"

set "PYTHON=%~dp0venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo StudyGrasp kan niet starten: de Python-omgeving ontbreekt.
  echo Verwacht bestand: %PYTHON%
  pause
  exit /b 1
)

rem Start niet nog een tweede server op dezelfde poort. Controleer bewust de
rem StudyGrasp-response (niet alleen of de poort bezet is).
powershell -NoProfile -Command "try { $r = Invoke-RestMethod 'http://127.0.0.1:8000/' -TimeoutSec 2; if ($r.service -eq 'StudyGrasp Backend v3') { exit 0 } }; exit 1" >nul 2>&1
if errorlevel 1 (
  echo Backend starten...
  powershell -NoProfile -Command "Start-Process -FilePath '%PYTHON%' -ArgumentList '-m','uvicorn','backend:app','--host','127.0.0.1','--port','8000' -WorkingDirectory '%~dp0' -WindowStyle Hidden"
) else (
  echo StudyGrasp backend draait al op poort 8000.
)

rem Frontend (no-cache server, zie frontend\serve.py). Ook hier geen duplicaat.
powershell -NoProfile -Command "try { $r = Invoke-WebRequest 'http://127.0.0.1:5173/' -TimeoutSec 2; if ($r.StatusCode -eq 200 -and $r.Content -match 'StudyGrasp') { exit 0 } }; exit 1" >nul 2>&1
if errorlevel 1 (
  echo Frontend starten...
  powershell -NoProfile -Command "Start-Process -FilePath '%PYTHON%' -ArgumentList 'frontend\serve.py','5173' -WorkingDirectory '%~dp0' -WindowStyle Hidden"
) else (
  echo StudyGrasp frontend draait al op poort 5173.
)

rem Wacht maximaal 20 seconden tot beide onderdelen echt reageren.
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(20); do { $b=$false; $f=$false; try { $r=Invoke-RestMethod 'http://127.0.0.1:8000/' -TimeoutSec 2; $b=$r.service -eq 'StudyGrasp Backend v3' } catch {}; try { $r=Invoke-WebRequest 'http://127.0.0.1:5173/' -UseBasicParsing -TimeoutSec 2; $f=$r.StatusCode -eq 200 -and $r.Content -match 'StudyGrasp' } catch {}; if (-not ($b -and $f)) { Start-Sleep -Milliseconds 500 } } until (($b -and $f) -or (Get-Date) -ge $deadline); if ($b -and $f) { exit 0 } else { exit 1 }"
if errorlevel 1 (
  echo.
  echo StudyGrasp kon niet volledig starten. Sluit oude StudyGrasp-vensters en probeer opnieuw.
  pause
  exit /b 1
)

echo StudyGrasp is klaar. De browser wordt geopend...
start http://localhost:5173
endlocal
