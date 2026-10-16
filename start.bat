@echo off
setlocal
cd /d "%~dp0"
set "LOG=%~dp0startup.log"
echo ========================================
echo AI OFFICE - Windows Launcher
echo ========================================
echo Log: "%LOG%"
echo [%date% %time%] Launcher started >> "%LOG%"
set "PYTHONUNBUFFERED=1"
set "PYEXE="
for %%P in (python.exe py.exe) do (
  if not defined PYEXE (
    where %%P >nul 2>&1
    if not errorlevel 1 set "PYEXE=%%P"
  )
)
if not defined PYEXE (
  echo [ERROR] Python command not found.
  echo [ERROR] Python command not found. >> "%LOG%"
  echo Please install Python 3.9 or higher and enable PATH.
  pause
  exit /b 1
)
echo Python command: %PYEXE%
%PYEXE% -c "import sys; assert sys.version_info >= (3,9), 'Python 3.9+ required'; print(sys.version)" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] Python not usable or version too old. See startup.log
  type "%LOG%"
  pause
  exit /b 1
)
echo Preparing selected local model...
%PYEXE% -u lmstudio_runtime.py >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [WARNING] Local model is not ready. See startup.log and AI OFFICE connection settings.
  echo [WARNING] Opening AI OFFICE so you can check the connection.
)
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8765/' -TimeoutSec 2; if ($r.StatusCode -eq 200 -and $r.Content -match '<title>AI OFFICE</title>') { Start-Process 'http://127.0.0.1:8765/'; exit 0 } } catch {}; exit 1"
if not errorlevel 1 exit /b 0
echo Starting local server at http://127.0.0.1:8765
echo Logs will be saved to startup.log
%PYEXE% -u app.py >> "%LOG%" 2>&1
set "RESULT=%ERRORLEVEL%"
echo [ERROR] Server exited with code %RESULT%.
echo [%date% %time%] Server exited with code %RESULT% >> "%LOG%"
type "%LOG%"
pause
exit /b %RESULT%
