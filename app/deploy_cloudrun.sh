#!/usr/bin/env bash
# Google Cloud Run에 올리기 — 쓴 만큼만 내는 구성(요청 없을 때 0으로 줄어듦) + 이력은 Cloud Storage에 실시간 복제(Litestream)해 보존.
# 사전: gcloud 설치·로그인(gcloud auth login), 결제가 연결된 프로젝트(Firebase Blaze와 같은 프로젝트여도 됨).
# 사용: PROJECT=내-프로젝트 REGION=asia-northeast3 APP_PASSWORD=접속암호 ./deploy_cloudrun.sh
#   선택: BUCKET=버킷이름(기본 <프로젝트>-ebs-history), SERVICE=서비스이름, ANTHROPIC_API_KEY=공통키(두면 접속자 전원이 이 키로 호출)
set -euo pipefail
: "${PROJECT:?PROJECT 필요}"; REGION="${REGION:-asia-northeast3}"; SERVICE="${SERVICE:-ebs-request-agent}"; BUCKET="${BUCKET:-${PROJECT}-ebs-history}"
gcloud config set project "$PROJECT" >/dev/null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com storage.googleapis.com >/dev/null
# 1) 이력 복제용 버킷(없으면 생성). 공개 접근 차단.
if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
fi
# 2) Cloud Run 서비스 계정(기본 compute SA)에 버킷 읽기·쓰기 권한
PN=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
SA="${PN}-compute@developer.gserviceaccount.com"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA" --role=roles/storage.objectAdmin >/dev/null
# 2-b) Cloud Run 서비스 에이전트에 컨테이너 저장소 읽기 권한(새 프로젝트의 'Container import failed' 방지)
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:service-${PN}@serverless-robot-prod.iam.gserviceaccount.com" --role=roles/artifactregistry.reader --condition=None >/dev/null || true
# 3) 빌드와 배포를 나눈다(새 프로젝트의 'Container import failed' 회피)
REPO="cloud-run-source-deploy"
gcloud artifacts repositories describe "$REPO" --location="$REGION" >/dev/null 2>&1 || gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION"
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/$SERVICE:$(date +%Y%m%d-%H%M%S)"
gcloud builds submit --tag "$IMAGE" --region "$REGION" --timeout 1200 .
ENVS="LITESTREAM_REPLICA_URL=gcs://$BUCKET/history,HISTORY_DB=/data/history.db,LLM_PROVIDER=anthropic"
[ -n "${APP_PASSWORD:-}" ] && ENVS="$ENVS,APP_PASSWORD=$APP_PASSWORD"          # 생략하면 기존 비밀번호 유지
[ -n "${ANTHROPIC_API_KEY:-}" ] && ENVS="$ENVS,ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"
gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" --platform managed --allow-unauthenticated \
  --min-instances 0 --max-instances 1 --concurrency 40 --memory 1Gi --cpu 1 --cpu-boost --timeout 3600 --session-affinity \
  --update-env-vars "$ENVS"
echo "접속 URL: $(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo "내리기: gcloud run services delete $SERVICE --region $REGION   (이력 복제본은 gs://$BUCKET 에 남음)"
