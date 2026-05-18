@echo off
chcp 65001 >nul
title Paid Assets Sync Setup

echo =======================================================
echo     유료 에셋 동기화 시스템 초기 설정 (Setup + Git Guard)
echo =======================================================
echo.
echo 본인의 PC에 마운트된 구글 드라이브 공유 폴더 경로를 입력하세요.
echo (윈도우 탐색기에서 복사한 경로를 그대로 붙여넣으셔도 됩니다.)
echo.
echo 기본값 (엔터 클릭 시): G:\내 드라이브\Unreal7th_PaidAssets
echo -------------------------------------------------------
echo.

:ASK_PATH
set "GDRIVE_PATH="
set /p GDRIVE_PATH=">> 경로 입력: "

:: 입력이 없으면 기본값으로 세팅
if "%GDRIVE_PATH%"=="" set "GDRIVE_PATH=G:\내 드라이브\Unreal7th_PaidAssets"

:: 1. 학생들이 "경로로 복사" 기능을 썼을 때 유입되는 앞뒤 쌍따옴표(")를 완벽히 제거
set "GDRIVE_PATH=%GDRIVE_PATH:"=%"

:: 2. 입력한 경로가 실제로 존재하는지 사전 검증 (오타/마운트 안 됨 조기 차단)
if not exist "%GDRIVE_PATH%\" (
    echo.
    echo [-] 경고: 입력한 경로를 찾을 수 없습니다.
    echo     입력값: %GDRIVE_PATH%
    echo     구글 드라이브가 마운트되어 있고 폴더가 실제로 존재하는지 확인하세요.
    echo.
    choice /c YN /m "그래도 이 경로로 진행하시겠습니까? (Y=진행, N=다시 입력)"
    if errorlevel 2 (
        echo.
        goto ASK_PATH
    )
)

:: 3. 모든 백슬래시(\)를 두 개(\\)로 자동 치환 (JSON 이스케이프)
set "ESCAPED_PATH=%GDRIVE_PATH:\=\\%"

:: 4. config.json을 UTF-8(No BOM)로 강제 저장
::    cmd의 echo는 콘솔 코드페이지에 따라 인코딩이 흔들려 한글이 깨질 수 있으므로
::    PowerShell로 명시적으로 UTF-8 저장하여 sync_assets.py(encoding="utf-8") 와 정합 보장
::    경로/특수문자 안전을 위해 환경변수로 전달
set "CFG_OUT=%~dp0config.json"
powershell -NoProfile -Command "$p = $env:ESCAPED_PATH; $json = '{ \"gdrive_path\": \"' + $p + '\" }'; [System.IO.File]::WriteAllText($env:CFG_OUT, $json, (New-Object System.Text.UTF8Encoding $false))"
if errorlevel 1 (
    echo [-] config.json 생성 실패. PowerShell 실행 권한을 확인하세요.
    pause
    exit /b 1
)

echo.
echo -------------------------------------------------------
echo [안내] Content/PaidAssets 폴더는 이미 .gitignore에 등록되어 있어
echo        일반 'git clean -df' 명령으로는 삭제되지 않습니다.
echo        다만 'git clean -xdf' (-x 옵션 포함) 는 ignored 파일까지 제거하므로
echo        절대 실행하지 마세요. 필요 시 다음 alias를 권장합니다:
echo            git clean -df --exclude=Content/PaidAssets
echo -------------------------------------------------------

echo.
echo =======================================================
echo [+] 설정이 성공적으로 완료되었습니다!
echo 설정된 경로: %GDRIVE_PATH%
echo 이제 UploadAssets.bat 또는 DownloadAssets.bat을 사용하세요.
echo =======================================================
echo.
pause