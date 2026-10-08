"""Claude Platform on AWS — Anthropic이 AWS 인프라 위에서 직접 운영하는 Claude API(IAM 인증·AWS 청구, 기능은 공식 API와 동일).
회사가 AWS 계약 안에서 Claude를 쓰기로 했을 때 고른다. 모델 ID는 공식과 같다(claude-opus-5-5 등).
필요: pip install "anthropic[aws]" · 환경변수 AWS_REGION, ANTHROPIC_AWS_WORKSPACE_ID · AWS 자격증명(환경변수·프로파일·인스턴스 역할 중 하나).
서버 환경변수 LLM_PROVIDER=aws 로 기본 선택."""
from __future__ import annotations
from providers import AnthropicProvider, MAX_RETRIES, TIMEOUT_S


class AwsProvider(AnthropicProvider):
    name = "aws"
    label = "Claude Platform on AWS"
    fields = [
        {"key": "aws_region", "label": "AWS 리전(예: us-east-1) — 비우면 환경변수 AWS_REGION", "secret": False, "required": False, "env": "AWS_REGION"},
        {"key": "workspace_id", "label": "워크스페이스 ID — 비우면 환경변수 ANTHROPIC_AWS_WORKSPACE_ID", "secret": True, "required": False, "env": "ANTHROPIC_AWS_WORKSPACE_ID"},
        {"key": "effort", "label": "추론 강도(effort) — low·medium·high. 비우면 모델 기본", "secret": False, "required": False, "env": "LLM_EFFORT"},
    ]
    docs_url = "https://platform.claude.com/docs/en/build-with-claude/claude-platform-on-aws"

    def ready(self) -> bool:                       # 자격증명은 SDK가 AWS 표준 순서로 찾으므로 리전·워크스페이스만 있으면 시도한다
        return bool(self.value("aws_region") and self.value("workspace_id"))

    def _client(self):
        import anthropic
        return anthropic.AnthropicAWS(aws_region=self.value("aws_region") or None, workspace_id=self.value("workspace_id") or None,
                                      max_retries=MAX_RETRIES, timeout=TIMEOUT_S)

    def _ws_kwargs(self, fn) -> dict: return {}    # 워크스페이스는 클라이언트 생성 때 이미 넘김


PROVIDER = AwsProvider
