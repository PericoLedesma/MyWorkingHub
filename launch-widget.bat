
@echo off
cd /d "%~dp0"

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
