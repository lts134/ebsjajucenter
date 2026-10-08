"""가진 자료 맞춤: 요구 항목의 이름이 사전·저장된 지표와 달라도 뜻이 같거나 포괄하는 자료를 찾아 연결한다.
예) '센터별 월별 관리인원' ↔ 저장된 '현원', '센터별 투입 인력 현황' → '학습코디네이터 수'·'행정지원인력 수'(항목 하나를 지표 둘로 펼침).
규칙(사전 동의어·낱말 겹침)으로 먼저 맞추고, 남으면 모델에 저장된 지표 목록을 주고 고르게 한다(없으면 빈 목록).
값은 계산하지 않고, 맞춘 사실은 notes로 돌려 담당자와 모델이 보게 한다. 지표가 아닌 설명 항목은 plan이 참고 문서로 넘긴다."""
from __future__ import annotations
import re
import db, normalize, llm

STOP = {"센터별", "센터", "월별", "연도별", "현황", "자료", "기준", "전체", "및", "현재", "요청", "제출", "관련", "대상", "각", "년", "월", "일", "최근", "지역별", "분기별", "총",
        "학생", "인원", "건수", "횟수", "비율", "명단", "목록", "현원수", "운영", "실적"}        # 흔한 낱말은 낱말 겹침에 쓰지 않는다(사전 동의어·동의어 표로만)
SYNONYMS = {                                                     # 요구 표현의 낱말 → 저장된 지표 이름에서 찾을 낱말(부분 일치)
    "현원": ["관리인원", "이용중", "이용 중", "재원"], "재원": ["관리인원", "현원"], "관리인원": ["현원", "재원", "이용 중", "이용중"], "관리": ["현원", "재원"],
    "퇴소": ["하차", "퇴소"], "퇴소자": ["하차", "퇴소"], "하차": ["퇴소"], "탈락": ["하차", "퇴소"],
    "인력": ["코디네이터", "코디", "행정지원", "행정 지원", "운영인력", "운영 인력", "인력", "직원", "강사"], "투입": ["코디네이터", "행정지원", "인력"], "운영인력": ["코디네이터", "행정지원", "인력"],
    "코디네이터": ["코디"], "학생수": ["인원", "학생 수"], "이용자": ["이용 중", "등록 학생"], "예산": ["사업비", "운영비", "집행"], "출석률": ["등원율"], "출석": ["등원"],
}

def _words(text: str) -> list[str]:
    out = []
    for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", str(text or "")):
        if t in STOP: continue
        out.append(t)
        if re.fullmatch(r"[가-힣]{3,}", t): out.append(t[:-1])            # 조사·접미가 붙은 말('관리인원을')의 앞부분도 후보
    return list(dict.fromkeys(out))

def _flat(s: str) -> str: return re.sub(r"[\s·()（）\-_/,.]", "", str(s or "")).lower()

def rule_match(item_text: str, names: list[str], current: str | None = None) -> list[str]:
    """규칙 맞춤: ① 사전 동의어로 정규화한 지표가 저장돼 있으면 그것 ② 항목(또는 사전 지표·동의어)의 낱말이 저장 이름에 들어 있거나 그 반대 ③ 동의어 표."""
    if not names: return []
    canon, _ = normalize.normalize(item_text)
    if canon and canon in names: return [canon]
    if current and current in names: return [current]
    words = [w for w in dict.fromkeys(_words(item_text)) if len(w) >= 2]
    aliases = [a for c in (canon, current) if c and c in normalize.CANON for a in normalize.CANON[c]]     # 사전 동의어는 통째로 비교(낱말로 쪼개면 '학생'처럼 흔한 말이 엉뚱한 지표에 걸린다)
    hits = []
    for n in names:
        fn = _flat(n)
        if any(_flat(w) in fn for w in words if len(_flat(w)) >= 2) or any(_flat(a) in fn for a in aliases if len(_flat(a)) >= 2): hits.append(n); continue
        if any(len(_flat(x)) >= 2 and _flat(x) in _flat(item_text) for x in _words(n)): hits.append(n); continue
        if any(_flat(s) in fn for w in words for s in SYNONYMS.get(w, [])): hits.append(n)
    hits = list(dict.fromkeys(hits))
    return hits if len(hits) <= 4 else []                               # 너무 넓게 걸리면(5개 이상) 규칙으로는 정하지 않는다

MATCH_SYSTEM = "당신은 공공기관 요구자료 담당자를 돕는 자료 맞춤 보조입니다. 요구 항목에 맞는 저장 지표를 목록에서만 고릅니다. 이름이 달라도 같은 뜻이면 고르고, 포괄적인 요구는 관련 지표 여러 개를 고르며, 맞는 것이 없으면 빈 목록으로 둡니다. 값을 추정하거나 지표를 지어내지 않습니다."

def llm_match(items: list[dict], catalog: list[dict]) -> dict[str, dict]:
    """모델 맞춤: {item_text: {"indicators": [...], "reason": str}}. 저장 지표 목록(정의·기간)을 주고 enum으로 묶는다."""
    names = [c["indicator"] for c in catalog]
    if not items or not names or not llm.available(): return {}
    cat_lines = [f"- {c['indicator']}: {c.get('definition') or '정의 없음'} ({c.get('first')}~{c.get('last')}, 센터 {c.get('n_centers')}곳)" for c in catalog[:80]]
    prompt = ("저장된 지표 목록:\n" + "\n".join(cat_lines) + "\n\n요구 항목:\n" + "\n".join(f"- {it['item_text']}" for it in items)
              + "\n\n항목마다 맞는 저장 지표를 고르세요. 예: '관리인원'은 '현원'과 같은 뜻이면 '현원', '투입 인력 현황'은 '학습코디네이터 수'와 '행정지원인력 수' 둘 다, '사업 필요성'처럼 수치 지표가 아닌 설명 요구는 빈 목록. "
              "reason에는 왜 그 지표인지 한 문장. JSON만 출력: {\"items\": [{\"item_text\": ..., \"indicators\": [...], \"reason\": ...}]}")
    schema = {"type": "object", "additionalProperties": False, "required": ["items"],
              "properties": {"items": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["item_text", "indicators", "reason"],
                                                                    "properties": {"item_text": {"type": "string"}, "indicators": {"type": "array", "items": {"type": "string", "enum": names}}, "reason": {"type": "string"}}}}}}
    res = llm.ask_json(prompt, MATCH_SYSTEM, 6000, purpose="자료 맞춤", schema=schema)
    out = {}
    for d in (res.get("items") if isinstance(res, dict) else res) or []:
        inds = [x for x in (d.get("indicators") or []) if x in names]
        if inds: out[d.get("item_text")] = {"indicators": list(dict.fromkeys(inds)), "reason": d.get("reason") or ""}
    return out

def match_items(items: list[dict], use_llm: bool = True) -> tuple[list[dict], list[str]]:
    """지표가 없거나 가진 자료에 없는 항목을 저장 지표에 맞춘다. 여러 지표에 맞으면 항목을 같은 원문으로 펼친다. (새 항목 목록, 설명 줄들)"""
    catalog = db.indicator_catalog(); names = [c["indicator"] for c in catalog]
    if not names or not items: return items, []
    out, notes, pending = [], [], []
    for it in items:
        ind = it.get("indicator")
        if ind and ind in names: out.append(it); continue
        if ind and db.past_dates_for(ind): out.append(it); continue                 # 과거 제출값으로는 낼 수 있는 지표
        chosen = [c for c in (it.get("data_candidates") or []) if c in names]       # 요구서를 읽을 때 모델이 가진 자료 목록을 보고 고른 것이 있으면 그것이 우선
        if chosen: _apply(out, notes, it, chosen, "모델 판단: 요구서를 읽을 때 가진 자료 목록에서 고름"); continue
        cands = rule_match(it.get("item_text", ""), names, ind)
        if cands: _apply(out, notes, it, cands, "규칙: 이름·동의어 맞춤")
        else: pending.append(it)
    if pending and use_llm:
        try: found = llm_match(pending, catalog)
        except Exception as e: found = {}; notes.append(f"모델 맞춤 실패({type(e).__name__}) — 규칙 결과만 적용")
    else: found = {}
    for it in pending:
        f = found.get(it.get("item_text"))
        if f: _apply(out, notes, it, f["indicators"], "모델: " + (f.get("reason") or "뜻이 같은 지표"))
        else: out.append(it)
    # 원래 순서 유지(펼친 항목은 원 항목 바로 뒤)
    order = {id(it): i for i, it in enumerate(items)}
    out.sort(key=lambda x: (order.get(id(x), order.get(id(x.get("_from")), 10**6)), x.get("_k", 0)))
    for x in out: x.pop("_from", None); x.pop("_k", None)
    return out, notes

def _apply(out, notes, it, cands, how):
    first = dict(it); first["indicator"] = cands[0]; first["matched_by"] = f"가진 자료 맞춤({how})"; first["_from"] = it; first["_k"] = 0
    out.append(first)
    for k, c in enumerate(cands[1:], 1):
        extra = dict(it); extra["indicator"] = c; extra["matched_by"] = first["matched_by"]; extra["_from"] = it; extra["_k"] = k; out.append(extra)
    notes.append(f"'{it.get('item_text')}' → {'·'.join(cands)} ({how})")
