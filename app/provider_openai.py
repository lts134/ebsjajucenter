"""OpenAI 호환 공급자(Chat Completions API, HTTP). OpenAI 본사 API뿐 아니라 같은 형식을 쓰는 게이트웨이(Azure OpenAI 호환 엔드포인트, 사내 프록시, vLLM 등)도
'기본 주소' 칸만 바꾸면 쓸 수 있다. SDK 없이 httpx(anthropic SDK가 이미 쓰는 라이브러리)로 호출하므로 새 의존성이 없다.

변환: llm.py가 주는 Anthropic 모양(system / messages[{"role","content":블록}] / tools[{name,description,input_schema}] / JSON 스키마)을
Chat Completions 형식(messages[system|user|assistant(tool_calls)|tool], tools[{type:function}], response_format json_schema)으로 바꾸고,
응답은 providers.SimpleMessage(TextBlock·ToolUseBlock, stop_reason, usage)로 되돌린다. 추론 모델(gpt-5 계열)은 temperature를 받지 않으므로 보내지 않는다."""
from __future__ import annotations
import json, os, re, time
import httpx
from providers import Provider, SimpleMessage, SimpleUsage, TextBlock, ToolUseBlock, HttpError, iter_blocks, block_get, MAX_RETRIES

DEFAULT_BASE = "https://api.openai.com/v1"


class OpenAIProvider(Provider):
    name = "openai"
    label = "OpenAI 호환(OpenAI·Azure·게이트웨이)"
    fields = [
        {"key": "api_key", "label": "API 키", "secret": True, "required": True, "env": "OPENAI_API_KEY"},
        {"key": "base_url", "label": f"기본 주소 — 비우면 {DEFAULT_BASE} (호환 게이트웨이면 그 주소)", "secret": False, "required": False, "env": "OPENAI_BASE_URL"},
    ]
    # 모델 지정이 없을 때 시도 순서. 실제 쓸 수 있는 목록은 설정 화면 '연결 테스트'가 /models로 조회한다.
    default_models = ["gpt-5.1", "gpt-5", "gpt-5-mini", "gpt-4.1"]
    # USD / 100만 토큰(입력, 출력). 공개 가격표 캐시(2026-10 기준) — 실제 청구는 콘솔 확인 [확인 필요]
    pricing = {"gpt-5.1": (1.25, 10.00), "gpt-5": (1.25, 10.00), "gpt-5-mini": (0.25, 2.00), "gpt-5-nano": (0.05, 0.40),
               "gpt-4.1": (2.00, 8.00), "gpt-4.1-mini": (0.40, 1.60), "gpt-4o": (2.50, 10.00), "gpt-4o-mini": (0.15, 0.60)}
    docs_url = "https://platform.openai.com"
    transport = None            # 테스트용 httpx 전송 계층 주입

    def base(self) -> str:
        b = (self.value("base_url") or DEFAULT_BASE).rstrip("/")
        # 공용 키(서버 환경변수)를 접속자가 바꾼 주소로 보내는 것을 막는다(키 유출 경로). 공용 키면 환경변수 주소나 기본 주소로만.
        if self.from_env.get("api_key") and not self.from_env.get("base_url") and b != (os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE).rstrip("/"):
            raise PermissionError("공용 API 키는 기본 주소로만 호출할 수 있습니다. 다른 주소를 쓰려면 설정 화면에 본인 키를 넣으세요.")
        return b

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(base_url=self.base(), timeout=timeout, transport=self.transport,
                            headers={"Authorization": f"Bearer {self.value('api_key')}", "Content-Type": "application/json"})

    @staticmethod
    def timeout_for(max_tokens: int) -> float: return float(min(600, 90 + max_tokens / 1000 * 20))

    # ---- 요청 변환 ----
    @staticmethod
    def to_messages(messages: list[dict], system: str | None) -> list[dict]:
        out = [{"role": "system", "content": system}] if system else []
        for m in messages:
            role, blocks = m.get("role"), iter_blocks(m.get("content"))
            if role == "assistant":
                text = "".join(str(block_get(b, "text", "")) for b in blocks if block_get(b, "type") == "text")
                calls = [{"id": str(block_get(b, "id")), "type": "function",
                          "function": {"name": block_get(b, "name"), "arguments": json.dumps(block_get(b, "input") or {}, ensure_ascii=False)}}
                         for b in blocks if block_get(b, "type") == "tool_use"]
                msg = {"role": "assistant", "content": text or None}
                if calls: msg["tool_calls"] = calls
                out.append(msg)
            else:
                results = [b for b in blocks if block_get(b, "type") == "tool_result"]
                for b in results:                                        # 도구 결과는 role=tool 메시지 하나씩
                    c = block_get(b, "content")
                    out.append({"role": "tool", "tool_call_id": str(block_get(b, "tool_use_id")), "content": c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)})
                text = "".join(str(block_get(b, "text", "")) for b in blocks if block_get(b, "type") == "text")
                if text or not results: out.append({"role": "user", "content": text})
        return out

    @staticmethod
    def to_tools(tools: list[dict]) -> list[dict]:
        return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}} for t in tools]

    # ---- 응답 변환 ----
    @staticmethod
    def from_response(data: dict) -> SimpleMessage:
        ch = (data.get("choices") or [{}])[0]; msg = ch.get("message") or {}
        blocks = []
        if msg.get("content"): blocks.append(TextBlock(text=str(msg["content"])))
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try: args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError: args = {"_raw": fn.get("arguments")}
            blocks.append(ToolUseBlock(id=str(tc.get("id") or f"call_{len(blocks)}"), name=fn.get("name", ""), input=args if isinstance(args, dict) else {"value": args}))
        fr = ch.get("finish_reason")
        stop = {"tool_calls": "tool_use", "length": "max_tokens", "stop": "end_turn", "content_filter": "refusal"}.get(fr, "end_turn")
        if any(isinstance(b, ToolUseBlock) for b in blocks) and stop == "end_turn": stop = "tool_use"
        u = data.get("usage") or {}
        return SimpleMessage(content=blocks, stop_reason=stop, usage=SimpleUsage(u.get("prompt_tokens"), u.get("completion_tokens")))

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
        body = {"model": model, "messages": self.to_messages(messages, system), "max_completion_tokens": int(max_tokens)}
        eff = (effort or "").strip().lower()
        if eff and re.match(r"(gpt-5|o[134])", model or ""): body["reasoning_effort"] = {"xhigh": "high", "max": "high"}.get(eff, eff)   # 추론 모델만 받는 값. 캐싱은 OpenAI가 자동
        if tools: body["tools"] = self.to_tools(tools); body["tool_choice"] = "auto"
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema, "strict": False}}
        return self.from_response(self._post("/chat/completions", body, self.timeout_for(int(max_tokens))))

    def list_models(self) -> list[dict]:
        with self._client(30) as c: r = c.get("/models")
        if r.status_code >= 400: raise HttpError(r.status_code, r.text[:300])
        rows = [{"id": m.get("id"), "name": m.get("id"), "created": ""} for m in (r.json().get("data") or [])]
        rows = [m for m in rows if m["id"] and any(k in m["id"] for k in ("gpt", "o1", "o3", "o4"))] or rows
        return sorted(rows, key=lambda m: m["id"])

    def is_not_found(self, e):
        return isinstance(e, HttpError) and (e.status == 404 or ("model" in e.message.lower() and ("not found" in e.message.lower() or "does not exist" in e.message.lower())))

    def is_bad_request(self, e):
        return isinstance(e, HttpError) and e.status == 400

    def explain_error(self, e):
        if isinstance(e, HttpError):
            if e.status == 401: return "OpenAI 키가 잘못됐거나 만료됐습니다(401). 플랫폼 콘솔에서 키를 확인하세요."
            if e.status == 403: return "이 키로는 허용되지 않는 요청입니다(403). 프로젝트·조직 권한을 확인하세요."
            if e.status == 404: return "모델명이 없거나 이 키로 쓸 수 없습니다(404). 연결 테스트의 모델 목록에서 고르세요."
            if e.status == 429: return "호출 한도 또는 잔액 초과(429). 플랫폼 콘솔의 사용량·결제를 확인하고 잠시 후 다시 시도하세요."
            if e.status == 400: return f"요청 형식 오류(400): {e.message[:200]}"
            if e.status >= 500: return f"OpenAI 서버 오류({e.status}). 잠시 후 다시 시도하세요."
        if isinstance(e, PermissionError): return str(e)
        if isinstance(e, httpx.TimeoutException): return "응답 시간 초과. 네트워크 상태를 확인하거나 더 빠른 모델(예: gpt-5-mini)을 고르세요."
        if isinstance(e, httpx.HTTPError): return f"API 서버에 연결하지 못했습니다({self.base()}). 사내망 프록시·방화벽 허용 여부를 확인하세요."
        return None


PROVIDER = OpenAIProvider
