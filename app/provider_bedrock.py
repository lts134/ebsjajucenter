"""Amazon Bedrock — AWS가 운영하는 Claude(파트너 운영: 기능 일부 제한, 모델 ID에 anthropic. 접두).
회사가 Bedrock으로만 외부 AI를 허용할 때 고른다.
필요: pip install "anthropic[bedrock]" · 환경변수 AWS_REGION · AWS 자격증명(환경변수·프로파일·인스턴스 역할).
모델 ID는 Bedrock 콘솔의 모델 카탈로그에서 복사해 설정 화면 '모델' 칸이나 환경변수 LLM_MODEL에 넣는다 [확인 필요: 계정·리전마다 다름].
서버 환경변수 LLM_PROVIDER=bedrock 로 기본 선택."""
from __future__ import annotations
from providers import AnthropicProvider, MAX_RETRIES, TIMEOUT_S


class BedrockProvider(AnthropicProvider):
    name = "bedrock"
    label = "Amazon Bedrock"
    fields = [
        {"key": "aws_region", "label": "AWS 리전(예: ap-northeast-2) — 비우면 환경변수 AWS_REGION", "secret": False, "required": False, "env": "AWS_REGION"},
        {"key": "aws_access_key", "label": "AWS 액세스 키 — 인스턴스 역할·프로파일을 쓰면 비움", "secret": True, "required": False, "env": "AWS_ACCESS_KEY_ID"},
        {"key": "aws_secret_key", "label": "AWS 시크릿 키", "secret": True, "required": False, "env": "AWS_SECRET_ACCESS_KEY"},
        {"key": "effort", "label": "추론 강도(effort) — low·medium·high. 비우면 모델 기본", "secret": False, "required": False, "env": "LLM_EFFORT"},
    ]
    default_models: list[str] = []                 # Bedrock 모델 ID는 계정·리전마다 달라 여기 적지 않는다 — 설정 화면 '모델' 칸에 콘솔의 ID를 넣는다
    pricing = {}                                   # Bedrock 요금은 AWS 청구 기준 [확인 필요]
    docs_url = "https://platform.claude.com/docs/en/build-with-claude/claude-on-amazon-bedrock"

    def ready(self) -> bool: return bool(self.value("aws_region"))

    def _client(self):
        import anthropic
        return anthropic.AnthropicBedrock(aws_region=self.value("aws_region") or None, aws_access_key=self.value("aws_access_key") or None,
                                          aws_secret_key=self.value("aws_secret_key") or None, max_retries=MAX_RETRIES, timeout=TIMEOUT_S)

    def _ws_kwargs(self, fn) -> dict: return {}

    @staticmethod
    def wants_fallback(model: str) -> bool: return False     # 서버 측 거절 대체(fallbacks)는 Bedrock에 없다

    def request_kwargs(self, **kw) -> dict:
        d = super().request_kwargs(**kw)
        d.pop("cache_control", None)               # 요청 상위 cache_control은 구형 Bedrock 통합(Opus 4.6 이전)이 400을 낸다 — 시스템 블록 표식만 둔다
        return d

    def list_models(self) -> list[dict]:           # Bedrock은 모델 목록 API가 다른 서비스(bedrock:ListFoundationModels)라 SDK로 조회하지 않는다
        return []


PROVIDER = BedrockProvider
