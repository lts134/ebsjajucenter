"""LLM 공통 호출. 앱의 모든 AI 호출은 이 모듈을 거친다. 백엔드는 providers.py의 공급자 모듈이 맡는다(기본 Anthropic Claude, 교체 가능).

세션별 자격증명: Streamlit은 접속자마다 스크립트를 따로 실행하므로, 화면 코드가 매 실행 시작 때 configure()로
그 접속자의 설정(공급자·키·모델)과 상태(성공한 모델·호출 기록)를 넣어 준다. 값은 스레드 로컬에 두어 다른 접속자와 섞이지 않는다.
configure()가 호출되지 않은 곳(check_llm.py 같은 CLI)은 환경변수(ANTHROPIC_API_KEY 등)와 모듈 전역 상태를 쓴다.

- ask_json: JSON 스키마를 주면 구조화 출력으로 형식을 보장받고, 모델이 거부(400)하면 그 모델은 텍스트 파싱으로 자동 대체.
- run_tools: 도구 호출 루프(⑧ 기록에 묻기).
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
    pref = (_cfg().get("model") or os.environ.get("LLM_MODEL") or os.environ.get("CLAUDE_MODEL") or "").strip()
    resolved = _state().get("resolved")
    order = ([pref] if pref else []) + ([resolved] if resolved else []) + list(_provider().default_models)
    seen, out = set(), []
    for m in order:
        if m and m not in seen: seen.add(m); out.append(m)
    return out

def model_label() -> str:
    pref = (_cfg().get("model") or os.environ.get("LLM_MODEL") or os.environ.get("CLAUDE_MODEL") or "").strip()
    cands = _provider().default_models
    return pref or _state().get("resolved") or (cands[0] if cands else "-")

CACHE_WRITE_RATE, CACHE_READ_RATE = 1.25, 0.10     # Anthropic 프롬프트 캐시: 쓰기는 입력 단가의 1.25배(5분 기준), 읽기는 0.1배

def estimate_cost(model: str | None, input_tokens, output_tokens, cache_read=0, cache_write=0) -> float | None:
    """공급자 단가표에 있는 모델만 추정(USD). 없으면 None. input_tokens는 캐시되지 않은 입력(Anthropic usage 그대로)."""
    p = _provider().pricing.get(model or "")
    if not p or input_tokens is None or output_tokens is None: return None
    return round((input_tokens + (cache_write or 0) * CACHE_WRITE_RATE + (cache_read or 0) * CACHE_READ_RATE) / 1e6 * p[0] + output_tokens / 1e6 * p[1], 6)

# 용도별 추론 강도: 정형 작업은 낮게(빠르고 싸게). 설정 칸에 값이 있으면 그것이 우선. 요구서 추출은 정확도 검증(check_llm.py) 전까지 모델 기본값.
EFFORT_BY_PURPOSE = {"지표 분류": "low", "표 열 매핑 제안": "low", "연결 테스트": "low"}

def _effort_for(purpose: str) -> str | None:
    return (_cfg().get("effort") or "").strip() or EFFORT_BY_PURPOSE.get((purpose or "").replace("(잘림 재시도)", "").replace("(재시도)", "").strip())

def _record(msg, model: str, t0: float, purpose: str, structured: bool) -> str:
    text = "".join(getattr(b, "text", "") for b in msg.content)
    usage = getattr(msg, "usage", None)
    inp, out = getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)
    c_read, c_write = getattr(usage, "cache_read_input_tokens", None) or 0, getattr(usage, "cache_creation_input_tokens", None) or 0
    info = {"when": dt.datetime.now().strftime("%H:%M:%S"), "purpose": purpose, "model": model,
            "latency_s": round(time.time() - t0, 2), "input_tokens": inp, "output_tokens": out, "cache_read_tokens": c_read, "cache_write_tokens": c_write,
            "structured": structured, "stop_reason": getattr(msg, "stop_reason", None),
            "cost_usd": estimate_cost(model, inp, out, c_read, c_write)}
    LAST.clear(); LAST.update(info); log().append(info)
    return text

TOOL_LABELS: dict[str, str] = {}      # 도구 이름 → 화면에 보여 줄 우리말 단계명(agent·history_qa가 채운다)
def tool_label(name: str) -> str: return TOOL_LABELS.get(name, name)
RESULT_CHARS = 7600                   # 도구 결과를 모델에 넘길 때 글자 상한(넘으면 잘렸다고 알리고 범위를 좁히라고 안내)

MAX_TOKENS_CAP = 32000          # 잘림 재시도 때 늘릴 수 있는 상한. 추론 모델(Opus 5.5·Fable)은 생각 토큰도 이 한도에 들어가므로 넉넉히 둔다(한도는 상한일 뿐 쓴 만큼만 과금)

class Truncated(RuntimeError):
    """응답이 max_tokens에 잘렸다(stop_reason=max_tokens). JSON이면 해석 불가이므로 더 큰 한도로 다시 시도해야 한다."""
    def __init__(self, max_tokens: int, text: str):
        super().__init__(f"응답이 {max_tokens} 토큰 한도에서 잘렸습니다."); self.max_tokens, self.text = max_tokens, text

class Refused(RuntimeError):
    """모델(또는 공급자의 안전 분류기)이 응답을 거절했다(stop_reason=refusal). 규칙 경로로 대체되거나 담당자에게 안내한다."""
    def __init__(self, model: str):
        super().__init__(f"{model}이(가) 이 요청에 대한 응답을 거절했습니다(안전 분류). 입력에 개인정보·민감 내용이 없는지 확인하거나 다른 모델을 고르세요."); self.model = model

def set_progress(fn):
    """화면이 호출 진행 상황을 받을 콜백(문자열 하나). 세션(스레드) 한정. None이면 해제."""
    _ctx.progress = fn

def _notify(text: str):
    fn = getattr(_ctx, "progress", None)
    if fn:
        try: fn(text)
        except Exception: pass

DEFAULT_MAX_TOKENS = 8000       # 호출 기본 출력 한도. 추론 모델은 답 앞에 생각 토큰을 쓰므로 2,000으로는 JSON이 잘렸다

def ask(prompt: str, system: str | None = None, max_tokens: int = DEFAULT_MAX_TOKENS, purpose: str = "", schema: dict | None = None) -> str:
    """텍스트 응답. 모델 없음이면 다음 후보로. schema가 있으면 구조화 출력, 모델이 거부하면 그 모델은 이후 텍스트 방식.
    응답이 max_tokens에 잘리면 Truncated(잘린 본문 포함)를 올린다."""
    prov, state = _provider(), _state()
    last_err = None
    for model in _model_order():
        t0 = time.time()
        use_struct = schema is not None and model not in state["no_structured"]
        messages = [{"role": "user", "content": prompt}]
        _notify(f"{purpose or '호출'} — AI가 읽고 쓰는 중…")
        try:
            msg = prov.create_message(model=model, max_tokens=max_tokens, messages=messages, system=system, schema=schema if use_struct else None, effort=_effort_for(purpose))
        except Exception as e:
            if prov.is_not_found(e):                      # 모델명이 없음 → 다음 후보
                last_err = e; continue
            # 이 모델(또는 SDK)이 구조화 출력을 모름 → 같은 모델로 텍스트 방식 재시도. 그 외 오류는 그대로 올림
            if use_struct and (isinstance(e, TypeError) or (prov.is_bad_request(e) and ("output_config" in str(e) or "format" in str(e)))):
                state["no_structured"].add(model); t0 = time.time(); use_struct = False
                msg = prov.create_message(model=model, max_tokens=max_tokens, messages=messages, system=system, schema=None, effort=_effort_for(purpose))
            else:
                raise
        state["resolved"] = model
        text = _record(msg, model, t0, purpose, use_struct)
        _notify(f"{purpose or '호출'} — 응답 받음 ({LAST['latency_s']}초)")
        if getattr(msg, "stop_reason", None) == "max_tokens": raise Truncated(max_tokens, text)
        if getattr(msg, "stop_reason", None) == "refusal" and not text.strip(): raise Refused(model)
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

def ask_json(prompt: str, system: str | None = None, max_tokens: int = DEFAULT_MAX_TOKENS, expect: str = "object", purpose: str = "", schema: dict | None = None):
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
        txt = ask('다음을 JSON으로만 답하세요: {"ok": true}', max_tokens=1000, purpose="연결 테스트")     # 추론 모델은 생각 토큰도 한도에 들어가 20으로는 잘린다
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
            "cache_read_tokens": sum(x.get("cache_read_tokens") or 0 for x in L), "cache_write_tokens": sum(x.get("cache_write_tokens") or 0 for x in L),
            "avg_latency_s": round(sum(x.get("latency_s") or 0 for x in L) / n, 2) if n else None,
            "cost_usd": round(sum(c for c in costs if c is not None), 4) if n else 0.0,
            "cost_unknown_calls": sum(1 for c in costs if c is None),
            "structured_calls": sum(1 for x in L if x.get("structured"))}

def run_tools(prompt: str, system: str, tools: list[dict], handlers: dict, max_turns: int = 8, max_tokens: int = DEFAULT_MAX_TOKENS, purpose: str = "도구 질의") -> dict:
    """도구 호출 루프(수동). 모델이 tools 중 하나를 고르면 handlers[name](**input)을 실행해 결과를 돌려주고, 더 이상 호출이 없으면 최종 답을 반환.
    반환: {"text": 최종 답, "trace": [{"tool", "input", "result", "rows"}...], "turns": n}. 모델 없음이면 다음 후보로."""
    return run_tools_conv([{"role": "user", "content": prompt}], system, tools, handlers, max_turns, max_tokens, purpose)

def run_tools_conv(messages: list[dict], system: str, tools: list[dict], handlers: dict, max_turns: int = 8, max_tokens: int = DEFAULT_MAX_TOKENS,
                   purpose: str = "도구 질의", on_tool=None) -> dict:
    """이어지는 대화용 도구 호출 루프. messages(이전 대화 + 이번 사용자 메시지)를 받아 모델이 도구를 다 쓸 때까지 돌고,
    새 메시지들을 덧붙인 전체 대화를 "messages"로 돌려준다(다음 턴에 그대로 넘기면 기억이 이어짐).
    on_tool(name, input, result) 콜백으로 화면이 각 도구 결과를 받아 볼 수 있다."""
    prov, state = _provider(), _state()
    model_iter = iter(_model_order()); model = next(model_iter)
    messages = list(messages)
    trace, turns, last_err = [], 0, None
    while turns < max_turns:
        t0 = time.time()
        _notify(f"{purpose} — {turns + 1}단계: 다음에 할 일을 정하는 중…" if turns else f"{purpose} — 요청을 읽고 무엇부터 할지 정하는 중…")
        try:
            msg = prov.create_message(model=model, max_tokens=max_tokens, messages=list(messages), system=system, tools=tools, cache=True, effort=_effort_for(purpose))   # 호출 시점의 대화 스냅샷. 도구 루프는 접두사 캐싱
        except Exception as e:
            if not prov.is_not_found(e): raise
            last_err = e
            try: model = next(model_iter); continue
            except StopIteration: raise RuntimeError(f"사용 가능한 모델을 찾지 못함. 마지막 오류: {last_err}") from e
        state["resolved"] = model; turns += 1
        _record(msg, model, t0, purpose, False)
        uses = [b for b in msg.content if getattr(b, "type", "") == "tool_use"]
        messages.append({"role": "assistant", "content": msg.content})
        if msg.stop_reason == "max_tokens" and uses:
            # 출력 한도에서 잘린 도구 호출: 실행하지 않고 오류 결과를 붙여 대화 기록을 유효하게 둔다(결과 없는 tool_use가 남으면 다음 턴이 400으로 막힌다)
            messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": u.id, "content": "(응답이 출력 한도에서 잘려 이 작업을 실행하지 못했습니다)", "is_error": True} for u in uses]})
            return {"text": "한 번에 처리할 양이 너무 많아 답이 잘렸습니다. 요청을 나눠서 다시 말씀해 주세요.", "trace": trace, "turns": turns, "messages": messages}
        if msg.stop_reason != "tool_use" or not uses:
            _notify(f"{purpose} — 작업 {len(trace)}건을 바탕으로 답을 정리함 ({time.time() - t0:.1f}초)")
            text = "".join(getattr(b, "text", "") for b in msg.content).strip()
            if msg.stop_reason == "refusal" and not text: text = "AI가 이 요청에는 답하지 않았습니다(안전 분류). 개인정보가 들어 있지 않은지 확인하고 표현을 바꿔 다시 물어 주세요."
            elif msg.stop_reason == "max_tokens": text = (text + "\n\n(답이 출력 한도에서 잘렸습니다. 질문을 좁혀 다시 물어보세요.)").strip()
            return {"text": text, "trace": trace, "turns": turns, "messages": messages}
        results = []
        for u in uses:
            fn = handlers.get(u.name)
            try:
                if fn is None: out, err = {"error": f"알 수 없는 도구 {u.name}"}, True
                else: out, err = fn(**(u.input or {})), False
            except Exception as e:
                out, err = {"error": f"{type(e).__name__}: {e}"}, True
            s = json.dumps(out, ensure_ascii=False, default=str)
            if len(s) > RESULT_CHARS: s = s[:RESULT_CHARS] + f"\n…(잘림: 전체 {len(s):,}자 중 {RESULT_CHARS:,}자만 표시. 전체가 아니므로 '없음'으로 단정하지 말고, 지표·센터·기준일·검색어 인자로 범위를 좁혀 다시 조회하세요.)"
            trace.append({"tool": u.name, "input": u.input, "rows": len(out) if isinstance(out, list) else None, "result": out})
            arg = ", ".join(f"{k}={v}" for k, v in (u.input or {}).items())[:60]
            _notify(f"{purpose} — {tool_label(u.name)}" + (f"({arg})" if arg else "") + " → " + ("오류" if err else (f"{len(out)}건" if isinstance(out, list) else "완료")))
            if on_tool:
                try: on_tool(u.name, u.input, out)
                except Exception: pass
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": s, "is_error": err})
        messages.append({"role": "user", "content": results})
    messages.append({"role": "assistant", "content": [{"type": "text", "text": "(작업 단계 한도에 도달해 답을 마치지 못했습니다.)"}]})
    return {"text": "한 번에 처리할 수 있는 단계 수를 넘었습니다. 요청을 나눠서 말씀해 주세요.", "trace": trace, "turns": turns, "messages": messages}

# 하위 호환(설정 화면 문구 등에서 참조)
CANDIDATES = providers.AnthropicProvider.default_models
PRICING = providers.AnthropicProvider.pricing
