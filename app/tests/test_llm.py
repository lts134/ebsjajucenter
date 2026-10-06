"""llm.py를 가짜 클라이언트로 검증: 구조화 출력, 400 폴백, 404 모델 대체, 코드펜스 파싱, 재요청, 비용 추정, 오류 설명."""
import pytest, anthropic, llm
try: import httpx2 as httpx
except ImportError: import httpx

class _U: input_tokens = 1000; output_tokens = 300
class _B:
    def __init__(s, t): s.text, s.type = t, "text"
class _M:
    def __init__(s, t): s.content, s.usage, s.stop_reason = [_B(t)], _U(), "end_turn"

def err(cls, status, msg):
    req = httpx.Request("POST", "https://x"); return cls(msg, response=httpx.Response(status, request=req, json={"error": {"message": msg}}), body=None)

import providers

class Fake(providers.AnthropicProvider):
    """실제 SDK 대신 각본대로 응답하는 공급자. create_message 호출 인자를 calls에 기록."""
    def __init__(s, script): super().__init__({"api_key": "sk-test"}); s.script, s.calls = list(script), []
    def create_message(s, **kw):
        s.calls.append(kw); r = s.script.pop(0)
        if isinstance(r, Exception): raise r
        return _M(r)

@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    llm.reset()
    yield
    llm.reset()

def use(monkeypatch, fake): monkeypatch.setattr(llm, "_provider", lambda: fake); return fake

def test_structured_output_used_and_recorded(monkeypatch):
    f = use(monkeypatch, Fake(['{"a": 1}']))
    assert llm.ask_json("p", schema={"type": "object"}) == {"a": 1}
    assert f.calls[0]["schema"] is not None and llm.LAST["structured"] is True and llm.LAST["cost_usd"] == 0.0075

def test_400_on_output_config_falls_back_to_text_and_remembers(monkeypatch):
    f = use(monkeypatch, Fake([err(anthropic.BadRequestError, 400, "output_config: extra inputs"), '```json\n{"a": 2}\n```', '{"a": 3}']))
    assert llm.ask_json("p", schema={"type": "object"}) == {"a": 2}
    assert [(c["schema"] is not None) for c in f.calls[:2]] == [True, False] and llm.LAST["structured"] is False
    llm.ask_json("p", schema={"type": "object"})
    assert f.calls[2]["schema"] is None            # 같은 모델엔 다시 시도하지 않음

def test_404_moves_to_next_candidate(monkeypatch):
    f = use(monkeypatch, Fake([err(anthropic.NotFoundError, 404, "model"), '{"ok": true}']))
    llm.ask_json("p")
    assert [c["model"] for c in f.calls] == llm.CANDIDATES[:2] and llm.model_label() == llm.CANDIDATES[1]

def test_other_400_is_raised(monkeypatch):
    use(monkeypatch, Fake([err(anthropic.BadRequestError, 400, "credit balance is too low")]))
    with pytest.raises(anthropic.BadRequestError): llm.ask("p")

def test_broken_json_retries_once_then_parses(monkeypatch):
    f = use(monkeypatch, Fake(["not json", '{"b": 1}']))
    assert llm.ask_json("p") == {"b": 1} and len(f.calls) == 2 and "재시도" in llm.LOG[1]["purpose"]

def test_parse_json_array_and_noise():
    assert llm.parse_json('설명: [{"x":1}] 끝', expect="array") == [{"x": 1}]
    with pytest.raises(ValueError): llm.parse_json("없음")

def test_usage_summary_and_pricing_unknown(monkeypatch):
    use(monkeypatch, Fake(['{"a":1}', '{"a":1}']))
    llm.ask("p"); monkeypatch.setenv("CLAUDE_MODEL", "claude-unknown-9"); llm._GLOBAL_STATE["resolved"] = None; llm.ask("p")
    u = llm.usage_summary()
    assert u["calls"] == 2 and u["cost_unknown_calls"] == 1 and u["cost_usd"] == 0.0075

def test_explain_error_messages():
    assert "401" in llm.explain_error(err(anthropic.AuthenticationError, 401, "x"))
    assert "크레딧" in llm.explain_error(err(anthropic.BadRequestError, 400, "credit balance is too low"))
    assert "워크스페이스" in llm.explain_error(err(anthropic.PermissionDeniedError, 403, "key is not scoped to a workspace; pass anthropic-workspace-id"))
    assert "모델명" in llm.explain_error(err(anthropic.NotFoundError, 404, "x"))
    assert "한도" in llm.explain_error(err(anthropic.RateLimitError, 429, "x"))
