@echo off
chcp 65001 >nul
title [Git Master] Paid Assets Upload System

echo =======================================================
echo     [깃 마스터용] 유료 에셋 구글 드라이브 업로드 시스템
echo =======================================================
echo.

where py >nul 2>nul
if %errorlevel% neq 0 (
    echo [-] 에러: 시스템에 Python이 설치되어 있지 않거나 PATH에 등록되지 않았습니다.
    pause
    exit /b
)

py "%~dp0sync_assets.py" --upload

echo.
echo =======================================================
echo 작업을 완료했습니다. 창을 닫으려면 아무 키나 누르세요.
echo =======================================================
pause 