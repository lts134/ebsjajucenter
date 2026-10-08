# 내 PC(현재 Windows 사용자 계정)에 API 키를 환경변수로 저장한다. 혼자 쓰는 PC 전용: 이 계정으로 앱을 띄우는 사람은 모두 이 키로 호출한다.
# 저장되는 곳: 사용자 환경변수(레지스트리 HKCU\Environment). 앱 폴더·저장소에는 저장되지 않는다. 적용은 앱을 다시 실행해야 된다.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8   # 한글 깨짐 방지(콘솔 출력 UTF-8)
$sec = Read-Host "Anthropic API 키(sk-ant-...) 입력 — 화면에 표시되지 않음" -AsSecureString
$key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
if (-not $key) { Write-Host "입력이 비어 있어 저장하지 않았습니다."; exit 1 }
$key = $key.Trim()
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", $key, "User")
$ws = (Read-Host "워크스페이스 ID(wrkspc_...) — 키가 워크스페이스에 묶여 있지 않을 때만. 필요 없으면 Enter").Trim()
if ($ws) { [Environment]::SetEnvironmentVariable("ANTHROPIC_WORKSPACE_ID", $ws, "User") }
$model = (Read-Host "기본 모델(예: claude-sonnet-4-6) — 비우면 자동 선택").Trim()
if ($model) { [Environment]::SetEnvironmentVariable("CLAUDE_MODEL", $model, "User") }
$shown = $key.Substring(0, [Math]::Min(10, $key.Length)) + "... (" + $key.Length + "자)"
Write-Host ""
Write-Host "저장됨: ANTHROPIC_API_KEY = $shown"
if ($ws) { Write-Host "저장됨: ANTHROPIC_WORKSPACE_ID" }
if ($model) { Write-Host "저장됨: CLAUDE_MODEL = $model" }
Write-Host ""
Write-Host "적용: 떠 있는 앱을 끄고 run.bat(또는 update.bat)을 다시 실행하세요. 설정 화면의 키 칸은 비워 두면 이 키로 동작합니다."
Write-Host "지우기: unset_key.bat"
