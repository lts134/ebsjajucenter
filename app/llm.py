"""Claude API 공통 호출. 앱의 모든 AI 호출은 이 모듈을 거친다.
- 키: 환경변수 ANTHROPIC_API_KEY (설정 화면에서 세션 한정 입력 가능). 워크스페이스에 묶이지 않은 키는 ANTHROPIC_WORKSPACE_ID도 필요. 없으면 available()이 False → 각 모듈이 규칙 기반으로 동작.
- 모델: CLAUDE_MODEL을 먼저 쓰고, 없거나 404(모델 없음)면 CANDIDATES 순서로 자동 대체. 성공한 모델은 프로세스 안에서 기억.
- ask_json: JSON만 요구하고 코드펜스·앞뒤 잡음을 제거해 파싱. 실패하면 한 번 더 "JSON만" 요청.
- LAST/LOG: 마지막·누적 호출의 모델·지연시간·토큰 (설정 화면·점검 보고서용).
AI는 수치를 계산하거나 사유를 추정하지 않는다는 원칙은 호출하는 쪽(extract/normalize/draft)의 프롬프트가 지킨다."""
import os, re, json, time, datetime as dt

# 우선순위 후보(앞에서부터). 2026-09-28 API 모델 목록 조회로 확인한 실존 ID. 설정 화면 '연결 테스트'에서 다시 조회해 고를 수 있다.
CANDIDATES = ["claude-sonnet-4-6", "claude-sonnet-5", "claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"]
_RESOLVED: str | None = None          # 이 프로세스에서 성공한 모델
LAST: dict = {}                       # 마지막 호출 정보
LOG: list[dict] = []                  # 누적 호출 정보

def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))

def _client():
    import anthropic
    return anthropic.Anthropic()

def _ws_kwargs(fn) -> dict:
    """워크스페이스에 묶이지 않은 키는 ANTHROPIC_WORKSPACE_ID(wrkspc_…)가 필요. SDK가 workspace_id 인자를 알면 그것으로, 아니면 헤더로."""
    ws = (os.environ.get("ANTHROPIC_WORKSPACE_ID") or "").strip()
    if not ws: return {}
    import inspect
    try: params = inspect.signature(fn).parameters
    except (TypeError, ValueError): params = {}
    return {"workspace_id": ws} if "workspace_id" in params else {"extra_headers": {"anthropic-workspace-id": ws}}

def explain_error(e: Exception) -> str:
    """API 오류를 담당자가 조치할 수 있는 말로."""
    m = str(e)
    if "anthropic-workspace-id" in m or "not scoped to a workspace" in m:
        return "이 키는 워크스페이스에 묶여 있지 않아 워크스페이스 ID가 필요합니다. 설정 화면의 '워크스페이스 ID'에 콘솔(console.anthropic.com → Settings → Workspaces)의 wrkspc_… 값을 넣거나, 워크스페이스 안에서 만든 키를 쓰세요."
    if "authentication" in m.lower() or "401" in m: return "키가 잘못됐거나 만료됐습니다(401)."
    if "credit" in m.lower() or "billing" in m.lower(): return "API 크레딧 잔액이 부족합니다. 콘솔(Plans & Billing)에서 크레딧을 충전한 뒤 다시 시도하세요. 키·워크스페이스 설정은 정상입니다."
    if "rate" in m.lower() and "limit" in m.lower(): return "호출 한도 초과(429). 잠시 후 다시 시도하세요."
    if "not_found" in m or "404" in m: return "모델명이 없습니다. 연결 테스트의 모델 목록에서 고르세요."
    return f"{type(e).__name__}: {m[:300]}"

def _model_order() -> list[str]:
    pref = (os.environ.get("CLAUDE_MODEL") or "").strip()
    order = ([_RESOLVED] if _RESOLVED else []) + ([pref] if pref else []) + CANDIDATES
    seen, out = set(), []
    for m in order:
        if m and m not in seen: seen.add(m); out.append(m)
    return out

def ask(prompt: str, system: str | None = None, max_tokens: int = 2000, purpose: str = "") -> str:
    """텍스트 응답. 모델 없음(404)이면 다음 후보로 넘어간다. 그 외 오류는 그대로 올린다."""
    global _RESOLVED
    import anthropic
    client = _client()
    last_err = None
    for model in _model_order():
        t0 = time.time()
        try:
            kw = dict(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}], **_ws_kwargs(client.messages.create))
            if system: kw["system"] = system
            msg = client.messages.create(**kw)
        except anthropic.NotFoundError as e:       # 모델명이 없음 → 다음 후보
            last_err = e; continue
        _RESOLVED = model
        text = "".join(getattr(b, "text", "") for b in msg.content)
        info = {"when": dt.datetime.now().strftime("%H:%M:%S"), "purpose": purpose, "model": model,
                "latency_s": round(time.time() - t0, 2),
                "input_tokens": getattr(msg.usage, "input_tokens", None), "output_tokens": getattr(msg.usage, "output_tokens", None)}
        LAST.clear(); LAST.update(info); LOG.append(info)
        return text
    raise RuntimeError(f"사용 가능한 모델을 찾지 못함(시도: {', '.join(_model_order())}). 설정 화면에서 연결 테스트로 모델을 고르세요. 마지막 오류: {last_err}")

def parse_json(raw: str, expect: str = "object"):
    """코드펜스·설명문이 섞여도 첫 JSON 객체/배열을 꺼낸다."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.S)
    o, c = ("{", "}") if expect == "object" else ("[", "]")
    i, j = s.find(o), s.rfind(c)
    if i < 0 or j < 0: raise ValueError(f"응답에 JSON {expect}가 없음: {raw[:120]!r}")
    return json.loads(s[i:j + 1])

def ask_json(prompt: str, system: str | None = None, max_tokens: int = 2000, expect: str = "object", purpose: str = ""):
    sys_ = (system + "\n\n" if system else "") + "출력은 JSON만. 설명·코드펜스·주석을 붙이지 말 것."
    raw = ask(prompt, sys_, max_tokens, purpose)
    try:
        return parse_json(raw, expect)
    except (ValueError, json.JSONDecodeError):
        raw2 = ask(prompt + "\n\n(앞선 응답이 JSON으로 해석되지 않았습니다. JSON만 다시 출력하세요.)", sys_, max_tokens, purpose + "(재시도)")
        return parse_json(raw2, expect)

def model_label() -> str:
    return _RESOLVED or os.environ.get("CLAUDE_MODEL") or CANDIDATES[0]

def list_models() -> list[dict]:
    """API 계정에서 쓸 수 있는 모델 목록(id, 표시명)."""
    client = _client()
    out = []
    for m in client.models.list(limit=100, **_ws_kwargs(client.models.list)):
        out.append({"id": m.id, "name": getattr(m, "display_name", m.id), "created": str(getattr(m, "created_at", ""))[:10]})
    return out

def test_connection() -> dict:
    """설정 화면용: 키·모델·왕복시간 확인. 실패 시 원인 문자열."""
    if not available(): return {"ok": False, "error": "ANTHROPIC_API_KEY 없음"}
    res = {"ok": False}
    try:
        res["models"] = list_models()
    except Exception as e:
        res["models_error"] = explain_error(e)
    try:
        txt = ask('다음을 JSON으로만 답하세요: {"ok": true}', max_tokens=20, purpose="연결 테스트")
        res.update(ok=True, model=LAST.get("model"), latency_s=LAST.get("latency_s"), reply=txt.strip()[:60])
    except Exception as e:
        res["error"] = explain_error(e)
    return res

def usage_summary() -> dict:
    n = len(LOG)
    return {"calls": n, "input_tokens": sum(x.get("input_tokens") or 0 for x in LOG),
            "output_tokens": sum(x.get("output_tokens") or 0 for x in LOG),
            "avg_latency_s": round(sum(x.get("latency_s") or 0 for x in LOG) / n, 2) if n else None}
