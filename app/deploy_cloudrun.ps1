# Google Cloud Run에 올리기 (Windows PowerShell 판 — deploy_cloudrun.sh와 같은 동작)
# 사전: Google Cloud CLI 설치 + 로그인(gcloud init 또는 gcloud auth login). 결제가 연결된 프로젝트(Firebase Blaze와 같은 프로젝트여도 됨).
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
# gcloud는 안내문도 stderr로 내보내므로 PowerShell의 'Stop' 모드를 쓰지 않고 종료 코드로 성공·실패를 판단한다.
$ErrorActionPreference = "Continue"
function Step($msg) { Write-Host ""; Write-Host "== $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host ""; Write-Host "실패: $msg" -ForegroundColor Red; exit 1 }
if (-not $Bucket) { $Bucket = "$Project-ebs-history" }

Step "프로젝트 선택: $Project"
gcloud config set project $Project 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "프로젝트를 선택하지 못했습니다. 프로젝트 ID와 로그인 계정을 확인하세요." }

Step "필요한 API 사용 설정(처음 한 번, 1~2분)"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com storage.googleapis.com 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "API 사용 설정 실패. 프로젝트에 결제(Blaze)가 연결되어 있는지 확인하세요: Firebase 콘솔 → 프로젝트 → 요금제 업그레이드." }

Step "이력 복제용 버킷 확인: gs://$Bucket"
$exists = gcloud storage buckets list --project $Project --format="value(name)" --filter="name=$Bucket" 2>$null
if (-not $exists) {
  Write-Host "버킷이 없어 새로 만듭니다(공개 접근 차단)."
  gcloud storage buckets create "gs://$Bucket" --project $Project --location=$Region --uniform-bucket-level-access --public-access-prevention 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { Fail "버킷 생성 실패. 이름이 전 세계에서 유일해야 합니다. -Bucket 다른이름 으로 다시 시도하세요." }
} else { Write-Host "이미 있음." }

Step "Cloud Run 서비스 계정에 버킷 읽기·쓰기 권한"
$pn = (gcloud projects describe $Project --format="value(projectNumber)" 2>$null | Select-Object -First 1)
if (-not $pn) { Fail "프로젝트 번호를 읽지 못했습니다." }
$sa = "$pn-compute@developer.gserviceaccount.com"
gcloud storage buckets add-iam-policy-binding "gs://$Bucket" --member="serviceAccount:$sa" --role=roles/storage.objectAdmin 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "권한 부여에 실패했습니다(기본 서비스 계정이 아직 없을 수 있음). 배포 후 다시 한 번 이 스크립트를 실행하면 됩니다." -ForegroundColor Yellow }

Step "Cloud Run 서비스 에이전트에 컨테이너 저장소 읽기 권한(새 프로젝트에서 'Container import failed' 방지)"
$agent = "service-$pn@serverless-robot-prod.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding $Project --member="serviceAccount:$agent" --role=roles/artifactregistry.reader --condition=None 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "서비스 에이전트가 아직 만들어지지 않았을 수 있어 20초 기다린 뒤 다시 시도합니다." -ForegroundColor Yellow
  gcloud beta services identity create --service=run.googleapis.com --project=$Project 2>&1 | Out-Null
  Start-Sleep -Seconds 20
  gcloud projects add-iam-policy-binding $Project --member="serviceAccount:$agent" --role=roles/artifactregistry.reader --condition=None 2>&1 | Out-Null
}

Step "배포(소스에서 컨테이너 빌드, 3~6분). 질문이 나오면 y"
$envs = "LITESTREAM_REPLICA_URL=gcs://$Bucket/history,HISTORY_DB=/data/history.db,LLM_PROVIDER=anthropic,APP_PASSWORD=$AppPassword"
if ($AnthropicApiKey) { $envs += ",ANTHROPIC_API_KEY=$AnthropicApiKey" }
gcloud run deploy $Service --source . --region $Region --platform managed --allow-unauthenticated `
  --min-instances 0 --max-instances 1 --concurrency 40 --memory 1Gi --cpu 1 --timeout 3600 --session-affinity `
  --set-env-vars $envs
if ($LASTEXITCODE -ne 0) {
  Write-Host "첫 시도 실패. 새 프로젝트는 권한 전파에 시간이 걸리는 경우가 있어 30초 뒤 한 번 더 시도합니다." -ForegroundColor Yellow
  Start-Sleep -Seconds 30
  gcloud run deploy $Service --source . --region $Region --platform managed --allow-unauthenticated `
    --min-instances 0 --max-instances 1 --concurrency 40 --memory 1Gi --cpu 1 --timeout 3600 --session-affinity `
    --set-env-vars $envs
  if ($LASTEXITCODE -ne 0) { Fail "배포 실패. 위 출력의 마지막 ERROR 문구를 확인하세요." }
}

$url = gcloud run services describe $Service --region $Region --format="value(status.url)" 2>$null
Write-Host ""
Write-Host "완료. 접속 URL: $url" -ForegroundColor Green
Write-Host "접속 비밀번호: (지정한 값) · 접속자는 설정 → AI 연결에 자기 키를 넣습니다."
Write-Host "내리기: gcloud run services delete $Service --region $Region   (이력 복제본은 gs://$Bucket 에 남음)"
