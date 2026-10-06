#!/usr/bin/env bash
# 서버 모드(리눅스/맥): 여러 사람이 접속. 각자 설정 화면에 자기 키를 넣는다.
cd "$(dirname "$0")"
exec python3 -m streamlit run app.py --server.address 0.0.0.0 --server.port "${PORT:-8501}" --server.headless true --browser.gatherUsageStats false
