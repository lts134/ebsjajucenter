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
    t1 = threading.Thread(target=worker, args=("A", "sk-A", "claude-sonnet-4-6")); t2 = threading.Thread(target=worker, args=("B", "sk-B", "claude-haiku-4-5-20251001"))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert seen["A"] == ("sk-A", "claude-sonnet-4-6", True, True) and seen["B"] == ("sk-B", "claude-haiku-4-5-20251001", True, True)
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
