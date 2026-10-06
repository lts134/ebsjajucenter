@echo off
rem 여러 사람이 접속하는 서버 모드: 같은 망의 PC에서 http://<이 PC 이름 또는 IP>:8501 로 접속. 각자 설정 화면에 자기 키를 넣는다.
cd /d "%~dp0"
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true --browser.gatherUsageStats false
pause
