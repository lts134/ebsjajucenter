@echo off
chcp 65001 >nul
rem set_key.bat으로 저장한 환경변수(공급자·키·모델: ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY 등)를 지운다.
powershell -NoProfile -ExecutionPolicy Bypass -Command "foreach ($n in 'ANTHROPIC_API_KEY','ANTHROPIC_WORKSPACE_ID','CLAUDE_MODEL','LLM_MODEL','LLM_PROVIDER','OPENAI_API_KEY','OPENAI_BASE_URL','GEMINI_API_KEY','GEMINI_BASE_URL') { [Environment]::SetEnvironmentVariable($n, $null, 'User') }; Write-Host '지웠습니다. 앱을 다시 실행하면 키 없음(규칙 기반) 상태가 됩니다.'"
pause
