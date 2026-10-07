#!/bin/sh
# 컨테이너 시작: 복제본이 있으면 이력 DB를 먼저 복원하고, Litestream이 Streamlit을 자식으로 띄워 변경을 계속 복제한다.
# 복제 저장소에 접근하지 못하면(권한·설정 문제) 복제 없이라도 앱을 띄운다 — 서비스가 아예 안 뜨는 것보다 낫지만, 그 상태에서는 이력이 보존되지 않으므로 로그에 크게 남긴다.
# LITESTREAM_REPLICA_URL이 없으면(로컬 Docker 테스트 등) 그냥 Streamlit만 띄운다.
: "${HISTORY_DB:=/data/history.db}"; export HISTORY_DB
mkdir -p "$(dirname "$HISTORY_DB")"
APP="python -m streamlit run app.py --server.address 0.0.0.0 --server.port ${PORT:-8080} --server.headless true --browser.gatherUsageStats false"
echo "[start] PORT=${PORT:-8080} HISTORY_DB=$HISTORY_DB REPLICA=${LITESTREAM_REPLICA_URL:-없음}"
if [ -n "$LITESTREAM_REPLICA_URL" ]; then
  if [ ! -f "$HISTORY_DB" ]; then
    if litestream restore -if-replica-exists -o "$HISTORY_DB" "$LITESTREAM_REPLICA_URL"; then
      [ -f "$HISTORY_DB" ] && echo "[start] 이력 DB 복원됨" || echo "[start] 복제본 없음 — 새 DB로 시작"
    else
      echo "[start] !!! 복제 저장소에 접근하지 못했습니다($LITESTREAM_REPLICA_URL). 권한(storage.objectAdmin)·버킷 이름을 확인하세요. 복제 없이 실행합니다 — 이력이 보존되지 않습니다 !!!"
      exec $APP
    fi
  fi
  echo "[start] Litestream 복제 시작 → $LITESTREAM_REPLICA_URL"
  exec litestream replicate -config "${LITESTREAM_CONFIG:-/app/litestream.yml}" -exec "$APP"
else
  exec $APP
fi
