"""Gemini 공급자: contents 변환(functionCall/functionResponse에 도구 이름 매핑), 스키마 정리, 응답 변환(thought 제외·종료 사유·추론 토큰), llm.py 결합, 오류 안내."""
import json, httpx, pytest
import llm, providers
from provider_gemini import GeminiProvider, clean_schema

def _transport(script):
    calls = []
    def handler(req: httpx.Request):
        calls.append({"path": req.url.path, "body": json.loads(req.content or b"{}") if req.content else {}, "key": req.headers.get("x-goog-api-key")})
        status, data = script.pop(0); return httpx.Response(status, json=data)
    return httpx.MockTransport(handler), calls

def gen(parts, finish="STOP", usage=(50, 10, 0)):
    u = {"promptTokenCount": usage[0], "candidatesTokenCount": usage[1]}
    if usage[2]: u["thoughtsTokenCount"] = usage[2]
    return 200, {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}], "usageMetadata": u}

@pytest.fixture
def prov(monkeypatch):
    def make(script):
        t, calls = _transport(script); monkeypatch.setattr(GeminiProvider, "transport", t)
        return GeminiProvider({"api_key": "g-key"}), calls
    yield make
    monkeypatch.setattr(GeminiProvider, "transport", None)

def test_clean_schema_subset_and_nullable():
    sch = {"$schema": "x", "type": "object", "additionalProperties": False, "required": ["a"],
           "properties": {"a": {"type": ["string", "null"], "default": None}, "b": {"type": "array", "items": {"type": "object", "properties": {"c": {"type": "number", "minimum": 0}}}}}}
    out = clean_schema(sch)
    assert "$schema" not in out and "additionalProperties" not in out and out["properties"]["a"] == {"type": "string", "nullable": True}
    assert out["properties"]["b"]["items"]["properties"]["c"] == {"type": "number"} and out["required"] == ["a"]

def test_contents_translation_maps_tool_names():
    msgs = [{"role": "user", "content": "질문"},
            {"role": "assistant", "content": [providers.ToolUseBlock(id="call_1", name="search_requests", input={"keyword": "등원율"})]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": '[{"id": 1}]', "is_error": False}]}]
    c = GeminiProvider.to_contents(msgs)
    assert c[0] == {"role": "user", "parts": [{"text": "질문"}]} and c[1] == {"role": "model", "parts": [{"functionCall": {"name": "search_requests", "args": {"keyword": "등원율"}}}]}
    assert c[2] == {"role": "user", "parts": [{"functionResponse": {"name": "search_requests", "response": {"result": [{"id": 1}]}}}]}     # 리스트 결과는 객체로 감쌈
    tools = GeminiProvider.to_tools([{"name": "a", "description": "d", "input_schema": {"type": "object", "properties": {}}}, {"name": "b", "description": "d", "input_schema": {"type": "object", "properties": {"k": {"type": "string"}}, "additionalProperties": False}}])
    assert tools[0]["functionDeclarations"][0] == {"name": "a", "description": "d"} and tools[0]["functionDeclarations"][1]["parameters"] == {"type": "object", "properties": {"k": {"type": "string"}}}

def test_response_translation():
    m = GeminiProvider.from_response(gen([{"text": "생각", "thought": True}, {"functionCall": {"name": "lookup", "args": {"q": 1}}}], usage=(50, 10, 30))[1])
    assert m.stop_reason == "tool_use" and m.content[0].type == "tool_use" and m.content[0].name == "lookup" and m.content[0].id == "call_1" and m.usage.output_tokens == 40
    assert GeminiProvider.from_response(gen([{"text": "..."}], "MAX_TOKENS")[1]).stop_reason == "max_tokens"
    assert GeminiProvider.from_response(gen([], "SAFETY")[1]).stop_reason == "refusal"
    assert GeminiProvider.from_response((200, {"promptFeedback": {"blockReason": "SAFETY"}})[1]).stop_reason == "refusal"

def test_create_message_shape_and_structured(prov):
    p, calls = prov([gen([{"text": '{"a": 1}'}])])
    m = p.create_message(model="gemini-2.5-pro", max_tokens=700, messages=[{"role": "user", "content": "p"}], system="s", schema={"type": "object", "properties": {"a": {"type": ["integer", "null"]}}, "additionalProperties": False})
    b = calls[0]["body"]
    assert calls[0]["path"].endswith("/models/gemini-2.5-pro:generateContent") and calls[0]["key"] == "g-key"
    assert b["systemInstruction"] == {"parts": [{"text": "s"}]} and b["generationConfig"]["maxOutputTokens"] == 700
    assert b["generationConfig"]["responseMimeType"] == "application/json" and b["generationConfig"]["responseSchema"]["properties"]["a"] == {"type": "integer", "nullable": True}
    assert m.content[0].text == '{"a": 1}'

def test_llm_tool_loop_through_gemini(prov):
    p, calls = prov([gen([{"functionCall": {"name": "lookup", "args": {"q": "x"}}}]), gen([{"text": "1건입니다."}])])
    llm.reset(); llm.configure({"provider": "gemini", "api_key": "g-key", "model": "gemini-2.5-flash"}, {}, [])
    try:
        res = llm.run_tools("질문", "sys", [{"name": "lookup", "description": "d", "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}], {"lookup": lambda q: [{"id": 1}]})
        assert res["text"] == "1건입니다." and res["turns"] == 2
        parts = calls[1]["body"]["contents"]
        assert parts[-2]["parts"][0]["functionCall"]["name"] == "lookup" and parts[-1]["parts"][0]["functionResponse"] == {"name": "lookup", "response": {"result": [{"id": 1}]}}
        assert llm.LAST["cost_usd"] == round(50 / 1e6 * 0.30 + 10 / 1e6 * 2.50, 6)
    finally: llm.reset()

def test_llm_404_then_next_model_and_refusal(prov):
    p, calls = prov([(404, {"error": {"message": "models/gemini-2.5-pro is not found for API version v1beta"}}), gen([{"text": '{"ok": true}'}]), gen([], "SAFETY")])
    llm.reset(); llm.configure({"provider": "gemini", "api_key": "g-key"}, {}, [])
    try:
        assert llm.ask_json("p") == {"ok": True} and [c["path"].split("/models/")[1].split(":")[0] for c in calls] == ["gemini-2.5-pro", "gemini-2.5-flash"]
        with pytest.raises(llm.Refused): llm.ask("p")
    finally: llm.reset()
    assert "429" in p.explain_error(providers.HttpError(429, "quota")) and p.is_not_found(providers.HttpError(404, "x"))

def test_model_list_filters_generate_content(prov):
    p, calls = prov([(200, {"models": [{"name": "models/gemini-2.5-pro", "displayName": "Gemini 2.5 Pro", "supportedGenerationMethods": ["generateContent"]},
                                       {"name": "models/embedding-001", "displayName": "E", "supportedGenerationMethods": ["embedContent"]}]})])
    assert p.list_models() == [{"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "created": ""}]
