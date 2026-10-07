# 클라우드 갱신 한 번에: 최신 코드 받기(git pull) → 빌드 → 배포. 비밀번호·키는 기존 값 유지.
# 사용(앱 폴더에서):  .\update_cloud.ps1 -Project ebs-test-a2c4e
#   비밀번호를 바꾸려면 -AppPassword 새비밀번호 를 붙인다.
param([Parameter(Mandatory=$true)][string]$Project, [string]$Region = "asia-northeast3", [string]$AppPassword = "")
git pull --ff-only
if ($LASTEXITCODE -ne 0) { Write-Host "git pull 실패 — 이 폴더의 파일을 직접 고친 적이 있으면 충돌일 수 있습니다." -ForegroundColor Red; exit 1 }
& "$PSScriptRoot\deploy_cloudrun.ps1" -Project $Project -Region $Region -AppPassword $AppPassword
