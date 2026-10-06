@echo off
rem 내 PC에 API 키를 환경변수로 저장(혼자 쓰는 PC 전용). 자세한 내용은 set_key.ps1 주석 참고.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set_key.ps1"
pause
