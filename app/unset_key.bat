@echo off
rem set_key.bat으로 저장한 환경변수(ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID, CLAUDE_MODEL)를 지운다.
powershell -NoProfile -ExecutionPolicy Bypass -Command "foreach ($n in 'ANTHROPIC_API_KEY','ANTHROPIC_WORKSPACE_ID','CLAUDE_MODEL') { [Environment]::SetEnvironmentVariable($n, $null, 'User') }; Write-Host '지웠습니다. 앱을 다시 실행하면 키 없음(규칙 기반) 상태가 됩니다.'"
pause
