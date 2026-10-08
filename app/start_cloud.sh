#!/bin/sh
# 컨테이너 시작(Cloud Run·AWS·Azure·네이버 클라우드·사내 Docker 공통): 복제본이 있으면 기록 DB를 먼저 복원하고, Litestream이 Streamlit을 자식으로 띄워 변경을 계속 복제한다.
# 복제 저장소에 접근하지 못하면(권한·설정 문제) 시작하지 않는다(fail-closed) — 빈 DB로 뜨면 그 사이 작업이 사라지고 다음 복원 때 덮어쓸 위험이 있다.
# LITESTREAM_REPLICA_URL이 없으면(볼륨을 붙인 사내 서버, 로컬 Docker 테스트 등) 그냥 Streamlit만 띄운다.
#   복제 대상 예: gcs://버킷/history · s3://버킷/history(AWS) · s3://버킷/history + LITESTREAM_ENDPOINT=https://kr.object.ncloudstorage.com(네이버 클라우드·MinIO 등 S3 호환)
#               abs://계정/컨테이너/history(Azure Blob) · file:///mnt/backup/history(공유 디스크)
#   S3 호환 자격증명은 LITESTREAM_ACCESS_KEY_ID / LITESTREAM_SECRET_ACCESS_KEY, Azure는 LITESTREAM_AZURE_ACCOUNT_KEY 환경변수로.
: "${APP_STORAGE_DIR:=/data}"; export APP_STORAGE_DIR
: "${HISTORY_DB:=$APP_STORAGE_DIR/history.db}"; export HISTORY_DB
mkdir -p "$(dirname "$HISTORY_DB")"
umask 077                                                        # 기록 DB·회신·백업 파일은 실행 계정만 읽게
# 서버에서는 처리되지 않은 오류의 상세(코드 경로·값)를 화면에 내지 않고 종류만 보인다(상세는 서버 로그). 로컬 PC는 config.toml 기본(full)
APP="python -m streamlit run app.py --server.address 0.0.0.0 --server.port ${PORT:-8080} --server.headless true --browser.gatherUsageStats false --client.showErrorDetails ${STREAMLIT_ERROR_DETAILS:-type}"
echo "[start] PORT=${PORT:-8080} STORAGE=$APP_STORAGE_DIR HISTORY_DB=$HISTORY_DB REPLICA=${LITESTREAM_REPLICA_URL:-없음}"
if [ -n "$LITESTREAM_REPLICA_URL" ]; then
  if [ ! -f "$HISTORY_DB" ]; then
    if litestream restore -if-replica-exists -o "$HISTORY_DB" "$LITESTREAM_REPLICA_URL"; then
      [ -f "$HISTORY_DB" ] && echo "[start] 기록 DB 복원됨" || echo "[start] 복제본 없음 — 새 DB로 시작"
    else
      echo "[start] !!! 복제 저장소에 접근하지 못했습니다($LITESTREAM_REPLICA_URL). 자격증명·버킷 이름·엔드포인트를 확인하세요. 빈 DB로 띄우면 작업 내용이 사라지므로 시작하지 않습니다 !!!"
      exit 1
    fi
  fi
  CFG="${LITESTREAM_CONFIG:-}"
  if [ -z "$CFG" ]; then                                          # 환경변수로 설정 파일을 만든다(엔드포인트·경로 방식은 있을 때만 적는다)
    CFG="/tmp/litestream.yml"
    {
      echo "dbs:"; echo "  - path: $HISTORY_DB"; echo "    replicas:"; echo "      - url: $LITESTREAM_REPLICA_URL"
      [ -n "${LITESTREAM_ENDPOINT:-}" ] && echo "        endpoint: $LITESTREAM_ENDPOINT"
      [ "${LITESTREAM_PATH_STYLE:-}" = "true" ] && echo "        force-path-style: true"
      echo "        sync-interval: 1s"; echo "        retention: ${LITESTREAM_RETENTION:-720h}"
    } > "$CFG"
  fi
  echo "[start] Litestream 복제 시작 → $LITESTREAM_REPLICA_URL"
  exec litestream replicate -config "$CFG" -exec "$APP"
else
  exec $APP
fi
