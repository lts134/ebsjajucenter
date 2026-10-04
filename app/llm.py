"""Claude API 공통 호출. 앱의 모든 AI 호출은 이 모듈을 거친다.
- 키: 환경변수 ANTHROPIC_API_KEY (설정 화면에서 세션 한정 입력 가능). 워크스페이스에 묶이지 않은 키는 ANTHROPIC_WORKSPACE_ID도 필요. 없으면 available()이 False → 각 모듈이 규칙 기반으로 동작.
- 모델: CLAUDE_MODEL을 먼저 쓰고, 없거나 404(모델 없음)면 CANDIDATES 순서로 자동 대체. 성공한 모델은 프로세스 안에서 기억.
- ask_json: JSON 스키마를 주면 API의 구조화 출력(output_config.format)으로 형식을 보장받는다. 모델이 지원하지 않으면(400) 텍스트 응답을 파싱하는 방식으로 자동 대체.
  코드펜스·앞뒤 잡음을 제거해 파싱하고, 실패하면 한 번 더 "JSON만" 요청.
- 재시도: SDK가 429·5xx·네트워크 오류를 지수 백오프로 재시도(MAX_RETRIES). 요청 타임아웃 TIMEOUT_S.
- LAST/LOG: 마지막·누적 호출의 모델·지연시간·토큰·추정 비용 (설정 화면·점검 보고서용).
AI는 수치를 계산하거나 사유를 추정하지 않는다는 원칙은 호출하는 쪽(extract/normalize/draft)의 프롬프트가 지킨다."""
import os, re, json, time, datetime as dt

# 우선순위 후보(앞에서부터). 2026-09-28 API 모델 목록 조회로 확인한 실존 ID. 설정 화면 '연결 테스트'에서 다시 조회해 고를 수 있다.
CANDIDATES = ["claude-sonnet-4-6", "claude-sonnet-5", "claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"]
MAX_RETRIES = 3          # SDK 자동 재시도 횟수(429·5xx·연결 오류)
TIMEOUT_S = 90.0         # 요청 1건 타임아웃(초)

# 모델별 단가(USD / 100만 토큰, 입력·출력). 출처: Anthropic 공개 가격표(2026-09-25 기준 캐시). 실제 청구액은 콘솔 Usage에서 확인 [확인 필요].
PRICING = {
    "claude-sonnet-4-6": (3.00, 15.00), "claude-sonnet-5": (2.00, 10.00), "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00), "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-5-5": (4.00, 20.00), "claude-opus-5": (5.00, 25.00), "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00), "claude-opus-4-6": (5.00, 25.00),
}
_RESOLVED: str | None = None          # 이 프로세스에서 성공한 모델
LAST: dict = {}                       # 마지막 호출 정보
LOG: list[dict] = []                  # 누적 호출 정보
_NO_STRUCTURED: set[str] = set()      # 구조화 출력을 거부한 모델(400) — 이후엔 텍스트 파싱으로

def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))

def _client():
    import anthropic
    return anthropic.Anthropic(max_retries=MAX_RETRIES, timeout=TIMEOUT_S)

def _ws_kwargs(fn) -> dict:
    """워크스페이스에 묶이지 않은 키는 ANTHROPIC_WORKSPACE_ID(wrkspc_…)가 필요. SDK가 workspace_id 인자를 알면 그것으로, 아니면 헤더로."""
    ws = (os.environ.get("ANTHROPIC_WORKSPACE_ID") or "").strip()
    if not ws: return {}
    import inspect
    try: params = inspect.signature(fn).parameters
    except (TypeError, ValueError): params = {}
    return {"workspace_id": ws} if "workspace_id" in params else {"extra_headers": {"anthropic-workspace-id": ws}}

def explain_error(e: Exception) -> str:
    """API 오류를 담당자가 조치할 수 있는 말로. SDK 예외 클래스를 우선 보고, 없으면 메시지로 판단."""
    m = str(e)
    if "anthropic-workspace-id" in m or "not scoped to a workspace" in m:      # 400으로도 403으로도 올 수 있음
        return "이 키는 워크스페이스에 묶여 있지 않아 워크스페이스 ID가 필요합니다. 설정 화면의 '워크스페이스 ID'에 콘솔(Settings → Workspaces)의 wrkspc_… 값을 넣거나, 워크스페이스 안에서 만든 키를 쓰세요."
    try:
        import anthropic
        if isinstance(e, anthropic.AuthenticationError): return "키가 잘못됐거나 만료됐습니다(401). 콘솔에서 키를 다시 확인하세요."
        if isinstance(e, anthropic.PermissionDeniedError):
            if "anthropic-workspace-id" in m or "not scoped to a workspace" in m:
                return "이 키는 워크스페이스에 묶여 있지 않아 워크스페이스 ID가 필요합니다. 설정 화면의 '워크스페이스 ID'에 콘솔(Settings → Workspaces)의 wrkspc_… 값을 넣거나, 워크스페이스 안에서 만든 키를 쓰세요."
            return "이 키로는 허용되지 않는 요청입니다(403). 키 권한·워크스페이스 설정을 확인하세요."
        if isinstance(e, anthropic.NotFoundError): return "모델명이 없습니다(404). 연결 테스트의 모델 목록에서 고르세요."
        if isinstance(e, anthropic.RateLimitError): return "호출 한도 초과(429). 잠시 후 다시 시도하세요(자동 재시도 후에도 실패)."
        if isinstance(e, anthropic.BadRequestError):
            if "credit" in m.lower() or "billing" in m.lower():
                return "API 크레딧 잔액이 부족합니다. 콘솔(Plans & Billing)에서 충전한 뒤 다시 시도하세요. 키·워크스페이스 설정은 정상입니다."
            return f"요청 형식 오류(400): {m[:200]}"
        if isinstance(e, anthropic.APIStatusError) and e.status_code >= 500: return f"Anthropic 서버 오류({e.status_code}). 자동 재시도 후에도 실패했습니다. 잠시 후 다시 시도하세요."
        if isinstance(e, anthropic.APITimeoutError): return f"응답 시간 초과({TIMEOUT_S:.0f}초). 네트워크 상태를 확인하거나 다시 시도하세요."
        if isinstance(e, anthropic.APIConnectionError): return "API 서버에 연결하지 못했습니다. 사내망 프록시·방화벽에서 api.anthropic.com 허용 여부를 확인하세요."
    except ImportError:
        pass
    if "anthropic-workspace-id" in m or "not scoped to a workspace" in m:
        return "이 키는 워크스페이스에 묶여 있지 않아 워크스페이스 ID가 필요합니다. 설정 화면에서 wrkspc_… 값을 넣으세요."
    if "credit" in m.lower() or "billing" in m.lower(): return "API 크레딧 잔액이 부족합니다. 콘솔(Plans & Billing)에서 충전하세요."
    return f"{type(e).__name__}: {m[:300]}"

def _model_order() -> list[str]:
    """사용자가 고른 CLAUDE_MODEL이 가장 먼저, 다음은 이 프로세스에서 성공한 모델, 그다음 후보 순."""
    pref = (os.environ.get("CLAUDE_MODEL") or "").strip()
    order = ([pref] if pref else []) + ([_RESOLVED] if _RESOLVED else []) + CANDIDATES
    seen, out = set(), []
    for m in order:
        if m and m not in seen: seen.add(m); out.append(m)
    return out

def _record(msg, model: str, t0: float, purpose: str, structured: bool) -> str:
    text = "".join(getattr(b, "text", "") for b in msg.content)
    inp, out = getattr(msg.usage, "input_tokens", None), getattr(msg.usage, "output_tokens", None)
    info = {"when": dt.datetime.now().strftime("%H:%M:%S"), "purpose": purpose, "model": model,
            "latency_s": round(time.time() - t0, 2), "input_tokens": inp, "output_tokens": out,
            "structured": structured, "stop_reason": getattr(msg, "stop_reason", None),
            "cost_usd": estimate_cost(model, inp, out)}
    LAST.clear(); LAST.update(info); LOG.append(info)
    return text

def ask(prompt: str, system: str | None = None, max_tokens: int = 2000, purpose: str = "", schema: dict | None = None) -> str:
    """텍스트 응답. 모델 없음(404)이면 다음 후보로. schema가 있으면 구조화 출력으로 JSON 형식을 보장받고,
    모델이 거부(400)하면 그 모델은 이후 텍스트 방식으로 호출한다. 그 외 오류는 그대로 올린다."""
    global _RESOLVED
    import anthropic
    client = _client()
    last_err = None
    for model in _model_order():
        t0 = time.time()
        use_struct = schema is not None and model not in _NO_STRUCTURED
        kw = dict(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}], **_ws_kwargs(client.messages.create))
        if system: kw["system"] = system
        if use_struct: kw["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        try:
            msg = client.messages.create(**kw)
        except anthropic.NotFoundError as e:       # 모델명이 없음 → 다음 후보
            last_err = e; continue
        except (anthropic.BadRequestError, TypeError) as e:
            # 이 모델(또는 SDK)이 구조화 출력을 모름 → 같은 모델로 텍스트 방식 재시도. 그 외 400은 그대로 올림
            if use_struct and ("output_config" in str(e) or "format" in str(e) or isinstance(e, TypeError)):
                _NO_STRUCTURED.add(model); kw.pop("output_config", None); t0 = time.time()
                msg = client.messages.create(**kw); use_struct = False
            else:
                raise
        _RESOLVED = model
        return _record(msg, model, t0, purpose, use_struct)
    raise RuntimeError(f"사용 가능한 모델을 찾지 못함(시도: {', '.join(_model_order())}). 설정 화면에서 연결 테스트로 모델을 고르세요. 마지막 오류: {last_err}")

def parse_json(raw: str, expect: str = "object"):
    """코드펜스·설명문이 섞여도 첫 JSON 객체/배열을 꺼낸다."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.S)
    o, c = ("{", "}") if expect == "object" else ("[", "]")
    i, j = s.find(o), s.rfind(c)
    if i < 0 or j < 0: raise ValueError(f"응답에 JSON {expect}가 없음: {raw[:120]!r}")
    return json.loads(s[i:j + 1])

def ask_json(prompt: str, system: str | None = None, max_tokens: int = 2000, expect: str = "object", purpose: str = "", schema: dict | None = None):
    """JSON 응답. schema(JSON Schema, 객체 루트)가 있으면 구조화 출력을 쓴다. expect="array"면 schema 없이 텍스트 파싱."""
    sys_ = (system + "\n\n" if system else "") + "출력은 JSON만. 설명·코드펜스·주석을 붙이지 말 것."
    raw = ask(prompt, sys_, max_tokens, purpose, schema if expect == "object" else None)
    try:
        return parse_json(raw, expect)
    except (ValueError, json.JSONDecodeError):
        raw2 = ask(prompt + "\n\n(앞선 응답이 JSON으로 해석되지 않았습니다. JSON만 다시 출력하세요.)", sys_, max_tokens, purpose + "(재시도)")
        return parse_json(raw2, expect)

def model_label() -> str:
    return (os.environ.get("CLAUDE_MODEL") or "").strip() or _RESOLVED or CANDIDATES[0]

def estimate_cost(model: str | None, input_tokens, output_tokens) -> float | None:
    """단가표에 있는 모델만 추정(USD). 없으면 None."""
    p = PRICING.get(model or "")
    if not p or input_tokens is None or output_tokens is None: return None
    return round(input_tokens / 1e6 * p[0] + output_tokens / 1e6 * p[1], 6)

def list_models() -> list[dict]:
    """API 계정에서 쓸 수 있는 모델 목록(id, 표시명, 단가)."""
    client = _client()
    out = []
    for m in client.models.list(limit=100, **_ws_kwargs(client.models.list)):
        p = PRICING.get(m.id)
        out.append({"id": m.id, "name": getattr(m, "display_name", m.id), "created": str(getattr(m, "created_at", ""))[:10],
                    "입력 $/1M": p[0] if p else None, "출력 $/1M": p[1] if p else None})
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
    costs = [x.get("cost_usd") for x in LOG]
    return {"calls": n, "input_tokens": sum(x.get("input_tokens") or 0 for x in LOG),
            "output_tokens": sum(x.get("output_tokens") or 0 for x in LOG),
            "avg_latency_s": round(sum(x.get("latency_s") or 0 for x in LOG) / n, 2) if n else None,
            "cost_usd": round(sum(c for c in costs if c is not None), 4) if n else 0.0,
            "cost_unknown_calls": sum(1 for c in costs if c is None),
            "structured_calls": sum(1 for x in LOG if x.get("structured"))}

def run_tools(prompt: str, system: str, tools: list[dict], handlers: dict, max_turns: int = 8, max_tokens: int = 2000, purpose: str = "도구 질의") -> dict:
    """도구 호출 루프(수동). Claude가 tools 중 하나를 고르면 handlers[name](**input)을 실행해 결과를 돌려주고, 더 이상 호출이 없으면 최종 답을 반환.
    반환: {"text": 최종 답, "trace": [{"tool", "input", "result", "rows"}...], "turns": n}. 모델 없음(404)이면 다음 후보로."""
    global _RESOLVED
    import anthropic
    client = _client()
    model_iter = iter(_model_order()); model = next(model_iter)
    messages = [{"role": "user", "content": prompt}]
    trace, turns, last_err = [], 0, None
    while turns < max_turns:
        t0 = time.time()
        try:
            msg = client.messages.create(model=model, max_tokens=max_tokens, system=system, tools=tools, messages=messages, **_ws_kwargs(client.messages.create))
        except anthropic.NotFoundError as e:
            last_err = e
            try: model = next(model_iter); continue
            except StopIteration: raise RuntimeError(f"사용 가능한 모델을 찾지 못함. 마지막 오류: {last_err}") from e
        _RESOLVED = model; turns += 1
        _record(msg, model, t0, purpose, False)
        uses = [b for b in msg.content if getattr(b, "type", "") == "tool_use"]
        if msg.stop_reason != "tool_use" or not uses:
            return {"text": "".join(getattr(b, "text", "") for b in msg.content).strip(), "trace": trace, "turns": turns}
        messages.append({"role": "assistant", "content": msg.content})
        results = []
        for u in uses:
            fn = handlers.get(u.name)
            try:
                if fn is None: out, err = {"error": f"알 수 없는 도구 {u.name}"}, True
                else: out, err = fn(**(u.input or {})), False
            except Exception as e:
                out, err = {"error": f"{type(e).__name__}: {e}"}, True
            s = json.dumps(out, ensure_ascii=False, default=str)
            if len(s) > 8000: s = s[:8000] + f" …(이하 생략, 총 {len(s)}자)"
            trace.append({"tool": u.name, "input": u.input, "rows": len(out) if isinstance(out, list) else None, "result": out})
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": s, "is_error": err})
        messages.append({"role": "user", "content": results})
    return {"text": "(도구 호출 횟수 한도에 도달해 답을 마치지 못했습니다. 질문을 더 좁혀 주세요.)", "trace": trace, "turns": turns}
