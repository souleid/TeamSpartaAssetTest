@echo off
chcp 65001 >nul
title [Team Member] Paid Assets Sync System

echo =======================================================
echo     [팀원용] 유료 에셋 최신 버전 다운로드 및 동기화
echo =======================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [-] 에러: 시스템에 Python이 설치되어 있지 않거나 PATH에 등록되지 않았습니다.
    pause
    exit /b
)

python "%~dp0sync_assets.py" --download

echo.
echo =======================================================
echo 업데이트가 완료되었습니다. 창을 닫으려면 아무 키나 누르세요.
echo =======================================================
pause