# 컨테이너 이미지(Cloud Run · AWS App Runner/ECS · Azure Container Apps · 네이버 클라우드 · 사내 Docker 공통). 로컬·사내 PC에서는 run.bat / run_server.bat를 쓰면 되고 이 파일은 필요 없다.
FROM python:3.11-slim
ARG LITESTREAM_VERSION=v0.3.13
# 받은 파일의 SHA-256을 확인한다(공급망 변조 차단). 버전을 올리면 `curl -sSL <주소> | sha256sum` 으로 새 값을 넣는다
ARG LITESTREAM_SHA256=eb75a3de5cab03875cdae9f5f539e6aedadd66607003d9b1e7a9077948818ba0
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tzdata && rm -rf /var/lib/apt/lists/* \
 && curl -sSL -o /tmp/ls.tgz "https://github.com/benbjohnson/litestream/releases/download/${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-amd64.tar.gz" \
 && echo "${LITESTREAM_SHA256}  /tmp/ls.tgz" | sha256sum -c - \
 && tar -xz -C /usr/local/bin -f /tmp/ls.tgz litestream && rm /tmp/ls.tgz
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# 기록·회신 파일·백업은 /data(볼륨 또는 복제 대상). root가 아닌 사용자로 실행한다(컨테이너 탈출 피해 축소).
ENV PYTHONUNBUFFERED=1 APP_STORAGE_DIR=/data HISTORY_DB=/data/history.db TZ=Asia/Seoul HOME=/home/app
# Windows에서 받은 파일은 줄 끝이 CRLF로 바뀌어 '#!/bin/sh\r'를 찾다 실패하므로 빌드 때 LF로 되돌린다
RUN sed -i 's/\r$//' /app/start_cloud.sh /app/litestream.yml && chmod +x /app/start_cloud.sh \
 && useradd -m -u 10001 app && mkdir -p /data /app/storage && chown -R app:app /data /app/storage /app/.streamlit /app/sample_data /app/templates
USER app
# PORT 환경변수로 포트를 받는다(기본 8080). LITESTREAM_REPLICA_URL이 있으면 기록 DB를 복원·복제한다. 상태 확인: GET /_stcore/health
CMD ["/app/start_cloud.sh"]
