# 컨테이너로 올릴 때(Google Cloud Run 등). 로컬·사내 PC에서는 run.bat / run_server.bat를 쓰면 되고 이 파일은 필요 없다.
FROM python:3.11-slim
ARG LITESTREAM_VERSION=v0.3.13
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/* \
 && curl -sSL "https://github.com/benbjohnson/litestream/releases/download/${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-amd64.tar.gz" \
    | tar -xz -C /usr/local/bin litestream
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1 HISTORY_DB=/data/history.db
# Windows에서 받은 파일은 줄 끝이 CRLF로 바뀌어 '#!/bin/sh\r'를 찾다 실패하므로 빌드 때 LF로 되돌린다
RUN sed -i 's/\r$//' /app/start_cloud.sh /app/litestream.yml && mkdir -p /data && chmod +x /app/start_cloud.sh
# Cloud Run은 PORT 환경변수로 포트를 준다. LITESTREAM_REPLICA_URL(gcs://버킷/경로)이 있으면 이력 DB를 복원·복제한다.
CMD ["/app/start_cloud.sh"]
