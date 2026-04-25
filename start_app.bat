@echo off
setlocal

echo.
echo    🎬 MOVIEFLIX - PREMIUM CINEMATIC EXPERIENCE 🎬
echo ==================================================
echo [1/2] Checking environment...

if not exist venv (
    echo [ERROR] Virtual environment 'venv' not found!
    echo Please create it or run 'python -m venv venv'
    pause
    exit /b 1
)

echo [2/2] Launching MovieFlix Premium (Port 8000)...
start "MovieFlix Backend" /min cmd /c ".\venv\Scripts\python -m uvicorn backend.main:app --port 8000 --reload"

echo ==================================================
echo ✅ SYSTEM OPERATIONAL
echo.
echo 🌐 Open your portal: http://localhost:8000
echo.
echo Keep this window open to monitor status. 
echo Press any key to stop the system...
echo ==================================================
pause

echo Stopping services...
taskkill /F /FI "WINDOWTITLE eq MovieFlix Backend" >nul 2>&1
echo Done.
