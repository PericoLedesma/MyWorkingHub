
@echo off
cd /d "%~dp0"

REM Kill any leftover process on port 8765 (stale instance)
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr /r " :8765 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
)

if not exist node_modules (
    echo Installing Electron for the first time (~150 MB, please wait...
    npm install
    if errorlevel 1 (
        echo.
        echo ERROR: npm install failed. Make sure Node.js is installed.
        echo Download it from https://nodejs.org
        pause
        exit /b 1
    )
)

npm start
