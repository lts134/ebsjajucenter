"""공급자 플러그인 템플릿. app/provider_<이름>.py 로 복사해 채운 뒤 앱을 다시 시작하면 설정 화면 '공급자' 목록에 나타난다.
llm.py는 providers.Provider 인터페이스만 호출하므로 기존 코드는 손대지 않는다.

예: 사내 LLM 게이트웨이(HTTP)를 붙이는 경우 — create_message()에서 요청을 보내고 응답을 SimpleMessage로 감싸 돌려준다."""
from providers import Provider, SimpleMessage, SimpleUsage, TextBlock, ToolUseBlock  # noqa: F401  (플러그인 작성 시 사용)


class MyGatewayProvider(Provider):
    name = "my_gateway"                       # 설정 화면 식별자(영문·숫자·밑줄)
    label = "사내 LLM 게이트웨이"              # 표시 이름
    fields = [                                # 설정 화면 입력칸. env를 적으면 서버 환경변수로도 받을 수 있다
        {"key": "api_key", "label": "게이트웨이 토큰", "secret": True, "required": True, "env": "MY_GATEWAY_TOKEN"},
        {"key": "base_url", "label": "게이트웨이 주소", "secret": False, "required": True, "env": "MY_GATEWAY_URL"},
    ]
    default_models = ["gateway-default"]      # 모델 지정이 없을 때 시도 순서
    pricing = {}                              # 모르면 비워 둠(비용 추정 생략)

    def create_message(self, *, model, max_tokens, messages, system=None, tools=None, schema=None):
        """messages: [{"role": "user"|"assistant", "content": 문자열 또는 블록 리스트}].
        tools가 오면 도구 호출(⑧)이고, schema가 오면 JSON 스키마에 맞는 출력을 기대한다(지원하지 않으면 무시해도 됨 — llm.py가 텍스트 파싱으로 대체).
        반환: SimpleMessage(content=[TextBlock(text=...)] 또는 [ToolUseBlock(id, name, input)], stop_reason="end_turn"|"tool_use", usage=SimpleUsage(...))"""
        raise NotImplementedError("여기에 HTTP 호출을 구현하세요")

    def list_models(self):
        return [{"id": m, "name": m, "created": ""} for m in self.default_models]

    def is_not_found(self, e):               # 모델이 없다는 오류면 True → llm.py가 다음 후보 모델로 넘어감
        return "model" in str(e).lower() and "not found" in str(e).lower()

    def is_bad_request(self, e):
        return "400" in str(e)

    def explain_error(self, e):              # 담당자에게 보여 줄 안내문. 모르면 None
        return None


PROVIDER = MyGatewayProvider                  # providers.py가 이 이름을 찾는다
