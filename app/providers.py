"""LLM 공급자 모듈(플러그인). llm.py는 여기 정의된 인터페이스만 쓰므로, 다른 백엔드(사내 게이트웨이·다른 클라우드 등)는
이 파일을 건드리지 않고 `provider_<이름>.py` 파일 하나를 app 폴더에 추가하면 설정 화면의 공급자 목록에 자동으로 나타난다.

공급자가 지켜야 할 것(인터페이스):
    class Provider:
        name: str                      # 설정 화면에 표시되는 식별자(예: "anthropic")
        label: str                     # 사람이 읽는 이름
        fields: list[dict]             # 설정 화면 입력칸 정의 [{"key": "api_key", "label": "...", "secret": True, "env": "ANTHROPIC_API_KEY"}, ...]
        default_models: list[str]      # 모델 지정이 없을 때 순서대로 시도할 후보
        pricing: dict[str, (입력$/1M, 출력$/1M)]
        def __init__(self, cfg: dict)  # fields의 key → 값
        def ready(self) -> bool        # 호출에 필요한 값이 다 있는가(없으면 앱은 규칙 기반으로 동작)
        def create_message(self, *, model, max_tokens, messages, system=None, tools=None, schema=None) -> 응답
        def list_models(self) -> list[dict]   # [{"id","name","created"}]
        def is_not_found(self, e) -> bool     # 모델 없음 → 다음 후보로 넘어가도 되는 오류
        def is_bad_request(self, e) -> bool   # 요청 형식 거부(구조화 출력 미지원 판단에 사용)
        def explain_error(self, e) -> str | None   # 담당자용 안내문. 모르면 None
    응답 객체는 Anthropic SDK Message와 같은 모양이면 된다: .content(블록 리스트: .type == "text"면 .text, "tool_use"면 .id/.name/.input),
    .stop_reason("end_turn" | "tool_use" | ...), .usage.input_tokens / .usage.output_tokens. 다른 백엔드는 아래 SimpleMessage로 감싸면 된다.

예시 플러그인 골격은 docs/provider_template.py 참고."""
from __future__ import annotations
import importlib, inspect, os, pkgutil
from dataclasses import dataclass, field
from pathlib import Path

MAX_RETRIES = 3
TIMEOUT_S = 90.0

# ---------- 응답을 통일된 모양으로 만들 때 쓰는 간단한 자료형(다른 백엔드용) ----------
@dataclass
class SimpleUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None

@dataclass
class TextBlock:
    text: str
    type: str = "text"

@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"

@dataclass
class SimpleMessage:
    content: list = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: SimpleUsage = field(default_factory=SimpleUsage)


class Provider:
    """기본(추상) 공급자. 서브클래스가 name·fields·create_message 등을 채운다."""
    name = "base"
    label = "기본"
    fields: list[dict] = []
    default_models: list[str] = []
    pricing: dict[str, tuple[float, float]] = {}
    docs_url = ""

    def __init__(self, cfg: dict | None = None):
        self.cfg = {f["key"]: (cfg or {}).get(f["key"]) or os.environ.get(f.get("env", ""), "") for f in self.fields}

    def value(self, key: str) -> str:
        return (self.cfg.get(key) or "").strip()

    def ready(self) -> bool:
        return all(self.value(f["key"]) for f in self.fields if f.get("required"))

    def create_message(self, *, model, max_tokens, messages, system=None, tools=None, schema=None):
        raise NotImplementedError

    def list_models(self) -> list[dict]:
        return [{"id": m, "name": m, "created": ""} for m in self.default_models]

    def is_not_found(self, e: Exception) -> bool: return False
    def is_bad_request(self, e: Exception) -> bool: return False
    def explain_error(self, e: Exception) -> str | None: return None


class AnthropicProvider(Provider):
    """Anthropic Claude API(공식 SDK). 기본 공급자."""
    name = "anthropic"
    label = "Anthropic Claude API"
    fields = [
        {"key": "api_key", "label": "API 키", "secret": True, "required": True, "env": "ANTHROPIC_API_KEY"},
        {"key": "workspace_id", "label": "워크스페이스 ID(wrkspc_…) — 키가 워크스페이스에 묶여 있지 않을 때만", "secret": True, "required": False, "env": "ANTHROPIC_WORKSPACE_ID"},
    ]
    # 2026-09-28 API 모델 목록 조회로 확인한 실존 ID. 설정 화면 '연결 테스트'에서 다시 조회해 고를 수 있다.
    default_models = ["claude-sonnet-4-6", "claude-sonnet-5", "claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"]
    # USD / 100만 토큰(입력, 출력). 출처: Anthropic 공개 가격표(2026-09-25 기준 캐시). 실제 청구는 콘솔 확인 [확인 필요].
    pricing = {
        "claude-sonnet-4-6": (3.00, 15.00), "claude-sonnet-5": (2.00, 10.00), "claude-sonnet-5-5": (2.00, 10.00),
        "claude-haiku-4-5-20251001": (1.00, 5.00), "claude-haiku-4-5": (1.00, 5.00),
        "claude-opus-5-5": (4.00, 20.00), "claude-opus-5": (5.00, 25.00), "claude-opus-4-8": (5.00, 25.00),
        "claude-opus-4-7": (5.00, 25.00), "claude-opus-4-6": (5.00, 25.00),
    }
    docs_url = "https://console.anthropic.com"

    def _client(self):
        import anthropic
        return anthropic.Anthropic(api_key=self.value("api_key"), max_retries=MAX_RETRIES, timeout=TIMEOUT_S)

    def _ws_kwargs(self, fn) -> dict:
        ws = self.value("workspace_id")
        if not ws: return {}
        try: params = inspect.signature(fn).parameters
        except (TypeError, ValueError): params = {}
        return {"workspace_id": ws} if "workspace_id" in params else {"extra_headers": {"anthropic-workspace-id": ws}}

    def create_message(self, *, model, max_tokens, messages, system=None, tools=None, schema=None):
        client = self._client()
        kw = dict(model=model, max_tokens=max_tokens, messages=messages, **self._ws_kwargs(client.messages.create))
        if system: kw["system"] = system
        if tools: kw["tools"] = tools
        if schema is not None: kw["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        return client.messages.create(**kw)

    def list_models(self) -> list[dict]:
        client = self._client()
        return [{"id": m.id, "name": getattr(m, "display_name", m.id), "created": str(getattr(m, "created_at", ""))[:10]}
                for m in client.models.list(limit=100, **self._ws_kwargs(client.models.list))]

    def is_not_found(self, e):
        import anthropic
        return isinstance(e, anthropic.NotFoundError)

    def is_bad_request(self, e):
        import anthropic
        return isinstance(e, anthropic.BadRequestError)

    def explain_error(self, e):
        m = str(e)
        if "anthropic-workspace-id" in m or "not scoped to a workspace" in m:      # 400으로도 403으로도 올 수 있음
            return "이 키는 워크스페이스에 묶여 있지 않아 워크스페이스 ID가 필요합니다. 설정 화면의 '워크스페이스 ID'에 콘솔(Settings → Workspaces)의 wrkspc_… 값을 넣거나, 워크스페이스 안에서 만든 키를 쓰세요."
        try:
            import anthropic
        except ImportError:
            return None
        if isinstance(e, anthropic.AuthenticationError): return "키가 잘못됐거나 만료됐습니다(401). 콘솔에서 키를 다시 확인하세요."
        if isinstance(e, anthropic.PermissionDeniedError): return "이 키로는 허용되지 않는 요청입니다(403). 키 권한·워크스페이스 설정을 확인하세요."
        if isinstance(e, anthropic.NotFoundError): return "모델명이 없습니다(404). 연결 테스트의 모델 목록에서 고르세요."
        if isinstance(e, anthropic.RateLimitError): return "호출 한도 초과(429). 잠시 후 다시 시도하세요(자동 재시도 후에도 실패)."
        if isinstance(e, anthropic.BadRequestError):
            if "credit" in m.lower() or "billing" in m.lower():
                return "API 크레딧 잔액이 부족합니다. 콘솔(Plans & Billing)에서 충전한 뒤 다시 시도하세요. 키·워크스페이스 설정은 정상입니다."
            return f"요청 형식 오류(400): {m[:200]}"
        if isinstance(e, anthropic.APIStatusError) and e.status_code >= 500: return f"Anthropic 서버 오류({e.status_code}). 자동 재시도 후에도 실패했습니다. 잠시 후 다시 시도하세요."
        if isinstance(e, anthropic.APITimeoutError): return f"응답 시간 초과({TIMEOUT_S:.0f}초). 네트워크 상태를 확인하거나 다시 시도하세요."
        if isinstance(e, anthropic.APIConnectionError): return "API 서버에 연결하지 못했습니다. 사내망 프록시·방화벽에서 api.anthropic.com 허용 여부를 확인하세요."
        return None


class NoneProvider(Provider):
    """AI를 쓰지 않음 — 모든 기능이 규칙 기반으로 동작. 사내망에서 외부 API가 막혔을 때."""
    name = "none"
    label = "사용 안 함(규칙 기반만)"
    fields = []
    def ready(self) -> bool: return False
    def create_message(self, **kw): raise RuntimeError("AI 공급자가 '사용 안 함'으로 설정돼 있습니다.")


# ---------- 레지스트리 + 플러그인 자동 발견 ----------
PROVIDERS: dict[str, type[Provider]] = {AnthropicProvider.name: AnthropicProvider, NoneProvider.name: NoneProvider}

def _discover():
    """app 폴더의 provider_*.py 모듈에서 PROVIDER(클래스)를 찾아 등록한다. 실패한 플러그인은 건너뛰고 _errors에 기록."""
    here = Path(__file__).parent
    for info in pkgutil.iter_modules([str(here)]):
        if not info.name.startswith("provider_"): continue
        try:
            mod = importlib.import_module(info.name)
            cls = getattr(mod, "PROVIDER", None)
            if inspect.isclass(cls) and issubclass(cls, Provider) and cls.name not in PROVIDERS:
                PROVIDERS[cls.name] = cls
        except Exception as e:                     # 플러그인 오류가 앱을 멈추지 않게
            _errors[info.name] = f"{type(e).__name__}: {e}"
_errors: dict[str, str] = {}
_discover()

def names() -> list[str]:
    return list(PROVIDERS)

def get(name: str | None, cfg: dict | None = None) -> Provider:
    cls = PROVIDERS.get((name or "").strip() or os.environ.get("LLM_PROVIDER", "anthropic"), AnthropicProvider)
    return cls(cfg or {})
