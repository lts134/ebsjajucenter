# Google Cloud Run에 올리기 (Windows PowerShell 판 — deploy_cloudrun.sh와 같은 동작)
# 사전: Google Cloud CLI 설치 + 로그인(gcloud init 또는 gcloud auth login). 결제가 연결된 프로젝트(Firebase Blaze와 같은 프로젝트여도 됨).
# 사용(앱 폴더에서):  .\deploy_cloudrun.ps1 -Project 내-프로젝트-id -AppPassword 접속암호
#   선택: -Region asia-northeast3  -Bucket 버킷이름  -Service 서비스이름  -AnthropicApiKey 공통키(두면 접속자 전원이 이 키로 호출)
#         -LlmProvider anthropic|openai|gemini (기본 공급자. 생략하면 기존 설정 유지, 처음이면 anthropic)  -OpenAIApiKey 키  -GeminiApiKey 키
param(
  [Parameter(Mandatory=$true)][string]$Project,
  [string]$Region = "asia-northeast3",
  [string]$Service = "ebs-request-agent",
  [string]$Bucket = "",
  [string]$AppPassword = "",
  [string]$AnthropicApiKey = "",
  [string]$LlmProvider = "",
  [string]$OpenAIApiKey = "",
  [string]$GeminiApiKey = ""
)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8   # 한글 깨짐 방지(콘솔 출력 UTF-8)
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
if ($LASTEXITCODE -ne 0) { Write-Host "권한 부여 실패($sa). 기본 서비스 계정이 아직 없을 수 있습니다. 배포 후 이 스크립트를 한 번 더 실행하세요." -ForegroundColor Yellow }
else { Write-Host "권한 부여됨: $sa" }

Step "Cloud Run 서비스 에이전트에 컨테이너 저장소 읽기 권한(새 프로젝트에서 'Container import failed' 방지)"
$agent = "service-$pn@serverless-robot-prod.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding $Project --member="serviceAccount:$agent" --role=roles/artifactregistry.reader --condition=None 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "서비스 에이전트가 아직 만들어지지 않았을 수 있어 20초 기다린 뒤 다시 시도합니다." -ForegroundColor Yellow
  gcloud beta services identity create --service=run.googleapis.com --project=$Project 2>&1 | Out-Null
  Start-Sleep -Seconds 20
  gcloud projects add-iam-policy-binding $Project --member="serviceAccount:$agent" --role=roles/artifactregistry.reader --condition=None 2>&1 | Out-Null
}

Step "컨테이너 빌드(Cloud Build, 3~5분)"
# 'gcloud run deploy --source'는 빌드와 배포를 한 번에 하지만, 새 프로젝트에서 'Container import failed'가 반복되는 사례가 있어
# 빌드(gcloud builds submit)와 배포(gcloud run deploy --image)를 나눈다. 이미지 참조가 단순해져 가져오기 실패를 피한다.
$repoName = "cloud-run-source-deploy"
$repoExists = gcloud artifacts repositories list --location=$Region --format="value(name)" --filter="name~$repoName" 2>$null
if (-not $repoExists) {
  gcloud artifacts repositories create $repoName --repository-format=docker --location=$Region 2>&1 | Out-Null
}
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$image = "$Region-docker.pkg.dev/$Project/$repoName/${Service}:$stamp"
gcloud builds submit --tag $image --region $Region --timeout 1200 .
if ($LASTEXITCODE -ne 0) { Fail "컨테이너 빌드 실패. 위 로그 링크의 마지막 부분을 확인하세요." }

Step "배포"
# 환경변수는 '갱신'만 한다(--update-env-vars): 다시 배포할 때 -AppPassword를 생략해도 이미 설정된 비밀번호·키가 유지된다.
$envs = "LITESTREAM_REPLICA_URL=gcs://$Bucket/history,HISTORY_DB=/data/history.db"
if ($AppPassword) { $envs += ",APP_PASSWORD=$AppPassword" } else { Write-Host "비밀번호를 지정하지 않아 기존 값을 유지합니다." }
if ($LlmProvider) { $envs += ",LLM_PROVIDER=$LlmProvider" }
if ($AnthropicApiKey) { $envs += ",ANTHROPIC_API_KEY=$AnthropicApiKey" }
if ($OpenAIApiKey) { $envs += ",OPENAI_API_KEY=$OpenAIApiKey" }
if ($GeminiApiKey) { $envs += ",GEMINI_API_KEY=$GeminiApiKey" }
gcloud run deploy $Service --image $image --region $Region --platform managed --allow-unauthenticated `
  --min-instances 0 --max-instances 1 --concurrency 40 --memory 1Gi --cpu 1 --cpu-boost --timeout 3600 --session-affinity `
  --update-env-vars $envs
if ($LASTEXITCODE -ne 0) {
  Write-Host ""
  Write-Host "배포 실패. 진단 정보:" -ForegroundColor Yellow
  gcloud run services describe $Service --region $Region --format="yaml(status.conditions)"
  Write-Host "--- 컨테이너 로그(최근 30분, 최신 순) ---"
  gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=$Service" --project $Project --limit 40 --format="value(timestamp,severity,textPayload)" --freshness=30m
  Fail "배포 실패. 위 진단 정보(status.conditions의 message)를 보내 주세요."
}

$url = gcloud run services describe $Service --region $Region --format="value(status.url)" 2>$null
Write-Host ""
Write-Host "완료. 접속 URL: $url" -ForegroundColor Green
Write-Host "접속 비밀번호: (지정한 값) · 접속자는 설정 → AI 연결에 자기 키를 넣습니다."
Write-Host "내리기: gcloud run services delete $Service --region $Region   (이력 복제본은 gs://$Bucket 에 남음)"
