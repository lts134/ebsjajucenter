"""지표 정규화: 표현이 달라도 같은 지표로. 규칙(동의어 사전) + API 키 있으면 Claude 보조.
실데이터 도입 시 CANON에 부서 지표를 추가하면 추출·검색·제안·충족 검사가 모두 같은 이름을 쓴다."""
import re
import llm

CANON = {
    "등원율": ["등원율", "출석률", "출석율", "센터 이용률", "이용률", "등원 현황", "출석 현황", "등원 실적"],
    "등록 학생 수": ["등록 학생 수", "등록 학생", "등록 인원", "등록자 수", "이용 학생 수", "참여 학생 수", "학생 수"],
    "센터 현황": ["센터 현황", "센터 목록", "센터 운영 현황", "운영 현황", "센터별 현황", "운영기관 목록"],
    "운영 센터 수": ["운영 센터 수", "센터 수", "개소 수", "센터 개소", "지역별 분포"],
    "예산 집행률": ["예산 집행률", "집행률", "집행 실적", "예산 집행 현황", "예산 집행"],     # '집행액'(금액)은 집행률(비율)과 다른 지표라 제외
    "운영 예산": ["운영 예산", "예산 현황", "예산액", "사업비", "운영비"],
    "프로그램 운영 실적": ["프로그램 운영 실적", "프로그램 실적", "프로그램 운영", "강좌 운영", "강좌명"],
}
_ORDER = sorted(((alias, canon) for canon, al in CANON.items() for alias in al), key=lambda x: -len(x[0]))

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "")

def normalize(text: str) -> tuple[str | None, str | None]:
    """(정규 지표명, 매칭된 표현) — 긴 표현 우선, 첫 번째 것만"""
    t = _clean(text)
    for alias, canon in _ORDER:
        if alias in t: return canon, alias
    return None, None

def normalize_all(text: str) -> list[tuple[str, str]]:
    """한 문장에 든 지표를 모두 찾는다(긴 표현 우선, 겹치는 구간은 한 번만). 등장 순서로 반환.
    예: '운영 예산 및 집행률' → [(운영 예산, '운영 예산'), (예산 집행률, '집행률')]"""
    t = _clean(text)
    found, taken = [], []
    for alias, canon in _ORDER:
        for m in re.finditer(re.escape(alias), t):
            s, e = m.span()
            if any(a < e and s < b for a, b in taken): continue
            taken.append((s, e)); found.append((s, canon, alias))
    out, seen = [], set()
    for _, canon, alias in sorted(found):
        if canon not in seen: seen.add(canon); out.append((canon, alias))
    return out

def normalize_items(items: list[dict]) -> list[dict]:
    """indicator가 비면 사전으로 채우고, 있으면 사전 표기로 통일."""
    for it in items:
        if not it.get("indicator"):
            canon, alias = normalize(it.get("item_text", ""))
            it["indicator"] = canon; it["matched_by"] = alias
        else:
            canon, alias = normalize(it["indicator"])
            if canon: it["indicator"] = canon
            elif it["indicator"] not in CANON: it["indicator"] = None   # 사전에 없는 이름은 버린다(AI가 지어낸 지표 방지)
    return items

SYSTEM = "당신은 공공기관 요구자료 담당자를 돕는 분류기입니다. 목록에 있는 지표명만 쓰고, 애매하면 null로 둡니다."

def normalize_llm(items: list[dict]) -> list[dict]:
    """API 키가 있을 때: 규칙으로 못 잡은 항목만 Claude에게 지표 분류를 맡긴다."""
    todo = [it for it in items if not it.get("indicator")]
    if not todo or not llm.available(): return items
    try:
        prompt = ("다음 요구 항목을 지표명으로 분류하세요. 지표 목록: %s.\n"
                  "규칙: 목록에 없는 지표는 null. 목록의 지표를 설명·정의·사유 형태로 묻는 항목도 그 지표로 분류. 새 지표명을 만들지 말 것.\n"
                  "JSON만 출력: {\"items\": [{\"item_text\": ..., \"indicator\": ...}]}\n%s"
                  % (", ".join(CANON), "\n".join(f"- {it['item_text']}" for it in todo)))
        schema = {"type": "object", "additionalProperties": False, "required": ["items"],
                  "properties": {"items": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["item_text", "indicator"],
                                                                        "properties": {"item_text": {"type": "string"},
                                                                                       "indicator": {"anyOf": [{"type": "string", "enum": list(CANON)}, {"type": "null"}]}}}}}}
        res = llm.ask_json(prompt, SYSTEM, 8000, purpose="지표 분류", schema=schema)
        got = {d.get("item_text"): d.get("indicator") for d in (res.get("items") if isinstance(res, dict) else res) or []}
        for it in todo:
            g = got.get(it["item_text"])
            if g in CANON: it["indicator"] = g; it["matched_by"] = "Claude"
    except Exception as e:
        for it in todo: it["_norm_error"] = str(e)
    return items
