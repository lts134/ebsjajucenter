"""OpenAI 호환 공급자: 요청 변환(메시지·도구·스키마), 응답 변환(도구 호출·종료 사유·토큰), llm.py와의 결합(도구 루프·구조화 출력·404 대체), 오류 안내."""
import json, httpx, pytest
import llm, providers
from provider_openai import OpenAIProvider

def _transport(script):
    """script: 요청마다 (status, json) 또는 callable(request)->(status, json). 받은 요청 본문을 calls에 기록."""
    calls = []
    def handler(req: httpx.Request):
        body = json.loads(req.content or b"{}") if req.content else {}
        calls.append({"path": req.url.path, "body": body, "auth": req.headers.get("authorization")})
        item = script.pop(0); status, data = item(req) if callable(item) else item
        return httpx.Response(status, json=data)
    return httpx.MockTransport(handler), calls

def chat(text=None, tool_calls=None, finish="stop", usage=(100, 20)):
    msg = {"role": "assistant", "content": text}
    if tool_calls: msg["tool_calls"] = [{"id": i, "type": "function", "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}} for i, n, a in tool_calls]
    return 200, {"choices": [{"message": msg, "finish_reason": finish}], "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}

@pytest.fixture
def prov(monkeypatch):
    def make(script):
        t, calls = _transport(script); monkeypatch.setattr(OpenAIProvider, "transport", t)
        return OpenAIProvider({"api_key": "sk-o"}), calls
    yield make
    monkeypatch.setattr(OpenAIProvider, "transport", None)

def test_message_translation_roundtrip():
    msgs = [{"role": "user", "content": "질문"},
            {"role": "assistant", "content": [providers.TextBlock(text="조회할게요"), providers.ToolUseBlock(id="c1", name="search_requests", input={"keyword": "등원율"})]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "[{\"id\": 1}]", "is_error": False}]}]
    out = OpenAIProvider.to_messages(msgs, "시스템")
    assert out[0] == {"role": "system", "content": "시스템"} and out[1] == {"role": "user", "content": "질문"}
    assert out[2]["role"] == "assistant" and out[2]["content"] == "조회할게요" and out[2]["tool_calls"][0]["function"] == {"name": "search_requests", "arguments": '{"keyword": "등원율"}'}
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": '[{"id": 1}]'} and len(out) == 4          # 도구 결과만 있는 user 턴은 빈 user 메시지를 만들지 않음
    tools = OpenAIProvider.to_tools([{"name": "x", "description": "d", "input_schema": {"type": "object", "properties": {"k": {"type": "string"}}}}])
    assert tools[0]["type"] == "function" and tools[0]["function"]["parameters"]["properties"]["k"] == {"type": "string"}

def test_response_translation():
    m = OpenAIProvider.from_response(chat(None, [("c9", "indicator_history", {"indicator": "등원율"})], "tool_calls")[1])
    assert m.stop_reason == "tool_use" and m.content[0].type == "tool_use" and m.content[0].id == "c9" and m.content[0].input == {"indicator": "등원율"} and m.usage.input_tokens == 100
    assert OpenAIProvider.from_response(chat("잘림", finish="length")[1]).stop_reason == "max_tokens"
    assert OpenAIProvider.from_response(chat(None, finish="content_filter")[1]).stop_reason == "refusal"
    assert OpenAIProvider.from_response(chat("답")[1]).content[0].text == "답"

def test_create_message_request_shape_and_structured(prov):
    p, calls = prov([chat('{"a": 1}')])
    m = p.create_message(model="gpt-5", max_tokens=500, messages=[{"role": "user", "content": "p"}], system="s", schema={"type": "object", "properties": {"a": {"type": "integer"}}})
    b = calls[0]["body"]
    assert calls[0]["path"] == "/v1/chat/completions" and calls[0]["auth"] == "Bearer sk-o"
    assert b["model"] == "gpt-5" and b["max_completion_tokens"] == 500 and "max_tokens" not in b and "temperature" not in b
    assert b["response_format"]["type"] == "json_schema" and b["response_format"]["json_schema"]["schema"]["properties"]["a"] == {"type": "integer"}
    assert m.content[0].text == '{"a": 1}'

def test_llm_tool_loop_through_openai(prov):
    p, calls = prov([chat(None, [("c1", "lookup", {"q": "x"})], "tool_calls"), chat("결과는 1건입니다.")])
    llm.reset(); llm.configure({"provider": "openai", "api_key": "sk-o", "model": "gpt-5"}, {}, [])
    try:
        res = llm.run_tools("질문", "sys", [{"name": "lookup", "description": "d", "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}], {"lookup": lambda q: [{"id": 1, "q": q}]})
        assert res["text"] == "결과는 1건입니다." and res["trace"][0]["tool"] == "lookup" and res["turns"] == 2
        second = calls[1]["body"]["messages"]
        assert second[-1] == {"role": "tool", "tool_call_id": "c1", "content": '[{"id": 1, "q": "x"}]'} and second[-2]["tool_calls"][0]["id"] == "c1"
        assert llm.LAST["cost_usd"] == round(100 / 1e6 * 1.25 + 20 / 1e6 * 10.0, 6) and llm.model_label() == "gpt-5"
    finally: llm.reset()

def test_llm_404_moves_to_next_model_and_errors_explained(prov):
    p, calls = prov([(404, {"error": {"message": "The model `gpt-5.1` does not exist"}}), chat('{"ok": true}')])
    llm.reset(); llm.configure({"provider": "openai", "api_key": "sk-o"}, {}, [])
    try:
        assert llm.ask_json("p") == {"ok": True} and [c["body"]["model"] for c in calls] == ["gpt-5.1", "gpt-5"]
    finally: llm.reset()
    e = providers.HttpError(429, "insufficient_quota"); assert "429" in p.explain_error(e) and p.is_bad_request(providers.HttpError(400, "x"))
    assert "401" in p.explain_error(providers.HttpError(401, "bad key"))

def test_base_url_override_and_model_list(prov):
    p, calls = prov([(200, {"data": [{"id": "gpt-5"}, {"id": "text-embedding-3-small"}, {"id": "gpt-4.1"}]})])
    p2 = OpenAIProvider({"api_key": "k", "base_url": "https://gateway.example.com/openai/v1/"}); p2.transport = OpenAIProvider.transport
    assert p2.base() == "https://gateway.example.com/openai/v1"
    assert [m["id"] for m in p2.list_models()] == ["gpt-4.1", "gpt-5"] and calls[0]["path"] == "/openai/v1/models"
