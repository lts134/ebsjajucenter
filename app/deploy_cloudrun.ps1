# Google Cloud Run에 올리기 (Windows PowerShell 판 — deploy_cloudrun.sh와 같은 동작)
# 사전: Google Cloud CLI 설치 + gcloud auth login. 결제가 연결된 프로젝트.
# 사용(앱 폴더에서):  .\deploy_cloudrun.ps1 -Project 내-프로젝트-id -AppPassword 접속암호
#   선택: -Region asia-northeast3  -Bucket 버킷이름  -Service 서비스이름  -AnthropicApiKey 공통키(두면 접속자 전원이 이 키로 호출)
param(
  [Parameter(Mandatory=$true)][string]$Project,
  [string]$Region = "asia-northeast3",
  [string]$Service = "ebs-request-agent",
  [string]$Bucket = "",
  [string]$AppPassword = "",
  [string]$AnthropicApiKey = ""
)
$ErrorActionPreference = "Stop"
if (-not $Bucket) { $Bucket = "$Project-ebs-history" }
gcloud config set project $Project | Out-Null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com storage.googleapis.com | Out-Null
# 1) 이력 복제용 버킷(없으면 생성, 공개 접근 차단)
gcloud storage buckets describe "gs://$Bucket" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  gcloud storage buckets create "gs://$Bucket" --location=$Region --uniform-bucket-level-access --public-access-prevention
}
# 2) Cloud Run 서비스 계정에 버킷 읽기·쓰기 권한
$pn = gcloud projects describe $Project --format='value(projectNumber)'
$sa = "$pn-compute@developer.gserviceaccount.com"
gcloud storage buckets add-iam-policy-binding "gs://$Bucket" --member="serviceAccount:$sa" --role=roles/storage.objectAdmin | Out-Null
# 3) 배포
$envs = "LITESTREAM_REPLICA_URL=gcs://$Bucket/history,HISTORY_DB=/data/history.db,LLM_PROVIDER=anthropic,APP_PASSWORD=$AppPassword"
if ($AnthropicApiKey) { $envs += ",ANTHROPIC_API_KEY=$AnthropicApiKey" }
gcloud run deploy $Service --source . --region $Region --platform managed --allow-unauthenticated `
  --min-instances 0 --max-instances 1 --concurrency 40 --memory 1Gi --cpu 1 --timeout 3600 --session-affinity `
  --set-env-vars $envs
$url = gcloud run services describe $Service --region $Region --format='value(status.url)'
Write-Host ""
Write-Host "접속 URL: $url"
Write-Host "내리기: gcloud run services delete $Service --region $Region   (이력 복제본은 gs://$Bucket 에 남음)"
