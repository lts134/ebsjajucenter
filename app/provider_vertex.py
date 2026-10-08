"""Google Cloud Vertex AI — Google이 운영하는 Claude(파트너 운영: 기능 일부 제한).
GCP 프로젝트의 서비스 계정(ADC)으로 인증하므로 API 키를 따로 두지 않아도 된다(Cloud Run이면 실행 서비스 계정에 Vertex 권한만 주면 됨).
필요: pip install "anthropic[vertex]" · 환경변수 ANTHROPIC_VERTEX_PROJECT_ID, CLOUD_ML_REGION.
모델 ID는 Vertex Model Garden의 Claude 페이지에서 복사해 설정 화면 '모델' 칸이나 환경변수 LLM_MODEL에 넣는다 [확인 필요: 리전마다 다름].
서버 환경변수 LLM_PROVIDER=vertex 로 기본 선택."""
from __future__ import annotations
from providers import AnthropicProvider, MAX_RETRIES, TIMEOUT_S


class VertexProvider(AnthropicProvider):
    name = "vertex"
    label = "Google Vertex AI"
    fields = [
        {"key": "project_id", "label": "GCP 프로젝트 ID — 비우면 환경변수 ANTHROPIC_VERTEX_PROJECT_ID", "secret": False, "required": False, "env": "ANTHROPIC_VERTEX_PROJECT_ID"},
        {"key": "region", "label": "리전(예: us-east5, asia-northeast1) — 비우면 환경변수 CLOUD_ML_REGION", "secret": False, "required": False, "env": "CLOUD_ML_REGION"},
        {"key": "effort", "label": "추론 강도(effort) — low·medium·high. 비우면 모델 기본", "secret": False, "required": False, "env": "LLM_EFFORT"},
    ]
    default_models: list[str] = []                 # Vertex 모델 ID는 리전·버전마다 달라 여기 적지 않는다 — 설정 화면 '모델' 칸에 Model Garden의 ID를 넣는다
    pricing = {}                                   # Vertex 요금은 GCP 청구 기준 [확인 필요]
    docs_url = "https://platform.claude.com/docs/en/build-with-claude/claude-on-vertex-ai"

    def ready(self) -> bool: return bool(self.value("project_id") and self.value("region"))

    def _client(self):
        import anthropic
        return anthropic.AnthropicVertex(project_id=self.value("project_id"), region=self.value("region"), max_retries=MAX_RETRIES, timeout=TIMEOUT_S)

    def _ws_kwargs(self, fn) -> dict: return {}

    @staticmethod
    def wants_fallback(model: str) -> bool: return False     # 서버 측 거절 대체(fallbacks)는 Vertex에 없다

    def list_models(self) -> list[dict]: return []            # Vertex는 모델 목록을 Model Garden에서 본다


PROVIDER = VertexProvider
