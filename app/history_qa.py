"""⑧ 기록에 묻기: 자연어 질문 → Claude가 기록 DB 조회 도구를 골라 호출 → 조회 결과만 근거로 답한다.
도구는 읽기 전용이며 DB 함수를 그대로 감싼다. 키가 없으면 keyword_search(규칙)만 제공."""
import db, normalize, suggest, llm, pii, refdocs

TOOLS = [
    {"name": "search_requests", "description": "요청 주체·제목·원문·항목·지표에 키워드가 포함된 요구서를 찾는다(최근순).",
     "input_schema": {"type": "object", "properties": {"keyword": {"type": "string", "description": "검색어(예: 등원율, 감사실, 2026-06-30)"}}, "required": ["keyword"]}},
    {"name": "request_detail", "description": "요구서 1건의 항목·제출본·초안·검토 이력.",
     "input_schema": {"type": "object", "properties": {"request_id": {"type": "integer"}}, "required": ["request_id"]}},
    {"name": "submission_values", "description": "제출본의 확정 수치(센터별 값·정의·집계기간·추출시점·원자료 버전·출처). center로 좁힐 수 있음.",
     "input_schema": {"type": "object", "properties": {"submission_id": {"type": "integer"}, "center": {"type": "string"}}, "required": ["submission_id"]}},
    {"name": "past_values_for", "description": "특정 지표·기준일로 과거에 제출한 값 전부(어느 기관에 언제 냈는지 포함).",
     "input_schema": {"type": "object", "properties": {"indicator": {"type": "string"}, "base_date": {"type": "string", "description": "YYYY-MM-DD"}}, "required": ["indicator", "base_date"]}},
    {"name": "diff_reasons", "description": "담당자가 입력한 수치 차이 사유 기록(최근순, 최대 50건). indicator·center·base_date로 좁힐 수 있다 — 특정 센터의 사유를 찾을 때는 center를 꼭 준다.",
     "input_schema": {"type": "object", "properties": {"indicator": {"type": "string"}, "center": {"type": "string"}, "base_date": {"type": "string", "description": "YYYY-MM-DD"}}}},
    {"name": "indicator_history", "description": "지표 1개의 제출 이력을 요구서·제출본 단위로 요약(요청 주체·제출일·기준일·값 건수·원자료 버전·추출시점). '어디에 언제 냈나' 질문은 이 도구 하나로 답할 수 있다.",
     "input_schema": {"type": "object", "properties": {"indicator": {"type": "string"}, "base_date": {"type": "string", "description": "선택. YYYY-MM-DD"}}, "required": ["indicator"]}},
    {"name": "overview", "description": "요구서 현황(기한·확정 제출본 수)과 요청 주체별·반복 지표 통계.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "indicators", "description": "지표 사전(정규 지표명과 동의어)과 데이터 카탈로그(출처·담당).", "input_schema": {"type": "object", "properties": {}}},
    {"name": "center_info", "description": "센터 명부(통합 대시보드에서 가져온 기초자료)에서 센터 1곳을 찾는다: 표준 이름·교육청·지역·시설 유형·규모·개소일·정원·주말 운영·2025 운영/2026 선정 여부. 이름 일부로도 찾는다.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "list_centers", "description": "센터 명부를 조건으로 거른다(지역·교육청·유형·시설·상태 글자 포함). 비우면 전체. 결과에 교육청별·연도별 집계(n)가 들어 있으니 개수는 그 숫자를 그대로 쓰고 직접 세지 않는다. 목록은 60곳까지만.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "예: '경남', '학교 밖', '2026', '취소'"}}}},
] + refdocs.TOOLS

PERSON_KEYS = ("submitted_by", "entered_by", "reviewer", "assignee", "loaded_by", "uploaded_by", "sent_by", "created_by", "updated_by")   # 직원 이름은 API로 보내지 않는다

def _strip(obj):
    """도구 결과에서 사람 이름 필드 제거(재귀)."""
    if isinstance(obj, dict): return {k: _strip(v) for k, v in obj.items() if k not in PERSON_KEYS}
    if isinstance(obj, list): return [_strip(x) for x in obj]
    return obj

def _values(submission_id: int, center: str | None = None):
    """제출본 값: 근거(정의·집계기간·추출시점·버전)는 지표·기준일 묶음마다 한 번만, 값은 [센터, 값] 쌍으로 — 432행이 8,000자에서 잘리던 것을 막는다."""
    rows = db.get_values(submission_id)
    if center: rows = [r for r in rows if center in str(r.get("center"))]
    groups: dict = {}
    for r in rows:
        g = groups.setdefault((r.get("indicator"), r.get("base_date")), {"indicator": r.get("indicator"), "base_date": r.get("base_date"), "definition": r.get("definition"), "calc_period": r.get("calc_period"),
                                                                         "extract_date": r.get("extract_date"), "source_version": r.get("source_version"), "values": []})
        g["values"].append([r.get("center"), r.get("value")])
    return {"submission_id": submission_id, "n_values": len(rows), "groups": list(groups.values())}

def _past_values(indicator: str, base_date: str):
    """같은 지표·기준일로 과거에 낸 값을 제출본별로 묶는다(제출본마다 기관·제출일·근거 한 번 + [센터, 값])."""
    by: dict = {}
    for r in db.past_values_for(indicator, base_date):
        g = by.setdefault(r["submission_id"], {"submission_id": r["submission_id"], "requester": r.get("requester"), "request_title": r.get("request_title"), "submitted_date": r.get("submitted_date"),
                                               "definition": r.get("definition"), "calc_period": r.get("calc_period"), "extract_date": r.get("extract_date"), "source_version": r.get("source_version"), "values": []})
        g["values"].append([r.get("center"), r.get("value")])
    return {"indicator": indicator, "base_date": base_date, "submissions": list(by.values())}

def _centers(query=None):
    rows = db.list_centers(query or None, limit=500)
    by_edu, by_year = {}, {"2025 운영": 0, "2026 선정": 0}
    for c in rows:
        by_edu[c.get("edu") or "미기재"] = by_edu.get(c.get("edu") or "미기재", 0) + 1
        if c.get("year25"): by_year["2025 운영"] += 1
        if c.get("year26"): by_year["2026 선정"] += 1
    return {"n": len(rows), "by_edu": dict(sorted(by_edu.items(), key=lambda x: -x[1])), "by_year": by_year,
            "centers": [{k: c.get(k) for k in ("name", "edu", "region", "facility", "type", "size", "open_date", "capacity", "status")} for c in rows[:60]],
            "note": "60곳 넘는 목록은 생략. 개수는 n·by_edu·by_year를 그대로 인용." if len(rows) > 60 else ""}

def _indicator_history(indicator: str, base_date: str | None = None):
    con = db.connect()
    q = """SELECT r.id AS request_id, r.requester, r.title, s.id AS submission_id, s.submitted_date, s.status, v.base_date,
                  COUNT(*) AS n_values, MIN(v.extract_date) AS extract_date, MIN(v.source_version) AS source_version, MIN(v.definition) AS definition
           FROM submission_values v JOIN submissions s ON s.id=v.submission_id JOIN requests r ON r.id=s.request_id
           WHERE v.indicator=?""" + (" AND v.base_date=?" if base_date else "") + " GROUP BY s.id, v.base_date ORDER BY s.submitted_date DESC"
    rows = con.execute(q, (indicator, base_date) if base_date else (indicator,)).fetchall(); con.close()
    return [dict(r) for r in rows]

_RAW = {
    "search_requests": lambda keyword: db.search_requests(keyword),
    "request_detail": lambda request_id: db.request_detail(int(request_id)),
    "submission_values": lambda submission_id, center=None: _values(int(submission_id), center),
    "past_values_for": lambda indicator, base_date: _past_values(indicator, base_date),
    "indicator_history": lambda indicator, base_date=None: _indicator_history(indicator, base_date)[:40],
    "diff_reasons": lambda indicator=None, center=None, base_date=None: db.all_reasons(indicator, 50, center, base_date),
    "overview": lambda: (lambda st: {"requests": sorted(db.request_overview(), key=lambda r: (r.get("confirmed_submissions") or 0, r.get("due_date") or ""))[:30], "by_requester": st[0], "by_indicator": st[1]})(db.requester_stats()),
    "indicators": lambda: {"지표 사전": normalize.CANON, "가진 자료(이름·정의·기간)": db.indicator_catalog(), "데이터 카탈로그": {k: {"출처": v[0], "담당": v[1]} for k, v in suggest.CATALOG.items()}},
    "center_info": lambda name: (db.list_centers(name, limit=5) or {"note": f"'{name}'과 맞는 센터가 명부에 없습니다. 명부는 '지표 데이터' 화면에서 대시보드 저장 파일을 가져오면 채워집니다."}),
    "list_centers": lambda query=None: _centers(query),
    **refdocs.HANDLERS,
}
def _wrap(fn):
    def h(*a, **kw): return pii.redact_obj(_strip(fn(*a, **kw)))   # 직원 이름 제거 + 전화·이메일 등 마스킹 후 API로
    return h
HANDLERS = {name: _wrap(fn) for name, fn in _RAW.items()}

SYSTEM = """당신은 EBS 지역교육협력부의 대외 요구자료 기록(기록 DB)·센터 명부·참고 문서를 조회해 답하는 보조입니다.
규칙:
(1) 반드시 도구로 조회한 결과만 근거로 답하고, 조회되지 않은 것은 '기록에 없음'이라고 말합니다. 원인·배경을 추측하지 않습니다('~로 보입니다', '~와 연관' 금지).
(2) 같은 지표·기준일의 제출값이 서로 다른 센터가 보이면 답하기 전에 diff_reasons(center 지정)로 기록된 사유를 찾아 그대로 인용하고, 없으면 '사유 기록 없음'이라고 적습니다.
(3) 수치는 레코드 값을 그대로 인용합니다. 평균·합계·차이값·증감률을 계산하지 않습니다. 두 값이 다르면 나란히 보여 주고 '다름'이라고만 표시합니다. 개수는 도구가 돌려준 집계(n 등)를 그대로 씁니다.
(4) 답에는 근거가 된 요구번호(#id)·제출본번호·제출일·요청 기관을 적습니다. '어떤 지표를 어디에 언제 냈나'는 indicator_history로 먼저 확인하고, 센터별 값이 필요할 때만 past_values_for·submission_values를 씁니다.
(5) 사업 자체에 대한 질문(이용 대상·비용·운영 시간·인원 구성·절차·근거)은 search_docs로 참고 문서를 찾아 그 문구만 근거로 답하고 문서 이름과 쪽을 밝힙니다. 센터의 지역·유형·개소일·정원은 center_info / list_centers.
(6) 도구 결과가 '잘림'으로 표시되면 전체가 아니므로 인자로 범위를 좁혀 다시 조회합니다. 날짜 표현('작년', '7월')은 사용자 메시지의 오늘 날짜를 기준으로 범위를 정합니다.
(7) 한국어 설명체로 간결하게. 이모지·장식 기호를 쓰지 않고, 도구 이름을 답에 쓰지 않습니다(우리말로). 표가 적절하면 마크다운 표(20행 이하). 사용자에게 "조회를 요청해 달라"고 하지 말고 필요한 도구를 직접 호출합니다."""

def ask(question: str) -> dict:
    """{"text", "trace", "turns", "how"}"""
    if not llm.available():
        return {"text": None, "trace": [], "turns": 0, "how": "키 없음 — 키워드 검색"}
    try:
        import datetime as _dt
        r = llm.run_tools(f"[오늘 {_dt.date.today()}] {question}", SYSTEM, TOOLS, HANDLERS, max_turns=8, max_tokens=6000, purpose="이력 질의")
        r["how"] = f"AI({llm.model_label()}) · 조회 {len(r['trace'])}회"; return r
    except Exception as e:
        return {"text": None, "trace": [], "turns": 0, "how": f"AI 오류: {llm.explain_error(e)}"}

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

# 화면 진행 문구용 우리말 단계명
llm.TOOL_LABELS.update({"search_requests": "요구서 찾기", "request_detail": "요구서 상세 보기", "submission_values": "제출값 보기", "past_values_for": "같은 기준일 제출값 찾기",
                        "diff_reasons": "차이 사유 기록 찾기", "indicator_history": "지표 제출 이력 보기", "overview": "현황 집계", "indicators": "지표 사전 보기",
                        "center_info": "센터 명부 찾기", "list_centers": "센터 명부 거르기", "search_docs": "참고 문서 찾기", "list_docs": "참고 문서 목록"})
