@echo off
chcp 65001 >nul 2>&1
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title Paid Assets Sync Setup

where py >nul 2>nul
if %errorlevel% neq 0 (
    echo [-] ERROR: Python is not installed or not in PATH.
    pause
    exit /b
)

py "%~dp0sync_assets.py" --setup

echo.
pause
