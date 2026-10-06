"""LLM 공통 호출. 앱의 모든 AI 호출은 이 모듈을 거친다. 백엔드는 providers.py의 공급자 모듈이 맡는다(기본 Anthropic Claude, 교체 가능).

세션별 자격증명: Streamlit은 접속자마다 스크립트를 따로 실행하므로, 화면 코드가 매 실행 시작 때 configure()로
그 접속자의 설정(공급자·키·모델)과 상태(성공한 모델·호출 기록)를 넣어 준다. 값은 스레드 로컬에 두어 다른 접속자와 섞이지 않는다.
configure()가 호출되지 않은 곳(check_llm.py 같은 CLI)은 환경변수(ANTHROPIC_API_KEY 등)와 모듈 전역 상태를 쓴다.

- ask_json: JSON 스키마를 주면 구조화 출력으로 형식을 보장받고, 모델이 거부(400)하면 그 모델은 텍스트 파싱으로 자동 대체.
- run_tools: 도구 호출 루프(⑧ 이력에 묻기).
- 재시도·타임아웃은 공급자(SDK)가 담당. 호출마다 모델·지연·토큰·추정 비용을 기록.
AI는 수치를 계산하거나 사유를 추정하지 않는다는 원칙은 호출하는 쪽(extract/normalize/draft/assist)의 프롬프트가 지킨다."""
import os, re, json, time, threading, datetime as dt
import providers

_ctx = threading.local()
_GLOBAL_STATE: dict = {"resolved": None, "no_structured": set()}   # configure() 없이 쓸 때(CLI)의 상태
LAST: dict = {}                                                     # 마지막 호출 정보(CLI·점검 보고서용)
LOG: list[dict] = []                                                # configure() 없이 쓸 때의 누적 호출 기록

def configure(cfg: dict | None, state: dict | None = None, log: list | None = None) -> None:
    """이 스레드(=이 접속자의 스크립트 실행)에서 쓸 공급자 설정·상태·기록을 등록한다.
    cfg: {"provider": "anthropic", "api_key": ..., "workspace_id": ..., "model": ...}. 비어 있는 값은 환경변수로 보완된다(공급자 fields의 env).
    state: {"resolved": 모델, "no_structured": set} — 세션에 보관해 넘기면 재실행 사이에 유지. log: 호출 기록 리스트(세션 보관)."""
    _ctx.cfg = dict(cfg or {})
    if state is not None:
        state.setdefault("resolved", None); state.setdefault("no_structured", set()); _ctx.state = state
    else:
        _ctx.state = {"resolved": None, "no_structured": set()}
    _ctx.log = log if log is not None else []

def reset() -> None:
    """상태 초기화(테스트·CLI)."""
    for k in ("cfg", "state", "log"):
        if hasattr(_ctx, k): delattr(_ctx, k)
    _GLOBAL_STATE.update(resolved=None, no_structured=set()); LOG.clear(); LAST.clear()

def _cfg() -> dict: return getattr(_ctx, "cfg", None) or {}
def _state() -> dict: return getattr(_ctx, "state", None) or _GLOBAL_STATE
def log() -> list[dict]: return getattr(_ctx, "log", None) if getattr(_ctx, "log", None) is not None else LOG

def _provider() -> providers.Provider:
    return providers.get(_cfg().get("provider"), _cfg())

def provider_name() -> str: return _provider().name

def available() -> bool:
    """AI 호출이 가능한가(공급자 준비됨). 아니면 각 모듈이 규칙 기반으로 동작."""
    try: return _provider().ready()
    except Exception: return False

def explain_error(e: Exception) -> str:
    """API 오류를 담당자가 조치할 수 있는 말로(공급자 안내문 우선)."""
    try:
        s = _provider().explain_error(e)
        if s: return s
    except Exception:
        pass
    m = str(e)
    if "anthropic-workspace-id" in m or "not scoped to a workspace" in m:
        return "이 키는 워크스페이스에 묶여 있지 않아 워크스페이스 ID가 필요합니다. 설정 화면에서 wrkspc_… 값을 넣으세요."
    if "credit" in m.lower() or "billing" in m.lower(): return "API 크레딧 잔액이 부족합니다. 콘솔(Plans & Billing)에서 충전하세요."
    return f"{type(e).__name__}: {m[:300]}"

def _model_order() -> list[str]:
    """사용자가 고른 모델이 가장 먼저, 다음은 이 세션에서 성공한 모델, 그다음 공급자 기본 후보 순."""
    pref = (_cfg().get("model") or os.environ.get("CLAUDE_MODEL") or "").strip()
    resolved = _state().get("resolved")
    order = ([pref] if pref else []) + ([resolved] if resolved else []) + list(_provider().default_models)
    seen, out = set(), []
    for m in order:
        if m and m not in seen: seen.add(m); out.append(m)
    return out

def model_label() -> str:
    pref = (_cfg().get("model") or os.environ.get("CLAUDE_MODEL") or "").strip()
    cands = _provider().default_models
    return pref or _state().get("resolved") or (cands[0] if cands else "-")

def estimate_cost(model: str | None, input_tokens, output_tokens) -> float | None:
    """공급자 단가표에 있는 모델만 추정(USD). 없으면 None."""
    p = _provider().pricing.get(model or "")
    if not p or input_tokens is None or output_tokens is None: return None
    return round(input_tokens / 1e6 * p[0] + output_tokens / 1e6 * p[1], 6)

def _record(msg, model: str, t0: float, purpose: str, structured: bool) -> str:
    text = "".join(getattr(b, "text", "") for b in msg.content)
    usage = getattr(msg, "usage", None)
    inp, out = getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)
    info = {"when": dt.datetime.now().strftime("%H:%M:%S"), "purpose": purpose, "model": model,
            "latency_s": round(time.time() - t0, 2), "input_tokens": inp, "output_tokens": out,
            "structured": structured, "stop_reason": getattr(msg, "stop_reason", None),
            "cost_usd": estimate_cost(model, inp, out)}
    LAST.clear(); LAST.update(info); log().append(info)
    return text

MAX_TOKENS_CAP = 16000          # 잘림 재시도 때 늘릴 수 있는 상한(비스트리밍 호출 10분 제한 안)

class Truncated(RuntimeError):
    """응답이 max_tokens에 잘렸다(stop_reason=max_tokens). JSON이면 해석 불가이므로 더 큰 한도로 다시 시도해야 한다."""
    def __init__(self, max_tokens: int, text: str):
        super().__init__(f"응답이 {max_tokens} 토큰 한도에서 잘렸습니다."); self.max_tokens, self.text = max_tokens, text

def set_progress(fn):
    """화면이 호출 진행 상황을 받을 콜백(문자열 하나). 세션(스레드) 한정. None이면 해제."""
    _ctx.progress = fn

def _notify(text: str):
    fn = getattr(_ctx, "progress", None)
    if fn:
        try: fn(text)
        except Exception: pass

def ask(prompt: str, system: str | None = None, max_tokens: int = 2000, purpose: str = "", schema: dict | None = None) -> str:
    """텍스트 응답. 모델 없음이면 다음 후보로. schema가 있으면 구조화 출력, 모델이 거부하면 그 모델은 이후 텍스트 방식.
    응답이 max_tokens에 잘리면 Truncated(잘린 본문 포함)를 올린다."""
    prov, state = _provider(), _state()
    last_err = None
    for model in _model_order():
        t0 = time.time()
        use_struct = schema is not None and model not in state["no_structured"]
        messages = [{"role": "user", "content": prompt}]
        _notify(f"{purpose or '호출'} — {model} 호출 중 (출력 한도 {max_tokens:,} 토큰, 입력 약 {len(prompt) // 2:,} 토큰)…")
        try:
            msg = prov.create_message(model=model, max_tokens=max_tokens, messages=messages, system=system, schema=schema if use_struct else None)
        except Exception as e:
            if prov.is_not_found(e):                      # 모델명이 없음 → 다음 후보
                last_err = e; continue
            # 이 모델(또는 SDK)이 구조화 출력을 모름 → 같은 모델로 텍스트 방식 재시도. 그 외 오류는 그대로 올림
            if use_struct and (isinstance(e, TypeError) or (prov.is_bad_request(e) and ("output_config" in str(e) or "format" in str(e)))):
                state["no_structured"].add(model); t0 = time.time(); use_struct = False
                msg = prov.create_message(model=model, max_tokens=max_tokens, messages=messages, system=system, schema=None)
            else:
                raise
        state["resolved"] = model
        text = _record(msg, model, t0, purpose, use_struct)
        _notify(f"{purpose or '호출'} — {model} 응답 {LAST['latency_s']}초, 출력 {LAST.get('output_tokens') or '?'} 토큰")
        if getattr(msg, "stop_reason", None) == "max_tokens": raise Truncated(max_tokens, text)
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

def ask_json(prompt: str, system: str | None = None, max_tokens: int = 2000, expect: str = "object", purpose: str = "", schema: dict | None = None):
    """JSON 응답. schema(JSON Schema, 객체 루트)가 있으면 구조화 출력을 쓴다. expect="array"면 schema 없이 텍스트 파싱."""
    sys_ = (system + "\n\n" if system else "") + "출력은 JSON만. 설명·코드펜스·주석을 붙이지 말 것."
    sch = schema if expect == "object" else None
    limit = max_tokens
    while True:                                                   # 잘리면 한도를 두 배로(상한까지) 다시 시도
        try:
            raw = ask(prompt, sys_, limit, purpose, sch); break
        except Truncated as t:
            if limit >= MAX_TOKENS_CAP:
                raise RuntimeError(f"응답이 {limit:,} 토큰 한도에서도 잘렸습니다. 요구서를 나누어 올리거나 항목 수를 줄이세요.") from t
            limit = min(limit * 2, MAX_TOKENS_CAP)
            _notify(f"{purpose} — 응답이 잘려 출력 한도를 {limit:,} 토큰으로 늘려 다시 시도…")
            purpose = purpose.replace("(잘림 재시도)", "") + "(잘림 재시도)"
    try:
        return parse_json(raw, expect)
    except (ValueError, json.JSONDecodeError):
        raw2 = ask(prompt + "\n\n(앞선 응답이 JSON으로 해석되지 않았습니다. JSON만 다시 출력하세요.)", sys_, limit, purpose + "(재시도)")
        return parse_json(raw2, expect)

def list_models() -> list[dict]:
    """공급자 계정에서 쓸 수 있는 모델 목록(id, 표시명, 단가)."""
    prov = _provider(); out = []
    for m in prov.list_models():
        p = prov.pricing.get(m["id"])
        out.append({**m, "입력 $/1M": p[0] if p else None, "출력 $/1M": p[1] if p else None})
    return out

def test_connection() -> dict:
    """설정 화면용: 키·모델·왕복시간 확인. 실패 시 원인 문자열."""
    if not available(): return {"ok": False, "error": "공급자 설정이 비어 있습니다(키 없음 또는 '사용 안 함')."}
    res = {"ok": False, "provider": provider_name()}
    try:
        res["models"] = list_models()
    except Exception as e:
        res["models_error"] = explain_error(e)
    try:
        before = len(log())
        txt = ask('다음을 JSON으로만 답하세요: {"ok": true}', max_tokens=20, purpose="연결 테스트")
        info = log()[-1] if len(log()) > before else {}
        res.update(ok=True, model=info.get("model"), latency_s=info.get("latency_s"), reply=txt.strip()[:60])
    except Exception as e:
        res["error"] = explain_error(e)
    return res

def usage_summary() -> dict:
    L = log(); n = len(L)
    costs = [x.get("cost_usd") for x in L]
    return {"calls": n, "input_tokens": sum(x.get("input_tokens") or 0 for x in L),
            "output_tokens": sum(x.get("output_tokens") or 0 for x in L),
            "avg_latency_s": round(sum(x.get("latency_s") or 0 for x in L) / n, 2) if n else None,
            "cost_usd": round(sum(c for c in costs if c is not None), 4) if n else 0.0,
            "cost_unknown_calls": sum(1 for c in costs if c is None),
            "structured_calls": sum(1 for x in L if x.get("structured"))}

def run_tools(prompt: str, system: str, tools: list[dict], handlers: dict, max_turns: int = 8, max_tokens: int = 2000, purpose: str = "도구 질의") -> dict:
    """도구 호출 루프(수동). 모델이 tools 중 하나를 고르면 handlers[name](**input)을 실행해 결과를 돌려주고, 더 이상 호출이 없으면 최종 답을 반환.
    반환: {"text": 최종 답, "trace": [{"tool", "input", "result", "rows"}...], "turns": n}. 모델 없음이면 다음 후보로."""
    prov, state = _provider(), _state()
    model_iter = iter(_model_order()); model = next(model_iter)
    messages = [{"role": "user", "content": prompt}]
    trace, turns, last_err = [], 0, None
    while turns < max_turns:
        t0 = time.time()
        try:
            msg = prov.create_message(model=model, max_tokens=max_tokens, messages=messages, system=system, tools=tools)
        except Exception as e:
            if not prov.is_not_found(e): raise
            last_err = e
            try: model = next(model_iter); continue
            except StopIteration: raise RuntimeError(f"사용 가능한 모델을 찾지 못함. 마지막 오류: {last_err}") from e
        state["resolved"] = model; turns += 1
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

# 하위 호환(설정 화면 문구 등에서 참조)
CANDIDATES = providers.AnthropicProvider.default_models
PRICING = providers.AnthropicProvider.pricing
