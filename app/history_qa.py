"""⑧ 이력에 묻기: 자연어 질문 → Claude가 이력 DB 조회 도구를 골라 호출 → 조회 결과만 근거로 답한다.
도구는 읽기 전용이며 DB 함수를 그대로 감싼다. 키가 없으면 keyword_search(규칙)만 제공."""
import db, normalize, suggest, llm

TOOLS = [
    {"name": "search_requests", "description": "요청 주체·제목·원문·항목·지표에 키워드가 포함된 요구서를 찾는다(최근순).",
     "input_schema": {"type": "object", "properties": {"keyword": {"type": "string", "description": "검색어(예: 등원율, 감사실, 2026-06-30)"}}, "required": ["keyword"]}},
    {"name": "request_detail", "description": "요구서 1건의 항목·제출본·초안·검토 이력.",
     "input_schema": {"type": "object", "properties": {"request_id": {"type": "integer"}}, "required": ["request_id"]}},
    {"name": "submission_values", "description": "제출본의 확정 수치(센터별 값·정의·집계기간·추출시점·원자료 버전·출처). center로 좁힐 수 있음.",
     "input_schema": {"type": "object", "properties": {"submission_id": {"type": "integer"}, "center": {"type": "string"}}, "required": ["submission_id"]}},
    {"name": "past_values_for", "description": "특정 지표·기준일로 과거에 제출한 값 전부(어느 기관에 언제 냈는지 포함).",
     "input_schema": {"type": "object", "properties": {"indicator": {"type": "string"}, "base_date": {"type": "string", "description": "YYYY-MM-DD"}}, "required": ["indicator", "base_date"]}},
    {"name": "diff_reasons", "description": "담당자가 입력한 수치 차이 사유 기록(최근순). indicator로 좁힐 수 있음.",
     "input_schema": {"type": "object", "properties": {"indicator": {"type": "string"}}}},
    {"name": "overview", "description": "요구서 현황(기한·확정 제출본 수)과 요청 주체별·반복 지표 통계.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "indicators", "description": "지표 사전(정규 지표명과 동의어)과 데이터 카탈로그(출처·담당).", "input_schema": {"type": "object", "properties": {}}},
]

def _values(submission_id: int, center: str | None = None):
    rows = db.get_values(submission_id)
    if center: rows = [r for r in rows if str(r.get("center")) == center]
    return [{k: v for k, v in r.items() if k not in ("id", "submission_id")} for r in rows]

HANDLERS = {
    "search_requests": lambda keyword: db.search_requests(keyword),
    "request_detail": lambda request_id: db.request_detail(int(request_id)),
    "submission_values": lambda submission_id, center=None: _values(int(submission_id), center),
    "past_values_for": lambda indicator, base_date: [{k: r[k] for k in ("center", "value", "base_date", "submitted_date", "requester", "request_title", "definition", "calc_period", "extract_date", "source_version")} for r in db.past_values_for(indicator, base_date)],
    "diff_reasons": lambda indicator=None: db.all_reasons(indicator),
    "overview": lambda: {"requests": db.request_overview(), "by_requester": db.requester_stats()[0], "by_indicator": db.requester_stats()[1]},
    "indicators": lambda: {"지표 사전": normalize.CANON, "데이터 카탈로그": {k: {"출처": v[0], "담당": v[1]} for k, v in suggest.CATALOG.items()}},
}

SYSTEM = """당신은 EBS 지역교육협력부의 대외 요구자료 이력 DB를 조회해 답하는 보조입니다.
규칙:
(1) 반드시 도구로 조회한 결과만 근거로 답하고, 조회되지 않은 것은 '이력에 없음'이라고 말합니다. 원인·배경을 추측하지 않습니다('~로 보입니다', '~와 연관' 금지). 같은 지표·기준일의 제출값이 서로 다른 센터가 보이면 답하기 전에 반드시 diff_reasons를 호출해 기록된 사유를 그대로 인용하고, 기록이 없으면 '사유 기록 없음'이라고 적습니다.
(2) 수치는 레코드 값을 그대로 인용합니다. 평균·합계·차이값·증감률을 계산하지 않습니다. 두 값이 다르면 두 값을 나란히 보여 주고 '다름'이라고만 표시합니다.
(3) 답에는 근거가 된 요구번호(#id)·제출본번호·제출일·요청 주체를 적습니다.
(4) 날짜 표현('작년', '7월')은 먼저 search_requests나 past_values_for로 범위를 확인합니다.
(5) 한국어 설명체로 간결하게. 이모지·장식 기호를 쓰지 않고, 도구 이름(search_requests, diff_reasons 등)을 답에 쓰지 않습니다('차이 사유 기록'처럼 우리말로). 표가 적절하면 마크다운 표. 사용자에게 "조회를 요청해 달라"고 하지 말고 필요한 도구를 직접 호출합니다."""

def ask(question: str) -> dict:
    """{"text", "trace", "turns", "how"}"""
    if not llm.available():
        return {"text": None, "trace": [], "turns": 0, "how": "키 없음 — 키워드 검색"}
    try:
        r = llm.run_tools(question, SYSTEM, TOOLS, HANDLERS, max_turns=8, max_tokens=1500, purpose="이력 질의")
        r["how"] = f"Claude({llm.model_label()}) 도구 호출 {len(r['trace'])}회"; return r
    except Exception as e:
        return {"text": None, "trace": [], "turns": 0, "how": f"Claude 오류: {llm.explain_error(e)}"}

def keyword_search(question: str) -> dict:
    """규칙 경로: 질문에서 2글자 이상 토큰을 뽑아 요구서·사유를 검색."""
    import re
    toks = [t for t in re.findall(r"[가-힣A-Za-z0-9\-]{2,}", question) if t not in ("어떻게", "언제", "무엇", "얼마", "있나", "있어", "했지", "했나", "냈지", "냈나", "제출", "자료", "이력", "요구", "기준")]
    reqs, seen = [], set()
    for t in toks:
        for r in db.search_requests(t):
            if r["id"] not in seen: seen.add(r["id"]); reqs.append(r)
    inds = [c for c in normalize.CANON if any(a in question for a in normalize.CANON[c])]
    reasons = [x for i in (inds or [None]) for x in db.all_reasons(i, limit=10)] if inds else []
    return {"tokens": toks, "requests": reqs[:20], "indicators": inds, "reasons": reasons}
