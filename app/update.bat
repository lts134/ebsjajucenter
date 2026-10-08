@echo off
chcp 65001 >nul
rem 최신 코드 받기 + 실행. GitHub에서 받은(git clone) 폴더에서만 동작. 이력 DB(storage/)와 설정은 그대로 유지된다.
cd /d "%~dp0"
where git >nul 2>nul || (echo Git이 설치돼 있지 않습니다. https://git-scm.com/download/win 에서 설치하거나 GitHub Desktop을 쓰세요. & pause & exit /b 1)
git pull --ff-only
if errorlevel 1 (
  echo.
  echo 업데이트에 실패했습니다. 이 폴더의 파일을 직접 고친 적이 있으면 충돌일 수 있습니다: git stash 후 다시 실행하거나, 새 폴더에 다시 clone 하세요.
  pause & exit /b 1
)
python start.py
pause
