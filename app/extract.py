"""요구서 분석: 요청 주체·접수일·제출기한·요구 항목(지표명·기준일·기간·단위) 추출.
ANTHROPIC_API_KEY가 있으면 Claude API, 없으면 규칙 기반(정규식)으로 동작. 두 경로의 출력 형식은 같다.
항목 필드: item_text(원문) / indicator(사전 지표명 또는 null) / base_date(YYYY-MM-DD 또는 null) / period(기간 표현 또는 null) / unit(센터별·전체·연도별·월별 또는 null)"""
import os, re, datetime as dt
from normalize import normalize_all, CANON
import llm

INDICATORS = list(CANON)

def today() -> dt.date:
    """연도 없는 날짜의 연도 보완 기준. 환경변수 APP_TODAY(YYYY-MM-DD)가 있으면 그 날(테스트·시연 재현용)."""
    v = (os.environ.get("APP_TODAY") or "").strip()
    try: return dt.date.fromisoformat(v) if v else dt.date.today()
    except ValueError: return dt.date.today()
ITEM_FIELDS = ["item_text", "indicator", "base_date", "period", "unit"]

# 항목 줄머리: 1. 1) (1) ① 가. 가) ○ - · • □ ■
BULLET = r"^(?:\(?\d{1,2}[.)]|[①-⑳]|[가-힣][.)]|[○●◦◎□■\-·•▪])\s*"
# 항목이 아닌 공문 관례 문장(관련 근거·안내문)
NOT_ITEM = re.compile(r"^(관련|위\s|상기|아\s*래|붙임|끝\.?$|※)")

def _norm_date(s: str, year_hint: int | None = None) -> str | None:
    """'2026. 6. 30.' '2026-06-30' \"'26. 6. 30.\" '26.6.30' '6월 30일' '9/19' → 'YYYY-MM-DD'. 연도 없으면 year_hint(없으면 올해)."""
    s = (s or "").strip()
    m = re.search(r"(?<!\d)'?(\d{4}|\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})(?!\d)", s)
    if m:
        y = int(m.group(1)); y = y + 2000 if y < 100 else y
        return f"{y:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(?<!\d)(\d{1,2})\s*(?:월|/)\s*(\d{1,2})\s*일?(?!\d)", s)
    if m:
        y = year_hint or today().year
        return f"{y:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None

def _period(body: str) -> str | None:
    """기간 표현: 2024~2026년 / 2026년 1~6월 / 6~8월 / 최근 3년 / 상반기·하반기 / 최근 3개월"""
    m = re.search(r"(\d{4})\s*년?\s*[~∼–-]\s*(\d{4})\s*년?", body)
    if m: return f"{m.group(1)}~{m.group(2)}"
    m = re.search(r"(?:(\d{4})\s*년\s*)?(\d{1,2})\s*[~∼–-]\s*(\d{1,2})\s*월", body)
    if m: return (f"{m.group(1)}년 " if m.group(1) else "") + f"{m.group(2)}~{m.group(3)}월"
    m = re.search(r"최근\s*\d+\s*(?:년|개월|분기)|(?:\d{4}\s*년\s*)?(?:상반기|하반기|\d\s*분기)|연도별", body)
    return m.group(0) if m else None

def _unit(body: str) -> str | None:
    for k in ("센터별", "연도별", "월별", "지역별"):
        if k in body: return k
    return "전체" if re.search(r"전체|전국|총\s", body) else None

def rule_based(text: str) -> dict:
    out = {"requester": None, "received_date": None, "due_date": None, "title": None, "items": []}
    m = (re.search(r"(?:요구|요청)\s*(?:주체|기관|자|위원)\s*[|:：]\s*(.+)", text)
         or re.search(r"^([가-힣A-Za-z]+)-\d+\s*\(\s*\d{4}", text, re.M)                       # 공문 번호 '감사실-1234 (2026. 8. 11.)'
         or re.search(r"^\[?(.*?(?:의원실|의원|감사실|감사원|교육부|국회|일보|신문|방송)[^\]\n]*)\]?", text, re.M))
    if m: out["requester"] = m.group(1).strip(" |")
    m = re.search(r"(?:제목|요구자료명|자료명)\s*[|:：]\s*(.+)", text)
    if m: out["title"] = m.group(1).strip(" |")
    m = re.search(r"(?:접수일|수신일|요청일|시행일)\s*[|:：]?\s*([0-9년월일.\-/' ]+)", text) or re.search(r"^\S+-\d+\s*\(([0-9. ]+)\)", text, re.M)
    if m: out["received_date"] = _norm_date(m.group(1))
    yh = int(out["received_date"][:4]) if out["received_date"] else None
    m = (re.search(r"(?:제출\s*기한|기한|제출일)\s*[|:：]?\s*([0-9년월일.\-/' ]+)", text)
         or re.search(r"([0-9년월일.\-/' ]+?)\s*(?:\([가-힣]\))?\s*(?:오전|오후)?\s*(?:\d{1,2}:\d{2})?\s*까지", text))
    if m: out["due_date"] = _norm_date(m.group(1), yh)
    if yh is None and out["due_date"]: yh = int(out["due_date"][:4])       # 접수일이 없으면 기한의 연도를, 그것도 없으면 문서의 다른 연도를 힌트로
    if yh is None:
        m4 = re.search(r"(?<!\d)(20\d{2})(?!\d)", text); yh = int(m4.group(1)) if m4 else None
    for line in text.splitlines():
        s = line.strip().lstrip("| ").strip()
        if not re.match(BULLET, s): continue
        body = re.sub(BULLET, "", s).strip()
        if len(body) < 4 or NOT_ITEM.match(body): continue
        if re.match(r"^\d{1,2}[.)]", s) and re.search(r"바랍니다|알림|계획|관련\s*[:：]", body): continue  # 공문 본문 번호 문단
        # 기준일: '기준'/'현재' 앞의 날짜. 여러 개면 항목을 날짜별로 나눈다.
        dates = [_norm_date(d, yh) for d in re.findall(r"('?\d{2,4}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}\.?|\d{1,2}\s*월\s*\d{1,2}\s*일)(?=[^가-힣]{0,6}(?:기준|현재|및))", body)]
        if not re.search(r"기준|현재", body): dates = []
        dates = [d for d in dict.fromkeys(dates) if d] or [None]
        inds = normalize_all(body) or [(None, None)]
        for bd in dates:
            for ind, alias in inds:
                out["items"].append({"item_text": body, "indicator": ind, "base_date": bd, "period": _period(body),
                                     "unit": _unit(body), "matched_by": alias})
    return out

SYSTEM = """당신은 EBS(한국교육방송공사) 지역교육협력부의 대외 요구자료 담당자를 돕는 추출기입니다.
의원실·감사·교육부·언론 등에서 온 요구서 원문에서 항목을 구조화합니다. 원문에 없는 것은 추정하지 않고 null로 둡니다."""

PROMPT = """다음 요구서에서 정보를 추출해 JSON으로만 출력하세요.

형식:
{"requester": 요청 주체(기관·부서·의원실 이름. 담당자 개인 이름은 넣지 말 것) 또는 null,
 "received_date": 접수일·문서 시행일 "YYYY-MM-DD" 또는 null,
 "due_date": 제출 기한 "YYYY-MM-DD" 또는 null,
 "title": 요구 제목·자료명 또는 null,
 "items": [{"item_text": 항목 원문 그대로,
            "indicator": 지표명(다음 목록 중 하나만: %s. 목록에 없으면 null. 목록 지표의 정의·산출 방식·차이 사유를 묻는 항목도 그 지표),
            "base_date": 항목에 '기준'·'현재'로 명시된 날짜 "YYYY-MM-DD" 또는 null,
            "period": 기간 표현 원문("2024~2026", "2026년 1~6월", "최근 3년", "2026년 상반기") 또는 null,
            "unit": "센터별" | "전체" | "연도별" | "월별" | "지역별" | null}]}

규칙:
1. 항목은 실제 자료 요구만. 관련 근거·안내·인사말·'아래와 같이 요청합니다' 같은 문장은 항목이 아님.
2. 한 항목에 지표가 여러 개면(예: "등원율 및 등록 학생 수", "예산 및 집행률") 지표마다 별도 원소로 나누되 item_text는 같은 원문을 반복.
3. 한 항목에 기준일이 여러 개면(예: "3. 31. 및 6. 30. 기준") 기준일마다 별도 원소.
4. 날짜 표기 '26. 6. 30. / 2026.6.30 / 6월 30일 현재 / 9/19 는 모두 YYYY-MM-DD로. 연도가 없으면 접수일 → 제출기한 → 문서의 다른 날짜 → 오늘(%s) 순서로 연도를 채운다(null로 두지 말 것).
5. 접수일·제출기한을 항목의 base_date로 쓰지 말 것. 연도별·기간 요구는 base_date가 아니라 period.
6. 요구서에 없는 지표·날짜를 만들지 말 것.

요구서:
%s"""

def _nullable(t: str) -> dict: return {"type": [t, "null"]}

def schema() -> dict:
    """구조화 출력용 JSON 스키마. indicator는 사전 지표명 또는 null만 허용 → 지어낸 지표가 형식 단계에서 차단된다."""
    item = {"type": "object", "additionalProperties": False,
            "properties": {"item_text": {"type": "string"},
                           "indicator": {"anyOf": [{"type": "string", "enum": INDICATORS}, {"type": "null"}]},
                           "base_date": _nullable("string"), "period": _nullable("string"),
                           "unit": {"anyOf": [{"type": "string", "enum": ["센터별", "전체", "연도별", "월별", "지역별"]}, {"type": "null"}]}},
            "required": ITEM_FIELDS}
    return {"type": "object", "additionalProperties": False,
            "properties": {"requester": _nullable("string"), "received_date": _nullable("string"), "due_date": _nullable("string"),
                           "title": _nullable("string"), "items": {"type": "array", "items": item}},
            "required": ["requester", "received_date", "due_date", "title", "items"]}

def llm_based(text: str) -> dict:
    res = llm.ask_json(PROMPT % (", ".join(INDICATORS), today().isoformat(), text), SYSTEM, 12000, purpose="요구서 추출", schema=schema())
    items = []
    for it in res.get("items") or []:
        if not isinstance(it, dict) or not it.get("item_text"): continue
        d = {k: it.get(k) for k in ITEM_FIELDS}
        d["base_date"] = _norm_date(str(d["base_date"])) if d.get("base_date") else None   # 형식 강제
        items.append(d)
    res["items"] = items
    for k in ("received_date", "due_date"):
        res[k] = _norm_date(str(res.get(k))) if res.get(k) else None
    return res

def extract(text: str) -> tuple[dict, str]:
    """(결과, 사용한 방식) 반환. Claude에는 개인정보 패턴(전화·이메일·주민번호·계좌)을 마스킹한 원문을 보낸다('_redacted'에 치환 수).
    Claude 오류 시 규칙 기반으로 대체하고 '_error'에 사유."""
    if llm.available():
        try:
            import pii
            safe, n = pii.redact(text)
            res = llm_based(safe)
            if n: res["_redacted"] = n
            return res, f"Claude API ({llm.model_label()})" + (f" · 개인정보 패턴 {n}건 마스킹 후 전송" if n else "")
        except Exception as e:
            r = rule_based(text); r["_error"] = f"{type(e).__name__}: {e}"
            return r, "규칙 기반(API 오류로 대체)"
    return rule_based(text), "규칙 기반(API 키 없음)"
