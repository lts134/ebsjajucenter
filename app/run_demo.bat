@echo off
rem 시연 모드: 화면에 샘플 파일 선택칸이 보인다(APP_DEMO=1). 실제 사용은 run.bat
cd /d "%~dp0"
set APP_DEMO=1
python start.py
pause
