# 컨테이너로 올릴 때(Google Cloud Run 등). 로컬·사내 PC에서는 run.bat / run_server.bat를 쓰면 되고 이 파일은 필요 없다.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1 HISTORY_DB=/data/history.db
RUN mkdir -p /data
# Cloud Run은 PORT 환경변수로 포트를 준다. 0.0.0.0에 바인딩해야 밖에서 접속된다.
CMD ["sh", "-c", "python -m streamlit run app.py --server.address 0.0.0.0 --server.port ${PORT:-8080} --server.headless true --browser.gatherUsageStats false"]
