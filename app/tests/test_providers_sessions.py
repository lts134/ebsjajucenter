"""세션별 자격증명 격리와 공급자 플러그인 구조."""
import threading, sys
from pathlib import Path
import llm, providers

def test_available_follows_session_cfg_not_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False); llm.reset()
    assert llm.available() is False
    llm.configure({"provider": "anthropic", "api_key": "sk-a"}, {}, [])
    assert llm.available() is True and llm.provider_name() == "anthropic"
    llm.configure({"provider": "none"}, {}, [])
    assert llm.available() is False and llm.provider_name() == "none"
    llm.reset()

def test_env_key_is_fallback_when_session_blank(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env"); llm.reset()
    llm.configure({"provider": "anthropic", "api_key": ""}, {}, [])
    assert llm.available() is True and llm._provider().value("api_key") == "sk-env"
    llm.reset()

def test_two_threads_keep_separate_keys_models_and_logs(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False); llm.reset()
    seen = {}
    def worker(name, key, model):
        log = []; st = {}
        llm.configure({"provider": "anthropic", "api_key": key, "model": model}, st, log)
        import time; time.sleep(0.05)
        seen[name] = (llm._provider().value("api_key"), llm.model_label(), llm.log() is log, llm._state() is st)
    t1 = threading.Thread(target=worker, args=("A", "sk-A", "claude-sonnet-4-6")); t2 = threading.Thread(target=worker, args=("B", "sk-B", "claude-haiku-5-5"))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert seen["A"] == ("sk-A", "claude-sonnet-4-6", True, True) and seen["B"] == ("sk-B", "claude-haiku-5-5", True, True)
    assert llm.available() is False            # 메인 스레드에는 아무 설정도 새지 않음

def test_state_dict_persists_resolved_model_across_configures():
    llm.reset(); st = {}
    llm.configure({"provider": "anthropic", "api_key": "k"}, st, [])
    llm._state()["resolved"] = "claude-sonnet-5"
    llm.configure({"provider": "anthropic", "api_key": "k"}, st, [])   # 재실행(rerun) 흉내
    assert llm._model_order()[0] == "claude-sonnet-5"
    llm.reset()

def test_registry_has_builtin_providers_and_plugin_discovery(tmp_path, monkeypatch):
    assert {"anthropic", "none"} <= set(providers.names())
    # 템플릿을 app 폴더에 복사했을 때 발견되는지(임시 모듈로 흉내)
    app_dir = Path(providers.__file__).parent
    plug = app_dir / "provider_zz_test.py"
    plug.write_text(Path(app_dir / "docs" / "provider_template.py").read_text(encoding="utf-8").replace('name = "my_gateway"', 'name = "zz_test"'), encoding="utf-8")
    try:
        sys.modules.pop("provider_zz_test", None); providers._discover()
        assert "zz_test" in providers.names()
        p = providers.get("zz_test", {"api_key": "t", "base_url": "http://x"})
        assert p.ready() and p.label == "사내 LLM 게이트웨이"
    finally:
        plug.unlink(missing_ok=True); providers.PROVIDERS.pop("zz_test", None); sys.modules.pop("provider_zz_test", None)

def test_simple_message_shape_works_with_record():
    llm.reset()
    msg = providers.SimpleMessage(content=[providers.TextBlock("안녕")], usage=providers.SimpleUsage(10, 5))
    assert llm._record(msg, "x-model", 0.0, "t", False) == "안녕" and llm.LAST["input_tokens"] == 10 and llm.LAST["cost_usd"] is None
    llm.reset()


def test_anthropic_request_kwargs_effort_fallback_and_defaults():
    p = providers.AnthropicProvider({"api_key": "k"})
    kw = p.request_kwargs(model="claude-opus-5-5", max_tokens=8000, messages=[{"role": "user", "content": "x"}], system="s", schema={"type": "object"})
    assert kw["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}}} and kw["timeout"] == 90 + 8000 / 1000 * 20 and "thinking" not in kw and "temperature" not in kw
    p2 = providers.AnthropicProvider({"api_key": "k", "effort": "Low"})
    kw2 = p2.request_kwargs(model="claude-opus-5-5", max_tokens=100, messages=[])
    assert kw2["output_config"] == {"effort": "low"}
    assert providers.AnthropicProvider.wants_fallback("claude-opus-5-5") and providers.AnthropicProvider.wants_fallback("claude-fable-5-1") and providers.AnthropicProvider.wants_fallback("claude-sonnet-5-5")
    assert not providers.AnthropicProvider.wants_fallback("claude-haiku-5-5") and not providers.AnthropicProvider.wants_fallback("claude-sonnet-4-6")
    assert providers.AnthropicProvider.default_models[0] == "claude-opus-5-5" and providers.AnthropicProvider.pricing["claude-fable-5-1"] == (10.0, 50.0)
    assert providers.AnthropicProvider.timeout_for(32000) == 730 and llm.MAX_TOKENS_CAP == 32000 and llm.DEFAULT_MAX_TOKENS == 8000


def test_prompt_caching_kwargs_cost_and_effort_by_purpose(monkeypatch):
    p = providers.AnthropicProvider({"api_key": "k"})
    kw = p.request_kwargs(model="claude-opus-5-5", max_tokens=8000, messages=[{"role": "user", "content": "x"}], system="시스템", tools=[{"name": "t", "input_schema": {"type": "object"}}], cache=True, effort="low")
    assert kw["system"] == [{"type": "text", "text": "시스템", "cache_control": {"type": "ephemeral", "ttl": "1h"}}] and kw["cache_control"] == {"type": "ephemeral"} and kw["output_config"] == {"effort": "low"}
    kw2 = p.request_kwargs(model="claude-opus-5-5", max_tokens=100, messages=[], system="s")
    assert kw2["system"] == "s" and "cache_control" not in kw2 and "output_config" not in kw2                        # 단발 호출은 캐시 끔
    assert "output_config" not in p.request_kwargs(model="claude-haiku-4-5-20251001", max_tokens=10, messages=[], effort="low")   # 구형 모델엔 effort 안 보냄
    assert providers.AnthropicProvider.supports_effort("claude-sonnet-4-6") and providers.AnthropicProvider.supports_effort("claude-fable-5-1") and not providers.AnthropicProvider.supports_effort("claude-sonnet-4-5-20250929")
    # 캐시 토큰이 비용에 반영된다(쓰기 1.25배, 읽기 0.1배)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k"); llm.reset(); llm.configure({"provider": "anthropic", "api_key": "k"}, {}, [])
    try:
        assert llm.estimate_cost("claude-opus-5-5", 1000, 100) == round(1000 / 1e6 * 4 + 100 / 1e6 * 20, 6)
        assert llm.estimate_cost("claude-opus-5-5", 100, 100, cache_read=3000, cache_write=0) == round((100 + 300) / 1e6 * 4 + 100 / 1e6 * 20, 6)
        class U: input_tokens = 100; output_tokens = 50; cache_read_input_tokens = 2000; cache_creation_input_tokens = 400
        class B:
            type, text = "text", "ok"
        class M: content = [B()]; usage = U(); stop_reason = "end_turn"
        llm._record(M(), "claude-opus-5-5", 0.0, "대화 처리", False)
        assert llm.LAST["cache_read_tokens"] == 2000 and llm.LAST["cache_write_tokens"] == 400 and llm.LAST["cost_usd"] == round((100 + 400 * 1.25 + 2000 * 0.1) / 1e6 * 4 + 50 / 1e6 * 20, 6)
        assert llm.usage_summary()["cache_read_tokens"] == 2000
        assert llm._effort_for("지표 분류") == "low" and llm._effort_for("지표 분류(잘림 재시도)") == "low" and llm._effort_for("요구서 추출") is None and llm._effort_for("대화 처리") is None
        llm.configure({"provider": "anthropic", "api_key": "k", "effort": "high"}, {}, []); assert llm._effort_for("지표 분류") == "high"   # 설정 칸이 우선
    finally: llm.reset()
