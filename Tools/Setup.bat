@echo off
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

set /p GDRIVE_PATH=">> 경로 입력: "

if "%GDRIVE_PATH%"=="" set GDRIVE_PATH=G:\내 드라이브\Unreal7th_PaidAssets
set GDRIVE_PATH=%GDRIVE_PATH:"=%
set ESCAPED_PATH=%GDRIVE_PATH:\=\\%

echo { "gdrive_path": "%ESCAPED_PATH%" } > "%~dp0config.json"

echo.
echo -------------------------------------------------------
echo [Git Guard] git clean 시 유료 에셋 폴더 삭제 방지 가드를 등록합니다...
git config clean.exclude "Content/PaidAssets"
echo [Git Guard] 가드 등록 완료!
echo -------------------------------------------------------

echo.
echo =======================================================
echo [+] 설정이 성공적으로 완료되었습니다!
echo 설정된 경로: %GDRIVE_PATH%
echo 이제 UploadAssets.bat 또는 DownloadAssets.bat을 사용하세요.
echo =======================================================
echo.
pause