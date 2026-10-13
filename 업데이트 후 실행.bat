@echo off
setlocal
cd /d "%~dp0"
echo [AI OFFICE] Checking for updates...
where git >nul 2>&1
if errorlevel 1 (
  echo Git is not installed. Starting existing version.
  pause
  call start.bat
  exit /b
)
if not exist ".git" (
  echo Git repository is not connected yet. Starting existing version.
  echo See UPDATE_GUIDE.md to connect once.
  pause
  call start.bat
  exit /b
)
git remote get-url origin >nul 2>&1
if errorlevel 1 (
  echo No remote repository connected. Starting existing version.
  pause
  call start.bat
  exit /b
)
git diff --quiet
if errorlevel 1 (
  echo Local changes detected. Update stopped to protect your work.
  pause
  call start.bat
  exit /b
)
git diff --cached --quiet
if errorlevel 1 (
  echo Staged changes detected. Update stopped.
  pause
  call start.bat
  exit /b
)
git pull --ff-only
if errorlevel 1 (
  echo Update failed or requires manual conflict resolution. Existing files retained.
  pause
) else (
  echo Update check completed.
)
call start.bat
