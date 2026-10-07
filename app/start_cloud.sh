#!/bin/sh
# 컨테이너 시작: 복제본이 있으면 이력 DB를 먼저 복원하고, Litestream이 Streamlit을 자식으로 띄워 변경을 계속 복제한다.
# LITESTREAM_REPLICA_URL이 없으면(로컬 Docker 테스트 등) 그냥 Streamlit만 띄운다.
set -e
: "${HISTORY_DB:=/data/history.db}"; export HISTORY_DB
mkdir -p "$(dirname "$HISTORY_DB")"
APP="python -m streamlit run app.py --server.address 0.0.0.0 --server.port ${PORT:-8080} --server.headless true --browser.gatherUsageStats false"
if [ -n "$LITESTREAM_REPLICA_URL" ]; then
  if [ ! -f "$HISTORY_DB" ]; then
    litestream restore -if-replica-exists -o "$HISTORY_DB" "$LITESTREAM_REPLICA_URL" || echo "복원 실패 — 새 DB로 시작"
    [ -f "$HISTORY_DB" ] && echo "이력 DB 복원됨: $HISTORY_DB" || echo "복제본 없음 — 새 DB로 시작"
  fi
  exec litestream replicate -config "${LITESTREAM_CONFIG:-/app/litestream.yml}" -exec "$APP"
else
  exec $APP
fi
