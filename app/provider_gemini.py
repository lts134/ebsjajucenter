"""Google Gemini 공급자(Gemini API generateContent, HTTP). Google AI Studio에서 받은 키 하나로 동작한다. SDK 없이 httpx로 호출.

변환: Anthropic 모양(system / messages / tools / JSON 스키마) → Gemini 형식(systemInstruction / contents[{role:user|model, parts}] /
tools[{functionDeclarations}] / generationConfig.responseSchema). 도구 결과(functionResponse)에는 도구 **이름**이 필요하므로 앞선 호출 id에서 이름을 찾는다.
Gemini의 스키마는 OpenAPI 부분집합이라 additionalProperties·$schema 같은 키를 빼고, ["string","null"] 꼴 type은 nullable로 바꾼다."""
from __future__ import annotations
import json, os, time, uuid
import httpx
from providers import Provider, check_base_url, SimpleMessage, SimpleUsage, TextBlock, ToolUseBlock, HttpError, iter_blocks, block_get, tool_names_by_id, MAX_RETRIES

DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"
SCHEMA_KEYS = {"type", "description", "properties", "required", "items", "enum", "nullable", "format", "minItems", "maxItems", "anyOf"}


def clean_schema(s):
    """Gemini가 받는 스키마 부분집합으로 정리(재귀). type이 리스트면 null을 nullable로 옮긴다."""
    if isinstance(s, list): return [clean_schema(x) for x in s]
    if not isinstance(s, dict): return s
    out = {}
    for k, v in s.items():
        if k not in SCHEMA_KEYS: continue
        if k == "type" and isinstance(v, list):
            ts = [t for t in v if t != "null"]
            out["type"] = ts[0] if ts else "string"
            if "null" in v: out["nullable"] = True
        elif k == "properties" and isinstance(v, dict): out[k] = {pk: clean_schema(pv) for pk, pv in v.items()}
        elif k in ("items", "anyOf"): out[k] = clean_schema(v)
        else: out[k] = v
    return out


class GeminiProvider(Provider):
    name = "gemini"
    label = "Google Gemini API"
    fields = [
        {"key": "api_key", "label": "API 키(Google AI Studio)", "secret": True, "required": True, "env": "GEMINI_API_KEY"},
        {"key": "base_url", "label": f"기본 주소 — 비우면 {DEFAULT_BASE}", "secret": False, "required": False, "env": "GEMINI_BASE_URL"},
    ]
    default_models = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"]
    # USD / 100만 토큰(입력, 출력). 공개 가격표 캐시(2026-10, 20만 토큰 이하 구간) — 실제 청구는 콘솔 확인 [확인 필요]
    pricing = {"gemini-2.5-pro": (1.25, 10.00), "gemini-2.5-flash": (0.30, 2.50), "gemini-2.5-flash-lite": (0.10, 0.40), "gemini-2.0-flash": (0.10, 0.40)}
    docs_url = "https://aistudio.google.com"
    transport = None

    def base(self) -> str:
        b = (self.value("base_url") or DEFAULT_BASE).rstrip("/")
        # 공용 키(서버 환경변수)를 접속자가 바꾼 주소로 보내는 것을 막는다(키 유출 경로). 공용 키면 환경변수 주소나 기본 주소로만.
        if self.from_env.get("api_key") and not self.from_env.get("base_url") and b != (os.environ.get("GEMINI_BASE_URL") or DEFAULT_BASE).rstrip("/"):
            raise PermissionError("공용 API 키는 기본 주소로만 호출할 수 있습니다. 다른 주소를 쓰려면 설정 화면에 본인 키를 넣으세요.")
        if b != DEFAULT_BASE.rstrip("/") and not self.from_env.get("base_url"): b = check_base_url(b)   # 접속자가 넣은 주소는 내부망 차단 검사
        return b

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(base_url=self.base(), timeout=timeout, transport=self.transport,
                            headers={"x-goog-api-key": self.value("api_key"), "Content-Type": "application/json"})

    @staticmethod
    def timeout_for(max_tokens: int) -> float: return float(min(600, 90 + max_tokens / 1000 * 20))

    # ---- 요청 변환 ----
    @staticmethod
    def to_contents(messages: list[dict]) -> list[dict]:
        names = tool_names_by_id(messages); out = []
        for m in messages:
            role = "model" if m.get("role") == "assistant" else "user"; parts = []
            for b in iter_blocks(m.get("content")):
                t = block_get(b, "type")
                if t == "text":
                    if str(block_get(b, "text", "")).strip(): parts.append({"text": str(block_get(b, "text"))})
                elif t == "tool_use":
                    parts.append({"functionCall": {"name": block_get(b, "name"), "args": block_get(b, "input") or {}}})
                elif t == "tool_result":
                    c = block_get(b, "content")
                    try: resp = json.loads(c) if isinstance(c, str) else c
                    except (json.JSONDecodeError, TypeError): resp = c
                    if not isinstance(resp, dict): resp = {"result": resp}
                    parts.append({"functionResponse": {"name": names.get(str(block_get(b, "tool_use_id")), "tool"), "response": resp}})
            if parts: out.append({"role": role, "parts": parts})
        return out

    @staticmethod
    def to_tools(tools: list[dict]) -> list[dict]:
        decls = []
        for t in tools:
            d = {"name": t["name"], "description": t.get("description", "")}
            sch = clean_schema(t.get("input_schema") or {})
            if sch.get("properties"): d["parameters"] = sch                      # 매개변수 없는 도구는 parameters를 비운다(빈 object 거부 방지)
            decls.append(d)
        return [{"functionDeclarations": decls}]

    # ---- 응답 변환 ----
    @staticmethod
    def from_response(data: dict) -> SimpleMessage:
        cand = (data.get("candidates") or [{}])[0]
        parts = ((cand.get("content") or {}).get("parts")) or []
        blocks, n = [], 0
        for p in parts:
            if p.get("thought"): continue
            if "text" in p and p["text"]: blocks.append(TextBlock(text=str(p["text"])))
            if "functionCall" in p:
                fc = p["functionCall"]; n += 1
                blocks.append(ToolUseBlock(id=f"call_{uuid.uuid4().hex[:12]}", name=fc.get("name", ""), input=fc.get("args") or {}))   # 회차마다 call_1부터 다시 세면 결과가 엉뚱한 호출에 붙는다
        fr = str(cand.get("finishReason") or data.get("promptFeedback", {}).get("blockReason") or "STOP")
        if any(isinstance(b, ToolUseBlock) for b in blocks): stop = "tool_use"
        elif fr == "MAX_TOKENS": stop = "max_tokens"
        elif fr in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY") or data.get("promptFeedback", {}).get("blockReason"): stop = "refusal"
        else: stop = "end_turn"
        u = data.get("usageMetadata") or {}
        out_tok = u.get("candidatesTokenCount"); th = u.get("thoughtsTokenCount")
        if out_tok is not None and th: out_tok += th                             # 추론 토큰도 출력 과금
        return SimpleMessage(content=blocks, stop_reason=stop, usage=SimpleUsage(u.get("promptTokenCount"), out_tok))

    def _post(self, path: str, body: dict, timeout: float) -> dict:
        last = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                with self._client(timeout) as c: r = c.post(path, json=body)
            except (httpx.ConnectError, httpx.RemoteProtocolError) as e:        # 연결 자체가 안 된 경우만 재시도. 시간 초과(이미 생성·과금됐을 수 있음)는 재전송하지 않는다
                last = e
                if attempt < MAX_RETRIES: time.sleep(0.5 * 2 ** attempt); continue
                raise
            if r.status_code < 400: return r.json()
            try: msg = (r.json().get("error") or {}).get("message") or r.text
            except ValueError: msg = r.text
            err = HttpError(r.status_code, str(msg)[:500], r.text[:2000])
            if r.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                last = err
                try: wait = float(r.headers.get("retry-after") or 0)
                except ValueError: wait = 0
                time.sleep(min(30, wait or 0.5 * 2 ** attempt)); continue       # 지수 백오프, Retry-After 우선
            raise err
        raise last  # pragma: no cover

    def create_message(self, *, model, max_tokens, messages, system=None, tools=None, schema=None, cache=False, effort=None):
        body = {"contents": self.to_contents(messages), "generationConfig": {"maxOutputTokens": int(max_tokens)}}      # cache·effort는 Gemini REST 기본 호출에서 받지 않아 무시
        if system: body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools: body["tools"] = self.to_tools(tools)
        if schema is not None:
            body["generationConfig"]["responseMimeType"] = "application/json"; body["generationConfig"]["responseSchema"] = clean_schema(schema)
        return self.from_response(self._post(f"/models/{model}:generateContent", body, self.timeout_for(int(max_tokens))))

    def list_models(self) -> list[dict]:
        with self._client(30) as c: r = c.get("/models", params={"pageSize": 200})
        if r.status_code >= 400: raise HttpError(r.status_code, r.text[:300])
        rows = []
        for m in r.json().get("models") or []:
            if "generateContent" not in (m.get("supportedGenerationMethods") or []): continue
            rows.append({"id": str(m.get("name", "")).removeprefix("models/"), "name": m.get("displayName") or m.get("name"), "created": ""})
        return sorted(rows, key=lambda m: m["id"])

    def is_not_found(self, e):
        return isinstance(e, HttpError) and (e.status == 404 or "is not found" in e.message.lower())

    def is_bad_request(self, e):
        return isinstance(e, HttpError) and e.status == 400

    def explain_error(self, e):
        if isinstance(e, HttpError):
            if e.status in (401, 403): return f"Gemini 키가 잘못됐거나 권한이 없습니다({e.status}). Google AI Studio에서 키를 확인하세요."
            if e.status == 404: return "모델명이 없거나 이 키로 쓸 수 없습니다(404). 연결 테스트의 모델 목록에서 고르세요."
            if e.status == 429: return "호출 한도 초과(429). 무료 등급이면 분당·일일 한도를, 유료면 결제 상태를 확인하고 잠시 후 다시 시도하세요."
            if e.status == 400: return f"요청 형식 오류(400): {e.message[:200]}"
            if e.status >= 500: return f"Gemini 서버 오류({e.status}). 잠시 후 다시 시도하세요."
        if isinstance(e, PermissionError): return str(e)
        if isinstance(e, httpx.TimeoutException): return "응답 시간 초과. 네트워크 상태를 확인하거나 더 빠른 모델(예: gemini-2.5-flash)을 고르세요."
        if isinstance(e, httpx.HTTPError): return f"API 서버에 연결하지 못했습니다({self.base()}). 사내망 프록시·방화벽 허용 여부를 확인하세요."
        return None


PROVIDER = GeminiProvider
