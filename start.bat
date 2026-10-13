@echo off
setlocal
cd /d "%~dp0"
set "LOG=%~dp0startup.log"
echo ========================================
echo AI OFFICE - Windows Launcher
echo ========================================
echo Log: "%LOG%"
echo [%date% %time%] Launcher started > "%LOG%"
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
echo Starting local server at http://127.0.0.1:8765
echo Logs will be saved to startup.log
%PYEXE% -u app.py >> "%LOG%" 2>&1
set "RESULT=%ERRORLEVEL%"
echo [ERROR] Server exited with code %RESULT%.
echo [%date% %time%] Server exited with code %RESULT% >> "%LOG%"
type "%LOG%"
pause
exit /b %RESULT%
