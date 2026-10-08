# 내 PC(현재 Windows 사용자 계정)에 AI 공급자(Anthropic·OpenAI 호환·Gemini)와 API 키를 환경변수로 저장한다. 혼자 쓰는 PC 전용: 이 계정으로 앱을 띄우는 사람은 모두 이 키로 호출한다.
# 저장되는 곳: 사용자 환경변수(레지스트리 HKCU\Environment). 앱 폴더·저장소에는 저장되지 않는다. 적용은 앱을 다시 실행해야 된다.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8   # 한글 깨짐 방지(콘솔 출력 UTF-8)
$pick = (Read-Host "AI 공급자 선택 — 1) Anthropic Claude(기본)  2) OpenAI 호환  3) Google Gemini  [1]").Trim()
switch ($pick) {
  "2" { $prov = "openai";    $keyVar = "OPENAI_API_KEY"; $hint = "OpenAI API 키(sk-...)";            $exModel = "gpt-5" }
  "3" { $prov = "gemini";    $keyVar = "GEMINI_API_KEY"; $hint = "Gemini API 키(Google AI Studio)";  $exModel = "gemini-2.5-pro" }
  default { $prov = "anthropic"; $keyVar = "ANTHROPIC_API_KEY"; $hint = "Anthropic API 키(sk-ant-...)"; $exModel = "claude-sonnet-4-6" }
}
$sec = Read-Host "$hint 입력 — 화면에 표시되지 않음" -AsSecureString
$key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
if (-not $key) { Write-Host "입력이 비어 있어 저장하지 않았습니다."; exit 1 }
$key = $key.Trim()
[Environment]::SetEnvironmentVariable($keyVar, $key, "User")
[Environment]::SetEnvironmentVariable("LLM_PROVIDER", $prov, "User")
if ($prov -eq "anthropic") {
  $ws = (Read-Host "워크스페이스 ID(wrkspc_...) — 키가 워크스페이스에 묶여 있지 않을 때만. 필요 없으면 Enter").Trim()
  if ($ws) { [Environment]::SetEnvironmentVariable("ANTHROPIC_WORKSPACE_ID", $ws, "User") }
} else {
  $base = (Read-Host "기본 주소 — 공식 API면 Enter, 사내 게이트웨이·Azure 호환 주소면 입력").Trim()
  if ($base) { [Environment]::SetEnvironmentVariable(($prov.ToUpper() + "_BASE_URL"), $base, "User") }
}
$model = (Read-Host "기본 모델(예: $exModel) — 비우면 자동 선택").Trim()
if ($model) { [Environment]::SetEnvironmentVariable("LLM_MODEL", $model, "User") }
$shown = $key.Substring(0, [Math]::Min(10, $key.Length)) + "... (" + $key.Length + "자)"
Write-Host ""
Write-Host "저장됨: LLM_PROVIDER = $prov"
Write-Host "저장됨: $keyVar = $shown"
if ($ws) { Write-Host "저장됨: ANTHROPIC_WORKSPACE_ID" }
if ($base) { Write-Host "저장됨: 기본 주소" }
if ($model) { Write-Host "저장됨: LLM_MODEL = $model" }
Write-Host ""
Write-Host "적용: 떠 있는 앱을 끄고 run.bat(또는 update.bat)을 다시 실행하세요. 설정 화면의 키 칸은 비워 두면 이 키로 동작합니다."
Write-Host "지우기: unset_key.bat"
