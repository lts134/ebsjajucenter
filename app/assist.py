"""담당자 판단을 돕는 AI 보조 2종. 둘 다 '결정'이 아니라 '후보·질문'만 내고, 근거를 함께 보여 준다. 키가 없으면 규칙으로 동작.
1) reason_candidates: 차이 센터의 사유 문구 후보 — 대조 단서(근거 필드 변경)와 과거에 입력된 사유 기록을 근거로.
2) foresee: 회신 초안·수치·대조 결과를 보고 요구 주체가 던질 법한 후속 질문과 준비할 자료를 예측."""
import json, re
import pandas as pd
import llm, db, pii

# ---------- 1) 차이 사유 후보 ----------
def _parse_clue(clue: str) -> list[tuple[str, str, str]]:
    """'추출시점 변경(2026-07-03→2026-09-10); 원자료 버전 변경(출결 v1→출결 v2)' → [(필드, 전, 후)]"""
    out = []
    for m in re.finditer(r"(지표 정의|집계기간|추출시점|원자료 버전) 변경\((.*?)→(.*?)\)(?:;|$)", clue or ""):
        out.append((m.group(1), m.group(2).strip(), m.group(3).strip()))
    return out

def reason_candidates_rule(row: dict, past: list[dict]) -> list[dict]:
    """row: 대조 결과 한 행(indicator, center, base_date, old_value, new_value, 단서). past: db.all_reasons(indicator)."""
    cands = []
    for field, a, b in _parse_clue(row.get("단서", "")):
        if field == "추출시점": cands.append({"문구": f"원자료 추출 시점 차이({a} → {b})로 그 사이 반영된 출결 사후 보정분이 포함된 재산출값", "근거": f"대조 단서: {field} 변경"})
        elif field == "원자료 버전": cands.append({"문구": f"원자료 버전 변경({a} → {b})에 따른 재산출값", "근거": f"대조 단서: {field} 변경"})
        elif field == "집계기간": cands.append({"문구": f"집계기간 조정({a} → {b})에 따른 재산출값", "근거": f"대조 단서: {field} 변경"})
        elif field == "지표 정의": cands.append({"문구": f"지표 정의 변경({a} → {b})에 따른 재산출값. 과거 제출값과 직접 비교 불가", "근거": f"대조 단서: {field} 변경"})
    seen = {c["문구"] for c in cands}
    for p in past:                                   # 같은 지표에 과거 입력된 사유(문구 중복 제외, 최근순)
        if len(cands) >= 4: break
        txt = (p.get("reason") or "").strip()
        if txt and txt not in seen:
            cands.append({"문구": txt, "근거": f"과거 입력 사유({(p.get('submitted_date') or p.get('created_at') or '')[:10]}, {p.get('requester') or '-'}, {p.get('center')})"}); seen.add(txt)
    if not cands:
        cands.append({"문구": "[확인 필요] 근거 필드(정의·집계기간·추출시점·원자료 버전)가 같아 차이 원인을 특정하지 못함 — 원자료 재확인 후 기재", "근거": "대조 단서 없음"})
    return cands

SYSTEM_REASON = """당신은 공공기관 자료 담당자의 보조입니다. 수치 차이의 '사유 문구 후보'를 제안하되, 근거는 반드시 입력으로 받은 대조 단서 또는 과거 입력 사유에서만 가져옵니다.
원인을 새로 추측하지 않습니다(예: '학생 수 감소 때문' 같은 추정 금지). 단서가 없으면 '[확인 필요]'를 붙인 후보 하나만 냅니다. 문구는 공문체 명사형으로 짧게."""

def reason_candidates(diff_rows: list[dict]) -> tuple[dict, str]:
    """{(indicator, center, base_date): [후보...]}, 방식. Claude가 있으면 한 번에 요청, 실패·부재 시 규칙."""
    past_by_ind = {}
    for r in diff_rows: past_by_ind.setdefault(r["indicator"], db.all_reasons(r["indicator"], limit=20))
    rule = {(r["indicator"], r["center"], r["base_date"]): reason_candidates_rule(r, past_by_ind[r["indicator"]]) for r in diff_rows}
    if not llm.available() or not diff_rows: return rule, "규칙(단서·과거 사유)"
    try:
        payload = {"차이 행": [{"key": f"{r['indicator']}|{r['center']}|{r['base_date']}", "과거값": r.get("old_value"), "신규값": r.get("new_value"), "단서": r.get("단서")} for r in diff_rows],
                   "과거 입력 사유": [{"지표": p["indicator"], "센터": p["center"], "기준일": p["base_date"], "사유": p["reason"], "입력일": (p.get("created_at") or "")[:10], "요청 주체": p.get("requester")} for ps in past_by_ind.values() for p in ps][:30]}
        schema = {"type": "object", "additionalProperties": False, "required": ["rows"],
                  "properties": {"rows": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["key", "candidates"],
                     "properties": {"key": {"type": "string"}, "candidates": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["문구", "근거"],
                        "properties": {"문구": {"type": "string"}, "근거": {"type": "string"}}}}}}}}}
        res = llm.ask_json("각 차이 행에 대해 사유 문구 후보를 1~3개씩 제안하세요. 근거에는 '대조 단서: …' 또는 '과거 입력 사유(입력일, 요청 주체, 센터)'를 적습니다.\n" + json.dumps(payload, ensure_ascii=False, default=str),
                           SYSTEM_REASON, 2000, purpose="사유 후보", schema=schema)
        out = {}
        for row in res.get("rows", []):
            parts = str(row.get("key", "")).split("|")
            if len(parts) == 3 and tuple(parts) in rule and row.get("candidates"):
                out[tuple(parts)] = [c for c in row["candidates"] if c.get("문구")][:3]
        for k in rule: out.setdefault(k, rule[k])
        return out, f"Claude({llm.model_label()}) + 규칙"
    except Exception as e:
        for v in rule.values(): v.append({"문구": "", "근거": f"(Claude 후보 실패: {type(e).__name__}) 규칙 후보만 표시"})
        return rule, "규칙(Claude 오류로 대체)"

# ---------- 2) 예상 후속 질문 ----------
def foresee_rule(req: dict, items: list[dict], values: pd.DataFrame, reasons: dict, checklist, draft: dict) -> list[dict]:
    qs = []
    diffs = [r for r in (checklist or []) if str(r.get("판정")) == "차이"]
    if diffs:
        centers = ", ".join(str(r.get("center") or r.get("센터")) for r in diffs)
        qs.append({"질문": f"{centers} 수치가 과거 제출값과 다른 사유와 산출 근거는 무엇인가", "근거": f"대조 결과 차이 {len(diffs)}건", "준비할 자료": "차이 사유·원자료 버전·추출 시점 비교표, 재산출 전후 값", "가능성": "높음"})
    inds = sorted({it["indicator"] for it in items if it.get("indicator")})
    for ind in inds:
        others = [p for p in db.past_values_for(ind, next((it["base_date"] for it in items if it.get("indicator") == ind and it.get("base_date")), None) or "") if p.get("requester") != req.get("requester")]
        if others:
            qs.append({"질문": f"같은 기준일 {ind}을(를) {others[0]['requester']}에 제출한 값과 이번 값이 일치하는가", "근거": f"동일 지표·기준일 타 기관 제출 이력 {len(others)}건", "준비할 자료": "기관별 제출값 대조표와 불일치 시 사유", "가능성": "높음"}); break
    missing = [it for it in items if it.get("indicator") and (not len(values) or not ((values["indicator"] == it["indicator"]) & (values["base_date"] == it.get("base_date"))).any())]
    if missing:
        qs.append({"질문": f"[확인 필요]로 남긴 {len(missing)}개 항목({', '.join(sorted({m['indicator'] for m in missing}))})은 언제 제출하는가", "근거": "초안에 확정 수치 없는 항목", "준비할 자료": "항목별 산출 일정·담당", "가능성": "높음"})
    if any(re.search(r"산출|정의|방식", it.get("item_text") or "") for it in items) or any(it.get("indicator") == "등원율" for it in items):
        qs.append({"질문": "등원율의 정의(분모·분자)와 집계 기간, 원자료는 무엇인가", "근거": "비율 지표는 산출 방식 질의가 잦음", "준비할 자료": "지표 정의서, 집계 기간, 원자료 출처", "가능성": "중간"})
    if any(it.get("period") and not it.get("base_date") for it in items):
        qs.append({"질문": "기간 요구 항목(연도별·최근 3년)의 기준 시점을 어떻게 잡았는가", "근거": "기준일 없는 기간 항목 존재", "준비할 자료": "연도별 기준 시점 명시", "가능성": "중간"})
    qs.append({"질문": "센터별 차이가 큰 센터의 원인과 개선 계획은 무엇인가", "근거": "센터별 수치 제출 시 통상 질의", "준비할 자료": "최고·최저 센터 현황 메모(수치 해석은 담당 부서 확인)", "가능성": "낮음"})
    return qs[:6]

SYSTEM_FORESEE = """당신은 국회·감사·교육부 자료 요구에 대응해 온 공공기관 실무자의 보조입니다. 회신 초안과 수치, 과거 제출 이력을 보고 요구 주체가 던질 법한 후속 질문을 예측합니다.
규칙: 질문은 입력된 요구 항목·초안·대조 결과·제출 이력에 근거한 것만. 수치를 새로 계산하거나 해석(증가·감소·개선)하지 않습니다. 각 질문에 근거와 준비할 자료를 붙입니다. 과장 없이 담백하게."""

def foresee(req: dict, items: list[dict], values: pd.DataFrame, reasons: dict, checklist, draft: dict) -> tuple[list[dict], str]:
    rule = foresee_rule(req, items, values, reasons, checklist, draft)
    if not llm.available(): return rule, "규칙"
    try:
        inds = sorted({it["indicator"] for it in items if it.get("indicator")})
        history = []
        for ind in inds:
            for bd in sorted({it.get("base_date") for it in items if it.get("indicator") == ind and it.get("base_date")}):
                for p in db.past_values_for(ind, bd)[:40]:
                    history.append({"지표": ind, "기준일": bd, "요청 주체": p["requester"], "제출일": p["submitted_date"]})
        hist_summary = sorted({(h["지표"], h["기준일"], h["요청 주체"], h["제출일"]) for h in history})
        payload = {"요구": pii.redact_obj({k: req.get(k) for k in ("requester", "received_date", "due_date", "title")}),
                   "요구 항목": pii.redact_obj(list(dict.fromkeys(it.get("item_text") for it in items if it.get("item_text")))),   # 중복 원문 제거 + 연락처 마스킹
                   "확정 수치 요약": {"지표": sorted({v for v in values["indicator"]}) if len(values) else [], "센터 수": int(values["center"].nunique()) if len(values) else 0, "기준일": sorted({v for v in values["base_date"]}) if len(values) else []},
                   "대조 결과(코드)": [{"센터": r.get("center") or r.get("센터"), "판정": r.get("판정"), "단서": r.get("단서")} for r in (checklist or []) if str(r.get("판정")) == "차이"],
                   "담당자 입력 사유": {" ".join(k): v for k, v in reasons.items() if v},
                   "같은 지표·기준일을 다른 기관에 제출한 이력": [dict(zip(("지표", "기준일", "요청 주체", "제출일"), h, strict=True)) for h in hist_summary][:20],
                   "초안": draft}
        schema = {"type": "object", "additionalProperties": False, "required": ["questions"],
                  "properties": {"questions": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["질문", "근거", "준비할 자료", "가능성"],
                     "properties": {"질문": {"type": "string"}, "근거": {"type": "string"}, "준비할 자료": {"type": "string"}, "가능성": {"type": "string", "enum": ["높음", "중간", "낮음"]}}}}}}
        res = llm.ask_json("요구 주체가 이 회신을 받고 던질 법한 후속 질문 3~6개를 가능성 높은 순으로 제안하세요.\n" + json.dumps(payload, ensure_ascii=False, default=str),
                           SYSTEM_FORESEE, 2000, purpose="후속 질문 예측", schema=schema)
        qs = [q for q in res.get("questions", []) if q.get("질문")]
        return (qs or rule), f"Claude({llm.model_label()})" if qs else "규칙(Claude 응답 없음)"
    except Exception as e:
        return rule, f"규칙(Claude 오류로 대체: {type(e).__name__})"
