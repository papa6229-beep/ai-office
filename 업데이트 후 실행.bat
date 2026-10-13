@echo off
setlocal
cd /d "%~dp0"
echo [AI OFFICE] Checking for updates with backup and recovery...
set "PYEXE="
for %%P in (python.exe py.exe) do (
  if not defined PYEXE (
    where %%P >nul 2>&1
    if not errorlevel 1 set "PYEXE=%%P"
  )
)
if not defined PYEXE (
  echo Python was not found. Starting the installed version without updating.
  call start.bat
  exit /b
)
%PYEXE% updater.py apply
if errorlevel 1 (
  echo Update helper failed. Starting the installed version.
  call start.bat
)
