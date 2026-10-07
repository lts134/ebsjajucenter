#!/usr/bin/env bash
# Google Cloud Run에 올리기(시연·심사용). 사전: gcloud 설치·로그인, 결제 연결된 프로젝트, Cloud Run·Cloud Build API 사용 설정.
# 사용: PROJECT=내-프로젝트 REGION=asia-northeast3 APP_PASSWORD=접속암호 ./deploy_cloudrun.sh
# 주의: 단일 인스턴스(--max-instances 1)라도 컨테이너가 재시작·재배포되면 /data의 SQLite 이력은 사라진다. 실사용은 사내 서버(run_server.bat) 또는 외부 DB 전환 필요.
set -euo pipefail
: "${PROJECT:?PROJECT 필요}"; REGION="${REGION:-asia-northeast3}"; SERVICE="${SERVICE:-ebs-request-agent}"
gcloud config set project "$PROJECT" >/dev/null
gcloud run deploy "$SERVICE" --source . --region "$REGION" --platform managed --allow-unauthenticated \
  --min-instances 1 --max-instances 1 --memory 1Gi --cpu 1 --timeout 900 --session-affinity \
  --set-env-vars "APP_PASSWORD=${APP_PASSWORD:-},LLM_PROVIDER=anthropic"
echo "접속 URL은 위 출력의 Service URL. 접속자는 설정 화면에 자기 API 키를 넣는다(서버에 공통 키를 두려면 --set-env-vars ANTHROPIC_API_KEY=... 추가)."
